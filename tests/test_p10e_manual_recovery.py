"""Audited transition on private disposable PAPER fixtures ONLY. No real authority."""
import copy
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from trading.execution import ExecutionEngine
from ops.paper_recovery_diagnostic import diagnose
from ops.paper_session import validate_state
from ops.local_preflight import cold_lock, inspect
from ops.paper_manual_recovery import (inspect_plan, _transition, verify_transition, fingerprint,
    digest, AuthorityReceipt, RecoveryBlocked, main)
import tests.test_p10c_paper_watch as fixtures


class FakeAuthority:
    """SIMULATED independent consumed-nonce witness; not a real proof provider.

    Preapproved plan/approval are held outside all files being rolled back. A JSON
    boolean, changed plan, expired approval or a restored local copy cannot approve.
    """
    def __init__(self,plan,approval):
        self.plan=copy.deepcopy(plan);self.approval=copy.deepcopy(approval)
        self.consumed=False;self.after=None

    def receipt(self,phase,after=None):
        return AuthorityReceipt(self.approval['attempt_id'],fingerprint(self.plan),fingerprint(self.approval),phase,after)

    def reserve(self,plan,approval):
        if self.consumed or plan!=self.plan or approval!=self.approval:raise RecoveryBlocked('FAKE_AUTHORITY_DENIED')
        self.consumed=True
        return self.receipt('RESERVED')

    def commit(self,plan,approval,reservation,after):
        if not self.consumed or plan!=self.plan or approval!=self.approval:raise RecoveryBlocked('FAKE_AUTHORITY_DENIED')
        self.after=after
        return self.receipt('COMMITTED',after)

    def verify(self,plan,approval,after):
        if not self.consumed or self.after!=after or plan!=self.plan or approval!=self.approval:
            raise RecoveryBlocked('FAKE_AUTHORITY_DENIED')
        return self.receipt('COMMITTED',after)


class ReadOnlyRecoveryTests(unittest.TestCase):
    setUp=fixtures.PaperWatchTests.setUp
    factory=fixtures.PaperWatchTests.factory
    run_session=fixtures.PaperWatchTests.run_session

    def prepare(self):
        self.actions=['BUY'];self.run_session()
        saved=json.loads(self.state.read_bytes());saved['halt_reason']='PAPER_SESSION_FAILURE'
        self.state.write_text(json.dumps(saved));self.state.chmod(0o600)
        self.backup=self.root/'cold-backup.json';self.backup.write_bytes(self.state.read_bytes());self.backup.chmod(0o600)
        self.before=self.state.read_bytes();self.sha=digest(self.before)

    def test_cli_default_and_apply_never_mutate_or_start(self):
        self.prepare();before_names=set(self.root.iterdir())
        for extra in ([],['--apply']):
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(main(['--state',str(self.state),'--backup',str(self.backup),'--expected-sha',self.sha]+extra),2)
            self.assertEqual(json.loads(output.getvalue())['status'],'BLOCKED')
            self.assertEqual(self.before,self.state.read_bytes())
        self.assertEqual(before_names,set(self.root.iterdir()))

    def test_no_production_proof_cannot_change_ledger(self):
        self.prepare();r=inspect_plan(self.state,self.backup,self.sha)
        self.assertFalse(r['recovery_authorized']);self.assertEqual(r['status'],'BLOCKED')
        self.assertEqual(self.before,self.state.read_bytes())
        self.assertEqual(verify_transition(self.state,'a'*32)['status'],'HALTED')

    def test_invalid_sha_backup_and_missing_source_do_not_initialize(self):
        self.prepare()
        for sha in ('bad','0'*64):
            self.assertEqual(inspect_plan(self.state,self.backup,sha)['status'],'BLOCKED')
        self.backup.write_text('{}');self.assertEqual(inspect_plan(self.state,self.backup,self.sha)['status'],'BLOCKED')
        self.assertEqual(self.before,self.state.read_bytes())
        self.state.unlink();self.assertEqual(inspect_plan(self.state,self.backup,self.sha)['status'],'BLOCKED')
        self.assertFalse(self.state.exists())


@unittest.skipUnless(os.name=='posix','Audited write/read-only ownership protocol certified only for POSIX')
class AuditedRecoveryTests(ReadOnlyRecoveryTests):
    # Avoid inheriting and double-counting the three read-only tests.
    test_cli_default_and_apply_never_mutate_or_start=None
    test_no_production_proof_cannot_change_ledger=None
    test_invalid_sha_backup_and_missing_source_do_not_initialize=None

    def setUp(self):
        super().setUp();self.prepare()
        self.plan=inspect_plan(self.state,self.backup,self.sha)['plan']
        self.approval={'attempt_id':'a'*32,'plan_sha256':fingerprint(self.plan),'expires_at':time.time()+600,
                       'evidence_sha256':'b'*64,'review_sha256':'c'*64}
        self.authority=FakeAuthority(self.plan,self.approval)
        self.bundle=self.root/'.paper-recovery'/self.approval['attempt_id']

    def transition(self,**kwargs):
        return _transition(self.state,self.backup,self.sha,self.approval,authority=self.authority,**kwargs)

    def test_confirmed_buy_transition_preserves_all_financial_data_and_halt_evidence(self):
        with patch.object(ExecutionEngine,'open',side_effect=AssertionError('No implicit reset/start')):
            r=self.transition()
        self.assertEqual(r['status'],'PENDING_SEPARATE_READ_ONLY_VERIFICATION')
        self.assertEqual((self.bundle/'before.json').read_bytes(),self.before)
        original=json.loads(self.before);saved=json.loads(self.state.read_bytes())
        self.assertIsNone(saved.pop('halt_reason'));marker=saved.pop('manual_recovery')
        original.pop('halt_reason');self.assertEqual(saved,original)
        self.assertFalse(marker['restart_authorized']);self.assertEqual(self.backup.read_bytes(),self.before)
        self.assertEqual(json.loads((self.bundle/'before.json').read_bytes())['halt_reason'],'PAPER_SESSION_FAILURE')
        verified=verify_transition(self.state,self.approval['attempt_id'],authority=self.authority)
        self.assertEqual(verified['status'],'RECOVERY_VERIFIED_NO_START_AUTHORITY');self.assertFalse(verified['restart_authorized'])
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'])['status'],'HALTED')

    def test_default_authority_and_manual_boolean_do_not_grant_permission(self):
        for authority in (None,True):
            r=_transition(self.state,self.backup,self.sha,self.approval,authority=authority)
            self.assertEqual(r['status'],'BLOCKED');self.assertEqual(self.before,self.state.read_bytes())
        self.assertFalse((self.root/'.paper-recovery').exists())

    def test_unknown_cause_or_missing_review_or_evidence_blocked(self):
        for key in ('review_sha256','evidence_sha256'):
            original=self.approval[key];self.approval[key]=''
            self.assertEqual(self.transition()['status'],'BLOCKED');self.approval[key]=original
        saved=json.loads(self.before);saved['halt_reason']='UNKNOWN_FAILURE'
        self.state.write_text(json.dumps(saved));self.backup.write_bytes(self.state.read_bytes())
        r=_transition(self.state,self.backup,digest(self.state.read_bytes()),self.approval,authority=self.authority)
        self.assertEqual(r['status'],'BLOCKED');self.assertFalse(self.authority.consumed)

    def test_pending_unknown_cash_quantity_and_cost_mismatch(self):
        for issue in ('pending','unknown','cash','quantity','average','fee'):
            saved=json.loads(self.before);order=next(iter(saved['orders'].values()))
            if issue in ('pending','unknown'):order['status']='intent' if issue=='pending' else 'UNKNOWN'
            elif issue=='cash':saved['cash']='1'
            elif issue=='fee':order['paid_fee']='1'
            else:saved['positions']['KRW-BTC']['quantity' if issue=='quantity' else 'avg_price']='1'
            self.state.write_text(json.dumps(saved));raw=self.state.read_bytes()
            self.backup.write_bytes(raw)
            r=_transition(self.state,self.backup,digest(raw),self.approval,authority=self.authority)
            self.assertEqual(r['status'],'BLOCKED');self.assertEqual(raw,self.state.read_bytes())
        self.assertFalse(self.authority.consumed)

    def test_backup_or_source_changes_during_authorization_fail_cas(self):
        for target in (self.state,self.backup):
            self.state.write_bytes(self.before);self.backup.write_bytes(self.before)
            authority=FakeAuthority(self.plan,self.approval);original=authority.reserve
            def reserve(plan,approval):
                receipt=original(plan,approval);target.write_bytes(self.before+b' ');return receipt
            authority.reserve=reserve
            r=_transition(self.state,self.backup,self.sha,self.approval,authority=authority)
            self.assertEqual(r['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
            self.assertIsNotNone(json.loads(self.state.read_bytes())['halt_reason'])
            # Do not clean up or reuse an incomplete attempt in production.
            if self.bundle.exists():
                import shutil
                shutil.rmtree(self.root/'.paper-recovery')

    def test_busy_owner_blocks_before_authority_reservation(self):
        from ops.paper_manual_recovery import RecoveryStore,initial_cash
        store=RecoveryStore(self.state,initial_cash());store.acquire()
        try:
            self.assertEqual(self.transition()['status'],'BLOCKED');self.assertFalse(self.authority.consumed)
            self.assertEqual(self.before,self.state.read_bytes())
        finally:store.close()

    def test_competing_process_cannot_acquire_during_transition(self):
        original=self.authority.reserve
        def reserve(plan,approval):
            code="from ops.local_preflight import cold_lock;from pathlib import Path;import sys\nwith cold_lock(Path(sys.argv[1])) as s:print(s)"
            p=subprocess.run([sys.executable,'-c',code,str(self.state)],capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0);self.assertIn('BLOCKED_OWNER_ACTIVE',p.stdout)
            return original(plan,approval)
        self.authority.reserve=reserve
        self.assertEqual(self.transition()['status'],'PENDING_SEPARATE_READ_ONLY_VERIFICATION')

    def test_expiry_sha_binding_and_false_authority_receipt(self):
        original=copy.deepcopy(self.approval)
        for field,value in (('expires_at',0),('expires_at',float('inf')),('plan_sha256','0'*64)):
            self.approval=copy.deepcopy(original);self.approval[field]=value
            self.assertEqual(self.transition()['status'],'BLOCKED')
        self.approval=original;self.authority.reserve=lambda *_:True
        self.assertEqual(self.transition()['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
        self.assertEqual(self.before,self.state.read_bytes())

    def test_save_failure_consumes_approval_without_reset_or_restore(self):
        with patch('trading.state_store.StateStore.save',side_effect=OSError('private-secret-canary')):
            r=self.transition()
        self.assertEqual(r['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
        self.assertTrue(self.authority.consumed);self.assertEqual(self.before,self.state.read_bytes())
        self.assertEqual(self.transition()['status'],'BLOCKED')
        self.assertNotIn('private-secret-canary',json.dumps(r))
        self.assertEqual((self.bundle/'before.json').read_bytes(),self.before)

    def test_failure_after_atomic_replace_never_claims_success_or_starts_bot(self):
        from trading.state_store import StateStore
        real_save=StateStore.save
        def failed(store,saved):real_save(store,saved);raise OSError('disk fsync uncertain')
        with patch.object(StateStore,'save',new=failed):r=self.transition()
        self.assertEqual(r['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'],authority=self.authority)['status'],'HALTED')
        saved=json.loads(self.state.read_bytes())
        with self.assertRaises(ValueError):validate_state(saved,self.state,1000000)
        self.assertNotEqual(inspect(state=self.state)['checks']['paper_ledger'],'READY_LOCAL_RECONCILIATION_ONLY')
        self.assertEqual(diagnose(self.state)['status'],'PENDING_MANUAL_RECOVERY_VERIFICATION_NO_RESTART')

    def test_post_verification_tamper_and_forged_receipt_fail(self):
        self.transition();self.authority.verify=lambda *_:False
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'],authority=self.authority)['status'],'HALTED')
        (self.bundle/'before.json').write_bytes(b'{}')
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'],authority=self.authority)['status'],'HALTED')

    def test_single_use_even_if_local_state_and_audit_are_rolled_back(self):
        self.transition();self.state.write_bytes(self.before)
        import shutil
        shutil.rmtree(self.root/'.paper-recovery')  # Simulated joint local rollback only.
        self.assertEqual(self.transition()['status'],'BLOCKED');self.assertEqual(self.before,self.state.read_bytes())
        self.assertTrue(self.authority.consumed)  # Fake external witness was not rolled back.

    def interrupted_process(self,stage):
        # Abrupt process death is real; its in-memory Fake authority is NOT a
        # durable provider. This test claims only lock release/audit/start blocking.
        code='''import json,os,sys
from tests.test_p10e_manual_recovery import FakeAuthority
from ops.paper_manual_recovery import _transition
from trading.state_store import StateStore
x=json.load(sys.stdin);authority=FakeAuthority(x['plan'],x['approval']);real=StateStore.save
def interrupted(store,saved):
 if x['stage']=='after':real(store,saved)
 os._exit(73)
StateStore.save=interrupted
_transition(x['state'],x['backup'],x['sha'],x['approval'],authority=authority)
'''
        data={'state':str(self.state),'backup':str(self.backup),'sha':self.sha,
              'approval':self.approval,'plan':self.plan,'stage':stage}
        p=subprocess.run([sys.executable,'-c',code],input=json.dumps(data),capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,73)
        self.assertEqual((self.bundle/'before.json').read_bytes(),self.before)
        self.assertFalse((self.bundle/'commit.json').exists())
        with cold_lock(self.state) as owner:self.assertEqual(owner,'READY_IDLE_AT_SNAPSHOT')
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'])['status'],'HALTED')
        self.assertNotEqual(inspect(state=self.state)['checks']['paper_ledger'],'READY_LOCAL_RECONCILIATION_ONLY')

    def test_process_death_before_replace_keeps_original_halt_and_audit(self):
        self.interrupted_process('before');self.assertEqual(self.before,self.state.read_bytes())

    def test_process_death_after_replace_blocks_start_without_committed_receipt(self):
        self.interrupted_process('after')
        with self.assertRaises(ValueError):validate_state(json.loads(self.state.read_bytes()),self.state,1000000)

    def test_legacy_engine_open_cannot_bypass_recovery_restart_block(self):
        self.transition();before=self.state.read_bytes()
        from trading.execution import TradingHalted
        with self.assertRaises(TradingHalted):ExecutionEngine(self.state,1000000).open()
        self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(self.backup.read_bytes(),self.before)

    def test_approval_cannot_be_reused_for_identical_bytes_at_another_path(self):
        other=self.root/'other-paper.json';engine=ExecutionEngine(other,1000000);engine.open();engine.close()
        other.write_bytes(self.before)
        r=_transition(other,self.backup,self.sha,self.approval,authority=self.authority)
        self.assertEqual(r['diagnostic'],'APPROVAL_PLAN_MISMATCH')
        self.assertEqual(other.read_bytes(),self.before);self.assertEqual(self.state.read_bytes(),self.before)
        self.assertFalse(self.authority.consumed)

    def test_approval_expiring_after_reservation_never_writes_ledger(self):
        from unittest.mock import Mock
        t=time.time()
        r=self.transition(clock=Mock(side_effect=[t,t+1000]))
        self.assertEqual(r['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
        self.assertTrue(self.authority.consumed);self.assertEqual(self.state.read_bytes(),self.before)
        self.assertFalse((self.root/'.paper-recovery').exists())

    def test_approval_expiring_during_final_replay_cannot_start_atomic_write(self):
        from unittest.mock import Mock
        t=time.time()
        with patch('trading.state_store.StateStore.save') as save:
            r=self.transition(clock=Mock(side_effect=[t,t,t,t+1000]))
        save.assert_not_called();self.assertEqual(self.state.read_bytes(),self.before)
        self.assertEqual(r['status'],'HALTED_UNCONFIRMED_ATTEMPT_NO_RETRY')
        self.assertEqual((self.bundle/'before.json').read_bytes(),self.before)

    def test_fractional_btc_reported_price_preserves_decimal_precision(self):
        # Synthetic PAPER buy at the reported price, not the user's real ledger.
        self.state=self.root/'fractional-paper.json'
        engine=ExecutionEngine(self.state,1000000);engine.open()
        try:
            engine.buy('KRW-BTC',109212000,400000)
            engine.halt('PAPER_SESSION_FAILURE')
        finally:engine.close()
        self.before=self.state.read_bytes();self.sha=digest(self.before)
        self.backup.write_bytes(self.before)
        self.plan=inspect_plan(self.state,self.backup,self.sha)['plan']
        self.approval['plan_sha256']=fingerprint(self.plan)
        self.authority=FakeAuthority(self.plan,self.approval)
        self.assertEqual(self.transition()['status'],'PENDING_SEPARATE_READ_ONLY_VERIFICATION')
        saved=json.loads(self.state.read_bytes())
        self.assertEqual(saved['positions']['KRW-BTC']['quantity'],'0.003662601179357579753140680511')
        self.assertEqual(__import__('decimal').Decimal(saved['cash']),600000)
        self.assertEqual((self.bundle/'before.json').read_bytes(),self.before)
        self.assertEqual(verify_transition(self.state,self.approval['attempt_id'],authority=self.authority)['status'],
                         'RECOVERY_VERIFIED_NO_START_AUTHORITY')
