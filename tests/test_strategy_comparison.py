"""Synthetic strategy comparisons; never market/API/operational accounts."""
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backtesting.engine import CostModel
from backtesting.legacy_diagnostic import simulate,prepare
from backtesting.data import demo_candles
from backtesting.strategy_comparison import candidates,NAMES,describe,summarize,check_baseline,markdown,write_report,main
import tests.test_legacy_diagnostic as legacy_tests


class ComparisonTests(unittest.TestCase):
    setUp=legacy_tests.DiagnosticTests.setUp
    features=legacy_tests.DiagnosticTests.features

    def all_vote(self,action):
        f=self.features(action)
        if action=='BUY':
            f['macd_signal']=-3.;f['macd']=-2.;f.iloc[-2,f.columns.get_loc('macd')]=-2.5
            f['bb_lower']=10001.;f['bb_middle']=10005.;f['bb_upper']=10010.
        elif action=='SELL':
            f['macd_signal']=3.;f['macd']=2.;f.iloc[-2,f.columns.get_loc('macd')]=2.5
            f['bb_lower']=9990.;f['bb_middle']=9995.;f['bb_upper']=9999.
        return f
    def run_strategy(self,strategy,repeat=1):
        return simulate(self.frame,self.windows,market='KRW-BTC',bar_seconds=60,costs=CostModel(5,5,2),
            evaluation_start=self.frame.index[199],strategy=strategy,repeat_evaluations=repeat)
    def row(self,strategy='legacy_composite',market='KRW-BTC',net='1',unit=60):
        # Summary-only fixtures are explicitly synthetic, not a performance experiment.
        return {'source':'SYNTHETIC','strategy':strategy,'market':market,'bar_seconds':unit*60,'split':'validation',
            'costs':{'fee_bps':'5','slippage_bps':'5','spread_bps':'2'},'initial_cash':'1000000','data_sha256':'same-input',
            'evaluation_first_open':'same','execution_last_open':'same','fills':{'buys':1 if net is not None else 0,'sells':1 if net is not None else 0},
            'metrics':{'net_return_pct':net,'mdd_pct':'1','win_rate_pct':'50','expectancy_per_sell':'10',
                'buy_hold_100_net_pct':'2' if net is not None else None,'buy_hold_40_net_pct':'.8' if net is not None else None}}

    def test_registered_strategy_names_and_default_legacy_are_unchanged(self):
        self.assertEqual(tuple(s.name for s in candidates()),NAMES)
        a=self.run_strategy(candidates()[0])
        b=simulate(self.frame,self.windows,market='KRW-BTC',bar_seconds=60,costs=CostModel(5,5,2),evaluation_start=self.frame.index[199])
        self.assertEqual(a,b)
    def test_all_six_actual_signals_reach_same_fee_paper_accounting(self):
        self.windows[199]=self.all_vote('BUY');self.windows[204]=self.all_vote('SELL')
        results=[self.run_strategy(s) for s in candidates()]
        for r in results:
            self.assertTrue(r['accounting_verified']);self.assertEqual(r['fills'],{'buys':1,'sells':1})
            self.assertEqual(r['trace'][0]['executed_at'],self.frame.index[200].timestamp())
            self.assertGreater(Decimal(r['fees']),0)
        self.assertTrue(all(r['metrics']==results[0]['metrics'] for r in results))
    def test_quorum_candidates_are_not_silently_replaced_by_legacy(self):
        values={s.name:self.run_strategy(s) for s in candidates()}
        self.assertEqual(values['rsi']['fills']['buys'],1)
        for name in ('macd','bollinger','consensus_2of3','consensus_3of3'):
            self.assertEqual(values[name]['fills']['buys'],0)
            self.assertIsNone(values[name]['metrics']['net_return_pct'])
    def test_volume_and_opposite_direction_cooldown_apply_to_every_strategy(self):
        self.windows[199]=self.all_vote('BUY');self.windows[200]=self.all_vote('SELL');self.windows[204]=self.all_vote('SELL')
        for s in candidates():
            r=self.run_strategy(s);self.assertEqual(r['counts']['cooldown_blocked_actionable'],1)
        self.windows[199].iloc[-1,self.windows[199].columns.get_loc('volume')]=1
        for s in candidates():
            r=self.run_strategy(s);self.assertEqual(r['fills']['buys'],0);self.assertEqual(r['counts']['volume_blocked'],1)
    def test_repeated_candle_cannot_multiply_orders_for_any_strategy(self):
        self.windows[199]=self.all_vote('BUY');self.windows[204]=self.all_vote('SELL')
        for s in candidates():
            a=self.run_strategy(s);b=self.run_strategy(s,3)
            self.assertEqual(a['fills'],b['fills']);self.assertEqual(a['metrics'],b['metrics'])
            self.assertGreater(b['counts']['duplicate_evaluations_suppressed'],0)
    def test_common_risk_take_profit_overrides_each_signal_strategy(self):
        self.windows[199]=self.all_vote('BUY');self.windows[204]=self.features('HOLD')
        self.frame.loc[self.frame.index[201],'open']=11000.
        self.frame.loc[self.frame.index[201],'high']=11100.
        for s in candidates():
            r=self.run_strategy(s);self.assertGreaterEqual(r['fills']['sells'],1)
            self.assertGreater(r['counts']['risk_bypassed_analyzer'],0)
            self.assertEqual(r['trace'][1]['executed_at'],self.frame.index[201].timestamp())
    def test_future_suffix_does_not_change_earlier_decisions_for_any_candidate(self):
        f=demo_candles(205);changed=f.copy();changed.iloc[202:]*=2
        _,a=prepare(f,60);_,b=prepare(changed,60)
        from backtesting.legacy_diagnostic import snapshot
        for i in (199,200,201):
            for s in candidates():self.assertEqual(s.decide(snapshot(a[i])),s.decide(snapshot(b[i])))
    def test_descriptive_missing_cases_are_not_changed_to_zero_profit(self):
        r=describe([None,'-1','0','3']);self.assertEqual(r['available'],3);self.assertEqual(r['missing'],1)
        self.assertEqual(r['median'],0);self.assertEqual(r['positive'],1)
        rows=[self.row(net=None),self.row('rsi',net='2')]
        _,summary,_=summarize(rows);s=next(x for x in summary if x['strategy']=='rsi')
        self.assertEqual(s['legacy_net_delta_pp']['available'],0)
        self.assertEqual(s['net_return_pct']['available'],1)
    def test_one_outlier_does_not_create_unqualified_superiority(self):
        rows=[]
        for i,m in enumerate(('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL')):
            rows.extend([self.row(market=m,net='0'),self.row('rsi',market=m,net='100' if i==0 else '-1')])
        _,summary,sensitivity=summarize(rows)
        s=next(x for x in summary if x['strategy']=='rsi')
        self.assertGreater(Decimal(s['net_return_pct']['mean']),0)
        self.assertLess(Decimal(s['net_return_pct']['median']),0)
        leave=next(x for x in sensitivity if x['strategy']=='rsi' and x['excluded_market']=='KRW-BTC')
        self.assertEqual(leave['net_return_pct']['mean'],'-1');self.assertEqual(s['legacy_net_delta_pp']['positive'],1)
    def test_unpaired_inputs_costs_and_duplicate_cases_fail_closed(self):
        for field,value in (('initial_cash','999'),('data_sha256','other'),('evaluation_first_open','other'),('costs',{'fee_bps':'5','slippage_bps':'5','spread_bps':'3'})):
            r=self.row('rsi');r[field]=value
            with self.assertRaises(ValueError):summarize([self.row(),r])
        with self.assertRaises(ValueError):summarize([self.row(),self.row()])
    def test_p11a_regression_check_includes_trace_and_real_accounting(self):
        import hashlib
        r=self.run_strategy(candidates()[0]);old=deepcopy(r)
        old.update(trace_count=len(r['trace']),trace_sha256=hashlib.sha256(json.dumps(r['trace'],sort_keys=True).encode()).hexdigest())
        check_baseline(r,old)
        old['metrics']['net_return_pct']='99'
        with self.assertRaisesRegex(ValueError,'P11A_BASELINE_REGRESSION'):check_baseline(r,old)
        old=deepcopy(r);old.update(trace_count=0,trace_sha256='wrong')
        with self.assertRaisesRegex(ValueError,'P11A_TRACE_REGRESSION'):check_baseline(r,old)
    def test_explicit_offline_cli_never_overwrites_existing_directory(self):
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main([])
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'old';p.mkdir();file=p/'sentinel';file.write_text('preserve')
            with patch('sys.stdout',new_callable=io.StringIO):self.assertEqual(main(['--data','missing','--output',str(p)]),2)
            self.assertEqual(file.read_text(),'preserve');self.assertEqual(list(p.iterdir()),[file])
    def test_incomplete_study_is_marked_and_has_no_success_artifact(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp)/'new'
            with patch('sys.stdout',new_callable=io.StringIO):self.assertEqual(main(['--data','missing','--output',str(p)]),2)
            self.assertTrue((p/'INCOMPLETE').exists());self.assertFalse((p/'results.json').exists())

    def test_json_markdown_csv_export_agree_on_synthetic_comparisons(self):
        import csv,hashlib
        self.windows[199]=self.all_vote('BUY');self.windows[204]=self.all_vote('SELL')
        rows=[]
        for s in candidates():
            r=self.run_strategy(s);trace=r.pop('trace')
            r.update(strategy=s.name,split='validation',trace_count=len(trace),
                trace_sha256=hashlib.sha256(json.dumps(trace,sort_keys=True).encode()).hexdigest())
            rows.append(r)
        values,summary,sensitivity=summarize(rows)
        report={'source':'SYNTHETIC','comparisons':values,'summaries':summary,'leave_one_market_out':sensitivity}
        with TemporaryDirectory() as tmp:
            p=Path(tmp);write_report(report,p)
            result=json.loads((p/'results.json').read_text())
            self.assertEqual(len(result['comparisons']),6)
            with (p/'metrics.csv').open(newline='') as f:table=list(csv.DictReader(f))
            self.assertEqual({r['strategy'] for r in table},set(NAMES))
            self.assertTrue(all(r['filled_buys']=='1' and r['filled_sells']=='1' for r in table))
            text=(p/'REPORT.md').read_text();self.assertIn('INSUFFICIENT_REAL_DATA',text)
            for name in NAMES:self.assertIn(name,text)

    def test_candidate_order_does_not_change_shared_inputs_or_outcomes(self):
        before={i:df.copy(deep=True) for i,df in self.windows.items()}
        forward={s.name:self.run_strategy(s) for s in candidates()}
        reverse={s.name:self.run_strategy(s) for s in reversed(candidates())}
        self.assertEqual(forward,reverse)
        import pandas as pd
        for i,df in before.items():pd.testing.assert_frame_equal(df,self.windows[i])

    def test_invalid_volume_cannot_bypass_legacy_validation_in_research_candidates(self):
        self.windows[199]=self.all_vote('BUY')
        self.windows[199].iloc[-1,self.windows[199].columns.get_loc('volume')]=float('nan')
        for s in candidates():
            with self.assertRaises(ValueError):self.run_strategy(s)
