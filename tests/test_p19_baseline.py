"""Small synthetic P17 parity/causality/timeout checks; no real datasets."""
from pathlib import Path
from tempfile import TemporaryDirectory
from decimal import Decimal as D
import json,os,time,unittest
from unittest.mock import patch
from backtesting import p19_baseline as p
from backtesting.legacy_diagnostic import prepare
from trading.execution import ExecutionEngine
from trading.costs import CostModel,policy
from ops import paper_experiment as exp
import tests.test_legacy_diagnostic as fixtures


def stall(data,out,pipe):
    p.write_json(out/'results.json',{'status':'INCOMPLETE','cases':[],'excluded':[]})
    time.sleep(30)


class EndedProgressReader:
    def __init__(self,reader):self.reader=reader
    def poll(self,timeout):raise BrokenPipeError('SYNTHETIC_ENDED_PROGRESS_PIPE')
    def close(self):self.reader.close()


class PureTests(unittest.TestCase):
    def test_fixed_scope_and_offline_collection_has_no_side_effect(self):
        self.assertEqual(p.protocol()['max_runs'],18)
        with patch.object(p,'fresh',side_effect=AssertionError('No files')):
            self.assertEqual(p.collect('unused')['network_calls'],0)
    def test_protocol_line_endings_are_equivalent_but_content_remains_pinned(self):
        raw=p.PROTOCOL.read_bytes().replace(b'\r\n',b'\n')
        with TemporaryDirectory() as root:
            path=Path(root)/'protocol.json'
            for content in (raw,raw.replace(b'\n',b'\r\n')):
                path.write_bytes(content)
                with patch.object(p,'PROTOCOL',path):
                    self.assertEqual(p.protocol()['max_runs'],18)
            changed=raw.replace(b'"max_runs": 18',b'"max_runs": 17')
            self.assertNotEqual(changed,raw);path.write_bytes(changed)
            with patch.object(p,'PROTOCOL',path),self.assertRaisesRegex(ValueError,'PROTOCOL_HASH_MISMATCH'):
                p.protocol()
    def test_account_and_holdout_paths_rejected_before_loading(self):
        with TemporaryDirectory() as root:
            root=Path(root).resolve();(root/'experiment.json').write_text('{}')
            with self.assertRaises(ValueError):p.safe_path(root/'study')
        with self.assertRaises(ValueError):p.safe_path('/tmp/holdout/data')
    def test_invalid_time_cap_and_existing_output_are_rejected(self):
        with self.assertRaises(ValueError):p.analyze('/tmp/input','/tmp/out',seconds=601)
        with TemporaryDirectory() as root:
            with self.assertRaises(ValueError):p.fresh(root)
    def test_timeout_preserves_incomplete_and_reports_all_unfinished(self):
        with TemporaryDirectory() as root:
            root=Path(root).resolve()
            r=p.analyze(root/'missing',root/'out',seconds=1,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue((root/'out/INCOMPLETE').exists())
            self.assertEqual(len(json.loads((root/'out/results.json').read_text())['unfinished']),18)
    def test_closed_progress_pipe_cannot_complete_unfinished_analysis(self):
        from types import SimpleNamespace
        ctx=p.mp.get_context('spawn')
        def broken_pipe(*args,**kwargs):
            reader,writer=ctx.Pipe(*args,**kwargs)
            return EndedProgressReader(reader),writer
        proxy=SimpleNamespace(Pipe=broken_pipe,Process=ctx.Process)
        with TemporaryDirectory() as root:
            root=Path(root).resolve()
            with patch.object(p.mp,'get_context',return_value=proxy):
                r=p.analyze(root/'missing',root/'out',seconds=1,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE')
            self.assertTrue((root/'out/INCOMPLETE').exists())
            self.assertEqual(len(json.loads((root/'out/results.json').read_text())['unfinished']),18)
    def test_invalid_daily_input_does_not_become_success(self):
        with TemporaryDirectory() as root:
            root=Path(root).resolve();r=p.analyze(root/'missing',root/'out',seconds=15)
            self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA');self.assertEqual(len(r['primary_rows']),6)
            self.assertEqual(r['finished_cases'],0)


@unittest.skipUnless(os.name=='posix','P17 private independent accounts are POSIX only')
class ParityTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.DiagnosticTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
    def test_identical_reference_orders_use_exact_p17_receipts_including_minimum(self):
        with TemporaryDirectory() as root:
            root=Path(root).resolve();cost=CostModel(5,5,2);p1=root/'adapter';p1.mkdir(mode=0o700)
            clock=[1800000000]
            a=p.P17ResearchEngine(p1/'state.json',1000000,cost,lambda:clock[0])
            target=root/'direct';exp.create(target,cost_policy=policy(cost));token=exp.begin(target)
            b=ExecutionEngine(target/exp.STATE,1000000,real_trading=False);b._experiment_session=token
            a.open();b.open()
            try:
                for price,amount,averaging in [(10000,5000,False),(10000,D('5002.50'),False),(9000,5000,True),(9000,D('5002.50'),True)]:
                    self.assertEqual(a.buy('KRW-BTC',price,amount,averaging=averaging),b.buy('KRW-BTC',price,amount,averaging=averaging));clock[0]+=1
                self.assertTrue(a.sell('KRW-BTC',10010));self.assertTrue(b.sell('KRW-BTC',10010))
                fields=('funds','paid_fee','executed_volume','average_fill_price')
                self.assertEqual([[o[k] for k in fields] for o in a.state['orders'].values()],[[o[k] for k in fields] for o in b.state['orders'].values()])
                self.assertEqual(a.cash,b.cash);self.assertEqual(a.state['positions'],b.state['positions'])
            finally:a.close();b.close()
    def test_causal_next_open_and_cost_case_reuse_without_window_mutation(self):
        f=self.fixture.frame;w=self.fixture.windows;before=[x.copy(deep=True) for x in w.values()]
        r=p.case(f,w,'KRW-BTC',(5,5,2))
        self.assertEqual(r['trace'][0]['executed_at'],f.index[200].timestamp())
        self.assertEqual(r['fills'],{'buys':1,'sells':1});self.assertGreater(D(r['fees']),0)
        self.assertEqual(D(r['metrics']['unrealized_pnl']),0)
        for a,b in zip(before,w.values()):self.assertTrue(a.equals(b))
    def test_no_trade_is_na_and_does_not_manufacture_end_sale(self):
        w={i:self.fixture.features('HOLD') for i in self.fixture.windows}
        r=p.case(self.fixture.frame,w,'KRW-BTC',(5,5,2))
        self.assertIsNone(r['metrics']['net_return_pct']);self.assertIsNone(r['metrics']['expectancy_per_sell'])
        self.assertEqual(r['fills'],{'buys':0,'sells':0})
    def test_open_position_realized_and_unrealized_remain_separate(self):
        w={i:self.fixture.features('HOLD') for i in self.fixture.windows};w[199]=self.fixture.features('BUY')
        r=p.case(self.fixture.frame,w,'KRW-BTC',(5,5,2))
        self.assertEqual(r['fills'],{'buys':1,'sells':0});self.assertEqual(D(r['metrics']['net_realized_pnl']),0)
        self.assertLess(D(r['metrics']['unrealized_pnl']),0)

class OrchestrationTests(unittest.TestCase):
    def test_collection_is_exactly_three_bounded_daily_calls(self):
        with TemporaryDirectory() as root,patch.object(p.minute,'collect',return_value={'status':'COLLECTED_DEVELOPMENT_SAMPLE','network_calls':40}) as call:
            r=p.collect(Path(root).resolve()/'data',connect_public=True)
            self.assertEqual(call.call_count,3);self.assertEqual(r['network_calls'],120)
            self.assertEqual([c.args[2] for c in call.call_args_list],[p.day_start(d).isoformat() for d in p.DAYS])
            self.assertTrue(all(c.args[1]==list(p.MARKETS) for c in call.call_args_list))
    def test_worker_reuses_six_preparations_across_eighteen_cases(self):
        from unittest.mock import Mock
        import pandas as pd
        f=pd.DataFrame({'open':[10000.]*202,'high':[10001.]*202,'low':[9999.]*202,'close':[10000.]*202,'volume':[100.]*202},
                       index=pd.date_range('2026-10-30',periods=202,freq='min',tz='UTC'))
        meta={'datasets':{m:{'quality':{'passed':True},'dataset_sha256':'SYNTHETIC_TEST_ONLY'} for m in p.MARKETS}}
        plans=iter([{'markets':list(p.MARKETS),'start':p.day_start(d).isoformat(),'end_exclusive':(p.day_start(d)+pd.Timedelta(days=1)).isoformat()} for d in p.DAYS])
        def plan(path):return next(plans)
        result={'metrics':{'net_return_pct':None},'costs':{'fee_bps':'0','slippage_bps':'0','spread_bps':'0'},'fills':{'buys':0,'sells':0}}
        with TemporaryDirectory() as root:
            root=Path(root).resolve();out=p.fresh(root/'out')
            with patch.object(p.minute,'read_json',side_effect=plan),patch.object(p.minute,'load_partitioned_sample',return_value=(meta,{m:f for m in p.MARKETS},{})),patch.object(p,'prepare',return_value=(f,{})) as prep,patch.object(p,'case',side_effect=lambda *a:json.loads(json.dumps(result))) as run:
                import logging
                previous=logging.root.manager.disable
                pipe=Mock();p.work(root/'input',out,pipe)
                self.assertEqual(logging.root.manager.disable,previous)
                self.assertEqual(prep.call_count,6);self.assertEqual(run.call_count,18)
            report=json.loads((out/'results.json').read_text());self.assertEqual(report['status'],'COMPLETE')
            self.assertEqual(len(report['cases']),18);self.assertEqual(pipe.send.call_count,18)
            self.assertEqual(report['protocol_sha256'],p.PROTOCOL_SHA256)
