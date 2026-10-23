"""Audited manual PAPER recovery protocol. CLI is read-only; real writes BLOCKED.

The internal transition is exercised ONLY with isolated fixtures and a Fake authority.
No production evidence/approval/anti-rollback authority is implemented or selectable.
"""
import ast
import copy
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import time

from ops.local_preflight import ROOT, _private, _literal, cold_lock, _json
from ops.paper_recovery_diagnostic import snapshot, SafeParser
from ops.paper_session import ExistingPaperStore

HEX=re.compile(r'[0-9a-f]{64}')
ATTEMPT=re.compile(r'[0-9a-f]{32}')


class RecoveryBlocked(ValueError):
    pass


def require(condition, code):
    if not condition:raise RecoveryBlocked(code)


def encoded(value):
    return json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')


def digest(raw):return hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return digest(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8'))


def initial_cash():
    tree=ast.parse((ROOT/'config.py').read_text(encoding='utf-8'))
    require(all(_literal(tree,k) is False for k in ('REAL_TRADING','SLACK_NOTIFICATIONS_ENABLED')),'POLICY_NOT_OFF')
    policy=_json((ROOT/'.github/automation/policy.json').read_text(encoding='utf-8'))
    cost=_json((ROOT/'.github/automation/zero-cost.json').read_text(encoding='utf-8'))
    require(policy['enabled'] is False and cost['enabled'] is False and cost['mode']=='fake-only'
            and type(cost['extra_spend_krw']) is int and cost['extra_spend_krw']==0
            and all(cost[k] is False for k in ('allow_api_billing','allow_credit_purchase',
                'allow_usage_expansion','allow_paid_runner','allow_paid_hosting_or_database',
                'allow_auth_export','allow_live_worker','allow_live_publish','allow_merge')),'POLICY_NOT_OFF')
    return _literal(tree,'START_CASH')


class RecoveryStore(ExistingPaperStore):
    """Reuse the exact existing, non-creating state lock, not engine.open()."""
    def validate_existing(self,path):
        return snapshot(path,self.initial_cash)[0]


def locked_plan(state,backup,expected_sha):
    require(isinstance(expected_sha,str) and HEX.fullmatch(expected_sha),'EXPECTED_SHA_REQUIRED')
    p=_private(state,{'.json'});b=_private(backup,{'.json'})
    require(p!=b,'DISTINCT_COLD_BACKUP_REQUIRED')
    saved,a,sha=snapshot(p,initial_cash());old,_,bsha=snapshot(b,initial_cash())
    require(sha==expected_sha,'SOURCE_CAS_MISMATCH')
    require(saved.get('halt_reason')=='PAPER_SESSION_FAILURE','HALT_CAUSE_NOT_SUPPORTED')
    require('manual_recovery' not in saved,'RECOVERY_ALREADY_PRESENT')
    # Only a post-incident cold backup of the EXACT current evidence is accepted.
    require(sha==bsha and saved==old,'POST_INCIDENT_BACKUP_MISMATCH')
    financial={k:v for k,v in saved.items() if k!='halt_reason'}
    plan={'schema_version':1,'operation':'AUDITED_PAPER_RECOVERY_NO_RESTART',
          'source_sha256':sha,'backup_sha256':bsha,
          'source_path_sha256':digest(str(p).encode()),'backup_path_sha256':digest(str(b).encode()),
          'accounting_sha256':fingerprint(financial),'halt_class':'SESSION_FAILURE_GENERIC',
          'confirmed_buys':a['buy_count'],'confirmed_sells':a['sell_count'],
          'pending_orders':0,'implementation_sha256':digest(Path(__file__).read_bytes()),
          'cause':'UNPROVEN_REQUIRES_EXTERNAL_EVIDENCE',
          'rollback_protection':'UNPROVEN_REQUIRES_INDEPENDENT_AUTHORITY'}
    return p,b,saved,plan


def inspect_plan(state,backup,expected_sha):
    result={'status':'BLOCKED','state_writes':0,'network_calls':0,'model_calls':0,
            'recovery_authorized':False,'restart_authorized':False,
            'blockers':['EXTERNAL_CAUSE_EVIDENCE_UNVERIFIED','INDEPENDENT_ROLLBACK_AUTHORITY_UNAVAILABLE',
                        'ONE_TIME_USER_APPROVAL_UNVERIFIED','REAL_WRITE_ADAPTER_NOT_ENABLED']}
    try:
        p=_private(state,{'.json'})
        with cold_lock(p) as owner:
            result['ownership']=owner
            require(owner=='READY_IDLE_AT_SNAPSHOT','OWNERSHIP_UNVERIFIED')
            _,_,_,plan=locked_plan(p,backup,expected_sha)
            result.update(local_evidence='RECONCILED_READ_ONLY',plan=plan,plan_sha256=fingerprint(plan))
    except RecoveryBlocked as exc:result['diagnostic']=str(exc)
    except Exception:result['diagnostic']='INPUT_OR_LEDGER_UNVERIFIED'
    return result


class UnavailableAuthority:
    """No booleans, JSON assertions or CLI options can replace a trusted verifier.

    A future reviewed provider must independently verify cause, no post-incident
    fills, user identity/consent, code review and a rollback-resistant single-use
    reservation bound to the entire plan and approval. No such provider exists here.
    reserve/commit are NOT wired to any remote service.
    """
    def reserve(self,plan,approval):raise RecoveryBlocked('AUTHORITY_UNAVAILABLE')
    def commit(self,plan,approval,reservation,after_sha):raise RecoveryBlocked('AUTHORITY_UNAVAILABLE')
    def verify(self,plan,approval,after_sha):raise RecoveryBlocked('AUTHORITY_UNAVAILABLE')


@dataclass(frozen=True)
class AuthorityReceipt:
    """Structured provider result, not an authentication token or a JSON permit."""
    attempt_id: str
    plan_sha256: str
    approval_sha256: str
    phase: str
    after_sha256: str | None = None


def check_receipt(receipt,plan,approval,phase,after_sha=None):
    require(type(receipt) is AuthorityReceipt and receipt.attempt_id==approval['attempt_id']
            and receipt.plan_sha256==fingerprint(plan) and receipt.approval_sha256==fingerprint(approval)
            and receipt.phase==phase and receipt.after_sha256==after_sha,'AUTHORITY_RECEIPT_UNVERIFIED')


def approval_check(plan,approval,now):
    require(type(approval) is dict and set(approval)=={
        'attempt_id','plan_sha256','expires_at','evidence_sha256','review_sha256'},'APPROVAL_SCHEMA_UNVERIFIED')
    require(isinstance(approval['attempt_id'],str) and ATTEMPT.fullmatch(approval['attempt_id']),'APPROVAL_SCHEMA_UNVERIFIED')
    for k in ('plan_sha256','evidence_sha256','review_sha256'):
        require(isinstance(approval[k],str) and HEX.fullmatch(approval[k]),'APPROVAL_SCHEMA_UNVERIFIED')
    require(approval['plan_sha256']==fingerprint(plan),'APPROVAL_PLAN_MISMATCH')
    require(type(approval['expires_at']) in (int,float) and now<approval['expires_at']<=now+900,'APPROVAL_EXPIRED_OR_UNBOUNDED')


def sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def exclusive_write(path,raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as handle:
        handle.write(raw);handle.flush();os.fsync(handle.fileno())
    sync_dir(path.parent)


def audit_parent(p):
    parent=p.parent/'.paper-recovery'
    if not parent.exists():
        parent.mkdir(mode=0o700);sync_dir(p.parent)
    require(not parent.is_symlink() and parent.is_dir(),'AUDIT_PATH_UNSAFE')
    info=parent.stat()
    require(info.st_uid==os.getuid() and not info.st_mode&0o077,'AUDIT_PATH_UNSAFE')
    return parent


def owner_check(store):
    require(store._lock is not None,'OWNER_LOST')
    held=os.fstat(store._lock.fileno());actual=Path(str(store.path)+'.lock').lstat()
    require((held.st_dev,held.st_ino,held.st_nlink)==(actual.st_dev,actual.st_ino,1),'OWNER_CHANGED')


def _transition(state,backup,expected_sha,approval,*,authority=None,clock=time.time):
    """Protocol primitive, not a live CLI adapter. Tests inject a Fake authority.

    A reservation is consumed BEFORE any local audit/state mutation; a crash never
    refunds it. Incomplete attempts are not replayed, rolled back or initialized.
    """
    result={'status':'BLOCKED','restart_authorized':False,'network_calls':0,'model_calls':0}
    store=None;reserved=False
    try:
        require(os.name=='posix','PLATFORM_NOT_CERTIFIED')
        store=RecoveryStore(state,initial_cash());store.acquire()
        p,b,saved,plan=locked_plan(state,backup,expected_sha)
        approval_check(plan,approval,clock())
        authority=authority if authority is not None else UnavailableAuthority()
        reservation=authority.reserve(copy.deepcopy(plan),copy.deepcopy(approval));reserved=True
        check_receipt(reservation,plan,approval,'RESERVED')
        # A reserved approval is never automatically retried, even before a write.
        approval_check(plan,approval,clock());owner_check(store)
        parent=audit_parent(p);bundle=parent/approval['attempt_id']
        bundle.mkdir(mode=0o700);sync_dir(parent)  # O_EXCL semantics: never reuse.
        before=p.read_bytes();require(digest(before)==expected_sha,'SOURCE_CAS_MISMATCH')
        exclusive_write(bundle/'before.json',before)
        record={'plan':plan,'approval':approval,'scope':'MANUAL_PAPER_RECOVERY_NO_START'}
        exclusive_write(bundle/'intent.json',encoded(record))
        target=copy.deepcopy(saved);target['halt_reason']=None
        target['manual_recovery']={'schema_version':1,'attempt_id':approval['attempt_id'],
                                   'plan_sha256':fingerprint(plan),'restart_authorized':False}
        after=encoded(target);after_sha=digest(after)
        exclusive_write(bundle/'after.json',after)
        # CAS immediately before the original atomic/fsync storage operation.
        owner_check(store);approval_check(plan,approval,clock())
        require(digest(p.read_bytes())==expected_sha and digest(b.read_bytes())==plan['backup_sha256'],'SOURCE_OR_BACKUP_CAS_MISMATCH')
        require(locked_plan(p,b,expected_sha)[3]==plan,'PLAN_CHANGED_BEFORE_WRITE')
        store.save(target)
        require(p.read_bytes()==after,'POST_WRITE_MISMATCH')
        exclusive_write(bundle/'commit.json',encoded({'after_sha256':after_sha,'intent_sha256':digest(encoded(record))}))
        committed=authority.commit(copy.deepcopy(plan),copy.deepcopy(approval),reservation,after_sha)
        check_receipt(committed,plan,approval,'COMMITTED',after_sha)
        result.update(status='PENDING_SEPARATE_READ_ONLY_VERIFICATION',attempt_id=approval['attempt_id'],after_sha256=after_sha)
    except RecoveryBlocked as exc:result['diagnostic']=str(exc)
    except Exception:result['diagnostic']='IO_OR_PROOF_UNVERIFIED'
    finally:
        if store is not None:store.close()
    if reserved and result['status']=='BLOCKED':result['status']='HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY'
    return result


def verify_transition(state,attempt_id,*,authority=None):
    """Separate, read-only check AFTER transition releases its lock. No success
    without externally confirmed reservation/commit; never permits bot startup.
    """
    result={'status':'HALTED','state_writes':0,'restart_authorized':False,
            'rollback_protection':'UNPROVEN_WITHOUT_EXTERNAL_AUTHORITY'}
    try:
        require(isinstance(attempt_id,str) and ATTEMPT.fullmatch(attempt_id),'ATTEMPT_INVALID')
        p=_private(state,{'.json'})
        with cold_lock(p) as owner:
            require(owner=='READY_IDLE_AT_SNAPSHOT','OWNERSHIP_UNVERIFIED')
            saved,_,sha=snapshot(p,initial_cash())
            parent=p.parent/'.paper-recovery'
            require(not parent.is_symlink() and parent.is_dir(),'AUDIT_PATH_UNSAFE')
            bundle=parent/attempt_id
            require(not bundle.is_symlink() and bundle.is_dir(),'AUDIT_PATH_UNSAFE')
            def read(name):return _private(bundle/name,{'.json'}).read_bytes()
            before=read('before.json');intent=read('intent.json');after=read('after.json');commit=_json(read('commit.json').decode())
            record=_json(intent.decode());plan=record['plan'];approval=record['approval']
            require(plan['source_path_sha256']==digest(str(p).encode()),'AUDIT_SOURCE_PATH_MISMATCH')
            require(digest(before)==plan['source_sha256']==plan['backup_sha256'],'AUDIT_BEFORE_MISMATCH')
            require(sha==digest(after)==commit['after_sha256'] and digest(intent)==commit['intent_sha256'],'AUDIT_AFTER_MISMATCH')
            original=_json(before.decode());target=copy.deepcopy(original);target['halt_reason']=None
            target['manual_recovery']={'schema_version':1,'attempt_id':attempt_id,'plan_sha256':fingerprint(plan),'restart_authorized':False}
            require(saved==target and saved.get('halt_reason') is None,'TRANSITION_FIELDS_MISMATCH')
            require(approval['attempt_id']==attempt_id and approval['plan_sha256']==fingerprint(plan),'APPROVAL_PLAN_MISMATCH')
            # Historical committed approval expiry is checked by the authority.
            proof=authority if authority is not None else UnavailableAuthority()
            verified=proof.verify(copy.deepcopy(plan),copy.deepcopy(approval),sha)
            check_receipt(verified,plan,approval,'COMMITTED',sha)
            result.update(status='RECOVERY_VERIFIED_NO_START_AUTHORITY',after_sha256=sha,
                          rollback_protection='EXTERNAL_AUTHORITY_CHECKED_NOT_LOCAL_HASH_PROOF')
    except RecoveryBlocked as exc:result['diagnostic']=str(exc)
    except Exception:result['diagnostic']='AUDIT_OR_PROOF_UNVERIFIED'
    return result


def main(argv=None):
    parser=SafeParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=ROOT/'runtime/paper-state.json')
    parser.add_argument('--backup',type=Path)
    parser.add_argument('--expected-sha')
    parser.add_argument('--verify-attempt')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    if args.apply:
        result={'status':'BLOCKED','diagnostic':'REAL_WRITE_ADAPTER_NOT_ENABLED','state_writes':0,'restart_authorized':False}
    elif args.verify_attempt:
        result=verify_transition(args.state,args.verify_attempt)
    else:result=inspect_plan(args.state,args.backup,args.expected_sha)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 2  # No CLI output grants execution or recovery permission.


if __name__=='__main__':raise SystemExit(main())
