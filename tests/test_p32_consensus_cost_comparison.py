"""P32 synthetic-only tests: existing ConsensusStrategy(2) through the P17/P9 path on P19 inputs. No network."""
from copy import deepcopy
from decimal import Decimal as D
import json,os,shutil,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch

from backtesting import p19_baseline as base
from backtesting import p20_attribution as p20
from backtesting import p21_entry_filter as p21
from backtesting import p32_consensus_cost_comparison as p32
from strategies import signals
from strategies.signals import ConsensusStrategy,LegacyCompositeStrategy,Signal
from tests import p29_fixture as fx

ROOT=Path(__file__).resolve().parents[1]
BEGIN,END='<!-- P32_COMMAND_BEGIN -->','<!-- P32_COMMAND_END -->'
STATE={}
ORIGINAL_LOADER=base.minute.load_partitioned_sample


def setUpModule():
    temp=TemporaryDirectory();STATE['temp']=temp;root=Path(temp.name).resolve()
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
        STATE['p19'],STATE['p21']=fx.build(root/'fixture')
    STATE['root']=root


def tearDownModule():STATE['temp'].cleanup()


def tree(path):return {p.relative_to(path):p.read_bytes() for p in sorted(Path(path).rglob('*')) if p.is_file()}


def frame_and_windows(p19=None):
    daily=Path(p19 or STATE['p19'])/'data'/base.DAYS[0]
    _,frames,_=ORIGINAL_LOADER(daily,allow_synthetic=True,quarantine_gaps=True);return frames['KRW-XRP']


class ConsensusContractTests(unittest.TestCase):
    """The existing definition, unchanged: >=2 same-direction votes and zero opposite votes."""
    def votes(self,rsi,macd,bb):
        with patch.object(signals.RSIStrategy,'decide',lambda self,s:Signal(rsi,'r' if rsi!='HOLD' else None,1.0 if rsi=='BUY' else None)),\
             patch.object(signals.MACDStrategy,'decide',lambda self,s:Signal(macd,'m' if macd!='HOLD' else None,2.0 if macd=='BUY' else None)),\
             patch.object(signals.BollingerStrategy,'decide',lambda self,s:Signal(bb,'b' if bb!='HOLD' else None,3.0 if bb=='BUY' else None)):
            return ConsensusStrategy(quorum=2).decide(object())

    def test_two_agree_opposition_single_and_hold(self):
        for votes,expected in ((('BUY','BUY','HOLD'),'BUY'),(('BUY','BUY','BUY'),'BUY'),(('BUY','BUY','SELL'),'HOLD'),
                (('BUY','HOLD','HOLD'),'HOLD'),(('HOLD','HOLD','HOLD'),'HOLD'),(('SELL','SELL','HOLD'),'SELL'),
                (('SELL','HOLD','SELL'),'SELL'),(('SELL','SELL','BUY'),'HOLD')):
            with self.subTest(votes=votes):
                s=self.votes(*votes);self.assertEqual(s.action,expected)
                self.assertIsNone(s.target_price)  # Existing contract: consensus supplies no legacy target.
                self.assertEqual([a for _,a in s.votes],list(votes))
        self.assertEqual(ConsensusStrategy(quorum=2).name,'consensus_2of3')

    def test_p32_injects_the_existing_class_with_quorum_two_and_no_filter(self):
        seen=[]
        def spy(frame,windows,market,strategy):seen.append(strategy);raise ValueError('STOP')
        with patch.object(p32,'strategy_case',side_effect=spy),self.assertRaisesRegex(ValueError,'STOP'):
            p32.consensus_case(None,None,{'market':'KRW-XRP'})
        self.assertIs(type(seen[0]),ConsensusStrategy);self.assertEqual(seen[0].quorum,2)
        self.assertEqual(p32.POLICIES,('legacy','entry_bandwidth_44bps','consensus_2of3'))


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
    def tearDown(self):self.guard.stop()

    def test_injection_path_with_legacy_equals_p19_case_exactly(self):
        frame,windows=base.prepare(frame_and_windows(),60)
        ours=p32.strategy_case(frame,windows,'KRW-XRP',LegacyCompositeStrategy())
        theirs=base.case(frame,windows,'KRW-XRP',(5,5,2),details=True)
        self.assertEqual(base.minute.canonical(ours),base.minute.canonical(theirs))

    def test_future_rows_do_not_change_earlier_consensus_decisions_or_fills(self):
        raw=frame_and_windows();cut=900
        changed=raw.copy()
        for column in ('open','high','low','close'):changed.iloc[cut:,changed.columns.get_loc(column)]*=1.2  # Valid candles, different future.
        a=p32.strategy_case(*base.prepare(raw,60),'KRW-XRP',ConsensusStrategy(quorum=2))
        b=p32.strategy_case(*base.prepare(changed,60),'KRW-XRP',ConsensusStrategy(quorum=2))
        limit=raw.index[cut-1].timestamp()
        before=lambda r:[t for t in r['trace'] if t['executed_at']<limit]
        self.assertTrue(before(a));self.assertEqual(before(a),before(b))  # Prefix-only: decisions before the change are identical.
        self.assertNotEqual(a['final_equity'],b['final_equity'])


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        shutil.copytree(STATE['p19'],self.root/'p19');self.src=self.root/'p19'
        self.patches=[patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),
            patch.object(base.minute,'load_partitioned_sample',side_effect=fx.synthetic_loader())]
        for x in self.patches:x.start()
    def tearDown(self):
        for x in reversed(self.patches):x.stop()
        self.t.cleanup()
    def run_work(self,name='out'):
        before=tree(self.src);out=base.fresh(self.root/name);p32.work(self.src,out,Mock())
        self.assertEqual(before,tree(self.src));return out,p20.read_results(out/'results.json')

    def test_three_policies_match_existing_tools_and_reconcile(self):
        out,r=self.run_work()
        self.assertEqual(r['status'],'VERIFIED_PARTIAL');self.assertEqual(len(r['excluded']),5)
        self.assertEqual([c['policy'] for c in r['completed_policies']],list(p32.POLICIES))
        results={c['policy']:c['result'] for c in r['completed_policies']}
        frame,windows=base.prepare(frame_and_windows(self.src),60)
        old=next(x for x in json.loads((self.src/'analysis/results.json').read_text())['cases'] if x['costs']==p20.COST)
        self.assertEqual(results['legacy'],json.loads(json.dumps(p21.baseline_case(frame,windows,old))))  # Every field, not only return.
        self.assertEqual(results['entry_bandwidth_44bps'],json.loads(json.dumps(p21.candidate_case(frame,windows,old))))
        c=results['consensus_2of3'];self.assertTrue(c['p9_reconciled']);self.assertEqual(c['strategy'],'consensus_2of3')
        self.assertFalse(c['entry_filter']['enabled']);self.assertEqual(c['costs'],{'fee_bps':'5','slippage_bps':'5','spread_bps':'2'})
        equity,initial=D(c['final_equity']),D(1000000)
        self.assertTrue(base.close(D(c['realized_pnl'])+D(c['unrealized_pnl']),equity-initial))
        self.assertTrue(base.close(D(c['cash'])+D(c['open_position']['quantity'])*D(c['open_position']['mark_price']),equity))
        case=r['cases'][0];row=case['policies']['consensus_2of3']
        if row['status']=='TRADES':
            self.assertEqual(D(row['marked_vs_legacy_pp']),D(row['marked_return_pct'])-D(case['policies']['legacy']['marked_return_pct']))
            self.assertEqual(D(row['exit_vs_bh40_pp']),D(row['exit_return_pct'])-D(case['buy_hold_40']['hypothetical_exit_return_pct']))
        self.assertEqual(case['buy_hold_40']['marked_return_pct'],old['metrics']['buy_hold_40_net_pct'])
        self.assertIsNone(case['policies']['legacy']['marked_vs_legacy_pp'])
        r['unfinished']=[];r['elapsed_seconds']=0;p32.write_report(out,r)
        for name in ('REPORT.md','comparison.csv'):self.assertTrue((out/name).is_file())
        self.assertEqual(len((out/'comparison.csv').read_text().splitlines()),4)
        lines,code=p32.summary(r);self.assertLessEqual(len(lines),8);self.assertEqual(code,0)

    def test_hash_mismatch_unfinalized_and_existing_output_fail_closed(self):
        csv_path=self.src/'data'/base.DAYS[0]/'KRW-XRP-1.csv';orig=csv_path.read_bytes()
        csv_path.write_bytes(orig+b'\n');_,r=self.run_work('bad');self.assertEqual((r['status'],r['failure_reason']),('INCOMPLETE','FILE_SHA_MISMATCH'))
        csv_path.write_bytes(orig)
        path=self.src/'analysis/results.json';data=json.loads(path.read_text());data['status']='RUNNING';base.write_json(path,data)
        _,r=self.run_work('unfinal');self.assertEqual(r['failure_reason'],'P19_NOT_FINALIZED')
        self.assertEqual(p32.summary(r)[1],2)
        (self.root/'exists').mkdir()
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS_NO_OVERWRITE'):p32.analyze(self.src,self.root/'exists')


class LimitTests(unittest.TestCase):
    def test_six_inputs_eighteen_policies_and_partial_and_failure(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'p19';(source/'analysis').mkdir(parents=True)
            meta={'datasets':{m:{'file_sha256':base.minute.sha(b'SYNTHETIC_ONLY'),'dataset_sha256':'SYNTHETIC_ONLY'} for m in base.MARKETS}}
            primary={}
            for day in base.DAYS:
                daily=source/'data'/day;daily.mkdir(parents=True)
                base.write_json(daily/'plan.json',{'markets':list(base.MARKETS),'start':base.day_start(day).isoformat(),
                    'end_exclusive':(base.day_start(day)+base.minute.pd.Timedelta(days=1)).isoformat()})
                base.write_json(daily/'manifest.json',{'source':'SYNTHETIC_ONLY'})
                for m in base.MARKETS:(daily/(m+'-1.csv')).write_bytes(b'SYNTHETIC_ONLY');primary[(day,m)]={'input_quality':meta['datasets'][m]}
            (source/'analysis/results.json').write_text('{}');digest=base.minute.sha(b'{}');value={'status':'NO_TRADES'}
            case={'status':'NO_TRADES_ALL_POLICIES'}
            with patch.object(p20,'inputs',return_value=(source,{},primary,digest)),\
                    patch.object(base.minute,'load_partitioned_sample',return_value=(meta,{m:object() for m in base.MARKETS},{})),\
                    patch.object(base,'prepare',return_value=(object(),object())) as prepare,\
                    patch.object(p21,'baseline_case',return_value=value),patch.object(p21,'candidate_case',return_value=value),\
                    patch.object(p32,'consensus_case',return_value=value) as consensus,patch.object(p32.p29,'buy_hold_40',return_value={}),\
                    patch.object(p32,'compare',return_value=case),patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                out=base.fresh(root/'out');p32.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual(r['status'],'VERIFIED');self.assertEqual(len(r['completed_policies']),18)
                self.assertEqual(prepare.call_count,6);self.assertEqual(consensus.call_count,6)
                self.assertEqual(r['evidence'],'NO_TRADES_ONLY')  # All no-trades is never reported as strategy success.
                for key in [(d,'KRW-BTC') for d in base.DAYS[:2]]:primary.pop(key)
                out=base.fresh(root/'partial');p32.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual((r['status'],len(r['completed_policies']),len(r['excluded'])),('VERIFIED_PARTIAL',12,2))
                consensus.side_effect=ValueError('P32_COMPARISON_WINDOW_MISMATCH')
                out=base.fresh(root/'fail');p32.work(source,out,Mock());r=p20.read_results(out/'results.json')
                self.assertEqual((r['status'],r['failure_reason']),('INCOMPLETE','P32_COMPARISON_WINDOW_MISMATCH'))
                self.assertEqual(len(r['completed_policies']),2)  # Finished policies preserved; never promoted.
                big=dict(primary);big.update({(f'x{i}','KRW-BTC'):{} for i in range(3)})
            with patch.object(p20,'inputs',return_value=(source,{},big,digest)):
                out=base.fresh(root/'big');p32.work(source,out,Mock())
                self.assertEqual(p20.read_results(out/'results.json')['failure_reason'],'P32_INPUT_LIMIT')

    def test_timeout_uses_existing_600_second_worker_limit(self):
        from tests.test_p21_entry_filter import stall
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/'p19').mkdir()
            out,r=p32.analyze(root/'p19',root/'slow',seconds=1,worker=stall)
            self.assertEqual(r['status'],'INCOMPLETE');self.assertTrue((out/'INCOMPLETE').exists())
            with self.assertRaisesRegex(ValueError,'TIME_LIMIT'):p32.analyze(root/'p19',root/'long',seconds=601)

    def test_cli_has_no_strategy_threshold_period_or_cost_options(self):
        for args in (['--input','x','--output','y','--quorum','3'],['--input','x','--output','y','--threshold','40'],
                     ['--input','x','--output','y','--days','2026-11-03'],['--input','x']):
            with self.subTest(args=args),patch('sys.stderr'),self.assertRaises(SystemExit):p32.main(args)

    def test_p22_frozen_sources_unchanged(self):
        from tools import p22_isolated as iso
        self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])


@unittest.skipUnless(os.name=='posix','bash and P17 disposable accounts required')
class CommandTests(unittest.TestCase):
    """Whole documented block, unpatched real CLI, in a disposable git copy; synthetic candles only."""
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name).resolve()
        files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0')
        repo=root/'repo'
        for name in filter(None,files):
            src=ROOT/name.decode()
            if src.is_file() and not src.is_symlink():
                dst=repo/name.decode();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        git=['git','-c','user.name=p32-test','-c','user.email=p32@test.invalid','-c','commit.gpgsign=false']
        for cmd in (['init','-q'],['add','-A'],['commit','-q','-m','snapshot']):subprocess.run(git+cmd,cwd=repo,check=True)
        cls.repo=repo;fixture=root/'public';shutil.copytree(STATE['root']/'fixture',fixture)
        fx.relabel_public(fixture/'p19',fixture/'p21'/'results.json');cls.fixture=fixture
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def block(self):
        doc=(ROOT/'docs/P32_CONSENSUS_COST_COMPARISON.md').read_text(encoding='utf-8')
        body=doc.split(BEGIN,1)[1].split(END,1)[0].strip()
        self.assertTrue(body.startswith('```bash\n') and body.endswith('\n```'))
        return body[len('```bash\n'):-len('\n```')]

    def run_block(self,home,stale):
        (home/'research').mkdir(parents=True);shutil.copytree(self.fixture/'p19',home/'research'/'p19')
        script=self.block();self.assertIn('.venv/bin/python -m backtesting.p32_consensus_cost_comparison',script)
        script=script.replace('.venv/bin/python',f'"{sys.executable}"')
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','P19_ROOT','P32_OUT')}
        env.update(HOME=str(home),PYTHON_DOTENV_DISABLED='1')
        if stale:env.update(P19_ROOT=str(home/'stale'),P32_OUT=str(home/'stale-out'))
        return subprocess.run(['bash','-c',script],cwd=self.repo,env=env,capture_output=True,text=True,timeout=600)

    def test_full_block_clean_stale_and_dirty(self):
        for stale in (False,True):
            with self.subTest(stale=stale),TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,stale);lines=done.stdout.splitlines()
                self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertLessEqual(len(lines),8)
                self.assertTrue(lines[0].startswith('P32 VERIFIED_PARTIAL exit=0 pairs=1 policies=3/18 excluded=5'),lines)
                outs=list((home/'research').glob('p32-*'));self.assertEqual(len(outs),1);self.assertTrue((outs[0]/'results.json').is_file())
                self.assertFalse((home/'stale-out').exists());self.assertNotIn(str(home),done.stdout)
                self.assertNotIn('Traceback',done.stdout+done.stderr)
        readme=self.repo/'README.md';original=readme.read_bytes()
        try:
            readme.write_bytes(original+b'\nlocal change\n')
            with TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,False)
                self.assertNotEqual(done.returncode,0);self.assertIn('STOP',done.stdout)
                self.assertEqual(list((home/'research').glob('p32-*')),[])
        finally:readme.write_bytes(original)


if __name__=='__main__':unittest.main()
