"""Independent PAPER experiment. Offline default; explicit creation != execution."""
from decimal import Decimal
from functools import partial
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import subprocess
import uuid
import unicodedata

from ops.local_preflight import ROOT, _private, _json, cold_lock
from ops.paper_manual_recovery import initial_cash, exclusive_write
from ops.paper_recovery_diagnostic import SafeParser
from ops.paper_session import ExistingPaperStore, validate_state, offline_report, public_analyzer_factory
from services.public_market import PublicDataError, PUBLIC_ERROR_CODES
from trading.execution import ExecutionEngine
from trading.state_store import StateStore
from utils.process_lock import lock_handle

SCOPE='NEW_INDEPENDENT_PAPER_EXPERIMENT_NOT_RECOVERY'
STATE='experiment-state.json'
MAX_CANDIDATES=30
MAX_REQUESTS=64
ID=re.compile(r'[0-9a-f]{32}')


class ExperimentError(ValueError):pass


def require(ok,code):
    if not ok:raise ExperimentError(code)


def encoded(value):return json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
def sha(raw):return hashlib.sha256(raw).hexdigest()


def directory(value):
    require(os.name=='posix','PLATFORM_PRIVATE_OWNERSHIP_UNVERIFIED')
    require(value is not None,'EXPLICIT_DIRECTORY_REQUIRED')
    p=Path(value).expanduser().absolute()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',p.name),'INVALID_EXPERIMENT_NAME')
    require(not any(q.is_symlink() for q in (p,*p.parents)),'SYMLINK_PATH_FORBIDDEN')
    p=p.resolve()
    require(not p.is_relative_to(ROOT) and not ROOT.is_relative_to(p),'PROJECT_PATH_FORBIDDEN')
    require(not any((q/'.git').is_file() or (q/'.git/HEAD').is_file() for q in p.parents),'GIT_CHECKOUT_PATH_FORBIDDEN')
    require(not any(q.name.lower() in ('.ssh','.aws','.codex','.git','keychains') for q in p.parents),'PRIVATE_AUTH_PATH_FORBIDDEN')
    for q in (p.parent,)+((p,) if p.exists() else ()):
        info=q.stat()
        require(q.is_dir() and info.st_uid==os.getuid() and not info.st_mode&0o077,'PRIVATE_DIRECTORY_REQUIRED')
        if sys.platform=='darwin':
            acl=subprocess.run(['/bin/ls','-lde',str(q)],capture_output=True,text=True,timeout=5)
            require(acl.returncode==0 and len(acl.stdout.splitlines())==1,'EXTENDED_ACL_UNVERIFIED')
    return p


def location_hash(p):
    # Conservatively treat case/Unicode spelling variants as the same reservation,
    # including on macOS case-insensitive APFS. Never recreate a lost account alias.
    return sha(unicodedata.normalize('NFC',str(p)).casefold().encode())


def receipt_path(p):return p.parent/('.paper-experiment-'+location_hash(p)+'.json')
def first_use_path(p):return p.parent/('.paper-experiment-used-'+location_hash(p)+'.json')


def identity(identifier,p,last=None):
    return {'schema_version':1,'scope':SCOPE,'experiment_id':identifier,
            'directory_sha256':sha(str(p).encode()),'initial_cash':'1000000','last_session_id':last}


def create(value):
    result={'status':'BLOCKED','account_scope':SCOPE,'network_calls':0,'automatic_start':False}
    try:
        require(sys.version_info[:2]==(3,12),'PYTHON312_REQUIRED')
        require(initial_cash()==1_000_000,'FIXED_EXPERIMENT_CAPITAL_REQUIRED')
        p=directory(value);require(not p.exists(),'DIRECTORY_ALREADY_EXISTS_NO_RESET')
        state=ExecutionEngine(p/STATE,1_000_000,real_trading=False).state
        state['paper_experiment']=identity(uuid.uuid4().hex,p)
        raw=encoded(state)
        receipt={'schema_version':1,'identity':state['paper_experiment'],'initial_state_sha256':sha(raw)}
        # Parent reservation survives leaf loss; never removed on failure/retry.
        exclusive_write(receipt_path(p),encoded(receipt))
        p.mkdir(mode=0o700)
        exclusive_write(p/(STATE+'.lock'),b'')
        with (p/(STATE+'.lock')).open('r+b') as owner:
            lock_handle(owner,initialize=False)
            exclusive_write(p/'initial-state.json',raw)
            exclusive_write(p/'experiment.json',encoded(receipt))
            exclusive_write(p/STATE,raw)  # Last creation commit; never engine.open/reset.
        result.update(status='CREATED_NEW_INDEPENDENT_EXPERIMENT',initial_cash='1000000',
                      inherited_positions=False,initial_orders=0,verification_required=True)
    except ExperimentError as exc:result['reason']=str(exc)
    except Exception:result['reason']='CREATION_UNVERIFIED_NO_RETRY_OR_CLEANUP'
    return result


def validate_identity(saved,state):
    p=directory(Path(state).parent)
    require(Path(state)==p/STATE,'EXPERIMENT_STATE_ALIAS_FORBIDDEN')
    receipt_raw=_private(receipt_path(p),{'.json'}).read_bytes()
    require(_private(p/'experiment.json',{'.json'}).read_bytes()==receipt_raw,'CREATION_RECEIPT_MISMATCH')
    r=_json(receipt_raw.decode());meta=saved['paper_experiment']
    require(type(meta) is dict and isinstance(meta.get('experiment_id'),str) and ID.fullmatch(meta['experiment_id']),
            'EXPERIMENT_IDENTITY_UNVERIFIED')
    require(meta==identity(meta['experiment_id'],p,meta.get('last_session_id')),'EXPERIMENT_IDENTITY_UNVERIFIED')
    require(meta['last_session_id'] is None or (isinstance(meta['last_session_id'],str) and ID.fullmatch(meta['last_session_id'])),
            'EXPERIMENT_SESSION_INVALID')
    require(r['identity']==identity(meta['experiment_id'],p),'CREATION_RECEIPT_MISMATCH')
    initial=_private(p/'initial-state.json',{'.json'}).read_bytes()
    require(sha(initial)==r['initial_state_sha256'],'INITIAL_STATE_MISMATCH')
    expected=ExecutionEngine(p/STATE,1_000_000,real_trading=False).state
    expected['paper_experiment']=identity(meta['experiment_id'],p)
    require(_json(initial.decode())==expected,'INITIAL_INVENTORY_OR_CAPITAL_NOT_EMPTY')
    if meta['last_session_id'] is not None:validate_first_use(saved,p)
    return p,r


def validate_first_use(saved,p):
    used=_json(_private(first_use_path(p),{'.json'}).read_text(encoding='utf-8'))
    require(used.get('experiment_id')==saved['paper_experiment']['experiment_id'],'FIRST_USE_RECEIPT_MISMATCH')


def marker(p):return _json(_private(p/'session.json',{'.json'}).read_text(encoding='utf-8'))


def resumable(saved,state):
    p,r=validate_identity(saved,state)
    require(not (p/'blocked.json').exists(),'UNCONFIRMED_SESSION_FINALIZATION')
    current=sha(Path(state).read_bytes());last=saved['paper_experiment']['last_session_id']
    if not (p/'session.json').exists():
        require(not first_use_path(p).exists() and last is None and current==r['initial_state_sha256'],
                'UNKNOWN_SESSION_NOT_RESET')
        return 'NEVER_STARTED'
    require(first_use_path(p).exists(),'FIRST_USE_RECEIPT_MISSING')
    validate_first_use(saved,p)
    m=marker(p)
    require(isinstance(m.get('session_id'),str) and ID.fullmatch(m['session_id']),'SESSION_RECORD_INVALID')
    if m['phase']=='STOPPED_VERIFIED':
        require(last==m['session_id'] and current==m['end_sha256'],'SESSION_LEDGER_MISMATCH')
    elif m['phase']=='PREPARATION_FAILED':
        require(last==m['previous_session'] and current==m['before_sha256']==m['end_sha256'],'SESSION_LEDGER_MISMATCH')
    else:raise ExperimentError('UNCERTAIN_SESSION_NO_AUTOMATIC_RESTART')
    return m['phase']


def save_marker(owner,value):
    # Borrow the already-held ORIGINAL state lock; no second SQLite/lock framework.
    writer=StateStore(owner.path.parent/'session.json');writer._lock=owner._lock
    try:writer.save(value)
    finally:writer._lock=None  # The original owner alone releases the shared handle.


def begin(value):
    require(sys.version_info[:2]==(3,12),'PYTHON312_REQUIRED')
    p=directory(value);owner=ExistingPaperStore(p/STATE,1_000_000)
    try:
        require(initial_cash()==1_000_000,'FIXED_EXPERIMENT_CAPITAL_REQUIRED')
        owner.acquire();saved=owner.load();resumable(saved,owner.path)
        token=uuid.uuid4().hex
        if not first_use_path(p).exists():
            exclusive_write(first_use_path(p),encoded({'experiment_id':saved['paper_experiment']['experiment_id']}))
        m={'session_id':token,'previous_session':saved['paper_experiment']['last_session_id'],
           'phase':'IN_PROGRESS','before_sha256':sha(owner.path.read_bytes())}
        save_marker(owner,m)
        return token
    finally:owner.close()


def validate_start(saved,state,token):
    p,_=validate_identity(saved,state)
    require(isinstance(token,str) and ID.fullmatch(token),'EXPERIMENT_EXPLICIT_SESSION_REQUIRED')
    require(not (p/'blocked.json').exists(),'UNCONFIRMED_SESSION_FINALIZATION')
    m=marker(p)
    require(m['phase']=='IN_PROGRESS' and m['session_id']==token
            and m['previous_session']==saved['paper_experiment']['last_session_id']
            and m['before_sha256']==sha(Path(state).read_bytes()),'EXPERIMENT_START_CAS_MISMATCH')
    validate_first_use(saved,p)


def finish(value,token,result):
    p=directory(value);owner=ExistingPaperStore(p/STATE,1_000_000)
    try:
        owner.acquire();saved=owner.load();validate_first_use(saved,p);m=marker(p)
        require(not (p/'blocked.json').exists(),'UNCONFIRMED_SESSION_FINALIZATION')
        require(m['phase']=='IN_PROGRESS' and m['session_id']==token,'SESSION_OWNER_MISMATCH')
        leaf=result.get('last_snapshot') or result
        if (result.get('status')=='STOPPED' and leaf.get('engine_started') is True
                and leaf.get('post_validation')=='VERIFIED_UNDER_OWNER_LOCK'
                and saved['paper_experiment']['last_session_id']==token):
            m['phase']='STOPPED_VERIFIED'
        elif (leaf.get('engine_started') is False and sha(owner.path.read_bytes())==m['before_sha256']
                and saved['paper_experiment']['last_session_id']==m['previous_session']):
            m['phase']='PREPARATION_FAILED'  # Failure, never a successful stop.
        else:raise ExperimentError('UNCONFIRMED_SESSION_NO_RESTART')
        m['end_sha256']=sha(owner.path.read_bytes());save_marker(owner,m)
        return {'status':m['phase'],'automatic_restart':False,'account_scope':SCOPE}
    except Exception:
        # A failure after marker rename must remain blocked even if bytes look clean.
        try:
            if owner._lock is not None:
                exclusive_write(p/'blocked.json',encoded({'status':'UNCONFIRMED_FINALIZATION_NO_RESET'}))
        except Exception:pass  # Total storage loss cannot be repaired/hidden by this tool.
        leaf=result.get('last_snapshot') or result
        known={'PAPER_STALE_TICK','PAPER_CLOCK_DISCONTINUITY','PAPER_STREAM_TIMEOUT','PAPER_MARKET_STALE',
               'PAPER_INVALID_TICK','SESSION_FAILED','STREAM_CLOSE_UNCONFIRMED','WORKER_FAILED',
               'PAPER_POST_VALIDATION_FAILURE','PUBLIC_CLIENT_CLOSE_UNCONFIRMED'} | PUBLIC_ERROR_CODES
        reason=leaf.get('reason') if leaf.get('status')=='HALTED' and leaf.get('reason') in known else 'UNCONFIRMED_SESSION_NO_RESTART'
        return {'status':'HALTED','reason':reason,'session_finalization':'HALTED_NO_RESTART',
                'supervisor_reason':result.get('supervisor_reason'),'forced_termination':result.get('forced_termination',False),
                'account_scope':SCOPE,'automatic_restart':False}
    finally:owner.close()


def inspect_experiment(value):
    result={'status':'BLOCKED','account_scope':SCOPE,'execution_authorized':False,'network_calls':0}
    try:
        p=directory(value)
        result['diagnostic']=offline_report(p/STATE,outbox=p/'notifications/outbox.sqlite3')
        require(result['diagnostic']['preflight']['checks'].get('paper_slack_automation_off')=='READY','SAFE_POLICY_NOT_OFF')
        with cold_lock(p/STATE) as status:
            require(status=='READY_IDLE_AT_SNAPSHOT','EXPERIMENT_OWNER_UNVERIFIED')
            saved=validate_state(_json(_private(p/STATE,{'.json'}).read_text(encoding='utf-8')),p/STATE,1_000_000)
            result['session_state']=resumable(saved,p/STATE)
            result['status']='READ_ONLY_NO_EXECUTION_AUTHORITY'
    except ExperimentError as exc:result['reason']=str(exc)
    except Exception:result['reason']='EXPERIMENT_UNVERIFIED_NO_INITIALIZATION'
    return result


def prepare_markets(saved,client,top,tickers,options,emit,analyzer_factory=None):
    eligible=options.get('eligible_top');limit=options.get('candidate_limit')
    wanted=eligible if eligible is not None else (len(tickers) if tickers else top)
    require(type(wanted) is int and 1<=wanted<=MAX_CANDIDATES,'SELECTION_LIMIT_INVALID')
    if eligible is not None:require(type(limit) is int and wanted<=limit<=MAX_CANDIDATES,'CANDIDATE_LIMIT_INVALID')
    candidates=list(tickers) if tickers else client.top_tickers(limit if eligible is not None else top)
    require(len(set(candidates))==len(candidates) and len(candidates)<=MAX_CANDIDATES,'CANDIDATES_INVALID')
    report={'mode':'CONTIGUOUS_CANDLES_ELIGIBLE' if eligible is not None else 'STRICT_EXPLICIT_OR_TOP',
            'requested_count':wanted,'candidate_limit':len(candidates),'ranked_candidates':candidates,
            'excluded':[],'selected':[],'final_subscription':[],'inspected_candidates':0,
            'held_added':[],'request_budget':MAX_REQUESTS,
            'held_required':sorted(t for t,v in saved['positions'].items() if Decimal(v['quantity'])>0)}
    frames={};gaps=set();analyzers={};candle_times={}
    started_wall=client.clock();started_mono=client.monotonic()
    def remember(t,frame):
        # PublicMarketClient returns KST-naive original bars. No timestamp repair.
        require(len(frame)==200 and frame.index.tz is None,'CANDIDATES_INVALID')
        candle_times[t]=frame.index[-1].tz_localize('Asia/Seoul').timestamp()
    class CachedClient:
        def candles(self,t):
            if t in frames:return frames.pop(t)
            return client.candles(t)
    factory=analyzer_factory or public_analyzer_factory(CachedClient())
    try:
        for t in candidates:
            report['inspected_candidates']+=1
            try:frames[t]=client.candles(t)
            except PublicDataError as exc:
                if str(exc)!='PUBLIC_CANDLE_GAP':raise
                gaps.add(t);report['excluded'].append({'market':t,'reason':'PUBLIC_CANDLE_GAP',
                        'meaning':'NO_TRADE_INTERVAL_POSSIBLE_NOT_PROOF_OF_API_FAILURE'})
                continue
            remember(t,frames[t])
            analyzers[t]=factory(t);analyzers[t].update_data();report['selected'].append(t)
            if eligible is not None and len(report['selected'])==wanted:break
        if eligible is None and gaps:raise PublicDataError('PUBLIC_CANDLE_GAP')
        require(len(report['selected'])==wanted,'INSUFFICIENT_CONTIGUOUS_CANDIDATES')
        held=sorted(t for t,v in saved['positions'].items() if Decimal(v['quantity'])>0)
        require(len(set(held)|set(report['selected']))<=MAX_CANDIDATES,'HELD_MARKET_LIMIT')
        for t in held:
            require(t not in gaps,'HELD_CANDLE_GAP_RISK_MANAGEMENT_UNAVAILABLE')
            if t in report['selected']:continue
            try:frames[t]=client.candles(t)
            except PublicDataError as exc:
                if str(exc)=='PUBLIC_CANDLE_GAP':
                    report['excluded'].append({'market':t,'reason':'PUBLIC_CANDLE_GAP','role':'HELD_CANNOT_BE_DROPPED'})
                    raise ExperimentError('HELD_CANDLE_GAP_RISK_MANAGEMENT_UNAVAILABLE') from None
                raise
            remember(t,frames[t])
            analyzers[t]=factory(t);analyzers[t].update_data()
            report['selected'].append(t);report['held_added'].append(t)
        current_wall=client.clock();elapsed=client.monotonic()-started_mono
        if elapsed<0 or abs((current_wall-started_wall)-elapsed)>5:
            raise PublicDataError('PUBLIC_RESPONSE_CLOCK_UNVERIFIED')
        if any(not current_wall-180<=candle_times[t]<=current_wall+5 for t in report['selected']):
            raise PublicDataError('PUBLIC_STALE_CANDLE')
    except Exception:
        emit({'status':'PREPARATION_FAILED','account_scope':SCOPE,'selection':report,'engine_started':False})
        raise
    report['final_subscription']=report['selected'].copy()
    emit({'status':'DATA_PREPARED','account_scope':SCOPE,'selection':report,'engine_started':False})
    return report['selected'],analyzers


def main(argv=None):
    parser=SafeParser(description=__doc__)
    parser.add_argument('--directory',type=Path)
    modes=parser.add_mutually_exclusive_group()
    modes.add_argument('--create',action='store_true');modes.add_argument('--connect-public',action='store_true')
    modes.add_argument('--dry-run',action='store_true')
    parser.add_argument('--seconds',type=int)
    select=parser.add_mutually_exclusive_group()
    select.add_argument('--top',type=int);select.add_argument('--tickers',nargs='+');select.add_argument('--eligible-top',type=int)
    parser.add_argument('--candidate-limit',type=int)
    args=parser.parse_args(argv)
    def emit(r):print(json.dumps({'account_scope':SCOPE,**r},ensure_ascii=False,sort_keys=True),flush=True)
    if not args.connect_public:
        if any(v is not None for v in (args.seconds,args.top,args.tickers,args.eligible_top,args.candidate_limit)):
            parser.error('network options require explicit connection')
        r=create(args.directory) if args.create else inspect_experiment(args.directory)
        emit(r);return 0 if r['status']=='CREATED_NEW_INDEPENDENT_EXPERIMENT' else 2
    if args.seconds is None or not 1<=args.seconds<=3600:parser.error('explicit bounded seconds required')
    if args.top is not None and not 1<=args.top<=MAX_CANDIDATES:parser.error('invalid top')
    if args.eligible_top is not None:
        if args.candidate_limit is None or not 1<=args.eligible_top<=args.candidate_limit<=MAX_CANDIDATES:parser.error('invalid candidates')
    elif args.candidate_limit is not None:parser.error('candidate limit only for explicit eligibility mode')
    from services.public_market import MARKET
    if args.tickers is not None and (not 1<=len(args.tickers)<=MAX_CANDIDATES or len(set(args.tickers))!=len(args.tickers)
                                    or any(not MARKET.fullmatch(t) for t in args.tickers)):parser.error('invalid markets')
    try:
        from ops.paper_watch import supervise,_worker
        p=directory(args.directory);token=begin(p)
        options={'session_id':token,'eligible_top':args.eligible_top,'candidate_limit':args.candidate_limit}
        result=supervise(p/STATE,args.seconds,args.top or 10,args.tickers,emit=emit,
                         worker=partial(_worker,experiment=options),outbox=p/'notifications/outbox.sqlite3')
        terminal=finish(p,token,result);emit(terminal)
        return 0 if result['status']=='STOPPED' and terminal['status']=='STOPPED_VERIFIED' else 2
    except Exception:
        emit({'status':'HALTED','reason':'EXPERIMENT_UNVERIFIED_NO_RESTART','automatic_restart':False});return 2


if __name__=='__main__':raise SystemExit(main())
