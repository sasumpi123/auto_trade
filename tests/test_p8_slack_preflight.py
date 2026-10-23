from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from utils.message_queue import MessageQueue
from utils.slack_preflight import preflight


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'outbox.sqlite'
        self.q=MessageQueue(self.path,initialize=True,clock=lambda:100000)
        self.addCleanup(self.q.close)

    def test_offline_default_no_config_import(self):
        code='from utils.slack_preflight import preflight; import sys; assert "config" not in sys.modules; assert "slack_sdk" not in sys.modules; assert preflight()["default_off"]=="PASS"'
        r=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)

    def test_counts_and_no_writes_or_message_contents(self):
        for id in ('queued','sent','unknown','failed','inflight'):
            self.q.enqueue(id,'C1','private-message-canary')
        states=['SENT','UNKNOWN','FAILED','SENDING']
        for state in states:
            row=self.q.reserve()
            if state!='SENDING':self.q.finish(row[0],state)
            self.q.clock=lambda t=self.q.clock()+3:t
        self.q.enqueue('queued','C1','private-message-canary')
        before=self.path.read_bytes();directory=set(self.path.parent.iterdir())
        with patch('socket.socket.connect',side_effect=AssertionError('no network')):
            report=preflight(self.path)
        self.assertEqual(report['outbox'],'VERIFIED_LOCAL_METADATA_ONLY')
        self.assertEqual(report['counts'],{'FAILED':1,'QUEUED':1,'RATE_LIMITED':0,'SENDING':1,'SENT':1,'UNKNOWN':1})
        self.assertEqual(report['repeated_occurrences'],1)
        self.assertNotIn('private-message-canary',json.dumps(report))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(set(self.path.parent.iterdir()),directory)
        self.assertEqual(report['in_flight_after_restart'],'UNKNOWN_DO_NOT_RESEND')
        self.assertEqual(report['network_calls'],0)
        self.assertFalse(report['queued_is_delivered'])

    def test_missing_outbox_not_created(self):
        path=self.path.parent/'missing.db'
        report=preflight(path)
        self.assertEqual(report['outbox'],'BLOCKED_UNVERIFIED')
        self.assertFalse(path.exists())

    def test_corrupt_schema_or_logical_duplicates_blocked(self):
        self.q.enqueue('a','C1','withheld')
        with self.q.db:
            self.q.db.execute("INSERT INTO duplicates VALUES(?,1)",('f'*64,))
        self.assertEqual(preflight(self.path)['outbox'],'BLOCKED_UNVERIFIED')
        with self.q.db:
            self.q.db.execute('DELETE FROM duplicates')
            self.q.db.execute("UPDATE messages SET state='UNEXPECTED'")
        self.assertEqual(preflight(self.path)['outbox'],'BLOCKED_UNVERIFIED')

    def test_unrecorded_attempt_or_unconfirmed_sent_blocked(self):
        self.q.enqueue('a','C1','withheld')
        with self.q.db:self.q.db.execute("UPDATE messages SET state='SENT'")
        self.assertEqual(preflight(self.path)['outbox'],'BLOCKED_UNVERIFIED')

    def test_raw_database_error_not_printed(self):
        p=self.path.parent/'broken.db';p.write_bytes(b'private-db-error-canary');p.chmod(0o600)
        report=preflight(p)
        self.assertEqual(report['outbox'],'BLOCKED_UNVERIFIED')
        self.assertNotIn('private-db-error-canary',str(report))

    def test_off_flag_is_literal_and_never_executes(self):
        root=Path(self.temp.name)
        for flag in ('True','bool(0)','False\nSLACK_NOTIFICATIONS_ENABLED=True'):
            (root/'config.py').write_text('raise Exception("do not execute")\nSLACK_NOTIFICATIONS_ENABLED='+flag)
            report=preflight(root=root)
            self.assertEqual(report['default_off'],'FAIL')

    def test_schema_view_not_evaluated(self):
        with self.q.db:
            self.q.db.execute('ALTER TABLE messages RENAME TO original_messages')
            self.q.db.execute('CREATE VIEW messages AS SELECT * FROM original_messages')
        self.assertEqual(preflight(self.path)['outbox'],'BLOCKED_UNVERIFIED')

    def test_credential_paths_block_before_open(self):
        path=self.path.parent/'auth.db';path.write_text('private-token-canary')
        with patch('utils.slack_preflight.sqlite3.connect',side_effect=AssertionError('must not open')) as connect:
            self.assertEqual(preflight(path)['outbox'],'BLOCKED_UNVERIFIED')
            connect.assert_not_called()

    def test_argument_errors_redacted(self):
        r=subprocess.run([sys.executable,'-m','utils.slack_preflight','--fake-private-canary'],capture_output=True,text=True)
        self.assertEqual(r.returncode,2)
        self.assertNotIn('fake-private-canary',r.stderr)

    def test_local_no_attempts_not_global_no_messages_proof(self):
        report=preflight(self.path)
        self.assertEqual(report['recorded_attempts'],0)
        self.assertEqual(report['history_proves_no_actual_messages'],'UNVERIFIABLE_FROM_LOCAL_DATA')
        self.assertFalse(report['message_sent_by_this_tool'])
