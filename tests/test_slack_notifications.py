import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from decimal import Decimal
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse
from services.notification_service import NotificationService, format_event, format_fill, format_report, safe
from utils.message_queue import MessageQueue
from tools.check_safety import audit_invariants


class Notifications(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'outbox.db'
        self.now = 100000
        self.q = MessageQueue(self.path, initialize=True, clock=lambda:self.now, daily_limit=3)
        self.client = Mock()
        self.client.chat_postMessage.return_value = {'ok':True,'ts':'1','channel':'C1'}
        self.service = NotificationService(enabled=True,approved=True,token='xoxb-'+'fake-token-value',
            client=self.client,queue=self.q,channels={'trades':'C1','status':'C1','errors':'C2','reports':'C3'})

    def tearDown(self):
        self.q.close()
        self.tmp.cleanup()

    def event(self, id='a', channel='status'):
        return self.service.send_message(channel,format_event('STATUS',event_id=id),event_id=id)

    def test_disabled_zero_calls(self):
        for approved, enabled in ((False,True),(True,False),(False,False)):
            s = NotificationService(enabled=enabled,approved=approved,client=self.client)
            self.assertFalse(s.send_trade_alert('test'))
            self.assertEqual(s.flush_one(),'DISABLED')
        self.client.chat_postMessage.assert_not_called()

    def test_wrong_tokens(self):
        self.event()
        for token in (None,'','xapp-fake-value','xoxp-fake-value','other'):
            self.service.token = token
            self.assertEqual(self.service.flush_one(),'BOT_TOKEN_REQUIRED')
        self.client.chat_postMessage.assert_not_called()

    def test_enqueue_never_sends(self):
        self.event()
        self.client.chat_postMessage.assert_not_called()
        self.assertEqual(self.service.flush_one(),'SENT')
        self.assertEqual(self.service.flush_one(),'DEFERRED')

    def test_duplicate(self):
        self.assertTrue(self.event())
        self.assertFalse(self.event())
        self.assertEqual(self.q.db.execute('SELECT repeats FROM duplicates').fetchone()[0],1)
        self.service.flush_one()
        self.assertIn('추가 발생: 1회',self.client.chat_postMessage.call_args.kwargs['text'])

    def test_daily_and_channel_limit(self):
        self.event('a'); self.event('b'); self.event('c'); self.event('d')
        self.assertEqual(self.service.flush_one(),'SENT')
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.now += 2
        self.assertEqual(self.service.flush_one(),'SENT')
        self.now += 2
        self.assertEqual(self.service.flush_one(),'SENT')
        self.now += 2
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.now += 86400
        self.assertEqual(self.service.flush_one(),'SENT')

    def test_prioritize_fill_and_errors(self):
        self.event('status')
        self.service.send_event('HALTED',event_id='error')
        self.assertEqual(self.q.reserve()[1],'C2')

    def test_restart_unknown_no_retry(self):
        self.event(); self.q.reserve(); self.q.close()
        self.q = MessageQueue(self.path,clock=lambda:self.now)
        self.service.queue = self.q
        self.assertEqual(self.q.db.execute('SELECT state FROM messages').fetchone()[0],'UNKNOWN')
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.assertFalse(self.event())

    def test_no_silent_reinitialize(self):
        with self.assertRaises(FileExistsError):
            MessageQueue(self.path,initialize=True)
        with self.assertRaises(ValueError):
            MessageQueue(Path(self.tmp.name)/'missing.db')

    def test_timeout_no_retry(self):
        self.event(); self.client.chat_postMessage.side_effect=TimeoutError('private raw data')
        self.assertEqual(self.service.flush_one(),'UNKNOWN')
        self.now += 100
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.assertEqual(self.client.chat_postMessage.call_count,1)

    def test_ambiguous_response(self):
        self.event(); self.client.chat_postMessage.return_value={'ok':True}
        self.assertEqual(self.service.flush_one(),'UNKNOWN')

    def test_429_bounded(self):
        response=SlackResponse(client=self.client,http_verb='POST',api_url='',req_args={},
            data={'ok':False},headers={'Retry-After':'5'},status_code=429)
        self.client.chat_postMessage.side_effect=SlackApiError('redacted',response)
        self.event()
        self.assertEqual(self.service.flush_one(),'RATE_LIMITED')
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.now += 5
        self.assertEqual(self.service.flush_one(),'RATE_LIMITED')
        self.now += 500
        self.assertEqual(self.service.flush_one(),'DEFERRED')
        self.assertEqual(self.client.chat_postMessage.call_count,2)

    def test_legacy_raw_withheld(self):
        self.service.send_error_alert('private-account-data')
        self.service.send_trade_alert('private-account-data')
        rows=self.q.db.execute('SELECT text FROM messages').fetchall()
        self.assertNotIn('private-account-data',str(rows))

    def test_malicious_text(self):
        for text in ('<@everyone>','token=private','https://secret.example','xoxb-'+'fake-value','abc\nprivate','abc\u202etxt'):
            self.assertNotEqual(safe(text),text)

    def test_fill_decimal_modes_sides(self):
        order={'status':'settled','exchange_state':'done','identifier':'safe','ticker':'KRW-BTC','side':'bid',
            'executed_volume':'0.123456789','average_fill_price':'12345.6789','funds':str(Decimal('0.123456789')*Decimal('12345.6789')),
            'paid_fee':'0.761','reason':'손절','averaging':True}
        for mode in ('PAPER','LIVE'):
            out=format_fill(order,mode=mode)
            self.assertIn(mode,out); self.assertIn('매수',out)
            self.assertIn('0.123456789',out); self.assertIn('N/A',out)
        order['side']='ask'
        self.assertIn('매도',format_fill(order))
        order['status']='pending'
        self.assertIn('ORDER_UNKNOWN',format_fill(order))
        self.assertNotIn('FILL',format_fill(order))

    def test_report_unknown_not_zero(self):
        out=format_report({},event_id='report')
        for key in ('시작 자산','종료 평가자산','현금','평가 손익','실현 손익','누적 수익률','승률','수수료 합계','전략별 결과'):
            self.assertIn(key,out)
        self.assertIn('N/A',out)
        self.assertNotIn('0 %',out)

    def test_fake_development_format_only(self):
        for kind in ('PR_CREATED','CI_RESULT','WAIT_REVIEW','CHANGES_REQUESTED','TEST_FAILED','DEVELOPMENT_HALTED','MERGED'):
            self.assertIn(kind,format_event(kind,event_id='fake',subject='PR16'))
            self.assertFalse(self.service.send_message('status',format_event(kind,event_id='fake')))
            with self.assertRaises(ValueError):
                self.service.send_event(kind,event_id='fake')
        self.client.chat_postMessage.assert_not_called()

    def test_no_memory_delivery(self):
        q=MessageQueue()
        try:
            self.service.queue=q
            self.assertFalse(self.event())
            self.assertEqual(self.service.flush_one(),'DURABLE_OUTBOX_REQUIRED')
        finally:
            q.close()

    def test_recovery_and_errors_formats(self):
        for kind in ('START','STOP','HALTED','RECOVERY_REQUIRED','ORDER_UNKNOWN','API_ERROR','DATA_ERROR','SAFETY_LIMIT','CONSECUTIVE_LOSSES'):
            out=format_event(kind,event_id='id',mode='PAPER',status='미확인')
            self.assertIn('+00:00',out); self.assertIn('PAPER',out);self.assertIn('미확인',out)

    def test_fill_same_id_new_timestamp_deduplicates(self):
        order={'status':'settled','exchange_state':'done','identifier':'order1','ticker':'KRW-BTC','side':'bid',
               'executed_volume':'1','average_fill_price':'2','funds':'2','paid_fee':'0'}
        self.assertTrue(self.service.send_trade_alert(format_fill(order)))
        self.assertFalse(self.service.send_trade_alert(format_fill(order)))

    def test_unknown_channel_blocks_before_request(self):
        self.service.channels['status']='unverified-channel-name'
        self.assertEqual(self.service.flush_one(),'CHANNEL_ID_REQUIRED')
        self.client.chat_postMessage.assert_not_called()

    def test_restart_preserves_daily_attempts(self):
        self.event(); self.service.flush_one(); self.q.close()
        self.q=MessageQueue(self.path,clock=lambda:self.now,daily_limit=1)
        self.service.queue=self.q
        self.event('next');self.now+=10
        self.assertEqual(self.service.flush_one(),'DEFERRED')

    def test_queue_failure_no_external_request(self):
        with patch.object(self.q,'enqueue',side_effect=RuntimeError('private')):
            self.assertFalse(self.event())
            self.assertEqual(self.service.last_delivery,'OUTBOX_BLOCKED')
        self.client.chat_postMessage.assert_not_called()

    def test_raw_trade_reason_withheld(self):
        order={'status':'settled','exchange_state':'done','identifier':'order1','ticker':'KRW-BTC','side':'bid',
               'executed_volume':'1','average_fill_price':'2','funds':'2','paid_fee':'0',
               'reason':'private-person-account'}
        self.assertNotIn('private-person-account',format_fill(order))

    def test_report_live_preserved(self):
        self.service.send_report(format_report({},event_id='r',mode='LIVE'))
        self.assertIn('[LIVE]',self.q.db.execute('SELECT text FROM messages').fetchone()[0])

    def test_no_delivery_from_bot_fill(self):
        # Reuse the real P0 harness: durable execution calls enqueue, never flush.
        from tests.test_p0_safety import SafetyTests, TICKER
        case=SafetyTests('test_reporting_exception_halts_after_durable_fill_without_repeat')
        case.setUp()
        try:
            bot=case.bot(notification=self.service)
            self.assertTrue(bot.buy_coin(TICKER,10000))
            self.client.chat_postMessage.assert_not_called()
            self.assertEqual(len(bot.engine.state['orders']),1)
            self.assertEqual(next(iter(bot.engine.state['orders'].values()))['status'],'settled')
        finally:
            case.tearDown()


    def test_clock_rollback_blocks(self):
        self.event();self.service.flush_one();self.event('other')
        self.now -= 86400
        self.assertEqual(self.service.flush_one(),'DEFERRED')

    def test_corruption_not_initialized(self):
        broken=Path(self.tmp.name)/'broken.db'
        broken.write_bytes(b'not a database')
        broken.chmod(0o600)
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError):
            MessageQueue(broken)
        self.assertEqual(broken.read_bytes(),b'not a database')



class Invariants(unittest.TestCase):
    def test_policy_mutations(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual(audit_invariants(root),[])
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder);(p/'.github/automation').mkdir(parents=True)
            for name in ('policy.json','zero-cost.json'):
                (p/'.github/automation'/name).write_text((root/'.github/automation'/name).read_text())
            (p/'config.py').write_text('SLACK_NOTIFICATIONS_ENABLED = False')
            for field in ('enabled','allow_merge','allow_credit_purchase','allow_new_paid_feature'):
                source=json.loads((root/'.github/automation/zero-cost.json').read_text())
                source[field]=True
                (p/'.github/automation/zero-cost.json').write_text(json.dumps(source))
                self.assertTrue(audit_invariants(p))
