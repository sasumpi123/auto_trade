"""Future-clock synthetic transport only. No real future or operational data."""
from copy import deepcopy
import io,json,os,socket,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch

import pandas as pd
from backtesting import p22_forward as p
from backtesting.data import demo_candles


def stall(source,out,pipe):
    p.base.write_json(out/'results.json',{'status':'INCOMPLETE','cases':[],'excluded':[],
        'completed_policies':[{'day':p.DAYS[0],'market':'KRW-XRP','policy':p.POLICIES[0],'result':{}}]})
    time.sleep(30)


class FuturePandas:
    """Only the existing loader's wall-clock check is moved for synthetic tests."""
    class Timestamp:
        def __new__(cls,*args,**kwargs):return pd.Timestamp(*args,**kwargs)
        @staticmethod
        def now(*args,**kwargs):return p.END+pd.Timedelta(hours=2)
    def __getattr__(self,name):return getattr(pd,name)


class FrozenTests(unittest.TestCase):
    def setUp(self):
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('No actual network'))
        blocker.start();self.addCleanup(blocker.stop)

    def test_registration_and_source_are_pinned_before_period(self):
        spec=p.frozen()
        self.assertEqual(spec['policies'],['legacy','entry_bandwidth_44bps'])
        self.assertEqual(spec['costs_bps'],{'fee_bps':'5','slippage_bps':'5','spread_bps':'2'})
        self.assertLess(p.minute.utc(spec['declared_at_utc']),p.START)
        receipt=json.loads(p.normalized(p.REGISTRATION));self.assertLess(p.minute.utc(receipt['observed_at_utc']),p.START)
        self.assertEqual(receipt['protocol_commit'],p.FREEZE_COMMIT)
        with patch.object(p,'REGISTRATION_SHA','0'*64),self.assertRaisesRegex(ValueError,'P22_REGISTRATION_UNVERIFIED'):p.frozen()
        original=p.sha
        with patch.object(p,'sha',side_effect=lambda path:'0'*64 if path.name=='signals.py' else original(path)):
            with self.assertRaisesRegex(ValueError,'P22_FROZEN_CODE_CHANGED'):p.frozen()

    def test_missed_freeze_or_changed_dates_cost_threshold_cannot_be_blessed(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();receipt=json.loads(p.normalized(p.REGISTRATION))
            receipt['observed_at_utc']=p.START.isoformat();file=root/'receipt.json';p.base.write_json(file,receipt)
            with patch.object(p,'REGISTRATION',file),patch.object(p,'REGISTRATION_SHA',p.sha(file)):
                with self.assertRaisesRegex(ValueError,'P22_MISSED_OR_UNVERIFIED_FREEZE'):p.frozen()
            for field,value in (('days',['2026-11-04','2026-11-05','2026-11-06']),
                                ('entry_block_if_less_than_bps','43'),('costs_bps',{'fee_bps':'0','slippage_bps':'5','spread_bps':'2'})):
                spec=json.loads(p.normalized(p.PROTOCOL));spec[field]=value;file=root/'changed.json';p.base.write_json(file,spec)
                with patch.object(p,'PROTOCOL',file),patch.object(p,'PROTOCOL_SHA',p.sha(file)):
                    with self.assertRaisesRegex(ValueError,'P22_FIXED_CONDITIONS_CHANGED'):p.frozen()

    def test_early_and_default_execution_never_constructs_transport_or_files(self):
        with TemporaryDirectory() as temp:
            out=Path(temp).resolve()/'no-create'
            with patch.object(p.minute,'collect',side_effect=AssertionError('No request')),patch.object(p.base,'fresh',side_effect=AssertionError('No writes')):
                for instant in (p.START-pd.Timedelta(seconds=1),p.START,p.END-pd.Timedelta(microseconds=1)):
                    for connect in (False,True):
                        r=p.collect(out,connect_public=connect,clock=lambda:instant.timestamp())
                        self.assertEqual(r['status'],'NOT_READY');self.assertEqual(r['network_calls'],0)
                    with patch.object(p,'utc_now',return_value=instant):
                        _,r=p.analyze('/tmp/not-read',out);self.assertEqual(r['status'],'NOT_READY')
                r=p.collect(out,clock=lambda:p.END.timestamp());self.assertEqual(r['status'],'OFFLINE_PLAN_ONLY')
            self.assertFalse(out.exists())

    def test_deadline_preserves_one_finished_policy_without_startup_race(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();real=time.monotonic;start=real()
            def expires():return start+601 if (root/'out/results.json').exists() or real()-start>30 else start
            with patch.object(p,'utc_now',return_value=p.END),patch.object(p.time,'monotonic',side_effect=expires):
                out,r=p.analyze(root/'missing',root/'out',seconds=1,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(r['completed_policies']),1)
            self.assertEqual(len(r['unfinished_policies']),11);self.assertTrue((out/'INCOMPLETE').exists())
            self.assertIn('INCOMPLETE',(out/'REPORT.md').read_text())
            with patch.object(p,'utc_now',return_value=p.END):
                with self.assertRaises(ValueError):p.analyze(root/'missing',out)
                with self.assertRaises(ValueError):p.analyze(root/'missing',root/'new',seconds=601)

    def test_cli_does_not_allow_dates_threshold_network_analysis_or_schedule(self):
        for args in (['collect','--output','/tmp/new','--days','2026-11-04'],
                     ['analyze','--input','/tmp/sample','--output','/tmp/new','--connect-public'],
                     ['collect','--output','/tmp/new','--threshold-bps','45'],
                     ['collect','--output','/tmp/new','--wait']):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):p.main(args)

    def test_future_suffix_cannot_change_completed_bandwidth_or_features(self):
        a=demo_candles(205);a.index=pd.date_range(p.START,periods=205,freq='min')
        b=a.copy();b.iloc[202:]*=2
        _,x=p.base.prepare(a,60);_,y=p.base.prepare(b,60)
        for i in (199,200,201):
            pd.testing.assert_frame_equal(x[i],y[i])
            self.assertEqual(p.p21.bandwidth_bps(x[i].iloc[-1]),p.p21.bandwidth_bps(y[i].iloc[-1]))

    def test_unsafe_paths_and_existing_fake_halted_backup_unchanged(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'experiment.json').write_text('{}')
            records={root/'paper-state.json':b'FAKE_HALTED',root/'backup.json':b'FAKE_BACKUP'}
            for file,raw in records.items():file.write_bytes(raw)
            with patch.object(p,'utc_now',return_value=p.END),self.assertRaises(ValueError):p.analyze(root,root/'new')
            self.assertFalse((root/'new').exists());self.assertEqual(records,{f:f.read_bytes() for f in records})
        with patch.object(p,'utc_now',return_value=p.END),self.assertRaises(ValueError):p.analyze('/tmp/holdout/data','/tmp/new')


class LimitTests(unittest.TestCase):
    def test_three_daily_calls_capped_and_no_early_market_or_date_replacement(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();calls=[]
            def fake(path,markets,start,end,**kwargs):
                calls.append((markets,start,end,kwargs));path.mkdir(parents=True)
                p.base.write_json(path/'manifest.json',{'complete':True})
                return {'status':'COLLECTED_DEVELOPMENT_SAMPLE','network_calls':40}
            with patch.object(p.minute,'collect',side_effect=fake):r=p.collect(root/'sample',connect_public=True,clock=lambda:p.END.timestamp())
            self.assertEqual(r['network_calls'],120);self.assertEqual(len(calls),3)
            self.assertEqual([c[1] for c in calls],[p.day_start(d).isoformat() for d in p.DAYS])
            self.assertTrue(all(c[0]==list(p.MARKETS) for c in calls));self.assertEqual(r['status'],'COLLECTED')
            with self.assertRaises(ValueError):p.collect(root/'sample',connect_public=True,clock=lambda:p.END.timestamp())

    def test_over_budget_and_incomplete_collection_are_not_success(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            with patch.object(p.minute,'collect',return_value={'network_calls':41}) as collect:
                r=p.collect(root/'bad',connect_public=True,clock=lambda:p.END.timestamp())
            self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(r['failure_reason'],'P22_COLLECTION_LIMIT')
            self.assertEqual(collect.call_count,1);self.assertTrue((root/'bad/INCOMPLETE').exists())


@unittest.skipUnless(os.name=='posix','Actual P17 private ownership requires POSIX')
class PipelineTests(unittest.TestCase):
    def test_synthetic_future_collection_quarantine_actual_p17_p9_and_no_p19_registry(self):
        """Six future synthetic inputs, five gapped: one true prepared pair, not real data."""
        with TemporaryDirectory() as temp,patch.object(socket.socket,'connect',side_effect=AssertionError('No actual API')):
            root=Path(temp).resolve();requested=[];started=time.monotonic();future=p.END+pd.Timedelta(hours=1)
            def clock():return future.timestamp()+time.monotonic()-started
            def factory(day):
                frame=demo_candles(1440);frame.index=pd.date_range(p.day_start(day),periods=1440,freq='min')
                class FakeRest:
                    def get(self,endpoint,params):
                        requested.append((day,endpoint,dict(params)))
                        f=frame if (day,params['market'])==(p.DAYS[0],'KRW-XRP') else frame.drop(frame.index[100])
                        f=f.loc[f.index<pd.Timestamp(params['to'])].iloc[-params['count']:]
                        return [{'market':params['market'],'unit':1,'candle_date_time_utc':t.tz_localize(None).isoformat(),
                            'opening_price':float(r.open),'high_price':float(r.high),'low_price':float(r.low),
                            'trade_price':float(r.close),'candle_acc_trade_volume':float(r.volume)} for t,r in f.iloc[::-1].iterrows()]
                    def close(self):pass
                return FakeRest()
            with patch.object(p.base,'protocol',side_effect=AssertionError('No P19 date registry')):
                collection=p.collect(root/'sample',connect_public=True,clock=clock,client_factory=factory)
                self.assertEqual(collection['status'],'PARTIAL_REAL_DATA');self.assertLessEqual(len(requested),120)
                source=root/'sample';original={f:f.read_bytes() for f in source.rglob('*') if f.is_file()}
                loader=p.minute.load_partitioned_sample
                def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
                with patch.object(p,'utc_now',return_value=future+pd.Timedelta(hours=1)),patch.object(p.minute,'pd',FuturePandas()):
                    # Real CLI loader cannot silently accept synthetic provenance.
                    out=p.base.fresh(root/'reject-synthetic');p.work(source,out,Mock())
                    self.assertEqual(p.p20.read_results(out/'results.json')['failure_reason'],'REAL_SOURCE_UNVERIFIED')
                    with patch.object(p.minute,'load_partitioned_sample',side_effect=synthetic):
                        out=p.base.fresh(root/'analysis');p.work(source,out,Mock())
                        r=p.p20.read_results(out/'results.json')
                        self.assertEqual(r['status'],'VERIFIED_PARTIAL');self.assertEqual(len(r['excluded']),5)
                        self.assertEqual(len(r['cases']),1);self.assertEqual(len(r['completed_policies']),2)
                        for name in ('baseline','candidate'):self.assertTrue(r['cases'][0][name]['p9_reconciled'])
                        r['elapsed_seconds']=0;p.write_report(out,r)
                        self.assertTrue((out/'comparison.csv').exists());self.assertIn('P22 frozen',(out/'REPORT.md').read_text())
                        # Hash corruption is blocked before research, preserving originals after test restoration.
                        file=source/'data'/p.DAYS[0]/'KRW-XRP-1.csv';file.write_bytes(original[file]+b'\n')
                        bad=p.base.fresh(root/'bad-csv');p.work(source,bad,Mock())
                        self.assertEqual(p.p20.read_results(bad/'results.json')['failure_reason'],'FILE_SHA_MISMATCH')
                        file.write_bytes(original[file])
                self.assertEqual(original,{f:f.read_bytes() for f in source.rglob('*') if f.is_file()})

    def test_six_shared_preparations_twelve_cases_and_partial_failure_preservation(self):
        """Bounded dispatch only; preceding integration tests real CSV/P17/P9."""
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'sample';source.mkdir()
            (source/'protocol.json').write_bytes(p.normalized(p.PROTOCOL));(source/'registration.json').write_bytes(p.normalized(p.REGISTRATION))
            records=[];metas={}
            for day in p.DAYS:
                daily=source/'data'/day;daily.mkdir(parents=True)
                plan={'markets':list(p.MARKETS),'start':p.day_start(day).isoformat(),'end_exclusive':(p.day_start(day)+pd.Timedelta(days=1)).isoformat()}
                p.base.write_json(daily/'plan.json',plan);p.base.write_json(daily/'manifest.json',{'source':'SYNTHETIC_DISPATCH'})
                meta={'started_at':p.END.isoformat(),'network_calls':1,'datasets':{}}
                for market in p.MARKETS:
                    file=daily/(market+'-1.csv');file.write_bytes(b'SYNTHETIC_DISPATCH')
                    meta['datasets'][market]={'file_sha256':p.minute.sha(file.read_bytes()),'dataset_sha256':'SYNTHETIC_DISPATCH'}
                metas[day]=meta
                records.append({'day':day,'complete':True,'network_calls':1,'manifest_sha256':p.minute.sha((daily/'manifest.json').read_bytes())})
            record={'schema':'P22_COLLECTION_V1','status':'COLLECTED','protocol_sha256':p.PROTOCOL_SHA,'registration_sha256':p.REGISTRATION_SHA,
                'runner_sha256':p.runner_sha(),'started_at':p.END.isoformat(),'finished_at':p.END.isoformat(),'days':records,'network_calls':3}
            p.base.write_json(source/'collection.json',record)
            def load(path,**kwargs):return metas[path.name],{m:object() for m in p.MARKETS},{}
            value={'status':'NO_TRADES','net_return_pct':None};context={'data_sha256':'SYNTHETIC_DISPATCH'}
            with patch.object(p,'utc_now',return_value=p.END),patch.object(p.minute,'load_partitioned_sample',side_effect=load),patch.object(p.base,'prepare',return_value=(object(),object())) as prepare,patch.object(p,'run_policy',return_value=(value,context)) as run:
                out=p.base.fresh(root/'out');p.work(source,out,Mock());r=p.p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'VERIFIED');self.assertEqual(prepare.call_count,6);self.assertEqual(run.call_count,12)
                for a,b in zip(run.call_args_list[::2],run.call_args_list[1::2]):
                    self.assertIs(a.args[0],b.args[0]);self.assertIs(a.args[1],b.args[1])
                run.side_effect=[(value,context),ValueError('P22_COMPARISON_WINDOW_MISMATCH')]
                out=p.base.fresh(root/'failure');p.work(source,out,Mock());r=p.p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(r['completed_policies']),1)
                self.assertEqual(len(r['cases']),0)
                run.side_effect=None
                record['status']='INCOMPLETE';p.base.write_json(source/'collection.json',record)
                out=p.base.fresh(root/'failed-collection');p.work(source,out,Mock());r=p.p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'INCOMPLETE');self.assertEqual(len(r['completed_policies']),12)
                self.assertIn({'reason':'COLLECTION_NOT_COMPLETED'},r['input_issues'])


class IsolatedP22Suite(unittest.TestCase):
    """P25 changed the P22-pinned ops/paper_experiment.py, so frozen() fails closed in the working tree.

    No P22 test is skipped: all 11 unchanged P22 tests run inside the isolated tree that
    tools/p22_isolated.py builds from the exact registered bytes (verified by the unchanged frozen()).
    """
    def test_working_tree_fails_closed_only_on_registered_drift(self):
        from tools import p22_isolated as iso
        with self.assertRaisesRegex(ValueError,'P22_FROZEN_CODE_CHANGED'):p.frozen()
        self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])
        spec=json.loads(Path(p.PROTOCOL).read_text())
        self.assertEqual(iso.sha(iso.ROOT/iso.PINNED_COPIES['ops/paper_experiment.py']),spec['code_sha256']['ops/paper_experiment.py'])
        self.assertEqual((iso.PROTOCOL_SHA,iso.REGISTRATION_SHA,iso.RUNNER_SHA),(p.PROTOCOL_SHA,p.REGISTRATION_SHA,p.runner_sha()))

    def test_full_p22_suite_passes_in_isolated_registered_tree(self):
        import subprocess,sys
        from tools import p22_isolated as iso
        expected=sum(unittest.defaultTestLoader.loadTestsFromTestCase(c).countTestCases() for c in ORIGINAL_P22_CASES)
        self.assertEqual(expected,11)  # Every pre-P25 P22 test: 7 frozen + 2 limit + 2 pipeline.
        with TemporaryDirectory() as d:
            tree=Path(d).resolve()/'source'
            self.assertEqual(iso.build(tree),['ops/paper_experiment.py'])
            self.assertEqual(iso.verify(tree),json.loads(Path(p.PROTOCOL).read_text())['code_sha256'])
            env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
            env.update(PYTHON_DOTENV_DISABLED='1',P22_ISOLATED_TREE='1')
            out=subprocess.run([sys.executable,'-m','unittest','-v','tests.test_p22_forward'],cwd=tree,env=env,
                               capture_output=True,text=True,timeout=600)
        tail=out.stderr.strip().splitlines()
        self.assertEqual(out.returncode,0,out.stderr[-3000:])
        self.assertIn(f'Ran {expected} tests',out.stderr);self.assertEqual(tail[-1],'OK')  # No skips, failures or errors.


ORIGINAL_P22_CASES=(FrozenTests,LimitTests,PipelineTests)


def load_tests(loader,tests,pattern):
    if os.environ.get('P22_ISOLATED_TREE')=='1':
        # The complete original P22 suite, inside the registered tree.
        return unittest.TestSuite(loader.loadTestsFromTestCase(c) for c in ORIGINAL_P22_CASES)
    return loader.loadTestsFromTestCase(IsolatedP22Suite)
