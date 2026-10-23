"""Diagnostics must neither initialize storage, recover SENDING nor call external APIs."""
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

from ops.local_preflight import ROOT, inspect
from trading.execution import ExecutionEngine
from utils.message_queue import MessageQueue


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.state=self.root/'paper.json';self.outbox=self.root/'outbox.db'
        engine=ExecutionEngine(self.state,1_000_000);engine.open();engine.close()
        q=MessageQueue(self.outbox,initialize=True);q.close()

    def report(self,**kw):return inspect(state=self.state,outbox=self.outbox,**kw)
    def snapshot(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_missing_runtime_is_pending_and_not_created(self):
        before=self.snapshot()
        r=inspect(state=self.root/'missing.json',outbox=self.root/'none.db')
        self.assertEqual(r['checks']['paper_ledger'],'PENDING_FILE_MISSING_NO_INITIALIZATION')
        self.assertEqual(r['checks']['outbox'],'PENDING_FILE_MISSING_NO_INITIALIZATION')
        self.assertEqual(before,self.snapshot())

    def test_diagnostics_do_not_write_or_call_network_or_subprocess(self):
        before=self.snapshot()
        with patch.object(socket.socket,'connect',side_effect=AssertionError()), \
             patch('subprocess.run',side_effect=AssertionError()), \
             patch.dict('os.environ',{'SLACK_BOT_TOKEN':'private-canary'}):
            r=self.report()
        self.assertEqual(before,self.snapshot())
        self.assertNotIn('private-canary',json.dumps(r))
        self.assertEqual(r['network_calls'],0);self.assertEqual(r['state_writes'],0)
        self.assertFalse(r['execution_authorized']);self.assertNotEqual(r['overall'],'READY')

    @unittest.skipUnless(os.name=='posix','Windows diagnostic lock probe deliberately BLOCKED')
    def test_idle_snapshot_validates_ledger_and_outbox_without_prices(self):
        r=self.report()
        self.assertEqual(r['checks']['paper_ledger'],'READY_LOCAL_RECONCILIATION_ONLY')
        self.assertEqual(r['checks']['outbox'],'READY_METADATA_ONLY')
        self.assertEqual(r['outbox_counts']['SENT'],0)
        self.assertEqual(r['checks']['launchd_registration'],'USER_ACTION_REQUIRED_NOT_QUERIED')

    def test_live_owner_is_not_ready_and_sending_untouched(self):
        q=MessageQueue(self.outbox)
        try:
            q.enqueue('e','C1','private-message');q.reserve()
            before=self.snapshot();r=self.report()
            self.assertTrue(r['checks']['outbox'].startswith('BLOCKED'))
            self.assertEqual(before,self.snapshot())
            self.assertEqual(q.db.execute('SELECT state FROM messages').fetchone()[0],'SENDING')
        finally:q.close()

    def test_wal_sidecars_never_open_sqlite(self):
        Path(str(self.outbox)+'-wal').write_bytes(b'offline-fake')
        with patch('utils.slack_preflight.sqlite3.connect',side_effect=AssertionError('must not open')):
            r=self.report()
        self.assertTrue(r['checks']['outbox'].startswith('BLOCKED'))

    @unittest.skipUnless(os.name=='posix','POSIX read-only lock probe')
    def test_unknown_delivery_blocked_counts_not_delivery(self):
        q=MessageQueue(self.outbox);q.enqueue('e','C1','private');q.reserve();q.close()
        q=MessageQueue(self.outbox);q.close()
        r=self.report()
        self.assertEqual(r['outbox_counts']['UNKNOWN'],1)
        self.assertEqual(r['checks']['outbox'],'BLOCKED_UNCERTAIN_OR_UNVERIFIED')
        self.assertIn('전달 성공이 아닙니다',r['delivery_notice'])

    def test_corrupt_json_and_database_never_ready(self):
        self.state.write_text('{bad',encoding='utf-8');self.outbox.write_bytes(b'bad SQLite')
        r=self.report()
        for key in ('paper_ledger','outbox'):self.assertTrue(r['checks'][key].startswith('BLOCKED'))
        self.assertEqual(r['overall'],'BLOCKED')

    @unittest.skipUnless(os.name=='posix','POSIX ownership permissions')
    def test_public_permissions_and_symlink_input_are_blocked(self):
        self.state.chmod(0o644)
        self.assertEqual(self.report()['checks']['paper_ledger'],'BLOCKED_UNVERIFIED')
        link=self.root/'alias.db';link.symlink_to(self.outbox)
        self.assertEqual(inspect(state=self.state,outbox=link)['checks']['outbox'],'BLOCKED_UNVERIFIED')

    def test_missing_lock_is_unverified_and_never_created(self):
        Path(str(self.outbox)+'.lock').unlink()
        before=self.snapshot();r=self.report()
        self.assertTrue(r['checks']['outbox'].startswith('BLOCKED'))
        self.assertEqual(before,self.snapshot())

    def test_modified_configuration_fails_closed_without_import(self):
        root=self.root/'project';root.mkdir()
        (root/'config.py').write_text("raise AssertionError('do not execute')\nREAL_TRADING=True\nSLACK_NOTIFICATIONS_ENABLED=False\nSTART_CASH=1000000",encoding='utf-8')
        shutil.copytree(ROOT/'.github/automation',root/'.github/automation')
        r=self.report(root=root)
        self.assertEqual(r['checks']['paper_slack_automation_off'],'BLOCKED')

    @unittest.skipUnless(os.name=='posix','POSIX read-only snapshot')
    def test_pending_and_halted_records_remain_private_and_unmodified(self):
        saved=json.loads(self.state.read_text(encoding='utf-8'));saved['halt_reason']='private-reason'
        self.state.write_text(json.dumps(saved),encoding='utf-8')
        before=self.snapshot();r=self.report()
        self.assertEqual(r['checks']['paper_ledger'],'BLOCKED_PENDING_HALTED_OR_MISMATCH')
        self.assertTrue(r['halted']);self.assertNotIn('private-reason',json.dumps(r))
        self.assertEqual(before,self.snapshot())

    @unittest.skipUnless(os.name=='posix','POSIX cold snapshot; Windows BLOCKED')
    def test_wal_header_without_sidecars_cannot_create_shm(self):
        q=MessageQueue(self.outbox)
        self.assertEqual(q.db.execute('PRAGMA journal_mode=WAL').fetchone()[0],'wal')
        q.close()
        before=self.snapshot();r=self.report()
        self.assertEqual(r['checks']['outbox'],'READY_METADATA_ONLY')
        self.assertEqual(before,self.snapshot())

    def test_cli_returns_pending_without_echoing_private_arguments(self):
        import io
        from contextlib import redirect_stdout,redirect_stderr
        from ops.local_preflight import main
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch('sys.argv',['preflight','--unexpected','private-canary']),redirect_stdout(stdout),redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:main()
        self.assertEqual(caught.exception.code,2)
        self.assertNotIn('private-canary',stdout.getvalue()+stderr.getvalue())
