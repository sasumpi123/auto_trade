"""P20 synthetic observations only; no user data, API or operational accounts."""
import copy,json,os,time,unittest
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch,Mock
import pandas as pd
from backtesting import p20_attribution as p
from backtesting import p19_baseline as base
from backtesting.trade_attribution import attribute
from trading.costs import CostModel
import tests.test_legacy_diagnostic as fixtures


def stall(source,out,pipe):
    detail={'closed_sales':[],'loss_counts':{'price_loss':0,'positive_price_pnl_cost_loss':0},
            'net_realized_pnl':'0','unrealized_pnl':'0','open_position':{'remaining_cost':'0'}}
    base.write_json(out/'results.json',{'status':'INCOMPLETE','cases':[{'day':base.DAYS[0],'market':'KRW-XRP','attribution':detail}],'excluded':[]})
    time.sleep(30)


class InputTests(unittest.TestCase):
    def test_protocol_cost_registry_duplicates_and_incomplete_are_rejected(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'analysis').mkdir()
            rows=[{'day':base.DAYS[0],'market':'KRW-XRP','costs':dict(zip(p.COST,map(str,c))),
                   'status':'EVALUATED','accounting_verified':True} for c in base.COSTS]
            excluded=[{'day':d,'market':m,'reason':'MISSING_CANDLES'} for d in base.DAYS for m in base.MARKETS
                      if (d,m)!=(base.DAYS[0],'KRW-XRP')]
            value={'protocol':base.protocol(),'protocol_sha256':base.protocol_hash(),'status':'PARTIAL_REAL_DATA','cases':rows,'excluded':excluded}
            file=root/'analysis/results.json';base.write_json(file,value)
            self.assertEqual(len(p.inputs(root)[2]),1)
            for field in ('protocol','status','duplicates','costs','excluded'):
                bad=copy.deepcopy(value)
                if field=='protocol':bad['protocol']['max_runs']=99
                elif field=='status':bad['status']='INCOMPLETE'
                elif field=='duplicates':bad['cases'].append(copy.deepcopy(rows[0]))
                elif field=='costs':bad['cases'][1]['costs']['fee_bps']='6'
                else:bad['excluded']=[]
                base.write_json(file,bad)
                with self.assertRaises(ValueError):p.inputs(root)
    def test_timeout_keeps_completed_case_and_incomplete(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();clock=time.monotonic;started=clock()
            def expire_after_saved():
                return started+601 if (root/'out/results.json').exists() or clock()-started>30 else started
            # Expire after the completed snapshot, independent of spawn/import speed.
            with patch.object(p.time,'monotonic',side_effect=expire_after_saved):
                out,r=p.analyze(root/'missing',root/'out',seconds=4,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue((out/'INCOMPLETE').exists())
            self.assertEqual(len(r['cases']),1)
            self.assertLessEqual(len(r['unfinished']),6)
            self.assertIn('INCOMPLETE',(out/'REPORT.md').read_text());self.assertTrue((out/'sales.csv').exists())
    def test_inputs_outputs_accounts_and_holdout_are_not_opened(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'experiment.json').write_text('{}')
            with self.assertRaises(ValueError):p.inputs(root)
        with self.assertRaises(ValueError):base.safe_path('/tmp/holdout/sample')
    def test_cli_has_no_network_flag_and_success_is_four_lines_plus_source(self):
        import io
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):p.main(['--input','x','--output','y','--connect-public'])
        r={'status':'VERIFIED_PARTIAL','cases':[],'excluded':[{}],'elapsed_seconds':1}
        # macOS /tmp is a symlink; success fixtures must satisfy safe_path too.
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'source';output=root/'private-new'
            with patch.object(p,'analyze',return_value=(output,r)),patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(p.main(['--input',str(source),'--output',str(output)]),0)
            self.assertEqual(len(out.getvalue().splitlines()),5)


@unittest.skipUnless(os.name=='posix','Existing P17 private-account ownership requires POSIX')
class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.DiagnosticTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
    def test_observation_on_off_replays_identical_p19_result(self):
        old=base.case(self.f.frame,self.f.windows,'KRW-BTC',(5,5,2));old.pop('trace')
        old.update(day=base.DAYS[0],input_quality={'sha':'SYNTHETIC_TEST'})
        r=p.matched_case(self.f.frame,self.f.windows,old)
        self.assertTrue(r['verified']);self.assertEqual(len(r['closed_sales']),1)
        self.assertTrue(r['p9_reconciliation']['verified'])
        bad=copy.deepcopy(old);bad['fees']='0'
        with self.assertRaisesRegex(ValueError,'P19_REPLAY_MISMATCH'):p.matched_case(self.f.frame,self.f.windows,bad)
    def test_direct_p17_economics_unchanged_and_price_cost_loss_classes(self):
        for exit_price,price_loss,cost_loss in ((10010,False,True),(9900,True,False),(None,False,False)):
            states=[]
            with TemporaryDirectory() as temp:
                root=Path(temp).resolve()
                for enabled in (False,True):
                    folder=root/str(enabled);folder.mkdir(mode=0o700);clock=[1800000000]
                    e=base.P17ResearchEngine(folder/'unused',1000000,CostModel(5,5,2),lambda:clock[0],capture_details=enabled)
                    e.open()
                    try:
                        e.signal_at=clock[0]-60;e.buy('KRW-BTC',10000,100000,reason='RSI 과매도 반등(25.0)')
                        clock[0]+=60
                        if exit_price:e.sell('KRW-BTC',exit_price,reason='RSI 과매수 하락(75.0)')
                        states.append((e.state['cash'],e.state['positions'],[{k:o.get(k) for k in ('side','funds','executed_volume','paid_fee','profit_amount')} for o in e.state['orders'].values()]))
                        if enabled:
                            a=attribute(e.state,e.detail_fills,D(1000000),as_of=pd.Timestamp(clock[0]+60,unit='s',tz='UTC'),mark=D(exit_price or 10000),exposure=[])
                            r=p.summary(a)
                            self.assertEqual(r['loss_counts']['price_loss'],int(price_loss))
                            self.assertEqual(r['loss_counts']['positive_price_pnl_cost_loss'],int(cost_loss))
                            if exit_price:
                                self.assertEqual(r['closed_sales'][0]['exit_signals'],['RSI'])
                            else:
                                self.assertEqual(D(r['net_realized_pnl']),0);self.assertGreater(D(r['open_position']['remaining_cost']),0)
                                self.assertLess(D(r['unrealized_pnl']),0)
                            bad=copy.deepcopy(e.detail_fills);bad[0]['fee']='999'
                            with self.assertRaises(ValueError):attribute(e.state,bad,D(1000000),as_of=pd.Timestamp(clock[0]+60,unit='s',tz='UTC'),mark=D(10000),exposure=[])
                    finally:e.close()
            self.assertEqual(states[0],states[1])

    def test_worker_validates_real_loader_hashes_and_preserves_excluded_inputs(self):
        """Synthetic manifest/CSV fixture, actual loader; no collection or real data claim."""
        from backtesting.data import dataset_hash
        from backtesting.collection import quality_report
        import hashlib,socket
        day=base.DAYS[0];market='KRW-XRP'
        old=base.case(self.f.frame,self.f.windows,market,(5,5,2));old.pop('trace')
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'p19';daily=source/'data'/day;daily.mkdir(parents=True)
            (source/'analysis').mkdir()
            start=base.day_start(day);end=start+pd.Timedelta(days=1);collected=end+pd.Timedelta(hours=1)
            plan=p.minute.plan(list(base.MARKETS),start,end,now=collected)
            manifest={'schema':p.minute.SCHEMA,'complete':True,'plan':plan,'plan_sha256':p.minute.sha(p.minute.canonical(plan)),
                'source':'SYNTHETIC_TEST_TRANSPORT','started_at':collected.isoformat(),'finished_at':collected.isoformat(),
                'network_calls':2,'requests':[],'datasets':{}}
            for n,m in enumerate(base.MARKETS,1):
                frame=pd.DataFrame({'open':10000.,'high':10001.,'low':9999.,'close':10000.,'volume':100.},index=pd.date_range(start,periods=1440,freq='min'))
                if m=='KRW-BTC':frame=frame.drop(frame.index[50])
                file=daily/(m+'-1.csv');frame.to_csv(file,index_label='timestamp')
                manifest['datasets'][m]={'file':file.name,'file_sha256':p.minute.sha(file.read_bytes()),'dataset_sha256':dataset_hash(frame),
                    'quality':quality_report(frame,1,start,end,strict=False)}
                manifest['requests'].append({'endpoint':p.minute.ENDPOINT,'request_number':n,'params':{'market':m,'count':200,'to':end.isoformat()},
                    'rows':200,'response_sha256':'0'*64,'requested_at':collected.isoformat(),'fetched_at':collected.isoformat()})
            base.write_json(daily/'plan.json',plan);base.write_json(daily/'manifest.json',manifest)
            old.update(day=day,input_quality=manifest['datasets'][market])
            cases=[{**copy.deepcopy(old),'costs':dict(zip(p.COST,map(str,c)))} for c in base.COSTS]
            excluded=[{'day':d,'market':m,'reason':'MISSING_CANDLES'} for d in base.DAYS for m in base.MARKETS if (d,m)!=(day,market)]
            report={'protocol':base.protocol(),'protocol_sha256':base.protocol_hash(),'status':'PARTIAL_REAL_DATA','cases':cases,'excluded':excluded}
            result=source/'analysis/results.json';base.write_json(result,report)
            original={f:f.read_bytes() for f in source.rglob('*') if f.is_file()}
            loader=p.minute.load_partitioned_sample
            def synthetic_loader(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
            with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                # Production default rejects synthetic provenance before any replay.
                out=base.fresh(root/'no-synthetic');p.work(source,out,Mock())
                self.assertEqual(p.read_results(out/'results.json')['failure_reason'],'REAL_SOURCE_UNVERIFIED')
                with patch.object(p.minute,'load_partitioned_sample',side_effect=synthetic_loader),patch.object(base,'prepare',return_value=(self.f.frame,self.f.windows)) as prepare:
                    out=base.fresh(root/'valid');p.work(source,out,Mock());r=p.read_results(out/'results.json')
                    self.assertEqual(r['status'],'VERIFIED_PARTIAL');self.assertEqual(len(r['cases']),1)
                    self.assertEqual(len(r['excluded']),5);self.assertEqual(prepare.call_count,1)
                    self.assertEqual(r['p19_results_sha256'],hashlib.sha256(original[result]).hexdigest())
                    replay=p.matched_case
                    def changed_during_replay(*args):
                        detail=replay(*args)
                        (daily/'manifest.json').write_bytes(original[daily/'manifest.json']+b'\n')
                        return detail
                    with patch.object(p,'matched_case',side_effect=changed_during_replay):
                        bad=base.fresh(root/'changed-during-replay');p.work(source,bad,Mock())
                    partial=p.read_results(bad/'results.json')
                    self.assertEqual(partial['failure_reason'],'P19_INPUT_CHANGED')
                    self.assertEqual(partial['status'],'INCOMPLETE');self.assertEqual(len(partial['cases']),1)
                    (daily/'manifest.json').write_bytes(original[daily/'manifest.json'])
                    # Changed CSV bytes are blocked, never silently replaced or analyzed.
                    file=daily/'KRW-XRP-1.csv';file.write_bytes(original[file]+b'\n')
                    bad=base.fresh(root/'bad-csv');p.work(source,bad,Mock())
                    self.assertEqual(p.read_results(bad/'results.json')['failure_reason'],'FILE_SHA_MISMATCH')
                    file.write_bytes(original[file])
                    # A changed period is blocked before accepting the recorded dataset.
                    altered=dict(plan);altered['end_exclusive']=(end-pd.Timedelta(minutes=1)).isoformat();base.write_json(daily/'plan.json',altered)
                    bad=base.fresh(root/'bad-period');p.work(source,bad,Mock())
                    self.assertEqual(p.read_results(bad/'results.json')['failure_reason'],'P19_PERIOD_MISMATCH')
                    (daily/'plan.json').write_bytes(original[daily/'plan.json'])
            self.assertEqual(original,{f:f.read_bytes() for f in source.rglob('*') if f.is_file()})
