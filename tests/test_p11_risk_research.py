"""Causal overlay/accounting/cost and sealed-prefix regressions; synthetic fixtures only."""
from dataclasses import replace
from decimal import Decimal
import gzip
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting.engine import BacktestConfig, BacktestEngine, CostModel, SimulationPortfolio
from backtesting.risk import RiskOverlay, OVERLAYS
from backtesting.risk_research import load_risk_protocol
from backtesting.development_data import load_development
from backtesting.data import dataset_hash, indicators
from tests.test_backtesting import candles, ScriptedStrategy


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.config=BacktestConfig(CostModel(0,0),initial_cash=1000,position_fraction='.4',min_order=0,cooldown_seconds=0)

    def run_case(self,overlay,closes=None,opens=None,config=None,actions=('BUY',)):
        frame=candles()
        for name,changes in (('close',closes or {}),('open',opens or {})):
            for i,value in changes.items():frame.loc[frame.index[i],name]=value
        frame['high']=frame[['open','close']].max(axis=1)+1
        frame['low']=frame[['open','close']].min(axis=1)-1
        return BacktestEngine(config or self.config).run(frame,ScriptedStrategy(actions),overlay=RiskOverlay(overlay))

    def test_signal_only_retains_original_default_results(self):
        frame=candles()
        a=BacktestEngine(self.config).run(frame,ScriptedStrategy(['BUY','SELL']))
        b=BacktestEngine(self.config).run(frame,ScriptedStrategy(['BUY','SELL']),overlay=RiskOverlay())
        self.assertEqual(a.to_dict(),b.to_dict())

    def test_take_profit_observes_close_executes_next_open_with_gap(self):
        r=self.run_case('take_profit_5',{35:105},{36:103})
        self.assertEqual([f.reason for f in r.fills][1:],[ 'take_profit_5'])
        self.assertEqual(r.fills[1].price,103)
        self.assertEqual(r.fills[1].signal_bar,candles().index[35])
        self.assertEqual(r.fills[1].executed_at,candles().index[36])
        self.assertEqual(r.metrics['realized_pnl'],12)

    def test_stop_does_not_guarantee_five_percent_execution(self):
        r=self.run_case('stop_5',{35:95},{36:80})
        self.assertEqual(r.fills[1].reason,'stop_5')
        self.assertEqual(r.metrics['realized_pnl'],-80)

    def test_average_once_then_stop_uses_updated_execution_average(self):
        r=self.run_case('take_profit_average_once_5',{35:95,36:93},{36:95,37:90})
        self.assertEqual([f.side for f in r.fills],['BUY','BUY','SELL'])
        self.assertEqual(r.fills[1].notional,190)
        self.assertEqual(r.metrics['realized_pnl'],-50)
        self.assertEqual(r.metrics['averaging_fill_count'],1)
        self.assertEqual(r.metrics['stop_count'],1)
        self.assertEqual(r.metrics['ending_quantity'],0)

    def test_averaging_adds_exposure_not_a_free_loss_reduction(self):
        r=self.run_case('take_profit_average_once_5',{35:95},{36:95})
        self.assertGreater(r.metrics['max_market_exposure_pct'],40)
        self.assertEqual(r.metrics['fill_count'],2)

    def test_no_cash_stops_instead_of_borrowing_to_average(self):
        r=self.run_case('take_profit_average_once_5',{35:95},config=replace(self.config,position_fraction=1))
        self.assertEqual([f.side for f in r.fills],['BUY','SELL'])
        self.assertEqual(r.metrics['averaging_fill_count'],0)
        self.assertTrue(all(row['cash']>=0 for row in r.equity))

    def test_take_profit_only_does_not_add_a_stop(self):
        r=self.run_case('take_profit_5',{35:90})
        self.assertEqual(len(r.fills),1)

    def test_stop_only_does_not_add_a_take_profit(self):
        r=self.run_case('stop_5',{35:110})
        self.assertEqual(len(r.fills),1)

    def test_no_intrabar_threshold_or_ordering_invention(self):
        frame=candles();frame.loc[frame.index[35],['low','high']]=[70,130]
        r=BacktestEngine(self.config).run(frame,ScriptedStrategy(['BUY']),overlay=RiskOverlay('take_profit_stop_5'))
        self.assertEqual(len(r.fills),1)

    def test_risk_priority_bypasses_signal_volume_filter_and_cooldown(self):
        frame=candles();frame.loc[frame.index[35],['volume','close','high']]=[0,105,106]
        r=BacktestEngine(replace(self.config,cooldown_seconds=3600)).run(frame,ScriptedStrategy(['BUY']),overlay=RiskOverlay('take_profit_stop_5'))
        self.assertEqual(r.fills[1].reason,'take_profit_5')

    def test_final_bar_risk_signal_is_unfilled_not_profit(self):
        r=self.run_case('stop_5',{44:95})
        self.assertEqual(len(r.fills),1)
        self.assertEqual(r.unfilled_final_signal['reason'],'stop_5')
        self.assertEqual(r.metrics['realized_pnl'],0)
        self.assertEqual(r.metrics['unrealized_pnl'],-20)

    def test_fee_inclusive_net_identity_with_averaging(self):
        r=self.run_case('take_profit_average_once_5',{35:94,36:91},{36:94,37:90},config=replace(self.config,costs=CostModel(10,5,2)))
        self.assertEqual(r.metrics['net_pnl'],r.metrics['realized_pnl']+r.metrics['unrealized_pnl'])
        self.assertEqual(r.metrics['total_fees'],sum(f.fee for f in r.fills))
        self.assertEqual(r.fills[-1].realized_pnl,r.fills[-1].notional-r.fills[-1].fee-sum(f.notional+f.fee for f in r.fills[:-1]))
        self.assertEqual(r.metrics['win_rate_pct'],0)

    def test_fee_slippage_and_half_spread_apply_both_sides(self):
        costs=CostModel(10,5,2)
        self.assertEqual(costs.price(100,'BUY'),Decimal('100.06'))
        self.assertEqual(costs.price(100,'SELL'),Decimal('99.94'))
        r=self.run_case('signal_only',config=replace(self.config,costs=costs),actions=('BUY','SELL'))
        self.assertLess(r.metrics['net_pnl'],0)
        self.assertEqual(r.metrics['total_fees'],sum(f.notional*Decimal('.001') for f in r.fills))

    def test_zero_cost_flat_round_trip_is_not_a_win(self):
        r=self.run_case('signal_only',actions=('BUY','SELL'))
        self.assertEqual(r.metrics['net_pnl'],0);self.assertEqual(r.metrics['win_rate_pct'],0)

    def test_no_closed_trades_means_na_win_rate(self):
        r=self.run_case('signal_only')
        self.assertIsNone(r.metrics['win_rate_pct'])

    def test_average_flag_resets_only_after_full_exit(self):
        p=SimulationPortfolio(Decimal(1000));t=candles().index[0]
        def intent(**kw):return {'signal_bar':t,'decided_at':t,**kw}
        self.assertTrue(p.execute(intent(action='BUY'),t,100,self.config))
        avg=intent(action='BUY',averaging=True,budget=Decimal(100),reason='average_once')
        self.assertTrue(p.execute(avg,t,95,self.config));self.assertFalse(p.execute(avg,t,95,self.config))
        self.assertTrue(p.execute(intent(action='SELL'),t,100,self.config))
        self.assertFalse(p.averaging_used);self.assertEqual(p.entry_notional,0)
        self.assertTrue(p.execute(intent(action='BUY'),t,100,self.config))
        self.assertTrue(p.execute(avg,t,95,self.config))

    def test_unfillable_average_falls_back_to_sell_at_execution_open(self):
        r=self.run_case('take_profit_average_once_5',{35:95},{36:1e15})
        self.assertEqual([f.side for f in r.fills],['BUY','SELL'])
        self.assertEqual(r.fills[1].reason,'stop_average_unfilled')

    def test_gap_and_future_data_do_not_change_earlier_decisions(self):
        frame=candles(65)
        frame.loc[frame.index[35:50],'close']=95
        frame['low']=frame[['open','close']].min(axis=1)-1
        engine=BacktestEngine(self.config);risk=RiskOverlay('take_profit_average_once_5')
        prefix=engine.run(frame.iloc[:55],ScriptedStrategy(['BUY']),overlay=risk)
        frame.loc[frame.index[55:],'close']=200;frame.loc[frame.index[55:],'high']=201
        full=engine.run(frame,ScriptedStrategy(['BUY']),overlay=risk)
        self.assertEqual(prefix.fills,tuple(f for f in full.fills if f.executed_at<frame.index[55]))
        pd.testing.assert_frame_equal(indicators(frame.iloc[:55]),indicators(frame).iloc[:55])

    def test_missing_bar_fails_before_simulation(self):
        with self.assertRaisesRegex(ValueError,'gapped'):
            BacktestEngine(self.config).run(candles().drop(candles().index[35]),ScriptedStrategy(['BUY']),overlay=RiskOverlay('stop_5'))

    def test_fixed_hypotheses_and_no_live_imports(self):
        spec,_=load_risk_protocol();self.assertEqual(tuple(spec['overlays']),OVERLAYS)
        self.assertEqual(spec['take_profit'],'0.05')
        with self.assertRaises(ValueError):RiskOverlay('optimized')
        self.assertEqual(spec['splits'],['train','validation'])
        self.assertEqual(spec['max_averages_per_position'],1)


class DevelopmentPrefixTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.frame=candles(80);self.frame.index=pd.date_range('2025-01-01',periods=80,freq='15min',tz='UTC',name='timestamp')
        self.protocol={'start':self.frame.index[0].isoformat(),'train_end':self.frame.index[40].isoformat(),
            'validation_end':(self.frame.index[-1]+pd.Timedelta(minutes=15)).isoformat(),'units_minutes':[15]}
        self.meta={'splits':{name:{'rows':40,'sha256':dataset_hash(part)} for name,part in
            (('train',self.frame.iloc[:40]),('validation',self.frame.iloc[40:]))}}
        self.text=self.frame.to_csv(float_format='%.17g',lineterminator='\n')
        self.path=Path(self.tmp.name)/'snapshot.csv.gz'
        self.path.write_bytes(gzip.compress((self.text+'HOLDOUT_POISON_NOT_A_CSV_ROW\n').encode()))

    def test_never_requests_holdout_row_even_for_peeking(self):
        class Guard(StringIO):
            def __init__(self,text):super().__init__(text);self.reads=0
            def readline(self,*args):
                self.reads+=1
                if self.reads>81:raise AssertionError('Holdout row requested')
                return super().readline(*args)
        guard=Guard(self.text)
        with patch('backtesting.development_data.gzip.open',return_value=guard):
            frame=load_development(self.path,self.protocol,15,self.meta)
        self.assertEqual(len(frame),80);self.assertEqual(guard.reads,81)

    def test_poisoned_tail_is_not_parsed_or_used(self):
        self.assertEqual(dataset_hash(load_development(self.path,self.protocol,15,self.meta)),dataset_hash(self.frame))

    def test_development_tamper_is_blocked(self):
        self.meta['splits']['train']['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'hash'):load_development(self.path,self.protocol,15,self.meta)

    def test_calendar_count_tamper_is_blocked_before_open(self):
        self.meta['splits']['validation']['rows']=41
        with patch('backtesting.development_data.gzip.open',side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(ValueError,'row counts'):load_development(self.path,self.protocol,15,self.meta)

    def test_truncated_prefix_fails_instead_of_loading_tail(self):
        self.path.write_bytes(gzip.compress(self.text[:100].encode()))
        with self.assertRaises(ValueError):load_development(self.path,self.protocol,15,self.meta)


class ArchivedRiskReportTests(unittest.TestCase):
    root=Path(__file__).resolve().parents[1]

    def test_report_fixed_grid_accounting_and_holdout_contract(self):
        report=json.loads((self.root/'research/p11-risk-2026-10-23/results.json').read_text(encoding='utf-8'))
        self.assertTrue(report['complete']);self.assertTrue(report['baseline_archive_checked'])
        self.assertFalse(report['holdout_evaluated']);self.assertEqual(report['holdout_rows_parsed'],0)
        self.assertFalse(report['independent_validation']);self.assertFalse(report['multi_asset_portfolio_implemented'])
        _,digest=load_risk_protocol();self.assertEqual(report['risk_protocol_sha256'],digest)
        self.assertEqual(len(report['datasets']),8)
        rows=[r for d in report['datasets'].values() for r in d['results']]
        self.assertEqual(len(rows),3072)
        keys={(r['dataset'],r['split'],r['strategy'],r['overlay'],r['fee_bps'],r['slippage_bps']) for r in rows}
        self.assertEqual(len(keys),3072)
        self.assertEqual({r['split'] for r in rows},{'train','validation'})
        self.assertEqual(sum(r['overlay']=='signal_only' for r in rows),768)
        for row in rows:
            m=row['metrics'];d=lambda k:Decimal(str(m[k]))
            self.assertLessEqual(abs(d('net_pnl')-d('realized_pnl')-d('unrealized_pnl')),Decimal('1e-20'))
            self.assertGreaterEqual(d('ending_cash'),0)
            self.assertGreaterEqual(d('total_fees'),0)
            self.assertLessEqual(d('max_market_exposure_pct'),100)
            if m['closed_trades']==0:self.assertIsNone(m['win_rate_pct'])

    def test_actual_xrp_validation_baseline_and_average_reproduce(self):
        import hashlib
        from dataclasses import asdict
        from backtesting.engine import serializable
        from backtesting.historical import load_protocol
        from strategies.signals import LegacyCompositeStrategy
        directory=self.root/'research/2026-10-21/data'
        protocol,_=load_protocol()
        manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
        frame=load_development(directory/'KRW-XRP-15.csv.gz',protocol,15,manifest['datasets']['KRW-XRP-15'])
        config=BacktestConfig(CostModel(5,5,2),bar_seconds=900)
        engine=BacktestEngine(config);prepared=engine.prepare(frame)
        report=json.loads((self.root/'research/p11-risk-2026-10-23/results.json').read_text(encoding='utf-8'))
        with patch('socket.socket.connect',side_effect=AssertionError('No research network')):
            for overlay in ('signal_only','take_profit_average_once_5'):
                result=engine.run(prepared,LegacyCompositeStrategy(),evaluation_start=protocol['train_end'],overlay=RiskOverlay(overlay))
                archived=next(r for r in report['datasets']['KRW-XRP-15']['results'] if r['primary'] and r['split']=='validation'
                    and r['strategy']=='legacy_composite' and r['overlay']==overlay)
                self.assertEqual(result.data_hash,archived['context_sha256'])
                for key,value in result.metrics.items():
                    if value is None:self.assertIsNone(archived['metrics'][key])
                    else:self.assertAlmostEqual(float(value),float(archived['metrics'][key]),places=8)
                trace=hashlib.sha256(json.dumps(serializable([asdict(f) for f in result.fills]),sort_keys=True).encode()).hexdigest()
                self.assertEqual(trace,archived['fill_trace_sha256'])
