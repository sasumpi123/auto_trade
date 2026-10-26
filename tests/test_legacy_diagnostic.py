"""Synthetic decision -> actual PAPER accounting; sockets forbidden."""
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting.data import demo_candles
from backtesting.engine import CostModel
from backtesting.legacy_diagnostic import prepare,simulate,predicates,snapshot,ProbeAnalyzer,CostPaperEngine,main,run
from strategies.signals import LegacyCompositeStrategy,RSIStrategy,MACDStrategy
from services.notification_metrics import replay
from collections import Counter


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('Offline only'))
        blocker.start();self.addCleanup(blocker.stop)
        self.frame=demo_candles(212)
        for col in ('open','high','low','close'):self.frame[col]=10000.
        self.frame['high']=10100.;self.frame['low']=9900.;self.frame['volume']=100.
        self.windows={i:self.features('HOLD') for i in range(199,len(self.frame)-1)}
        self.windows[199]=self.features('BUY');self.windows[204]=self.features('SELL')
    def features(self,action):
        f=self.frame.iloc[:20].copy()
        f['rsi']=50.;f['macd']=0.;f['macd_signal']=0.
        f['bb_lower']=9000.;f['bb_middle']=10000.;f['bb_upper']=11000.
        if action=='BUY':f.loc[f.index[-2:],'rsi']=[20.,25.]
        if action=='SELL':f.loc[f.index[-2:],'rsi']=[80.,75.]
        return f
    def simulate(self,fee=5,slip=5,repeat=1,cash='1000000'):
        return simulate(self.frame,self.windows,market='KRW-BTC',bar_seconds=60,costs=CostModel(fee,slip,2),
            evaluation_start=self.frame.index[199],initial_cash=cash,repeat_evaluations=repeat)
    def test_actual_strategy_to_paper_fills_fee_accounting_and_next_open(self):
        r=self.simulate();self.assertTrue(r['accounting_verified']);self.assertEqual(r['fills'],{'buys':1,'sells':1})
        self.assertEqual(r['trace'][0]['executed_at'],self.frame.index[200].timestamp())
        self.assertGreater(Decimal(r['fees']),0);self.assertGreater(Decimal(r['slippage_cost']),0)
        self.assertLess(Decimal(r['metrics']['net_return_pct']),0)
        self.assertEqual(r['metrics']['win_rate_pct'],'0');self.assertEqual(r['source'],'SYNTHETIC_OR_CALLER_DATA_UNVERIFIED')
    def test_first_fill_uses_next_open_not_signal_close(self):
        self.frame.loc[self.frame.index[200],'open']=10050.
        r=self.simulate(0,0)
        self.assertEqual(Decimal(r['trace'][0]['price']),Decimal('10050')*Decimal('1.0001'))
    def test_cooldown_is_actual_global_signal_cooldown_not_same_action_only(self):
        self.windows[200]=self.features('SELL');r=self.simulate()
        self.assertGreater(r['counts']['cooldown_blocked'],0)
        self.assertEqual(r['counts']['cooldown_blocked_actionable'],1)
        self.assertEqual(r['fills']['sells'],1)
    def test_volume_gate_before_cooldown(self):
        self.windows[199].iloc[-1,self.windows[199].columns.get_loc('volume')]=1
        r=self.simulate();self.assertEqual(r['fills']['buys'],0);self.assertEqual(r['counts']['volume_blocked'],1)
    def test_repeated_same_candle_is_counted_and_suppressed_before_orders(self):
        one=self.simulate();many=self.simulate(repeat=3)
        self.assertEqual(one['fills'],many['fills']);self.assertEqual(one['metrics'],many['metrics'])
        self.assertEqual(many['counts']['duplicate_evaluations_suppressed'],many['counts']['evaluations']*2)
        self.assertEqual(many['counts']['repeat_signal_candidates'],4)
    def test_holding_and_missing_position_rejections(self):
        self.windows[204]=self.features('BUY');self.windows[210]=self.features('SELL')
        r=self.simulate();self.assertEqual(r['counts']['buy_while_held'],1)
        self.windows[199]=self.features('SELL');self.windows[204]=self.features('HOLD')
        r=self.simulate();self.assertGreater(r['counts']['sell_without_position'],0)
    def test_minimum_and_insufficient_cash_no_fake_profit(self):
        for cash,expected in (('1000','reject_cash_below_minimum'),('10000','reject_minimum_order')):
            r=self.simulate(cash=cash);self.assertEqual(r['fills']['buys'],0)
            self.assertGreater(r['counts'][expected],0)
            self.assertTrue(all(x is None for x in r['metrics'].values()));self.assertEqual(r['na_reason'],'NO_CONFIRMED_TRADES')
    def test_no_signals_reports_conditions_and_na_not_zero_profit(self):
        self.windows={i:self.features('HOLD') for i in self.windows};r=self.simulate()
        self.assertEqual(r['counts']['raw_BUY'],0);self.assertEqual(r['counts']['raw_SELL'],0)
        self.assertEqual(r['conditions']['rsi_lt_30']['not_met'],r['counts']['evaluations'])
        self.assertTrue(all(x is None for x in r['metrics'].values()))
    def test_fee_zero_and_positive_have_different_verified_net_results(self):
        free=self.simulate(0,0);paid=self.simulate(10,10)
        self.assertEqual(Decimal(free['fees']),0)
        self.assertGreater(Decimal(free['metrics']['net_return_pct']),Decimal(paid['metrics']['net_return_pct']))
    def test_rule_observation_agrees_with_actual_rsi_macd_and_legacy(self):
        for action in ('BUY','SELL','HOLD'):
            s=snapshot(self.features(action));flags=predicates(s)
            self.assertEqual(LegacyCompositeStrategy().decide(s).action,action)
            self.assertEqual(RSIStrategy().decide(s).action,action)
            self.assertEqual(flags['rsi_lt_30'] and flags['rsi_rising'],action=='BUY')
        f=self.features('HOLD');f['macd_signal']=-5.;f['macd']=-2.;f.iloc[-2,f.columns.get_loc('macd')]=-3.
        s=snapshot(f);self.assertEqual(MACDStrategy().decide(s).action,'BUY');self.assertTrue(predicates(s)['macd_rising'])
    def test_prefix_preparation_ignores_future_suffix_even_with_legacy_bfill(self):
        a=demo_candles(205);b=a.copy();b.iloc[202:]*=2
        _,x=prepare(a,60);_,y=prepare(b,60)
        for i in (199,200,201):pd.testing.assert_frame_equal(x[i],y[i])
        self.assertEqual(x[199].iloc[-1].close,a.iloc[199].close)
    def test_final_signal_is_not_filled_without_next_candle(self):
        self.windows={i:self.features('HOLD') for i in self.windows}
        self.frame=self.frame.iloc[:200];self.frame,self.windows=prepare(self.frame,60)
        r=self.simulate();self.assertEqual(r['fills']['buys'],0);self.assertEqual(r['counts']['evaluations'],0)
    def test_invalid_gapped_or_duplicate_candles_are_not_repaired(self):
        for f in (demo_candles(205).drop(demo_candles(205).index[5]),pd.concat([demo_candles(205),demo_candles(205).iloc[-1:]])):
            with self.assertRaises(ValueError):prepare(f,60)
    def test_partial_sell_reuses_engine_and_p9_fee_inclusive_replay(self):
        with TemporaryDirectory() as temp:
            now=[1000000.];e=CostPaperEngine(Path(temp)/'synthetic.json',1000,CostModel(100,0),lambda:now[0]);e.open()
            try:
                e.buy('KRW-BTC',100,101);now[0]+=60
                order={'identifier':'synthetic-partial','ticker':'KRW-BTC','side':'ask','averaging':False,'value':'.5','status':'intent'}
                e.state['orders']['synthetic-partial']=order;e.opening=Decimal(120)
                receipt={'state':'cancel','executed_volume':'.5','trades':[{'volume':'.5','funds':'60'}]}
                e._apply_fill(order,receipt);e.store.save(e.state)
                a=replay(e.state,Decimal(1000),as_of=pd.Timestamp(now[0]+1,unit='s',tz='UTC').to_pydatetime(),calendar='UTC')
                self.assertEqual(a['net_profit'],Decimal('8.9'))
                self.assertEqual(a['positions']['KRW-BTC']['cost'],Decimal('50.5'))
                self.assertEqual(e.store.load(),e.state)
            finally:e.close()
    def test_explicit_missing_or_unknown_real_data_never_downloads(self):
        with TemporaryDirectory() as tmp,patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['--data',tmp,'--market','KRW-BTC','--unit','60','--split','train','--format','json']),2)
            self.assertEqual(json.loads(out.getvalue())['status'],'INSUFFICIENT_REAL_DATA')
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main([])
    def test_holdout_and_unregistered_intervals_rejected_before_data_open(self):
        for unit,split in ((1,'train'),(60,'test')):
            from backtesting.historical import load_protocol
            protocol=load_protocol()
            with patch('backtesting.legacy_diagnostic.load_protocol',return_value=protocol),patch('pathlib.Path.read_text',side_effect=AssertionError('No input read')):
                with self.assertRaises(ValueError):run('not-read','KRW-BTC',unit,split)

    def test_nonfinite_indicator_while_held_cannot_hide_risk_or_drawdown(self):
        self.windows[200].iloc[-1,self.windows[200].columns.get_loc('rsi')]=float('nan')
        with self.assertRaisesRegex(ValueError,'NONFINITE_FEATURES_WHILE_HOLDING'):self.simulate()
    def test_report_is_deterministic_and_markdown_includes_na_reasons(self):
        from backtesting.legacy_diagnostic import markdown
        self.assertEqual(self.simulate(),self.simulate())
        self.windows={i:self.features('HOLD') for i in self.windows}
        text=markdown({'source':'SYNTHETIC','split':'synthetic','results':[self.simulate()]})
        self.assertIn('NO_CONFIRMED_TRADES',text);self.assertIn('N/A',text);self.assertIn('INSUFFICIENT_REAL_DATA',text)
    def test_direct_engine_insufficient_budget_is_rejected_without_fill(self):
        with TemporaryDirectory() as temp:
            e=CostPaperEngine(Path(temp)/'paper.json',1000,CostModel(5,5),lambda:1000000.)
            e.open()
            try:
                self.assertFalse(e.buy('KRW-BTC',100,1001));self.assertEqual(e.state['orders'],{})
                self.assertEqual(e.cash,1000)
            finally:e.close()
    def test_unrecognized_manifest_cannot_mislabel_synthetic_as_real(self):
        with TemporaryDirectory() as tmp:
            Path(tmp,'manifest.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'Archive manifest not recognized'):run(tmp,'KRW-BTC',60,'train')
