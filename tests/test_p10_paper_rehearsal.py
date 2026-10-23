"""Reuse P9B fixture/real PAPER engine; storage failure and cold recovery boundaries."""
import copy
import errno
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from unittest.mock import patch

from tests.test_p9b_paper_slack_e2e import PaperSlackFixture
from trading.execution import TradingHalted
from utils.message_queue import MessageQueue
from ops.local_preflight import inspect


class RehearsalTests(PaperSlackFixture, unittest.TestCase):
    def test_normal_stop_cold_backup_restore_restart_preserves_receipts(self):
        bot=self.bot(); self.tick(bot,10000,'BUY'); self.tick(bot,10200,'SELL')
        self.publish_report(bot); bot.stop(); self.drain(); self.q.close()
        backup=self.root/'cold'; backup.mkdir(mode=0o700)
        for name in ('paper.json','outbox.db'):
            shutil.copy2(self.root/name,backup/name)
        # Isolated restore only, never operator files. Locks stay local and are not receipts.
        restored=self.root/'restore'; restored.mkdir(mode=0o700)
        for name in ('paper.json','outbox.db'): shutil.copy2(backup/name,restored/name)
        q=MessageQueue(restored/'outbox.db')
        try:
            self.assertEqual(q.db.execute('PRAGMA integrity_check').fetchall(),[('ok',)])
            self.assertFalse(q.db.execute("SELECT 1 FROM messages WHERE state!='SENT'").fetchone())
            self.assertEqual(json.loads((restored/'paper.json').read_text(encoding='utf-8')),bot.engine.state)
            self.assertIsNone(q.reserve())
        finally: q.close()
        self.q=MessageQueue(self.root/'outbox.db',clock=lambda:self.wall)
        self.service=self.service_for(self.q)
        restarted=self.bot()
        self.assertEqual(restarted.engine.state['orders'],bot.engine.state['orders'])
        self.assertEqual(self.metrics(restarted)['실현 손익'],8000)

    def test_sqlite_enqueue_failure_after_committed_fill_does_not_undo_order(self):
        bot=self.bot()
        with patch.object(self.q,'enqueue',side_effect=sqlite3.OperationalError('disk full')):
            self.tick(bot,10000,'BUY')
        orders=copy.deepcopy(bot.engine.state['orders'])
        self.assertEqual(next(iter(orders.values()))['status'],'settled')
        self.assertFalse(any('] FILL |' in t for t in self.texts()))
        bot.stop(); restarted=self.bot()
        self.assertEqual(restarted.engine.state['orders'],orders)
        # Missing notifications are not reconstructed as fill receipts or re-submitted.
        self.assertEqual(len(orders),1)

    def test_full_disk_before_intent_never_emits_fill_or_confirmed_order(self):
        bot=self.bot(); before=self.path.read_bytes()
        with patch.object(bot.engine.store,'save',side_effect=OSError(errno.ENOSPC,'disk full')):
            with self.assertRaises(Exception): self.tick(bot,10000,'BUY')
        self.assertTrue(bot.engine.halted)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(any('] FILL |' in t for t in self.texts()))

    def test_outbox_quota_record_transaction_rolls_back_before_mock_call(self):
        bot=self.bot(); self.tick(bot,10000,'BUY')
        self.q.db.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON sends BEGIN SELECT RAISE(ABORT,'full'); END")
        before=self.path.read_bytes()
        self.assertEqual(self.service.flush_one(),'UNKNOWN')
        self.client.chat_postMessage.assert_not_called()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.q.db.execute('SELECT count(*) FROM sends').fetchone()[0],0)
        self.assertFalse(self.q.db.execute("SELECT 1 FROM messages WHERE state='SENDING'").fetchone())

    def test_corrupt_order_ledger_never_uses_outbox_as_order_authority(self):
        bot=self.bot(); self.tick(bot,10000,'BUY'); bot.stop()
        self.path.write_text('{damaged',encoding='utf-8')
        with self.assertRaises(TradingHalted): self.bot()
        self.assertFalse(self.q.db.execute("SELECT 1 FROM messages WHERE state='SENT'").fetchone())
        self.assertEqual(self.path.read_text(encoding='utf-8'),'{damaged')

    @unittest.skipUnless(__import__('os').name=='posix','POSIX read-only lock probe; Windows BLOCKED')
    def test_partial_backup_restore_cash_mismatch_is_blocked(self):
        bot=self.bot(); self.tick(bot,10000,'BUY'); bot.stop(); self.q.close()
        saved=json.loads(self.path.read_text(encoding='utf-8')); saved['cash']='1000000'
        self.path.write_text(json.dumps(saved),encoding='utf-8')
        report=inspect(state=self.path,outbox=self.root/'outbox.db')
        self.assertEqual(report['checks']['paper_ledger'],'BLOCKED_PENDING_HALTED_OR_MISMATCH')
        self.assertFalse(report['execution_authorized'])
        self.assertEqual(report['rollback_resistance'],'UNPROVEN_UNKNOWN_NOT_RESET')
