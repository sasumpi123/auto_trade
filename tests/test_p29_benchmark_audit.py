"""P29 synthetic-only tests: cash 100% / BH40 benchmark beside saved P19/P21 results. Sockets forbidden."""
from copy import deepcopy
from decimal import Decimal as D
import hashlib,json,os,shutil,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtesting import benchmark_audit as b
from backtesting import p19_baseline as base
from backtesting import p21_entry_filter as p21
from backtesting import terminal_exit_audit as p28
from trading.costs import CostModel
from tests import p29_fixture as fx
from tests.test_p28_terminal_exit_audit import policy

ROOT=Path(__file__).resolve().parents[1]
BEGIN,END='<!-- P29_COMMAND_BEGIN -->','<!-- P29_COMMAND_END -->'
F=D('0.0005');BUY=D('1.0006');SELL=D('0.9994')


def tree(path):return {p.relative_to(path):p.read_bytes() for p in sorted(Path(path).rglob('*')) if p.is_file()}


def doc_block():
    doc=(ROOT/'docs/P29_BENCHMARK_AUDIT.md').read_text(encoding='utf-8')
    body=doc.split(BEGIN,1)[1].split(END,1)[0].strip()
    assert body.startswith('```bash\n') and body.endswith('\n```'),body
    return body[len('```bash\n'):-len('\n```')]


def audit_row(status,marked,exit_):
    return {'status':status,'marked_return_pct':marked,'hypothetical_exit_return_pct':exit_}


def raw(buys=3,sells=2):return {'fills':{'buys':buys,'sells':sells},'time_held_pct':'25','mean_capital_exposure_pct':'10'}


class ComparisonTests(unittest.TestCase):
    def test_independent_candidate_beats_legacy_but_loses_to_cash_and_bh40(self):
        """Hand values: Legacy -2/-2.1, candidate -1/-1.05, BH40 +0.5/+0.44 (close/exit, %)."""
        bh={'status':'EVALUATED','marked_return_pct':'0.5','hypothetical_exit_return_pct':'0.44'}
        legacy=b.compare(audit_row('TRADES','-2','-2.1'),raw(),bh)
        cand=b.compare(audit_row('TRADES','-1','-1.05'),raw(1,0),bh)
        self.assertEqual((D(cand['marked_vs_cash_pp']),D(cand['marked_vs_bh40_pp'])),(D('-1'),D('-1.5')))
        self.assertEqual((D(cand['exit_vs_cash_pp']),D(cand['exit_vs_bh40_pp'])),(D('-1.05'),D('-1.49')))
        self.assertEqual(D(legacy['exit_vs_bh40_pp']),D('-2.54'))
        f=b.flags({'legacy':legacy,'entry_bandwidth_44bps':cand},bh)
        for basis in ('marked','exit'):
            self.assertEqual(f[basis],{'candidate_better_than_legacy':True,'candidate_not_above_cash':True,
                'candidate_not_above_bh40':True,'candidate_negative_return':True})
        self.assertEqual(cand['fills'],{'buys':1,'sells':0})  # Exposure/trade count difference is reported, not hidden.
        # Existing P21 compact and P28 audit carry no cash/BH40 comparison at all.
        p21_like=policy(cash='999000');self.assertFalse({'buy_hold_40','marked_vs_bh40_pp'}&set(p21_like))
        self.assertFalse({k for k in p28.exit_audit(p21_like,p21.POLICIES[0]) if 'bh40' in k or 'cash_100' in k})

    def test_negative_strategy_above_falling_bh40_is_not_profit(self):
        bh={'status':'EVALUATED','marked_return_pct':'-3','hypothetical_exit_return_pct':'-3.1'}
        cand=b.compare(audit_row('TRADES','-1','-1.2'),raw(),bh)
        self.assertGreater(D(cand['marked_vs_bh40_pp']),0);self.assertLess(D(cand['marked_vs_cash_pp']),0)
        f=b.flags({'legacy':b.compare(audit_row('TRADES','-2','-2.2'),raw(),bh),'entry_bandwidth_44bps':cand},bh)
        self.assertFalse(f['marked']['candidate_not_above_bh40']);self.assertTrue(f['marked']['candidate_negative_return'])
        self.assertTrue(f['marked']['candidate_not_above_cash'])

    def test_no_trades_and_missing_bh40_stay_na(self):
        bh={'status':'EVALUATED','marked_return_pct':'1','hypothetical_exit_return_pct':'0.9'}
        r=b.compare(audit_row('NO_TRADES',None,None),raw(0,0),bh)
        self.assertTrue(all(r[k] is None for k in b.FIELDS))
        r=b.compare(audit_row('TRADES','1','0.9'),raw(),{'status':'NA_NO_COMMON_EXECUTION_OPEN'})
        self.assertIsNone(r['marked_vs_bh40_pp']);self.assertEqual(r['marked_vs_cash_pp'],'1')
        f=b.flags({'legacy':b.compare(audit_row('NO_TRADES',None,None),raw(0,0),bh),'entry_bandwidth_44bps':r},bh)
        self.assertEqual(f,{'marked':None,'exit':None})


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class BuyHoldTests(unittest.TestCase):
    def frame(self,open_,close):
        idx=pd.date_range('2026-10-30T00:00:00Z',periods=3,freq='min')
        return pd.DataFrame({'open':[1.0,float(open_),1.0],'high':[2.0]*3,'low':[0.5]*3,'close':[1.0,1.0,float(close)],'volume':[1.0]*3},index=idx)

    def old(self,frame,stored):
        return {'execution_first_open':frame.index[1].isoformat(),'market':'KRW-BTC','metrics':{'buy_hold_40_net_pct':stored}}

    def test_up_down_flat_fractional_match_hand_formula_and_p19_buy_hold(self):
        for open_,close in (('10000','10500'),('10000','9500'),('10000','10000'),('98765432.1','98000000'),('0.987','1.234')):
            with self.subTest(open=open_,close=close),TemporaryDirectory() as temp:
                frame=self.frame(open_,close);old=self.old(frame,None)
                stored=base.buy_hold(frame,old,CostModel(5,5,2));old['metrics']['buy_hold_40_net_pct']=stored
                r=b.buy_hold_40(frame,old,'KRW-BTC',Path(temp).resolve()/'e')
                o,c=D(open_),D(close);funds=D(400000)/(1+F);q=funds/(o*BUY);marked=D(600000)+q*c
                self.assertEqual(D(r['quantity']),q);self.assertEqual(D(r['buy_fee']),funds*F);self.assertEqual(D(r['cash']),600000)
                self.assertTrue(base.close(D(r['marked_return_pct']),(marked/D(1000000)-1)*100))
                self.assertEqual(r['marked_return_pct'],stored)  # Exactly the P19 buy_hold_40_net_pct.
                exit_funds=q*c*SELL;hyp=D(600000)+exit_funds-exit_funds*F
                self.assertTrue(base.close(D(r['hypothetical_exit_equity']),hyp))
                self.assertLess(D(r['hypothetical_exit_return_pct']),D(r['marked_return_pct']))
                self.assertEqual(r['fills'],{'buys':1,'sells':0});self.assertFalse(r['hypothetical_sell_counted_as_fill'])

    def test_stored_mismatch_and_no_common_open(self):
        with TemporaryDirectory() as temp:
            frame=self.frame('10000','10500');old=self.old(frame,'1.0')
            with self.assertRaisesRegex(ValueError,'P29_BH40_MISMATCH'):b.buy_hold_40(frame,old,'KRW-BTC',Path(temp).resolve()/'a')
            na={'execution_first_open':None,'metrics':{'buy_hold_40_net_pct':None}}
            self.assertEqual(b.buy_hold_40(frame,na,'KRW-BTC',Path(temp).resolve()/'b'),{'status':'NA_NO_COMMON_EXECUTION_OPEN'})
            na['metrics']['buy_hold_40_net_pct']='0'
            with self.assertRaisesRegex(ValueError,'P29_BH40_MISMATCH'):b.buy_hold_40(frame,na,'KRW-BTC',Path(temp).resolve()/'c')
            bad=self.old(frame,'0');bad['execution_first_open']='2026-10-31T00:00:00+00:00'
            with self.assertRaisesRegex(ValueError,'P29_WINDOW_MISMATCH'):b.buy_hold_40(frame,bad,'KRW-BTC',Path(temp).resolve()/'d')

    def test_hypothetical_exit_equals_disposable_paper_sell(self):
        for close in ('10500','9500','10012.5'):
            with self.subTest(close=close),TemporaryDirectory() as temp,patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                root=Path(temp).resolve();frame=self.frame('10000',close);old=self.old(frame,None)
                old['metrics']['buy_hold_40_net_pct']=base.buy_hold(frame,old,CostModel(5,5,2))
                r=b.buy_hold_40(frame,old,'KRW-BTC',root/'audit')
                (root/'sold').mkdir(mode=0o700);clock=[frame.index[1].timestamp()]
                e=base.P17ResearchEngine(root/'sold'/'unused.json',1000000,CostModel(5,5,2),lambda:clock[0]);e.open()
                try:
                    self.assertTrue(e.buy('KRW-BTC',10000,400000));clock[0]+=60;self.assertTrue(e.sell('KRW-BTC',D(close)))
                    self.assertTrue(base.close(D(r['hypothetical_exit_equity']),e.cash))
                finally:e.close()
                self.assertEqual(r['fills'],{'buys':1,'sells':0})  # buy_hold_40 itself requires exactly one bid order.


class Fixture(unittest.TestCase):
    """One real synthetic P19 collection + real P21 work() output, copied per test."""
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();cls.base=Path(cls.temp.name).resolve()
        cls.p19,cls.p21=fx.build(cls.base/'fixture')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        shutil.copytree(self.base/'fixture',self.root/'in');self.src=self.root/'in'/'p19';self.res=self.root/'in'/'p21'/'results.json'
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
        self.load=patch.object(base.minute,'load_partitioned_sample',side_effect=fx.synthetic_loader());self.load.start()
    def tearDown(self):self.load.stop();self.guard.stop();self.t.cleanup()
    def run_audit(self,name='out',**kw):
        before=tree(self.root/'in');out,r=b.audit(self.src,self.res,self.root/name,**kw)
        self.assertEqual(before,tree(self.root/'in'))  # P19/P21 inputs are never modified.
        return out,r
    def edit_p21(self,change):
        r=json.loads(self.res.read_text());change(r);base.write_json(self.res,r)
    def relink_p19(self,change):
        """Change P19 results.json and keep P21's link hash consistent, to reach deeper checks."""
        path=self.src/'analysis/results.json';r=json.loads(path.read_text());change(r);base.write_json(path,r)
        digest=hashlib.sha256(path.read_bytes()).hexdigest();self.edit_p21(lambda x:x.update(p19_results_sha256=digest))


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class PipelineTests(Fixture):
    def test_partial_benchmark_matches_p19_bh40_and_preserves_exclusions(self):
        out,r=self.run_audit()
        self.assertEqual(r['status'],'BENCHMARKED_PARTIAL');self.assertEqual(len(r['cases']),1);self.assertEqual(len(r['excluded']),5)
        c=r['cases'][0];old=next(x for x in json.loads((self.src/'analysis/results.json').read_text())['cases']
            if x['costs']==b.P19_COST and x['market']=='KRW-XRP')
        self.assertEqual(c['buy_hold_40']['marked_return_pct'],old['metrics']['buy_hold_40_net_pct'])
        self.assertEqual(c['buy_hold_40']['entry_open_utc'],old['execution_first_open'])
        self.assertEqual(c['window']['execution_first_open'],old['execution_first_open'])
        self.assertEqual(c['cash_100']['marked_return_pct'],'0')
        for name in p21.POLICIES:
            row=c['policies'][name]
            self.assertEqual(D(row['marked_vs_bh40_pp']),D(row['marked_return_pct'])-D(c['buy_hold_40']['marked_return_pct']))
            self.assertEqual(D(row['exit_vs_bh40_pp']),D(row['exit_return_pct'])-D(c['buy_hold_40']['hypothetical_exit_return_pct']))
        self.assertFalse((out/'INCOMPLETE').exists());self.assertEqual(len((out/'benchmark.csv').read_text().splitlines()),5)
        self.assertTrue((out/'REPORT.md').is_file());self.assertEqual(len(r['input']['p19']['files_sha256']),5)
        self.assertLessEqual(len(b.summary(r,0)),8)

    def test_bh40_entry_does_not_follow_candidate_signals(self):
        _,first=self.run_audit('a')
        def shift(r):
            for x in [r['cases'][0]['candidate']]+[p['result'] for p in r['completed_policies'] if p['policy']==p21.POLICIES[1]]:
                x['open_position']['entry_first_at_utc']='2026-10-30T23:59:00+00:00'
        self.edit_p21(shift);_,second=self.run_audit('b')
        self.assertEqual(first['cases'][0]['buy_hold_40'],second['cases'][0]['buy_hold_40'])

    def test_link_hash_window_mark_and_cost_mismatches_fail_closed(self):
        cases=[('link',lambda:self.edit_p21(lambda r:r.update(p19_results_sha256='0'*64)),'P29_P19_P21_LINK_MISMATCH'),
            ('protocol',lambda:self.edit_p21(lambda r:r.update(protocol_sha256='0'*64)),'P29_P19_P21_LINK_MISMATCH'),
            ('hashes',lambda:self.edit_p21(lambda r:r['input_hashes'][base.DAYS[0]].update(plan='0'*64)),'P29_INPUT_HASH_MISMATCH'),
            ('nohashes',lambda:self.edit_p21(lambda r:r.pop('input_hashes')),'P29_INPUT_HASH_MISMATCH'),
            ('mark',lambda:self.edit_p21(lambda r:[x['open_position'].update(mark_at_utc='2026-10-30T23:59:00+00:00')
                for x in [r['cases'][0]['baseline']]+[p['result'] for p in r['completed_policies'] if p['policy']==p21.POLICIES[0]]]),'P29_MARK_MISMATCH'),
            ('bh40',lambda:self.relink_p19(lambda r:[c['metrics'].update(buy_hold_40_net_pct='9.9') for c in r['cases'] if c['costs']==b.P19_COST]),'P29_BH40_MISMATCH'),
            ('window',lambda:self.relink_p19(lambda r:[c.update(data_sha256='0'*64) for c in r['cases'] if c['costs']==b.P19_COST]),'P29_WINDOW_MISMATCH'),
            ('csv',lambda:(self.src/'data'/base.DAYS[0]/'KRW-XRP-1.csv').write_bytes(b'timestamp,open\n'),'FILE_SHA_MISMATCH'),
            ('dayincomplete',lambda:(self.src/'data'/base.DAYS[0]/'INCOMPLETE').touch(),'P29_P19_DAY_INCOMPLETE'),
            ('p21status',lambda:self.edit_p21(lambda r:r.update(status='INCOMPLETE')),'P28_INPUT_NOT_VERIFIED'),
            ('p21cost',lambda:self.edit_p21(lambda r:[x.update(costs={'fee_bps':'0','slippage_bps':'0','spread_bps':'0'})
                for x in [p['result'] for p in r['completed_policies']]+[c[k] for c in r['cases'] for k in ('baseline','candidate')]]),'P28_COST_MISMATCH'),
            ('p19corrupt',lambda:(self.src/'analysis/results.json').write_bytes(b'{'),'P29_INPUT_UNVERIFIED'),
            ('p21corrupt',lambda:self.res.write_bytes(b'{'),'P28_INVALID_JSON')]
        for name,change,reason in cases:
            with self.subTest(name):
                shutil.rmtree(self.root/'in');shutil.copytree(self.base/'fixture',self.root/'in');change()
                out,r=self.run_audit(name)
                self.assertEqual((r['status'],r['failure_reason']),('INCOMPLETE',reason))
                self.assertTrue((out/'INCOMPLETE').exists());self.assertFalse((out/'REPORT.md').exists())
                self.assertEqual(json.loads((out/'results.json').read_text())['cases'],[])

    def test_inputs_changed_bounded_growth_paths_timeout_interrupt(self):
        real=b.evaluate;csv_path=self.src/'data'/base.DAYS[0]/'KRW-XRP-1.csv'
        def touch(*a):
            value=real(*a);csv_path.write_bytes(csv_path.read_bytes()+b'\n');return value
        with patch.object(b,'evaluate',side_effect=touch):
            _,r=b.audit(self.src,self.res,self.root/'changed')
        self.assertEqual(r['failure_reason'],'P29_INPUT_CHANGED')
        shutil.rmtree(self.root/'in');shutil.copytree(self.base/'fixture',self.root/'in')
        with patch.object(p28,'MAX_INPUT_BYTES',64):
            self.assertEqual(self.run_audit('big')[1]['failure_reason'],'P28_INPUT_SIZE')
        reads=[];opener=p28._open
        def record(path):
            f=opener(path);orig=f.read
            class W:
                def __enter__(s):return s
                def __exit__(s,*a):f.close()
                def read(s,n):d=orig(n);reads.append((str(path),n,len(d)));return d
            return W()
        with patch.object(p28,'_open',side_effect=record):self.run_audit('bounded')
        for name in {p for p,_,_ in reads}:
            self.assertLessEqual(sum(n for p,n,_ in reads if p==name),2*(p28.MAX_INPUT_BYTES+1))
        self.assertTrue(all(n<=p28.CHUNK_BYTES for _,n,_ in reads))
        (self.root/'exists').mkdir()
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS_NO_OVERWRITE'):b.audit(self.src,self.res,self.root/'exists')
        (self.root/'link').symlink_to(self.src)
        self.assertEqual(b.audit(self.root/'link',self.res,self.root/'l')[1]['failure_reason'],'SYMLINK_FORBIDDEN')
        ticks=iter([0,0,0]+[61]*50)
        self.assertEqual(b.audit(self.src,self.res,self.root/'slow',clock=lambda:next(ticks))[1]['failure_reason'],'P29_TIME_LIMIT')
        with self.assertRaisesRegex(ValueError,'TIME_LIMIT'):b.audit(self.src,self.res,self.root/'long',seconds=61)
        with patch.object(b,'evaluate',side_effect=KeyboardInterrupt):
            out,r=b.audit(self.src,self.res,self.root/'stop')
        self.assertEqual(r['failure_reason'],'P29_INTERRUPTED');self.assertTrue((out/'INCOMPLETE').exists())

    def test_account_and_backup_paths_untouched_and_rejected(self):
        account=self.root/'accounts'/'halted';account.mkdir(parents=True)
        (account/'experiment.json').write_text('{"status":"HALTED"}');(account/'backup.json').write_text('{}')
        before=tree(self.root/'accounts');shutil.copytree(self.src,account/'p19')
        _,r=b.audit(account/'p19',self.res,self.root/'acct')
        self.assertEqual(r['failure_reason'],'ACCOUNT_OR_HOLDOUT_PATH_FORBIDDEN')
        shutil.rmtree(account/'p19');self.assertEqual(before,tree(self.root/'accounts'))


@unittest.skipUnless(os.name=='posix','P17 disposable accounts and bash required')
class CommandTests(unittest.TestCase):
    """The whole documented block, unpatched, in a disposable git copy of this checkout."""
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name).resolve();cls.root=root
        files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0')
        repo=root/'repo'
        for name in filter(None,files):
            src=ROOT/name.decode()
            if src.is_file() and not src.is_symlink():
                dst=repo/name.decode();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        git=['git','-c','user.name=p29-test','-c','user.email=p29@test.invalid','-c','commit.gpgsign=false']
        for cmd in (['init','-q'],['add','-A'],['commit','-q','-m','snapshot']):subprocess.run(git+cmd,cwd=repo,check=True)
        cls.repo=repo;fixture=root/'fixture'
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            p19,p21_results=fx.build(fixture)
        fx.relabel_public(p19,p21_results);cls.fixture=fixture
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_block(self,home,stale):
        research=home/'research';research.mkdir(parents=True)
        shutil.copytree(self.fixture/'p19',research/'p19');shutil.copytree(self.fixture/'p21',research/'p21-output')
        block=doc_block();self.assertIn('.venv/bin/python -m backtesting.benchmark_audit',block)
        script=block.replace('.venv/bin/python',f'"{sys.executable}"')
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','P19_ROOT','P21_RESULTS','P29_OUT')}
        env.update(HOME=str(home),PYTHON_DOTENV_DISABLED='1')
        if stale:env.update(P19_ROOT=str(home/'stale'),P21_RESULTS=str(home/'stale.json'),P29_OUT=str(home/'stale-out'))
        return subprocess.run(['bash','-c',script],cwd=self.repo,env=env,capture_output=True,text=True,timeout=120)

    def test_full_block_clean_stale_and_dirty_checkout(self):
        for stale in (False,True):
            with self.subTest(stale=stale),TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,stale);lines=done.stdout.splitlines()
                self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertLessEqual(len(lines),8)
                self.assertTrue(lines[0].startswith('P29 BENCHMARKED_PARTIAL pairs=1 excluded=5 exit=0'),lines)
                outs=list((home/'research').glob('p29-*'));self.assertEqual(len(outs),1)
                self.assertTrue((outs[0]/'results.json').is_file());self.assertFalse((home/'stale-out').exists())
                self.assertNotIn(str(home),done.stdout);self.assertNotIn('Traceback',done.stdout+done.stderr)
        readme=self.repo/'README.md';original=readme.read_bytes()
        try:
            readme.write_bytes(original+b'\nlocal change\n')
            with TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,False)
                self.assertNotEqual(done.returncode,0);self.assertIn('STOP',done.stdout)
                self.assertEqual(list((home/'research').glob('p29-*')),[])
        finally:readme.write_bytes(original)


if __name__=='__main__':unittest.main()
