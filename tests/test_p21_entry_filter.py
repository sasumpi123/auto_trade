"""P21 synthetic-only fixtures; actual P17 fills/P9 accounting, sockets forbidden."""
from copy import deepcopy
from decimal import Decimal as D
import io,json,os,socket,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch

from backtesting import p21_entry_filter as p
from backtesting import p19_baseline as base
from backtesting import p20_attribution as p20
from backtesting.data import demo_candles
from trading.execution import ExecutionEngine
import tests.test_legacy_diagnostic as fixtures


def stall(source,out,pipe):
    base.write_json(out/'results.json',{'status':'INCOMPLETE','cases':[],'excluded':[],
        'completed_policies':[{'day':base.DAYS[0],'market':'KRW-XRP','policy':p.POLICIES[0],'result':{}}]})
    time.sleep(30)


class ScopeTests(unittest.TestCase):
    def test_predeclared_boundary_and_invalid_values(self):
        self.assertEqual(p.THRESHOLD_BPS,D(44));self.assertEqual(p.hypothesis()['block_if_bandwidth_bps_less_than'],'44')
        for width in ('43.999999','44','44.000001'):
            row={'close':'10000','bb_lower':'10000','bb_upper':str(D(10000)+D(width))}
            self.assertEqual(p.bandwidth_bps(row),D(width))
            self.assertEqual(p.bandwidth_bps(row)<p.THRESHOLD_BPS,D(width)<44)
        for row in ({'close':'0','bb_lower':'1','bb_upper':'2'},
                    {'close':'10','bb_lower':'2','bb_upper':'1'},
                    {'close':'10','bb_lower':'1','bb_upper':'NaN'}):
            with self.assertRaises(ValueError):p.bandwidth_bps(row)

    def test_timeout_preserves_finished_policy_and_precise_unfinished(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();clock=time.monotonic;started=clock()
            def expire_after_saved():
                return started+601 if (root/'new/results.json').exists() or clock()-started>30 else started
            # Import/startup time is not evidence that a fixture case has finished.
            with patch.object(p20.time,'monotonic',side_effect=expire_after_saved):
                out,r=p.analyze(root/'absent',root/'new',seconds=4,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(r['completed_policies']),1)
            self.assertEqual(len(r['unfinished_policies']),11);self.assertTrue((out/'INCOMPLETE').exists())
            self.assertIn('INCOMPLETE',(out/'REPORT.md').read_text());self.assertTrue((out/'comparison.csv').exists())
            before=(out/'results.json').read_bytes()
            with self.assertRaises(ValueError):p.analyze(root/'absent',out)
            self.assertEqual(before,(out/'results.json').read_bytes())
        with self.assertRaises(ValueError):p.analyze('/tmp/input','/tmp/out',seconds=601)

    def test_cli_has_no_network_threshold_or_operational_options(self):
        for option in ('--connect-public','--threshold-bps','--real-trading'):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
                p.main(['--input','/tmp/p19','--output','/tmp/new',option])
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'experiment.json').write_text('{}')
            originals={root/'paper-state.json':b'SYNTHETIC_HALTED',root/'cold-backup.json':b'SYNTHETIC_BACKUP',root/'paper-state.json.lock':b''}
            for file,raw in originals.items():file.write_bytes(raw)
            with self.assertRaises(ValueError):p.analyze(root,root/'out')
            self.assertFalse((root/'out').exists())
            self.assertEqual(originals,{f:f.read_bytes() for f in originals})
        with self.assertRaises(ValueError):p.analyze('/tmp/holdout/forbidden','/tmp/new')


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX private ownership')
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DiagnosticTests();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def width(self,frame,width):
        result=frame.copy(deep=True)
        result['bb_lower']=10000.;result['bb_upper']=float(D(10000)+D(str(width)))
        result['bb_middle']=(result.bb_lower+result.bb_upper)/2
        return result

    def windows(self,width):return {i:self.width(f,width) for i,f in self.f.windows.items()}

    def run_case(self,windows,filtered=True,frame=None):
        return base.case(self.f.frame if frame is None else frame,windows,'KRW-BTC',(5,5,2),
                         details=True,bot_factory=p.EntryFilterBot if filtered else None)

    def test_threshold_before_equal_after_uses_actual_p17_orders(self):
        for width,buys,blocked in ((43.999,0,1),(44,1,0),(44.001,1,0)):
            r=self.run_case(self.windows(width))
            self.assertTrue(r['accounting_verified']);self.assertTrue(r['attribution']['verified'])
            self.assertEqual(r['fills'],{'buys':buys,'sells':buys})
            self.assertEqual(r['counts']['entry_filter_blocked'],blocked)
            self.assertEqual(r['counts']['entry_filter_checks'],1)
            self.assertGreater(D(r['fees']),0) if buys else self.assertEqual(D(r['fees']),0)

    def test_disabled_is_identical_to_entire_p19_case(self):
        windows=self.windows(1);before={i:f.copy(deep=True) for i,f in windows.items()}
        baseline=self.run_case(windows,False)
        with patch.object(p.EntryFilterBot,'entry_filter_enabled',False):candidate=self.run_case(windows)
        self.assertEqual(baseline,candidate)
        for i,f in windows.items():self.assertTrue(f.equals(before[i]))

    def test_block_does_not_undo_saved_signal_or_cooldown(self):
        windows={i:self.width(self.f.features('HOLD'),44) for i in self.f.windows}
        windows[199]=self.width(self.f.features('BUY'),1)
        windows[200]=self.width(self.f.features('BUY'),44)
        windows[210]=self.width(self.f.features('BUY'),44)
        saved=[];original=ExecutionEngine.save_signal
        def save(engine,*args):
            result=original(engine,*args);saved.append(args);return result
        with patch.object(ExecutionEngine,'save_signal',save):r=self.run_case(windows)
        self.assertEqual(r['counts']['entry_filter_blocked'],1);self.assertEqual(r['counts']['entry_filter_allowed'],1)
        self.assertEqual(r['counts']['cooldown_blocked_actionable'],1)
        self.assertEqual(saved[0],('KRW-BTC','BUY',self.f.frame.index[200].timestamp()))
        self.assertEqual(r['trace'][0]['executed_at'],self.f.frame.index[211].timestamp())
        self.assertEqual(r['fills'],{'buys':1,'sells':0})

    def test_held_buy_and_strategy_sell_are_unchanged(self):
        windows=self.windows(1);windows[199]=self.width(self.f.features('BUY'),44)
        windows[204]=self.width(self.f.features('BUY'),1);windows[210]=self.width(self.f.features('SELL'),1)
        a=self.run_case(windows,False);b=self.run_case(windows)
        self.assertEqual(a['fills'],b['fills']);self.assertEqual(a['metrics'],b['metrics'])
        self.assertEqual(b['counts']['buy_while_held'],1)
        self.assertEqual(b['counts']['entry_filter_checks'],1);self.assertEqual(b['counts']['entry_filter_blocked'],0)

    def test_take_profit_averaging_and_stop_loss_bypass_filter(self):
        for prices,reasons in (((10600,),['STRATEGY_BUY','TAKE_PROFIT']),
                              ((9450,8800),['STRATEGY_BUY','AVERAGING_DOWN','STOP_LOSS'])):
            frame=self.f.frame.copy();windows={i:self.width(self.f.features('HOLD'),1) for i in self.f.windows}
            windows[199]=self.width(self.f.features('BUY'),44)
            for i,price in enumerate(prices,201):
                for col in ('open','close'):frame.loc[frame.index[i],col]=float(price)
                frame.loc[frame.index[i],'high']=float(price+20);frame.loc[frame.index[i],'low']=float(price-20)
            a=self.run_case(windows,False,frame);b=self.run_case(windows,True,frame)
            self.assertEqual(a['trace'],b['trace']);self.assertEqual(a['metrics'],b['metrics'])
            self.assertEqual(a['fees'],b['fees']);self.assertEqual(a['attribution'],b['attribution'])
            self.assertEqual([e['reason'] for e in b['attribution']['events']],reasons)
            self.assertEqual(b['counts']['entry_filter_checks'],1);self.assertEqual(b['counts']['entry_filter_blocked'],0)

    def test_volume_and_minimum_cash_checks_are_not_relaxed(self):
        windows=self.windows(44);windows[199].iloc[-1,windows[199].columns.get_loc('volume')]=1
        r=self.run_case(windows)
        self.assertEqual(r['counts']['volume_blocked'],1);self.assertEqual(r['counts']['entry_filter_checks'],0)
        # Isolate order rejection from the auxiliary fixed-400000 B&H comparator.
        with patch('config.MIN_TRADING_AMOUNT',500001),patch('trading.auto_trade.MIN_TRADING_AMOUNT',500001),patch.object(base,'buy_hold',return_value=None):
            r=self.run_case(self.windows(44))
        self.assertEqual(r['fills']['buys'],0);self.assertEqual(r['counts']['reject_minimum_order'],1)

    def test_only_completed_row_controls_gate_not_future_execution_price(self):
        a=demo_candles(205);b=a.copy();b.iloc[202:]*=2
        _,x=base.prepare(a,60);_,y=base.prepare(b,60)
        for i in (199,200,201):
            self.assertEqual(p.bandwidth_bps(x[i].iloc[-1]),p.bandwidth_bps(y[i].iloc[-1]))
        frame=self.f.frame.copy()
        for col in ('open','close','high','low'):frame.loc[frame.index[200],col]=50000.
        r=self.run_case({199:self.width(self.f.features('BUY'),44)},frame=frame)
        self.assertEqual(r['counts']['entry_filter_allowed'],1);self.assertEqual(r['fills']['buys'],1)
        self.assertEqual(D(r['trace'][0]['price']),D('50000')*D('1.0006'))

    def test_zero_trade_is_not_counted_as_improvement_and_mismatch_is_blocked(self):
        windows=self.windows(1)
        old=self.run_case(windows,False);old.pop('trace');old.pop('attribution')
        old.update(day=base.DAYS[0],input_quality={'source':'SYNTHETIC_ONLY'})
        a=p.baseline_case(self.f.frame,windows,old);b=p.candidate_case(self.f.frame,windows,old)
        r=p.comparison(base.DAYS[0],'KRW-BTC',a,b)
        self.assertEqual(r['status'],'NO_TRADES');self.assertIsNone(r['delta_net_percentage_points'])
        self.assertIsNone(b['net_return_pct']);self.assertTrue(b['p9_reconciled'])
        # Both negative, verified returns are valid signed comparison inputs.
        tie=p.comparison(base.DAYS[0],'KRW-BTC',a,a)
        self.assertEqual(D(tie['delta_net_percentage_points']),0)
        with TemporaryDirectory() as temp:
            out=Path(temp).resolve();report={'status':'VERIFIED_PARTIAL','cases':[r],
                'completed_policies':[{'day':base.DAYS[0],'market':'KRW-BTC','policy':k} for k in p.POLICIES],
                'excluded':[{'day':d,'market':m} for d in base.DAYS for m in base.MARKETS if (d,m)!=(base.DAYS[0],'KRW-BTC')],
                'elapsed_seconds':1}
            p.write_report(out,report)
            self.assertIn('NO_TRADES',(out/'REPORT.md').read_text());self.assertIn('N/A',(out/'REPORT.md').read_text())
            self.assertIn('candidate_remaining_cost',(out/'comparison.csv').read_text())
            with patch.object(p,'analyze',return_value=(out,report)),patch('sys.stdout',new_callable=io.StringIO) as stdout:
                self.assertEqual(p.main(['--input',str(out/'p19-source'),'--output',str(out)]),0)
            self.assertEqual(len(stdout.getvalue().splitlines()),4);self.assertIn('N/A',stdout.getvalue())
        old['fees']='0'
        with self.assertRaisesRegex(ValueError,'P19_REPLAY_MISMATCH'):p.baseline_case(self.f.frame,windows,old)


class OrchestrationTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
    def test_csv_to_real_preparation_p19_match_p17_candidate_p9_and_exports(self):
        """Full synthetic file pipeline; no mocked features, strategy, engine or accounting."""
        from backtesting.collection import quality_report
        from backtesting.data import dataset_hash
        day=base.DAYS[0];start=base.day_start(day);end=start+p.minute.pd.Timedelta(days=1)
        collected=end+p.minute.pd.Timedelta(hours=1)
        with TemporaryDirectory() as temp,patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            root=Path(temp).resolve();source=root/'p19';daily=source/'data'/day;daily.mkdir(parents=True)
            (source/'analysis').mkdir()
            plan=p.minute.plan(list(base.MARKETS),start,end,now=collected)
            manifest={'schema':p.minute.SCHEMA,'complete':True,'plan':plan,
                'plan_sha256':p.minute.sha(p.minute.canonical(plan)),'source':'SYNTHETIC_TEST_TRANSPORT',
                'started_at':collected.isoformat(),'finished_at':collected.isoformat(),'network_calls':2,'requests':[],'datasets':{}}
            for n,market in enumerate(base.MARKETS,1):
                frame=demo_candles(1440);frame.index=p.minute.pd.date_range(start,periods=1440,freq='min')
                if market=='KRW-BTC':frame=frame.drop(frame.index[50])
                file=daily/(market+'-1.csv');frame.to_csv(file,index_label='timestamp')
                manifest['datasets'][market]={'file':file.name,'file_sha256':p.minute.sha(file.read_bytes()),
                    'dataset_sha256':dataset_hash(frame),'quality':quality_report(frame,1,start,end,strict=False)}
                manifest['requests'].append({'endpoint':p.minute.ENDPOINT,'request_number':n,
                    'params':{'market':market,'count':200,'to':end.isoformat()},'rows':200,'response_sha256':'0'*64,
                    'requested_at':collected.isoformat(),'fetched_at':collected.isoformat()})
            base.write_json(daily/'plan.json',plan);base.write_json(daily/'manifest.json',manifest)
            loader=p.minute.load_partitioned_sample
            meta,frames,_=loader(daily,allow_synthetic=True,quarantine_gaps=True)
            frame,windows=base.prepare(frames['KRW-XRP'],60)
            rows=[]
            for costs in base.COSTS:
                r=base.case(frame,windows,'KRW-XRP',costs);r.pop('trace')
                r.update(day=day,input_quality=meta['datasets']['KRW-XRP']);rows.append(r)
            excluded=[{'day':day,'market':'KRW-BTC','reason':'MISSING_CANDLES'}]+[
                {'day':d,'markets':list(base.MARKETS),'reason':'INVALID_OR_MISSING_DAILY_INPUT'} for d in base.DAYS[1:]]
            base.write_json(source/'analysis/results.json',{'protocol':base.protocol(),'protocol_sha256':base.protocol_hash(),
                'status':'PARTIAL_REAL_DATA','cases':rows,'excluded':excluded})
            original={f:f.read_bytes() for f in source.rglob('*') if f.is_file()}
            def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
            with patch.object(p.minute,'load_partitioned_sample',side_effect=synthetic):
                out=base.fresh(root/'comparison');p.work(source,out,Mock())
            report=p20.read_results(out/'results.json')
            self.assertEqual(report['status'],'VERIFIED_PARTIAL');self.assertEqual(len(report['completed_policies']),2)
            self.assertEqual(len(report['cases']),1);pair=report['cases'][0]
            for name in ('baseline','candidate'):self.assertTrue(pair[name]['p9_reconciled'])
            self.assertEqual(pair['baseline']['net_return_pct'],rows[1]['metrics']['net_return_pct'])
            report['unfinished']=[];p.write_report(out,report)
            self.assertTrue((out/'comparison.csv').is_file());self.assertTrue((out/'REPORT.md').is_file())
            self.assertEqual(original,{f:f.read_bytes() for f in source.rglob('*') if f.is_file()})

    def test_six_inputs_twelve_policies_shared_preparation_and_fail_closed(self):
        """Mock file loading only; separately tested P20/loader is the real entry contract."""
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'p19';(source/'analysis').mkdir(parents=True)
            meta={'datasets':{m:{'file_sha256':p.minute.sha(b'SYNTHETIC_ONLY'),'dataset_sha256':'SYNTHETIC_ONLY'} for m in base.MARKETS}}
            old={};primary={}
            for day in base.DAYS:
                daily=source/'data'/day;daily.mkdir(parents=True)
                base.write_json(daily/'plan.json',{'markets':list(base.MARKETS),'start':base.day_start(day).isoformat(),
                    'end_exclusive':(base.day_start(day)+p.minute.pd.Timedelta(days=1)).isoformat()})
                base.write_json(daily/'manifest.json',{'source':'SYNTHETIC_ONLY'})
                for m in base.MARKETS:
                    (daily/(m+'-1.csv')).write_bytes(b'SYNTHETIC_ONLY');primary[(day,m)]={'input_quality':meta['datasets'][m]}
            (source/'analysis/results.json').write_text('{}')
            digest=p.minute.sha(b'{}');compact={'status':'NO_TRADES','net_return_pct':None}
            with patch.object(p20,'inputs',return_value=(source,old,primary,digest)),patch.object(p.minute,'load_partitioned_sample',return_value=(meta,{m:object() for m in base.MARKETS},{})),patch.object(base,'prepare',return_value=(object(),object())) as prepare,patch.object(p,'baseline_case',return_value=compact) as baseline,patch.object(p,'candidate_case',return_value=compact) as candidate,patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                before={f:f.read_bytes() for f in source.rglob('*') if f.is_file()}
                out=base.fresh(root/'out');p.work(source,out,Mock());report=p20.read_results(out/'results.json')
                self.assertEqual(report['status'],'VERIFIED');self.assertEqual(len(report['completed_policies']),12)
                self.assertEqual(prepare.call_count,6);self.assertEqual(baseline.call_count,6);self.assertEqual(candidate.call_count,6)
                for a,b in zip(baseline.call_args_list,candidate.call_args_list):
                    self.assertIs(a.args[0],b.args[0]);self.assertIs(a.args[1],b.args[1])
                # Preserve P19 exclusions; do not replace the two absent BTC cases.
                absent=[(d,'KRW-BTC') for d in base.DAYS[:2]]
                for key in absent:primary.pop(key)
                out=base.fresh(root/'partial');p.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'VERIFIED_PARTIAL');self.assertEqual(len(r['completed_policies']),8)
                self.assertEqual({(e['day'],e['market']) for e in r['excluded']},set(absent))
                # Loader/baseline errors cannot become verified, and finished policies survive.
                baseline.side_effect=ValueError('P19_REPLAY_MISMATCH')
                out=base.fresh(root/'mismatch');p.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['failure_reason'],'P19_REPLAY_MISMATCH')
                baseline.side_effect=None;candidate.side_effect=ValueError('P21_TOTAL_PNL_MISMATCH')
                out=base.fresh(root/'candidate-error');p.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(r['completed_policies']),1)
                wrong=deepcopy(meta);wrong['datasets']['KRW-XRP']['quality']={'passed':False}
                prepare.reset_mock()
                with patch.object(p.minute,'load_partitioned_sample',return_value=(wrong,{m:object() for m in base.MARKETS},{})):
                    out=base.fresh(root/'wrong-input');p.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual(r['failure_reason'],'P19_INPUT_MISMATCH');self.assertEqual(prepare.call_count,0)
                self.assertEqual(before,{f:f.read_bytes() for f in source.rglob('*') if f.is_file()})
