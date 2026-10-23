import copy
from decimal import Decimal as D
import unittest
from services.notification_metrics import report_metrics, confirmed
from services.notification_service import format_fill, format_report


def order(id,side,q,f,fee='0',profit=None,avg=None,**extra):
    value={'identifier':id,'status':'settled','exchange_state':'done','ticker':'KRW-BTC',
        'side':side,'executed_volume':q,'funds':f,'paid_fee':fee,
        'average_fill_price':avg if avg is not None else str(D(f)/D(q)) if D(q) else None,
        'averaging':False}
    if profit is not None:value['profit_amount']=profit
    value.update(extra);return value


class MetricsTests(unittest.TestCase):
    def state(self,orders,cash,qty='0',avg='0'):
        return {'mode':'paper','halt_reason':None,'orders':{o['identifier']:o for o in orders},
                'cash':cash,'positions':{'KRW-BTC':{'quantity':qty,'avg_price':avg}} if D(qty) else {}}
    def metrics(self,state,start='1000',marks=None,verified=True):
        return report_metrics(state,start,marks or {},now=100,verified=verified)
    def test_buy_exact_units_and_paper(self):
        o=order('b','bid','0.123456789','12.3456789')
        text=format_fill(o)
        self.assertIn('PAPER',text);self.assertIn('매수',text)
        self.assertIn('0.123456789 코인',text);self.assertIn('12.3456789 KRW',text)
        self.assertIn('100 KRW/코인',text);self.assertIn('0 KRW',text)
        self.assertTrue(confirmed(o))

    def test_loss_is_signed_and_win_rate_denominator_is_sells(self):
        rows=[order('b1','bid','2','200'),order('s1','ask','1','90',profit='-10'),
              order('s2','ask','1','120',profit='20')]
        report=self.metrics(self.state(rows,'1010'))
        self.assertEqual(report['실현 손익'],D(10))
        self.assertEqual(report['승률'],D(50))
        self.assertEqual(report['거래 횟수'],3)
        self.assertEqual(report['누적 수익률'],D(1))
        self.assertEqual(report['평가 손익'],0)

    def test_negative_realized_and_return(self):
        state=self.state([order('b','bid','1','100'),order('s','ask','1','80',profit='-20')],'980')
        report=self.metrics(state)
        self.assertEqual(report['실현 손익'],D(-20));self.assertEqual(report['승률'],0)
        self.assertEqual(report['누적 수익률'],D(-2))
        self.assertIn('-20 KRW',format_report(report,event_id='r'))

    def test_unrealized_separate(self):
        state=self.state([order('b','bid','2','200')],'800','2','100')
        report=self.metrics(state,marks={'KRW-BTC':(D(110),99)})
        self.assertEqual(report['평가 손익'],20);self.assertEqual(report['실현 손익'],0)
        self.assertEqual(report['종료 평가자산'],1020)
        self.assertEqual(report['누적 수익률'],2)
        self.assertIsNone(report['승률'])

    def test_averaging_partial_and_zero_cancel(self):
        rows=[order('b','bid','1','100'),order('avg','bid','1','80',averaging=True),
              order('s','ask','0.5','50',profit='5',exchange_state='cancel'),
              order('cancel','bid','0','0',exchange_state='cancel')]
        report=self.metrics(self.state(rows,'870','1.5','90'),marks={'KRW-BTC':(D(100),99)})
        self.assertEqual(report['실현 손익'],5);self.assertEqual(report['거래 횟수'],3)
        self.assertEqual(report['승률'],100)
        self.assertIn('True',format_fill(rows[1]));self.assertIn('매도',format_fill(rows[2]))
        self.assertIn('ORDER_UNKNOWN',format_fill(rows[3]))

    def test_nonzero_fees_exact_but_unallocated_pnl_na(self):
        rows=[order('b','bid','1','100',fee='1'),order('s','ask','0.5','60',fee='0.5',profit='9.5')]
        report=self.metrics(self.state(rows,'958.5','0.5','100'),marks={'KRW-BTC':(D(110),99)})
        self.assertEqual(report['수수료 합계'],D('1.5'))
        self.assertNotIn('실현 손익',report);self.assertNotIn('승률',report)
        self.assertNotIn('평가 손익',report)
        self.assertEqual(report['종료 평가자산'],D('1013.5'))
        self.assertEqual(report['누적 수익률'],D('1.35'))
        self.assertIn('N/A',format_fill(rows[1]))

    def test_missing_stale_future_or_bad_prices_na(self):
        state=self.state([order('b','bid','1','100')],'900','1','100')
        for marks in ({},{'KRW-BTC':(D(100),-1000)},{'KRW-BTC':(D(100),101)},
                      {'KRW-BTC':(D('NaN'),99)},{'KRW-BTC':(D(0),99)}):
            report=self.metrics(state,marks=marks)
            self.assertNotIn('종료 평가자산',report)
            self.assertNotIn('누적 수익률',report)
            self.assertEqual(report['수수료 합계'],0)

    def test_cash_position_basis_start_mismatches_all_na(self):
        state=self.state([order('b','bid','1','100')],'900','1','100')
        for field,value in (('cash','901'),('positions',{'KRW-BTC':{'quantity':'2','avg_price':'100'}}),
                            ('positions',{'KRW-BTC':{'quantity':'1','avg_price':'101'}})):
            other=copy.deepcopy(state);other[field]=value
            report=self.metrics(other)
            self.assertNotIn('현금',report)
            self.assertIn('미검증',report['검증 상태'])
        self.assertNotIn('시작 자산',self.metrics(state,start='2000'))

    def test_not_ready_halted_real_or_pending_all_na(self):
        state=self.state([],'1000')
        self.assertNotIn('현금',self.metrics(state,verified=False))
        for key,value in (('mode','real'),('halt_reason','private reason'),('orders',{'p':order('p','bid','1','100',status='submitted')})):
            other=copy.deepcopy(state);other[key]=value
            report=self.metrics(other)
            self.assertNotIn('현금',report);self.assertNotIn('private reason',str(report))

    def test_corrupt_or_unconfirmed_fill_never_success(self):
        original=order('b','bid','1','100')
        for key,value in (('status','submitted'),('exchange_state','wait'),('funds','99'),
                          ('executed_volume','NaN'),('paid_fee','-1'),('side','unknown'),('average_fill_price',None)):
            o=dict(original);o[key]=value
            self.assertFalse(confirmed(o));self.assertIn('ORDER_UNKNOWN',format_fill(o))

    def test_daily_is_na_not_cumulative(self):
        report=self.metrics(self.state([],'1000'))
        text=format_report(report,event_id='r')
        self.assertIn('누적 확정 원장 (일간 아님)',text)
        self.assertIn('일간 손익: N/A',text)
        self.assertIn('enqueue는 로컬 수락',text)
        self.assertLessEqual(len(text.splitlines()),24)

    def test_report_does_not_mutate_ledger(self):
        state=self.state([order('b','bid','1','100')],'900','1','100');before=copy.deepcopy(state)
        self.metrics(state,marks={'KRW-BTC':(D(110),99)})
        self.assertEqual(before,state)


class BotReportTests(unittest.TestCase):
    def setUp(self):
        from tests.test_p0_safety import SafetyTests
        self.case=SafetyTests('test_reporting_exception_halts_after_durable_fill_without_repeat')
        self.case.setUp();self.addCleanup(self.case.tearDown)

    def test_buy_sell_loss_report_from_durable_paper(self):
        from tests.test_p0_safety import TICKER
        bot=self.case.bot()
        bot.buy_coin(TICKER,10000);bot.sell_coin(TICKER,9000)
        text=bot.notification_report()
        self.assertIn('실현 손익: -40000.0 KRW',text)
        self.assertIn('거래 횟수: 2 회',text)
        self.assertIn('승률: 0 %',text)
        self.assertIn('누적 수익률: -4.00 %',text)
        self.assertIn('일간 손익: N/A',text)

    def test_restart_retains_counts_and_does_not_invent_price(self):
        from tests.test_p0_safety import TICKER
        bot=self.case.bot();bot.buy_coin(TICKER,10000);bot.stop()
        restored=self.case.bot()
        text=restored.notification_report()
        self.assertIn('거래 횟수: 1 회',text)
        self.assertIn('현금: 600000.0 KRW',text)
        self.assertIn('종료 평가자산: N/A',text)
        self.assertIn('누적 수익률: N/A',text)
        self.assertEqual(len(restored.engine.state['orders']),1)

    def test_start_cash_mismatch_after_restart_reports_na(self):
        from tests.test_p0_safety import TICKER
        bot=self.case.bot();bot.buy_coin(TICKER,10000);bot.stop()
        restored=self.case.bot(start_cash=2000000)
        text=restored.notification_report()
        self.assertIn('현금: N/A',text)
        self.assertIn('검증 상태: N/A',text)

    def test_disk_mismatch_reports_na_without_mutating_order_state(self):
        from tests.test_p0_safety import TICKER
        bot=self.case.bot();bot.buy_coin(TICKER,10000)
        before=copy.deepcopy(bot.engine.state)
        disk=copy.deepcopy(before);disk['cash']='0'
        bot.engine.store.save(disk)
        text=bot.notification_report()
        self.assertIn('현금: N/A',text)
        self.assertEqual(bot.engine.state,before)
        self.assertFalse(bot.engine.halted)

    def test_float_price_cache_is_not_valuation_evidence(self):
        from tests.test_p0_safety import TICKER
        bot=self.case.bot();bot.buy_coin(TICKER,10000)
        bot.price_cache[TICKER].append(12000.0)
        self.assertIn('종료 평가자산: N/A',bot.notification_report())
        import time
        bot._notification_marks[TICKER]=(D('12000'),time.monotonic())
        self.assertIn('종료 평가자산: 1080000',bot.notification_report())
        bot._notification_marks[TICKER]=(D('12000'),time.monotonic()-301)
        self.assertIn('종료 평가자산: N/A',bot.notification_report())

    def test_slack_timeout_never_reexecutes_order_or_changes_ledger(self):
        from tests.test_p0_safety import TICKER
        from services.notification_service import NotificationService
        from utils.message_queue import MessageQueue
        from unittest.mock import Mock
        q=MessageQueue(self.case.path.parent/'outbox.db',initialize=True)
        self.addCleanup(q.close)
        client=Mock();client.chat_postMessage.side_effect=TimeoutError('private-canary')
        service=NotificationService(enabled=True,approved=True,token='xoxb-'+'fake-token-value',client=client,queue=q,
            channels={'trades':'C1','status':'C2','errors':'C3','reports':'C4'})
        bot=self.case.bot(notification=service);bot.buy_coin(TICKER,10000)
        before=copy.deepcopy(bot.engine.state);disk=bot.engine.store.path.read_bytes()
        self.assertEqual(service.flush_one(),'UNKNOWN')
        self.assertEqual(before,bot.engine.state)
        self.assertEqual(disk,bot.engine.store.path.read_bytes())
        self.assertEqual(len(bot.engine.state['orders']),1)
        # Other distinct messages may remain queued; the ambiguous fill itself cannot retry.
        self.assertEqual(q.db.execute("SELECT count(*) FROM messages WHERE state='UNKNOWN'").fetchone()[0],1)
        bot.notification=None  # q cleanup and bot cleanup have independent lifetimes

    def test_generic_success_event_cannot_claim_a_fill(self):
        from services.notification_service import NotificationService, format_event
        from utils.message_queue import MessageQueue
        from unittest.mock import Mock
        q=MessageQueue(self.case.path.parent/'outbox.db',initialize=True)
        self.addCleanup(q.close)
        client=Mock()
        service=NotificationService(enabled=True,approved=True,queue=q,client=client)
        with self.assertRaises(ValueError):service.send_event('FILL',event_id='fake',status='성공')
        self.assertFalse(service.send_trade_alert(format_event('FILL',event_id='fake',status='성공')))
        self.assertEqual(q.db.execute('SELECT count(*) FROM messages').fetchone()[0],0)
        client.chat_postMessage.assert_not_called()

    def test_legacy_status_profit_argument_is_not_evidence(self):
        from services.notification_service import NotificationService
        text=NotificationService().format_status_message('KRW-BTC',100,1000,1,999,{})
        self.assertIn('수익률: N/A',text)
        self.assertNotIn('999',text)
