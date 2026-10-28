"""Read-only reason labels; synthetic orders only, no exchange/Slack calls."""
import copy
from decimal import Decimal as D
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from services.notification_metrics import reason_group,report_metrics
from tests.test_p9_performance_accounting import trade,ledger,pos,ASOF,BASE


class ReasonTests(unittest.TestCase):
    def test_overbought_sell_uses_verified_ask(self):
        self.assertEqual(reason_group({'side':'ask','reason':'RSI 과매수 하락(75.0)'}),'매도')
    def test_oversold_buy_uses_verified_bid(self):
        self.assertEqual(reason_group({'side':'bid','reason':'RSI 과매도 반등(25.0)'}),'매수')
    def test_composite_reasons_and_conflicting_direction_text_use_side(self):
        for side,expected in [('bid','매수'),('ask','매도')]:
            for raw in ('RSI 과매수 하락(75.0) & MACD 골든크로스 상승(2.0)',
                        'RSI 과매도 반등(25.0) & BB 상단 돌파(3.0%) & 복합 매도 신호(2개)',
                        '매수 신호 & 매도 신호'):
                with self.subTest(side=side,raw=raw):self.assertEqual(reason_group({'side':side,'reason':raw}),expected)
    def test_risk_reasons_require_matching_direction_and_no_conflicting_risks(self):
        for side,raw,expected in [('bid','물타기 매수','물타기'),('ask','손절','손절'),
                ('ask','이익 실현 (5%)','이익 실현'),('ask','RSI 과매수 하락(75.0) & 손절','손절'),
                ('ask','물타기 매수','기타/미기록'),('bid','손절','기타/미기록'),
                ('bid','이익 실현 (5%)','기타/미기록'),('ask','손절 & 이익 실현','기타/미기록'),
                ('bid','물타기 & 손절','기타/미기록')]:
            with self.subTest(side=side,raw=raw):self.assertEqual(reason_group({'side':side,'reason':raw}),expected)
    def test_missing_unknown_reason_or_invalid_side_never_infers_strategy_or_leaks_raw(self):
        for side in ('bid','ask'):
            for raw in (None,'',' ','unknown private-canary',12,{},'RSI MACD BB'):
                self.assertEqual(reason_group({'side':side,'reason':raw}),'기타/미기록')
            self.assertEqual(reason_group({'side':side}),'기타/미기록')
        for side in (None,'buy','sell','BID',True,[]):
            self.assertEqual(reason_group({'side':side,'reason':'물타기 매수'}),'기타/미기록')
    def test_fee_accounting_unchanged_while_reason_aggregation_is_corrected(self):
        state=ledger([trade('b','bid','1','100','1',reason='RSI 과매도 반등(25.0)'),
                      trade('s','ask','0.5','60','0.5','9.5',ts=BASE+1,reason='RSI 과매수 하락(75.0)')],
                     '958.5',{'KRW-BTC':pos('0.5','100')})
        before=copy.deepcopy(state)
        def metrics():return report_metrics(state,'1000',{'KRW-BTC':(D(110),99)},now=100,verified=True,as_of=ASOF)
        # Frozen pre-fix presentation function: same accounting input, different labels only.
        def old(order):
            raw=order.get('reason')
            if not isinstance(raw,str):return '기타/미기록'
            return next((tag for tag in ('물타기','손절','이익 실현','매수','매도') if tag in raw),'기타/미기록')
        with patch('services.notification_metrics.reason_group',side_effect=old):previous=metrics()
        current=metrics()
        self.assertEqual(current['reason_statistics']['매수']['buy_count'],1)
        self.assertEqual(current['reason_statistics']['매수']['sell_count'],0)
        self.assertEqual(current['reason_statistics']['매도']['sell_count'],1)
        self.assertEqual(current['reason_statistics']['매도']['net_realized'],9)
        self.assertEqual(previous['reason_statistics']['매수']['sell_count'],1)
        self.assertIn('매도: 매수0/매도1',current['사유별 결과'])
        for report in (previous,current):
            report.pop('reason_statistics');report.pop('사유별 결과')
            for row in report['accounting_rows']:row.pop('reason_group')
        self.assertEqual(previous,current);self.assertEqual(state,before)
        self.assertEqual(current['수수료 합계'],D('1.5'));self.assertEqual(current['실현 손익'],9)
        self.assertEqual(current['평가 손익'],D('4.5'));self.assertIn('미기록/미검증',current['전략별 결과'])


@unittest.skipUnless(os.name=='posix','Existing private PAPER account ownership requires POSIX')
class PaperReportTests(unittest.TestCase):
    def test_report_relabels_persisted_fills_without_state_backup_or_accounting_mutation(self):
        from ops.paper_bot import initialize,read_report
        from ops import paper_experiment as exp
        from trading.execution import ExecutionEngine
        with tempfile.TemporaryDirectory(dir='/tmp') as temp:
            account=Path(temp).resolve()/'private'/'p16'
            self.assertEqual(initialize(account)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
            token=exp.begin(account);state=account/exp.STATE
            engine=ExecutionEngine(state,1_000_000,real_trading=False);engine._experiment_session=token;engine.open()
            try:
                self.assertTrue(engine.buy('KRW-BTC',10000,400000,reason='RSI 과매도 반등(25.0)'))
                self.assertTrue(engine.sell('KRW-BTC',10400,reason='RSI 과매수 하락(75.0)'))
                self.assertTrue(engine.buy('KRW-ETH',20000,200000))
            finally:engine.close()
            result=exp.finish(account,token,{'status':'STOPPED','engine_started':True,'post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
            self.assertEqual(result['status'],'STOPPED_VERIFIED')
            backup=account.parent/'cold-backup.json';backup.write_bytes(state.read_bytes())
            protected={p:p.read_bytes() for p in account.parent.rglob('*') if p.is_file()}
            with patch('services.notification_metrics.reason_group',return_value='기타/미기록'):before=read_report(account)
            after=read_report(account)
            self.assertEqual([t['reason_category'] for t in after['trades']],['매수','매도','기타/미기록'])
            self.assertEqual([t['side'] for t in after['trades']],['buy','sell','buy'])
            self.assertEqual(after['summary'],before['summary']);self.assertEqual(after['ledger_sha256'],before['ledger_sha256'])
            self.assertEqual(D(after['summary']['cash']),816000);self.assertEqual(D(after['summary']['net_realized_pnl']),16000)
            self.assertEqual(D(after['summary']['positions']['KRW-ETH']['quantity']),10)
            self.assertEqual(D(after['summary']['fees']),0)
            self.assertNotIn('RSI 과',json.dumps(after,ensure_ascii=False))
            for t in before['trades']+after['trades']:t.pop('reason_category')
            self.assertEqual(before,after)
            self.assertEqual(protected,{p:p.read_bytes() for p in account.parent.rglob('*') if p.is_file()})
