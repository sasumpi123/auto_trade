"""Real PAPER/controller/accounting/outbox, scripted market input and Mock Slack only."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from trading.auto_trade import AutoTrade
from trading.execution import ExecutionEngine, TradingHalted
from services.notification_service import NotificationService
from services.notification_metrics import report_metrics
from utils.message_queue import MessageQueue
from utils.slack_preflight import preflight
from tests.test_p0_safety import HoldAnalyzer, TICKER


class SignalAnalyzer(HoldAnalyzer):
    def __init__(self,ticker):
        super().__init__(ticker);self.actions=[]
    def analyze(self):
        action=self.actions.pop(0) if self.actions else 'HOLD'
        if action!='HOLD':self.last_signal=action;self.last_signal_time=time.time()
        return {'action':action,'reason':'매수 신호' if action=='BUY' else '매도 신호','target_price':None}


class PaperSlackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'paper.json'
        self.wall=time.time();self.q=MessageQueue(self.root/'outbox.db',initialize=True,clock=lambda:self.wall)
        self.bots=[]
        self.addCleanup(self.cleanup)
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('No real network'))
        blocker.start();self.addCleanup(blocker.stop)
        self.client=Mock();self.client.chat_postMessage.side_effect=lambda **kw:{'ok':True,'channel':kw['channel'],'ts':'1'}
        self.service=self.service_for(self.q)

    def service_for(self,q):
        return NotificationService(enabled=True,approved=True,queue=q,client=self.client,
            token='xoxb-'+'fake-token-value',channels={'trades':'C1','status':'C2','errors':'C3','reports':'C4'})

    def cleanup(self):
        for bot in self.bots:bot.stop()
        self.q.close()

    def bot(self,**kw):
        bot=AutoTrade(tickers=[TICKER],state_path=self.path,analyzer_factory=SignalAnalyzer,
                      notification=self.service,real_trading=False,**kw)
        self.bots.append(bot);bot.initialize();return bot

    def tick(self,bot,price,action='HOLD'):
        bot.analyzers[TICKER].actions=[action]
        return bot.process_market_data(TICKER,{'trade_price':price})

    def orders(self,bot):return list(bot.engine.state['orders'].values())
    def texts(self):return [r[0] for r in self.q.db.execute('SELECT text FROM messages ORDER BY rowid')]
    def metrics(self,bot):return report_metrics(bot.engine.state,bot.start_cash,bot._notification_marks,now=time.monotonic(),verified=True)

    def publish_report(self,bot):
        return self.service.send_report(bot.notification_report())

    def drain(self):
        for _ in range(80):
            if not self.q.db.execute("SELECT 1 FROM messages WHERE state='QUEUED'").fetchone():return
            self.wall+=3;self.service.flush_one()
        self.fail('Mock outbox did not drain')

    def test_signal_to_paper_ledger_accounting_outbox_and_mock_slack(self):
        bot=self.bot();self.tick(bot,10000,'BUY');self.tick(bot,10200,'SELL')
        self.assertEqual([o['side'] for o in self.orders(bot)],['bid','ask'])
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')),bot.engine.state)
        self.assertEqual(self.metrics(bot)['실현 손익'],8000)
        self.assertEqual(self.metrics(bot)['승률'],100)
        bot.log_status();self.assertTrue(self.publish_report(bot));bot.stop()
        self.client.chat_postMessage.assert_not_called()  # enqueue is not delivery
        before=self.path.read_bytes();self.drain()
        self.assertEqual(before,self.path.read_bytes())
        for kind in ('START','STATUS','FILL','REPORT','STOP'):
            self.assertTrue(any('] '+kind+' |' in text for text in self.texts()))
        sent=self.q.db.execute("SELECT count(*) FROM messages WHERE state='SENT'").fetchone()[0]
        self.assertEqual(sent,self.client.chat_postMessage.call_count)
        self.assertTrue(any('실현 손익: 8000' in text for text in self.texts()))
        self.assertTrue(all('PAPER' in text and 'enqueue는 로컬 수락' in text for text in self.texts()))

    def test_take_profit_uses_existing_five_percent_policy(self):
        bot=self.bot();self.tick(bot,10000,'BUY');self.tick(bot,10500)
        self.assertEqual(len(self.orders(bot)),2)
        self.assertEqual(self.orders(bot)[1]['reason'],'이익 실현 (5%)')
        self.assertEqual(self.metrics(bot)['실현 손익'],20000)
        self.assertTrue(self.publish_report(bot));self.drain()

    def test_averaging_once_then_stop_loss_to_report(self):
        bot=self.bot();self.tick(bot,10000,'BUY');self.tick(bot,9500);self.tick(bot,9000)
        orders=self.orders(bot)
        self.assertEqual([o['side'] for o in orders],['bid','bid','ask'])
        self.assertTrue(orders[1]['averaging']);self.assertEqual(orders[2]['reason'],'손절')
        result=self.metrics(bot)
        self.assertEqual(result['거래 횟수'],3);self.assertEqual(result['승률'],0)
        self.assertEqual(result['실현 손익'],-50000)
        self.assertTrue(self.publish_report(bot));self.drain()
        self.assertTrue(any('물타기' in text for text in self.texts()))
        self.assertTrue(any('손절' in text for text in self.texts()))

    def test_fee_injected_only_at_test_receipt_boundary(self):
        original=ExecutionEngine._apply_fill
        def with_fake_fee(engine,order,receipt):
            receipt=copy.deepcopy(receipt)
            receipt['paid_fee']=str(sum(Decimal(t['funds']) for t in receipt['trades'])*Decimal('0.01'))
            return original(engine,order,receipt)
        bot=self.bot()
        with patch.object(ExecutionEngine,'_apply_fill',with_fake_fee):
            self.tick(bot,10000,'BUY')
            interim=self.metrics(bot)
            self.assertEqual(interim['평가 손익'],-4000)
            self.assertEqual(interim['누적 수익률'],Decimal('-0.4'))
            self.assertTrue(self.service.send_message('reports',bot.notification_report(),event_id='fee-interim-test'))
            self.tick(bot,10200,'SELL')
        r=self.metrics(bot)
        self.assertEqual(r['수수료 합계'],8080)
        self.assertEqual(r['실현 손익'],-80);self.assertEqual(r['승률'],0)
        self.assertEqual(r['누적 수익률'],Decimal('-0.00800'))
        self.assertTrue(self.publish_report(bot));self.drain()
        self.assertTrue(any('실현 손익: -80' in t for t in self.texts()))
        self.assertTrue(any('평가 손익: -4000' in t for t in self.texts()))

    def test_start_stream_stop_and_status_without_network(self):
        owner=self
        class Stream:
            def __init__(self,tickers):self.count=0;self.closed=False;owner.stream=self
            def connect(self):pass
            def get(self):
                self.count+=1
                if self.count==2:bot.request_stop()
                return {'code':TICKER,'trade_price':10000}
            def close(self):self.closed=True
        bot=self.bot(stream_factory=Stream)
        with patch('trading.auto_trade.STATUS_INTERVAL',0):bot.start()
        self.assertTrue(self.stream.closed);self.assertFalse(bot.running)
        self.assertEqual(len(self.orders(bot)),0)
        self.assertTrue(any('] STATUS |' in t for t in self.texts()))
        self.drain()

    def test_bad_market_data_halts_and_never_exposes_raw_error(self):
        bot=self.bot()
        with self.assertRaises(Exception):self.tick(bot,'private-invalid-price')
        self.assertTrue(bot.engine.halted);self.assertFalse(self.orders(bot))
        self.assertTrue(any('] HALTED |' in t for t in self.texts()))
        self.assertNotIn('private-invalid-price',' '.join(self.texts()))
        self.drain()

    def test_unconfirmed_order_restart_halts_without_resubmit_or_fill(self):
        bot=self.bot()
        with patch.object(bot.engine,'_apply_fill',side_effect=TimeoutError('private-receipt-canary')):
            with self.assertRaises(TradingHalted):self.tick(bot,10000,'BUY')
        self.assertEqual(len(self.orders(bot)),1)
        self.assertEqual(self.orders(bot)[0]['status'],'intent')
        order=copy.deepcopy(self.orders(bot));bot.stop()
        with self.assertRaises(TradingHalted):self.bot()
        saved=json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(list(saved['orders'].values()),order)
        self.assertFalse(any('] FILL |' in t for t in self.texts()))
        self.assertTrue(any('RECOVERY_REQUIRED' in t for t in self.texts()))
        self.assertNotIn('private-receipt-canary',' '.join(self.texts()))
        self.drain()

    def test_timeout_then_restart_does_not_repeat_uncertain_delivery(self):
        bot=self.bot();self.tick(bot,10000,'BUY')
        order=copy.deepcopy(bot.engine.state['orders']);bot.stop()
        self.client.chat_postMessage.side_effect=TimeoutError('lost response')
        self.assertEqual(self.service.flush_one(),'UNKNOWN')  # priority fill first
        self.q.close();self.q=MessageQueue(self.root/'outbox.db',clock=lambda:self.wall)
        self.service=self.service_for(self.q)
        self.client.chat_postMessage.side_effect=lambda **kw:{'ok':True,'channel':kw['channel'],'ts':'1'}
        restored=self.bot()
        self.assertEqual(restored.engine.state['orders'],order)
        self.assertEqual(self.metrics(restored)['거래 횟수'],1)
        self.assertNotIn('종료 평가자산',self.metrics(restored))  # new price required
        restored.notification=self.service
        report=self.q.db.execute("SELECT count(*) FROM messages WHERE state='UNKNOWN'").fetchone()[0]
        self.assertEqual(report,1)
        before=self.path.read_bytes();self.drain();self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(sum('] FILL |' in call.kwargs['text'] for call in self.client.chat_postMessage.call_args_list),1)

    def test_inflight_crash_unknown_and_preflight_never_mutates(self):
        bot=self.bot();self.tick(bot,10000,'BUY');self.q.reserve()
        before=self.q.path.read_bytes();r=preflight(self.q.path)
        self.assertEqual(r['counts']['SENDING'],1);self.assertEqual(before,self.q.path.read_bytes())
        self.q.close();self.q=MessageQueue(self.root/'outbox.db',clock=lambda:self.wall)
        self.service=self.service_for(self.q);bot.notification=self.service
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM messages WHERE state='UNKNOWN'").fetchone()[0],1)
        before=self.path.read_bytes();self.drain();self.assertEqual(before,self.path.read_bytes())

    def test_duplicate_report_and_fill_are_local_not_sent(self):
        bot=self.bot();self.tick(bot,10000,'BUY')
        self.assertTrue(self.publish_report(bot));self.assertFalse(self.publish_report(bot))
        from services.notification_service import format_fill
        self.assertFalse(self.service.send_trade_alert(format_fill(self.orders(bot)[0])))
        self.client.chat_postMessage.assert_not_called()
        r=preflight(self.q.path)
        self.assertGreaterEqual(r['repeated_occurrences'],2);self.assertEqual(r['recorded_attempts'],0)
        self.assertFalse(r['queued_is_delivered']);self.drain()

    def test_cumulative_and_daily_are_separate_in_report(self):
        bot=self.bot();self.tick(bot,10000,'BUY');self.tick(bot,10200,'SELL')
        from datetime import datetime,timedelta,timezone
        r=report_metrics(bot.engine.state,bot.start_cash,{},now=time.monotonic(),verified=True,
                         as_of=datetime.now(timezone.utc)+timedelta(days=1))
        self.assertEqual(r['실현 손익'],8000);self.assertEqual(r['일간 순실현 손익'],0)
        self.assertIn('N/A',r['일간 총수익률'])
        self.assertTrue(self.publish_report(bot));self.drain()

    def test_halt_does_not_log_exception_or_persistence_error_contents(self):
        bot=self.bot()
        with self.assertLogs(level='ERROR') as logs:
            bot.engine.halt('private-local-reason-canary')
        self.assertNotIn('private-local-reason-canary',' '.join(logs.output))
        # Private durable reason is retained; recovery behavior/schema are unchanged.
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['halt_reason'],'private-local-reason-canary')
        with patch.object(bot.engine.store,'save',side_effect=OSError('private-storage-canary')):
            with self.assertLogs(level='ERROR') as logs:bot.engine.halt('private-local-reason-canary')
        self.assertNotIn('private-storage-canary',' '.join(logs.output))
        self.assertTrue(bot.engine.halted)

    def test_slack_failure_and_outbox_finish_failure_remain_unknown(self):
        from slack_sdk.errors import SlackApiError
        from slack_sdk.web.slack_response import SlackResponse
        bot=self.bot();self.tick(bot,10000,'BUY')
        before=self.path.read_bytes()
        response=SlackResponse(client=self.client,http_verb='POST',api_url='',req_args={},
                               data={'ok':False},headers={},status_code=403)
        self.client.chat_postMessage.side_effect=SlackApiError('private-slack-canary',response)
        with patch.object(self.q,'finish',side_effect=OSError('outbox unavailable')):
            self.assertEqual(self.service.flush_one(),'UNKNOWN')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM messages WHERE state='SENDING'").fetchone()[0],1)
        self.q.close();self.q=MessageQueue(self.root/'outbox.db',clock=lambda:self.wall)
        self.service=self.service_for(self.q);bot.notification=self.service
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM messages WHERE state='UNKNOWN'").fetchone()[0],1)
        self.assertNotIn('private-slack-canary',' '.join(self.texts()))

    def test_explicit_slack_failure_is_failed_without_affecting_orders(self):
        from slack_sdk.errors import SlackApiError
        from slack_sdk.web.slack_response import SlackResponse
        bot=self.bot();self.tick(bot,10000,'BUY');before=self.path.read_bytes()
        response=SlackResponse(client=self.client,http_verb='POST',api_url='',req_args={},data={'ok':False},headers={},status_code=403)
        self.client.chat_postMessage.side_effect=SlackApiError('withheld',response)
        self.assertEqual(self.service.flush_one(),'FAILED')
        self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(self.q.db.execute("SELECT count(*) FROM messages WHERE state='FAILED'").fetchone()[0],1)
