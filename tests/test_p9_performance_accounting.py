"""P9 pure PAPER accounting; no exchange, model, Slack or network calls."""
import copy
from datetime import datetime, timezone
from decimal import Decimal as D
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from services.notification_metrics import report_metrics
from services.notification_service import NotificationService, format_report
from utils.message_queue import MessageQueue

ASOF=datetime(2026,10,23,16,tzinfo=timezone.utc)
BASE=1792713600  # 2026-10-23 00:00 UTC


def trade(id,side,q,f,fee='0',profit=None,ts=BASE,**kw):
    o={'identifier':id,'status':'settled','exchange_state':'done','ticker':'KRW-BTC','side':side,
       'executed_volume':q,'funds':f,'paid_fee':fee,'average_fill_price':str(D(f)/D(q)) if D(q) else None,
       'averaging':False,'settled_at':ts}
    if profit is not None:o['profit_amount']=profit
    o.update(kw);return o


def ledger(orders,cash,positions=None):
    return {'mode':'paper','halt_reason':None,'cash':cash,'orders':{o['identifier']:o for o in orders},'positions':positions or {}}


def pos(q,avg,**kw):return {'quantity':q,'avg_price':avg,**kw}


def report(state,*,marks=None,**kw):
    return report_metrics(state,'1000',marks or {},now=100,verified=True,as_of=ASOF,**kw)


class AccountingTests(unittest.TestCase):
    def example(self):
        return ledger([trade('b','bid','1','100','1'),trade('s','ask','0.5','60','0.5','9.5',ts=BASE+1)],
                      '958.5',{'KRW-BTC':pos('0.5','100')})

    def test_requested_fee_example_and_legacy_definition(self):
        r=report(self.example(),marks={'KRW-BTC':(D(110),99)})
        b,s=r['accounting_rows']
        self.assertEqual(b['remaining_cost'],101)
        self.assertEqual(b['remaining_average_cost'],101)
        self.assertEqual(s['allocated_cost'],D('50.5'))
        self.assertEqual(s['funds']-s['fee'],D('59.5'))
        self.assertEqual(s['net_realized'],9)
        self.assertEqual(s['remaining_quantity'],D('0.5'))
        self.assertEqual(s['remaining_cost'],D('50.5'))
        self.assertEqual(r['engine_profit_amount_sum'],D('9.5'))
        self.assertEqual(r['실현 손익'],9);self.assertEqual(r['평가 손익'],D('4.5'))
        self.assertEqual(r['누적 수익률'],D('1.35'));self.assertEqual(r['승률'],100)
        self.assertEqual(r['수수료 합계'],D('1.5'))

    def test_zero_fees_preserve_gross_result(self):
        s=ledger([trade('b','bid','1','100'),trade('s','ask','0.5','60',profit='10',ts=BASE+1)],
                 '960',{'KRW-BTC':pos('0.5','100')})
        r=report(s,marks={'KRW-BTC':(D(110),99)})
        self.assertEqual(r['실현 손익'],10);self.assertEqual(r['평가 손익'],5)
        self.assertEqual(r['누적 수익률'],D('1.5'))

    def test_weighted_cost_averaging_and_partial_sell(self):
        s=ledger([trade('b','bid','1','100','1'),trade('a','bid','1','80','2',ts=BASE+1,averaging=True),
                  trade('s','ask','0.5','50','0.5','4.5',ts=BASE+2)],'866.5',{'KRW-BTC':pos('1.5','90',averaging_down_used=True)})
        r=report(s)
        self.assertEqual(r['positions']['KRW-BTC']['cost'],D('137.25'))
        self.assertEqual(r['accounting_rows'][-1]['allocated_cost'],D('45.75'))
        self.assertEqual(r['실현 손익'],D('3.75'))

    def test_full_exit_and_reentry_resets_cost(self):
        s=ledger([trade('b','bid','1','100','1'),trade('s','ask','1','110','1','9',ts=BASE+1),
                  trade('b2','bid','2','200','2',ts=BASE+2)],'806',{'KRW-BTC':pos('2','100')})
        r=report(s)
        self.assertEqual(r['실현 손익'],8);self.assertEqual(r['positions']['KRW-BTC']['cost'],202)

    def test_fee_changes_a_gross_win_to_net_loss(self):
        s=ledger([trade('b','bid','1','100','2'),trade('s','ask','1','102','1','1',ts=BASE+1)],'999')
        r=report(s)
        self.assertEqual(r['실현 손익'],-1);self.assertEqual(r['승률'],0)
        self.assertEqual(r['누적 수익률'],D('-0.1'))

    def test_zero_profit_is_not_win(self):
        s=ledger([trade('b','bid','1','100','1'),trade('s','ask','1','102','1','1',ts=BASE+1)],'1000')
        r=report(s);self.assertEqual(r['실현 손익'],0);self.assertEqual(r['승률'],0)

    def test_partial_fill_cancel_and_zero_cancel(self):
        s=self.example();s['orders']['s']['exchange_state']='cancel'
        z=trade('z','bid','0','0',ts=BASE+2,exchange_state='cancel');s['orders']['z']=z
        r=report(s);self.assertEqual(r['거래 횟수'],2);self.assertEqual(r['실현 손익'],9)
        self.assertEqual(r['매수 횟수'],1);self.assertEqual(r['매도 횟수'],1)

    def test_multiple_markets(self):
        s=ledger([trade('b','bid','1','100','1'),trade('e','bid','2','200','2',ts=BASE+1,ticker='KRW-ETH')],
                 '697',{'KRW-BTC':pos('1','100'),'KRW-ETH':pos('2','100')})
        r=report(s,marks={'KRW-BTC':(D(110),99),'KRW-ETH':(D(90),99)})
        self.assertEqual(r['종료 평가자산'],987);self.assertEqual(r['평가 손익'],-13)
        self.assertEqual(r['누적 수익률'],D('-1.3'))

    def test_reloaded_json_identical_and_no_mutation(self):
        s=self.example();before=copy.deepcopy(s)
        a=report(s);b=report(json.loads(json.dumps(s)))
        self.assertEqual(a,b);self.assertEqual(s,before)

    def test_ambiguous_missing_future_or_reversed_timestamp_na(self):
        for value in (None,BASE,BASE-1,ASOF.timestamp()+1,'NaN'):
            s=self.example();s['orders']['s']['settled_at']=value
            r=report(s)
            self.assertNotIn('실현 손익',r)
        s=self.example();del s['orders']['s']['settled_at']
        self.assertNotIn('현금',report(s))

    def test_dictionary_reordering_is_not_silently_repaired(self):
        s=self.example();s['orders']=dict(reversed(list(s['orders'].items())))
        self.assertNotIn('현금',report(s))

    def test_contradictory_terminal_timestamp_fails_even_on_buys(self):
        s=ledger([trade('a','bid','1','100'),trade('b','bid','1','100',ts=BASE-1)],'800',{'KRW-BTC':pos('2','100')})
        self.assertNotIn('현금',report(s))

    def test_missing_negative_nonfinite_or_excess_fee_na(self):
        for value in (None,'-1','NaN','Infinity','101',True):
            s=self.example();s['orders']['b']['paid_fee']=value
            self.assertNotIn('실현 손익',report(s))
        s=self.example();del s['orders']['b']['paid_fee']
        self.assertNotIn('실현 손익',report(s))

    def test_cash_position_average_legacy_profit_corruption_na(self):
        for target in ('cash','quantity','avg_price','profit_amount'):
            s=self.example()
            if target=='cash':s['cash']='1'
            elif target=='profit_amount':s['orders']['s'][target]='9'
            else:s['positions']['KRW-BTC'][target]='1'
            self.assertNotIn('실현 손익',report(s))

    def test_oversell_intermediate_cash_and_request_limit_na(self):
        s=self.example();s['orders']['b']['value']='99'
        self.assertNotIn('현금',report(s))
        s=ledger([trade('s','ask','1','100',profit='0')],'1100')
        self.assertNotIn('현금',report(s))
        s=ledger([trade('b','bid','1','1000','1'),trade('s','ask','1','1002',profit='2',ts=BASE+1)],'1001')
        self.assertNotIn('현금',report(s))

    def test_stale_future_missing_mark_keeps_realized_not_valuation(self):
        for marks in ({},{'KRW-BTC':(D(110),-500)},{'KRW-BTC':(D(110),101)}, {'KRW-BTC':(D('NaN'),99)}):
            r=report(self.example(),marks=marks)
            self.assertEqual(r['실현 손익'],9)
            self.assertNotIn('평가 손익',r);self.assertNotIn('누적 수익률',r)

    def test_utc_kst_daily_boundary_not_cumulative_substitution(self):
        s=self.example();s['orders']['b']['settled_at']=datetime(2026,10,23,14,59,59,tzinfo=timezone.utc).timestamp()
        s['orders']['s']['settled_at']=datetime(2026,10,23,15,tzinfo=timezone.utc).timestamp()
        utc=report(s,calendar='UTC');kst=report(s,calendar='KST')
        self.assertEqual(utc['집계 날짜'],'2026-10-23 UTC');self.assertEqual(kst['집계 날짜'],'2026-10-24 KST')
        self.assertEqual(utc['일간 집계']['buy_count'],1);self.assertEqual(kst['일간 집계']['buy_count'],0)
        self.assertEqual(kst['일간 집계']['sell_count'],1)
        self.assertEqual(kst['일간 순실현 손익'],9)
        self.assertEqual(kst['daily']['2026-10-23']['net_realized'],0)
        self.assertIn('N/A',kst['일간 총수익률']);self.assertIn('N/A',kst['일간 평가손익'])

    def test_before_boundary_realized_belongs_to_previous_day(self):
        s=self.example();r=report(s)  # fills at UTC midnight, ASOF after KST midnight
        self.assertEqual(r['실현 손익'],9);self.assertEqual(r['일간 순실현 손익'],0)

    def test_fractional_second_never_rounds_across_day(self):
        s=self.example();boundary=D(str(datetime(2026,10,23,15,tzinfo=timezone.utc).timestamp()))
        s['orders']['b']['settled_at']=str(boundary-D('2'))
        s['orders']['s']['settled_at']=str(boundary-D('0.0000001'))
        r=report(s);self.assertEqual(r['일간 순실현 손익'],0)

    def test_bad_calendar_or_naive_asof_na(self):
        self.assertNotIn('현금',report(self.example(),calendar='unknown'))
        r=report_metrics(self.example(),'1000',{},now=100,verified=True,as_of=datetime(2026,10,24))
        self.assertNotIn('현금',r)

    def test_strategy_reason_statistics_closed_labels_only(self):
        s=self.example();s['orders']['b'].update(strategy='RSI',reason='물타기 private-canary')
        s['orders']['s'].update(strategy='RSI',reason='손절 private-canary')
        r=report(s)
        self.assertEqual(r['strategy_statistics']['RSI']['net_realized'],9)
        self.assertEqual(r['reason_statistics']['물타기']['buy_count'],1)
        self.assertEqual(r['reason_statistics']['손절']['sell_count'],1)
        self.assertNotIn('private-canary',str(r))
        s['orders']['b']['strategy']='private-canary'
        self.assertNotIn('private-canary',str(report(s)))

    def test_slack_report_bounded_values_and_delivery_notice(self):
        r=report(self.example(),marks={'KRW-BTC':(D(110),99)})
        text=format_report(r,event_id='day')
        self.assertIn('[PAPER]',text);self.assertIn('실현 손익: 9.0',text)
        self.assertIn('평가 손익: 4.5',text);self.assertIn('KST',text)
        self.assertIn('일간 순실현 손익: 0',text)
        self.assertIn('N/A',text);self.assertIn('enqueue는 로컬 수락',text)
        self.assertLessEqual(len(text.splitlines()),24);self.assertLess(len(text),4000)
        self.assertNotIn('profit_amount',text)

    def test_unknown_strategy_is_not_inferred_from_reason(self):
        s=self.example();s['orders']['s']['reason']='RSI MACD BB 손절'
        self.assertIn('N/A',report(s)['전략별 결과'])

    def test_slack_timeout_duplicate_does_not_touch_source(self):
        s=self.example();before=copy.deepcopy(s)
        with tempfile.TemporaryDirectory() as folder:
            q=MessageQueue(Path(folder)/'outbox.db',initialize=True)
            try:
                client=Mock();client.chat_postMessage.side_effect=TimeoutError('private')
                service=NotificationService(enabled=True,approved=True,client=client,queue=q,
                    token='xoxb-'+'fake-token-value',channels={'reports':'C1'})
                text=format_report(report(s),event_id='stable-day')
                self.assertTrue(service.send_report(text));self.assertFalse(service.send_report(text))
                self.assertEqual(service.flush_one(),'UNKNOWN');self.assertEqual(service.flush_one(),'DEFERRED')
                self.assertEqual(client.chat_postMessage.call_count,1)
                self.assertEqual(s,before)
            finally:q.close()
