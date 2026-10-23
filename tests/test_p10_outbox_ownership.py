"""Actual OS process contention/crash tests; no network or real Slack."""
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from utils.message_queue import MessageQueue
from services.notification_service import NotificationService


def _hold(path, pipe):
    try:
        q = MessageQueue(path)
        q.enqueue('crash-test', 'C1', 'offline')
        q.reserve()
        pipe.send('SENDING')
        pipe.recv()
        q.close()
    except Exception:
        pipe.send('FAILED')
    finally:
        pipe.close()


def _try_open(path, pipe):
    try:
        q = MessageQueue(path)
    except RuntimeError:
        pipe.send('BLOCKED')
    else:
        q.close(); pipe.send('OPENED')
    pipe.close()


def _inherited(q, pipe):
    try:
        q.reserve()
    except RuntimeError:
        pipe.send('BLOCKED')
    else:
        pipe.send('UNSAFE')
    pipe.close()


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'outbox.db'
        self.q = MessageQueue(self.path, initialize=True)
        self.addCleanup(lambda: self.q.close())

    def child(self, target, *args, method='spawn'):
        ctx = multiprocessing.get_context(method)
        parent, child = ctx.Pipe()
        process = ctx.Process(target=target, args=(*args, child))
        process.start(); child.close()
        def cleanup():
            if process.is_alive(): process.terminate()
            process.join(10); parent.close()
        self.addCleanup(cleanup)
        self.assertTrue(parent.poll(15), 'Child did not report bounded status')
        return process, parent, parent.recv()

    def test_second_instance_cannot_recover_active_sending(self):
        self.q.enqueue('e', 'C1', 'offline'); self.q.reserve()
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError): MessageQueue(self.path)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.q.db.execute('SELECT state FROM messages').fetchone()[0], 'SENDING')

    def test_spawned_process_cannot_open_active_owner(self):
        _, _, status = self.child(_try_open, str(self.path))
        self.assertEqual(status, 'BLOCKED')

    def test_process_death_releases_lock_but_never_resends_sending(self):
        self.q.close()
        process, pipe, status = self.child(_hold, str(self.path))
        self.assertEqual(status, 'SENDING')
        with self.assertRaises(RuntimeError): MessageQueue(self.path)
        process.terminate(); process.join(10)
        self.assertFalse(process.is_alive())
        self.q = MessageQueue(self.path)
        self.assertEqual(self.q.db.execute('SELECT state FROM messages').fetchone()[0], 'UNKNOWN')
        self.assertIsNone(self.q.reserve())
        self.assertFalse(self.q.enqueue('crash-test','C1','offline'))

    @unittest.skipUnless(os.name == 'posix', 'fork is POSIX only; spawn tested on every OS')
    def test_inherited_connection_is_not_process_ownership(self):
        _, _, status = self.child(_inherited, self.q, method='fork')
        self.assertEqual(status, 'BLOCKED')
        self.assertTrue(self.q.enqueue('parent','C1','offline'))

    def test_close_waits_for_entire_mock_delivery(self):
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        client = Mock()
        def send(**kw):
            entered.set()
            if not release.wait(5): raise TimeoutError()
            return {'ok': True, 'ts': '1', 'channel': kw['channel']}
        client.chat_postMessage.side_effect = send
        service = NotificationService(enabled=True, approved=True, queue=self.q,
            client=client, token='xoxb-'+'fake-token-value', channels={'status':'C1'})
        service.send_event('START', event_id='thread-test')
        worker = threading.Thread(target=service.flush_one)
        closer = threading.Thread(target=lambda: (self.q.close(), closed.set()))
        worker.start()
        try:
            self.assertTrue(entered.wait(5)); closer.start()
            self.assertFalse(closed.wait(.1))
            # Enqueue is local and must not wait on a Slack network request.
            self.assertTrue(self.q.enqueue('during-send','C1','offline'))
            with self.assertRaises(RuntimeError): MessageQueue(self.path)
        finally:
            release.set(); worker.join(5)
            if closer.ident is not None: closer.join(5)
        self.assertTrue(closed.is_set())
        self.q = MessageQueue(self.path)
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM messages WHERE state='SENT'").fetchone()[0], 1)

    def test_failed_schema_open_releases_ownership(self):
        self.q.db.execute('PRAGMA user_version=99'); self.q.close()
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'schema'): MessageQueue(self.path)

    def test_closed_queue_cannot_reserve_or_enqueue(self):
        self.q.close(); self.q.close()
        with self.assertRaises(RuntimeError): self.q.reserve()
        with self.assertRaises(Exception): self.q.enqueue('e','C1','offline')

    @unittest.skipUnless(os.name == 'posix', 'symlink/hardlink attack simulation requires POSIX')
    def test_lock_replacement_invalidates_live_owner(self):
        lock = Path(str(self.path)+'.lock')
        lock.unlink(); lock.write_bytes(b'')
        with self.assertRaises(RuntimeError): self.q.reserve()

    def test_alias_parent_resolves_to_same_lock(self):
        alias = self.path.parent / '..' / self.path.parent.name / self.path.name
        with self.assertRaises(RuntimeError): MessageQueue(alias)

    def test_ownership_failure_precedes_database_creation(self):
        from utils.process_lock import OutboxOwnership
        missing=self.path.parent/'other.db'
        owner=OutboxOwnership(missing);owner.acquire()
        try:
            with self.assertRaises(RuntimeError): MessageQueue(missing,initialize=True)
            self.assertFalse(missing.exists())
        finally:owner.close()

    def test_failed_sqlite_connect_does_not_leak_ownership(self):
        from unittest.mock import patch
        import sqlite3
        self.q.close()
        with patch('utils.message_queue.sqlite3.connect',side_effect=sqlite3.OperationalError('unavailable')):
            with self.assertRaises(sqlite3.OperationalError):MessageQueue(self.path)
        self.q=MessageQueue(self.path)
        self.assertTrue(self.q.enqueue('after-failure','C1','offline'))
