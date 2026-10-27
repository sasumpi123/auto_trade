"""P11-D synthetic offline fills, quarantine, allocation and report regression."""
from copy import deepcopy
from decimal import Decimal as D
import io
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting.collection import write_json
from backtesting.data import demo_candles
from backtesting.engine import CostModel
from backtesting.legacy_diagnostic import CostPaperEngine,simulate
from backtesting.minute_study import collect,load_sample,load_partitioned_sample
from backtesting.loss_diagnostic import run,main,sensitivity,cost_cases
from backtesting.trade_attribution import attribute
from backtesting.historical import load_protocol
from services.notification_metrics import close


class LossDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        self.start=pd.Timestamp('2026-10-23T00:00Z');self.end=self.start+pd.Timedelta(minutes=240)
        self.now=pd.Timestamp('2026-10-24T00:00Z').timestamp()
        self.frame=demo_candles(240);self.frame.index=pd.date_range(self.start,periods=240,freq='min')
        block=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        block.start();self.addCleanup(block.stop)
    def acquire(self,markets=('KRW-BTC',),gaps=None,name='sample'):
        owner=self;gaps=gaps or {};path=self.root/name
        class Rest:
            def get(self,endpoint,params):
                f=owner.frame.drop(owner.frame.index[gaps.get(params['market'],[])])
                f=f.loc[(f.index<pd.Timestamp(params['to']))&(f.index>=owner.start)].iloc[-params['count']:]
                return [{'market':params['market'],'unit':1,'candle_date_time_utc':t.tz_localize(None).isoformat(),
                    'opening_price':float(r.open),'high_price':float(r.high),'low_price':float(r.low),
                    'trade_price':float(r.close),'candle_acc_trade_volume':float(r.volume)} for t,r in f.iloc[::-1].iterrows()]
            def close(self):pass
        collect(path,list(markets),self.start,self.end,connect_public=True,client=Rest(),clock=lambda:self.now,monotonic=lambda:0)
        return path
    def engine(self,costs=None):
        now=[1000000.];e=CostPaperEngine(self.root/'synthetic.json',1000,costs or CostModel(100,0,0),lambda:now[0],capture_details=True)
        e.open();self.addCleanup(e.close);return e,now
    def attr(self,e,now,mark=110):
        return attribute(e.state,e.detail_fills,D(1000),as_of=pd.Timestamp(now[0]+60,unit='s',tz='UTC'),mark=D(mark),
            exposure=[{'timestamp':now[0],'quantity':str(e.position('KRW-BTC')['quantity']),'reference_price':str(mark)}])
    def partial(self,e,now,q='.5',price=120):
        now[0]+=60
        o={'identifier':'synthetic-partial','ticker':'KRW-BTC','side':'ask','averaging':False,'value':q,'status':'intent','reason':'RSI synthetic sell'}
        e.state['orders'][o['identifier']]=o;e.opening=D(price)
        e._apply_fill(o,{'state':'cancel','executed_volume':q,'trades':[{'volume':q,'funds':str(D(q)*D(price))}]})
        e.store.save(e.state)
    def test_partial_sell_fee_inclusive_average_averaging_and_open_position(self):
        e,now=self.engine();e.buy('KRW-BTC',100,101,reason='RSI synthetic buy');now[0]+=60
        e.buy('KRW-BTC',90,'90.9',averaging=True,reason='물타기 매수');self.partial(e,now)
        a=self.attr(e,now);t=a['closed_sales'][0]
        self.assertEqual(D(t['net_realized_pnl']),D('11.425'))
        self.assertEqual(D(t['entry_fee_inclusive_cost']),D('47.975'))
        self.assertEqual(D(t['allocated_buy_fee']),D('.475'))
        self.assertEqual(D(a['open_position']['remaining_cost']),D('143.925'))
        self.assertEqual(D(a['unrealized_pnl']),D('21.075'))
        self.assertEqual(a['averaging']['buys'],1);self.assertTrue(t['had_averaging_down'])
        self.assertEqual(D(t['quantity_weighted_holding_seconds']),90)
        self.assertEqual(t['entry_signals'][0]['signal_components'],['RSI'])
        self.assertNotIn('synthetic-partial',json.dumps(a))
    def test_full_sell_then_rebuy_resets_entry_history_and_persists(self):
        e,now=self.engine();e.buy('KRW-BTC',100,101,reason='RSI');now[0]+=60
        e.sell('KRW-BTC',110,reason='이익 실현 (5%)');now[0]+=60
        e.buy('KRW-BTC',90,'90.9',reason='BB')
        a=self.attr(e,now);self.assertEqual(a['closed_sales'][0]['exit_reason'],'TAKE_PROFIT')
        self.assertEqual(a['open_position']['entry_first_at_utc'],pd.Timestamp(now[0],unit='s',tz='UTC').isoformat())
        self.assertEqual(e.store.load(),e.state)
    def test_wins_losses_expectancy_and_consecutive_loss_reset(self):
        e,now=self.engine(CostModel(0,0,0))
        for price in (90,80,100,110,90):
            e.buy('KRW-BTC',100,100,reason='MACD');now[0]+=60
            e.sell('KRW-BTC',price,reason='손절' if price<100 else 'MACD');now[0]+=60
        a=self.attr(e,now)
        self.assertEqual(a['max_consecutive_losing_sells'],2);self.assertEqual(a['losses']['count'],3)
        self.assertEqual(a['breakeven_sells'],1);self.assertEqual(D(a['wins']['mean_net_pnl']),10)
        self.assertEqual(D(a['expectancy_per_sell']),-6)
        self.assertEqual(a['exit_reasons']['STOP_LOSS']['sells'],3)
    def test_same_path_price_move_fee_slip_spread_identity(self):
        e,now=self.engine(CostModel(5,5,2));e.buy('KRW-BTC',100,400,reason='MACD');now[0]+=60
        e.sell('KRW-BTC',101,reason='MACD')
        a=self.attr(e,now);t=a['closed_sales'][0]
        net=D(t['gross_reference_price_pnl'])-sum(D(t[k]) for k in ('allocated_buy_fee','sell_fee','allocated_buy_slippage','sell_slippage','allocated_buy_half_spread','sell_half_spread'))
        self.assertTrue(close(net,D(t['net_realized_pnl'])))
        self.assertTrue(all(D(v)>0 for v in a['cost_totals'].values()))
    def test_unliquidated_buy_realized_zero_is_not_unrealized_zero(self):
        e,now=self.engine();e.buy('KRW-BTC',100,101,reason='BB')
        a=self.attr(e,now)
        self.assertEqual(D(a['net_realized_pnl']),0);self.assertEqual(D(a['unrealized_pnl']),9)
        self.assertIsNone(a['expectancy_per_sell']);self.assertIsNone(a['max_consecutive_losing_sells'])
        self.assertEqual(a['exposure']['position_held_time_pct'],'100')
    def test_order_cash_quantity_cost_or_event_tamper_fails_closed(self):
        e,now=self.engine();e.buy('KRW-BTC',100,101,reason='RSI')
        original=deepcopy(e.detail_fills)
        for field,value in (('quantity','2'),('fee','9'),('reference_price','50'),('executed_at',999999.),('reason','TAKE_PROFIT')):
            e.detail_fills=deepcopy(original);e.detail_fills[0][field]=value
            with self.assertRaises(ValueError):self.attr(e,now)
        e.detail_fills=original;e.state['cash']='999'
        with self.assertRaises(ValueError):self.attr(e,now)
    def test_signal_timestamp_cannot_reach_fill_or_future(self):
        e,now=self.engine();e.signal_at=now[0];e.buy('KRW-BTC',100,101)
        with self.assertRaisesRegex(ValueError,'ATTRIBUTION_LOOKAHEAD'):self.attr(e,now)
    def test_raw_reason_and_order_identifiers_are_not_exported(self):
        e,now=self.engine();e.buy('KRW-BTC',100,101,reason='private-secret-canary')
        text=json.dumps(self.attr(e,now));self.assertNotIn('private-secret-canary',text)
        self.assertNotIn('identifier',text);self.assertNotIn('orders',text)
    def test_valid_two_market_partial_result_and_quarantine_missing_other_two(self):
        data=self.acquire(('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL'),{'KRW-ETH':[100],'KRW-SOL':[100,101]})
        before={p.name:p.read_bytes() for p in data.iterdir()}
        with self.assertRaises(ValueError):load_sample(data,allow_synthetic=True)
        r=run([data],self.root/'report',allow_synthetic=True)
        self.assertEqual(r['status'],'SYNTHETIC_TEST_ONLY_PARTIAL_MARKET_STUDY')
        self.assertFalse(r['all_four_markets_complete']);self.assertEqual(len(r['comparisons']),108)
        self.assertEqual({x['market'] for x in r['comparisons']},{'KRW-BTC','KRW-XRP'})
        self.assertEqual([d['missing'] for d in r['datasets'] if d['status'].startswith('QUARANTINED')],[1,2])
        self.assertEqual(before,{p.name:p.read_bytes() for p in data.iterdir()})
        self.assertIn('PARTIAL_MARKET_STUDY',(self.root/'report'/'REPORT.md').read_text())
        self.assertTrue(all((self.root/'report'/f).exists() for f in ('results.json','summary.csv','closed_sales.csv','fills.csv','cost_sensitivity.csv')))
        for x in r['comparisons']:
            self.assertEqual(x['metrics']['net_realized_pnl'] or '0',x['attribution']['net_realized_pnl'])
        self.assertTrue(any(x['attribution']['closed_sales'] for x in r['comparisons']))
    def test_all_four_market_status_is_different_from_partial(self):
        data=self.acquire(('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL'))
        r=run([data],self.root/'report',allow_synthetic=True)
        self.assertEqual(r['status'],'SYNTHETIC_TEST_ONLY_COMPLETE_FOUR_MARKET_STUDY')
        self.assertTrue(r['all_four_markets_complete']);self.assertEqual(len(r['comparisons']),216)
        self.assertTrue(all(r['attribution']['verified'] for r in r['comparisons']))
    def test_all_quarantined_is_insufficient_with_no_fabricated_metrics(self):
        data=self.acquire(gaps={'KRW-BTC':[100]});r=run([data],self.root/'report',allow_synthetic=True)
        self.assertEqual(r['status'],'SYNTHETIC_TEST_ONLY_INSUFFICIENT_REAL_DATA')
        self.assertEqual(r['comparisons'],[]);self.assertEqual(r['cost_sensitivity'],[])
        self.assertFalse(r['all_four_markets_complete'])
    def test_missing_or_corrupt_market_is_not_silently_skipped(self):
        data=self.acquire(('KRW-BTC','KRW-ETH'),{'KRW-ETH':[100]})
        for name in ('KRW-BTC-1.csv','KRW-ETH-1.csv'):
            path=data/name;raw=path.read_bytes();path.write_bytes(raw+b'\n')
            with self.assertRaisesRegex(ValueError,'FILE_SHA_MISMATCH'):run([data],self.root/'report',allow_synthetic=True)
            path.write_bytes(raw)
        self.assertFalse((self.root/'report').exists())
    def test_metadata_quality_lie_is_not_gap_quarantine(self):
        data=self.acquire(gaps={'KRW-BTC':[100]});p=data/'manifest.json';m=json.loads(p.read_text());m['datasets']['KRW-BTC']['quality']['missing']=0;write_json(p,m)
        with self.assertRaisesRegex(ValueError,'QUALITY_OR_SHA_MISMATCH'):load_partitioned_sample(data,allow_synthetic=True,quarantine_gaps=True)
    def test_duplicate_and_unpaired_inputs_or_existing_outputs_fail_without_overwrite(self):
        a=self.acquire(name='a');b=self.acquire(('KRW-XRP',),name='b')
        with self.assertRaisesRegex(ValueError,'DUPLICATE_MARKET_INPUT'):run([a,a],self.root/'out',allow_synthetic=True)
        self.start+=pd.Timedelta(days=1);self.end+=pd.Timedelta(days=1);self.frame.index+=pd.Timedelta(days=1);self.now+=86400
        c=self.acquire(('KRW-SOL',),name='c')
        with self.assertRaisesRegex(ValueError,'UNPAIRED_PERIODS'):run([a,c],self.root/'out',allow_synthetic=True)
        (self.root/'out').mkdir();(self.root/'out'/'keep').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS'):run([a,b],self.root/'out',allow_synthetic=True)
        self.assertEqual((self.root/'out'/'keep').read_text(),'preserve')
    def test_default_cli_cannot_analyze_synthetic_or_connect_and_sanitizes_failure(self):
        data=self.acquire()
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['--data',str(data),'--output',str(self.root/'report')]),2)
        self.assertEqual(json.loads(out.getvalue())['network_calls'],0)
        self.assertFalse((self.root/'report').exists())
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main(['--connect-public'])
    def test_sealed_plan_rejected_before_csv_reads(self):
        data=self.acquire();p=data/'plan.json';spec=json.loads(p.read_text());spec['start']='2026-10-03T00:00Z';write_json(p,spec)
        m=json.loads((data/'manifest.json').read_text());from backtesting.minute_study import sha,canonical
        m.update(plan=spec,plan_sha256=sha(canonical(spec)));write_json(data/'manifest.json',m)
        with patch('backtesting.minute_study.load_csv',side_effect=AssertionError('Must not open holdout')):
            with self.assertRaises(ValueError):run([data],self.root/'report',allow_synthetic=True)
    def test_cost_grid_has_primary_and_component_removals_not_optimization(self):
        protocol,_=load_protocol();cases=[vars(c) for c in cost_cases(protocol)]
        self.assertEqual(len(cases),9);self.assertEqual(len({tuple(v.values()) for v in cases}),9)
        self.assertIn({'fee_bps':D(0),'slippage_bps':D(0),'spread_bps':D(0)},cases)
    def test_cost_sensitivity_reports_changed_paths_not_additive_effects(self):
        def row(slip,events):return {'market':'KRW-BTC','strategy':'rsi','costs':{'fee_bps':'5','slippage_bps':str(slip),'spread_bps':'2'},
            'metrics':{'net_return_pct':'-1' if slip else '1'},'attribution':{'events':events}}
        event={'side':'buy','executed_at_utc':'2026-10-25T00:00Z','reason':'STRATEGY_BUY','quantity':'1','price':'100'}
        r=sensitivity([row(5,[event]),row(0,[])])
        self.assertFalse(r[1]['same_action_times_and_reasons_as_primary'])
        self.assertEqual(r[1]['delta_vs_primary_pp'],'2');self.assertIn('NOT_ADDITIVE',r[1]['interpretation'])
    def scripted_run(self,prices,details):
        frame=demo_candles(212)
        for col in ('open','close'):frame[col]=10000.
        frame['high']=12000.;frame['low']=7000.;frame['volume']=100.
        for index,price in prices.items():frame.iloc[index,frame.columns.get_loc('open')]=price
        windows={}
        for i in range(199,211):
            f=frame.iloc[:20].copy();f['rsi']=50.;f['macd']=0.;f['macd_signal']=0.
            f['bb_lower']=9000.;f['bb_middle']=10000.;f['bb_upper']=11000.
            if i in (199,205):f.loc[f.index[-2:],'rsi']=[20.,25.]
            windows[i]=f
        return simulate(frame,windows,market='KRW-BTC',bar_seconds=60,costs=CostModel(5,5,2),
            evaluation_start=frame.index[0],details=details)
    def test_actual_strategy_risk_averaging_stop_and_default_trace_unchanged(self):
        plain=self.scripted_run({201:9400,202:8000},False)
        detailed=self.scripted_run({201:9400,202:8000},True)
        a=detailed.pop('attribution');self.assertEqual(plain,detailed)
        self.assertEqual(a['averaging']['buys'],1);self.assertEqual(a['exit_reasons']['STOP_LOSS']['sells'],1)
        self.assertTrue(a['closed_sales'][0]['had_averaging_down'])
        self.assertEqual(a['net_realized_pnl'],detailed['metrics']['net_realized_pnl'])
        self.assertGreater(D(a['exposure']['position_held_time_pct']),0)
        self.assertLess(D(a['exposure']['position_held_time_pct']),100)
    def test_take_profit_preempts_strategy_and_next_open_time_is_recorded(self):
        r=self.scripted_run({201:10600},True);a=r['attribution']
        self.assertEqual(a['exit_reasons']['TAKE_PROFIT']['sells'],1)
        self.assertGreater(D(a['closed_sales'][0]['net_realized_pnl']),0)
        self.assertLess(a['events'][0]['signal_candle_open_utc'],a['events'][0]['executed_at_utc'])
    def test_no_trade_performance_na_and_no_artificial_terminal_liquidation(self):
        data=self.acquire();r=run([data],self.root/'report',allow_synthetic=True)
        zero=[x for x in r['comparisons'] if sum(x['fills'].values())==0]
        self.assertTrue(zero)
        for x in zero:
            self.assertIsNone(x['metrics']['net_return_pct']);self.assertIsNone(x['attribution']['expectancy_per_sell'])
            self.assertEqual(x['attribution']['closed_sales'],[])
        open_buy=self.scripted_run({},True)
        self.assertEqual(open_buy['fills'],{'buys':1,'sells':0})
        self.assertGreater(D(open_buy['attribution']['open_position']['quantity']),0)
    def test_interrupted_output_is_incomplete_and_existing_halted_files_unchanged(self):
        data=self.acquire();old=self.root/'old';old.mkdir()
        for name in ('paper-state.json','paper-state.json.lock','backup.json'):(old/name).write_text('HALTED private synthetic canary')
        before={p.name:p.read_bytes() for p in old.iterdir()}
        with patch('backtesting.loss_diagnostic.simulate',side_effect=OSError('private-write-canary')):
            with self.assertRaises(OSError):run([data],self.root/'report',allow_synthetic=True)
        self.assertTrue((self.root/'report'/'INCOMPLETE').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in old.iterdir()})
    def test_cli_partial_and_full_success_exit_codes_are_distinct(self):
        for status,code in (('PARTIAL_MARKET_STUDY',3),('COMPLETE_FOUR_MARKET_STUDY',0),('INSUFFICIENT_REAL_DATA',2)):
            fake={'status':status,'all_four_markets_complete':code==0,'comparisons':[],'datasets':[]}
            with patch('backtesting.loss_diagnostic.run',return_value=fake),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(main(['--data','explicit-input','--output','explicit-output']),code)
            self.assertEqual(json.loads(out.getvalue())['status'],status)
