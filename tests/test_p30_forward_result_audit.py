"""P30 synthetic-only tests: supplemental audit of a frozen P22 result. Sockets forbidden.

The P22 fixture is produced by the UNCHANGED frozen P22 collect/work inside the isolated registered
tree (tests/p30_fixture_gen.py via tools.p22_isolated.run); only the wall clock is moved, exactly as in
the original P22 tests. The audit itself runs in the working tree and never calls frozen().
"""
from copy import deepcopy
from decimal import Decimal as D
import hashlib,json,os,shutil,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtesting import benchmark_audit as p29
from backtesting import forward_result_audit as f
from backtesting import p19_baseline as base
from backtesting import p21_entry_filter as p21
from backtesting import p22_forward as p22
from backtesting import terminal_exit_audit as p28
from tests.test_p22_forward import FuturePandas

ROOT=Path(__file__).resolve().parents[1]
BEGIN,END='<!-- P30_COMMAND_BEGIN -->','<!-- P30_COMMAND_END -->'
FUTURE=p22.END+pd.Timedelta(hours=3)
STATE={}


def setUpModule():
    temp=TemporaryDirectory();STATE['temp']=temp;out=Path(temp.name).resolve()/'fixture'
    from tools import p22_isolated as iso
    code,replaced=iso.run([str(out)],module='tests.p30_fixture_gen')
    assert code==0 and replaced==['ops/paper_experiment.py'],(code,replaced)
    STATE['fixture']=out


def tearDownModule():STATE['temp'].cleanup()


def tree(path):return {p.relative_to(path):p.read_bytes() for p in sorted(Path(path).rglob('*')) if p.is_file()}


def doc_block():
    doc=(ROOT/'docs/P30_P22_SUPPLEMENT_AUDIT.md').read_text(encoding='utf-8')
    body=doc.split(BEGIN,1)[1].split(END,1)[0].strip()
    assert body.startswith('```bash\n') and body.endswith('\n```'),body
    return body[len('```bash\n'):-len('\n```')]


def future_clock():
    """Test-only: the exact clock move of the original P22 tests (loader wall clock + p22.utc_now)."""
    loader=p22.minute.load_partitioned_sample
    def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
    return [patch.object(p22,'utc_now',return_value=FUTURE),patch.object(p22.minute,'pd',FuturePandas()),
            patch.object(p22.minute,'load_partitioned_sample',side_effect=synthetic)]


class Case(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve();self.reset()
        self.patches=[patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))]+future_clock()
        for x in self.patches:x.start()
    def tearDown(self):
        for x in reversed(self.patches):x.stop()
        self.t.cleanup()
    def reset(self):
        shutil.rmtree(self.root/'in',ignore_errors=True);shutil.copytree(STATE['fixture'],self.root/'in')
        self.col=self.root/'in'/'collection';self.res=self.root/'in'/'analysis'/'results.json'
    def run_audit(self,name='out',**kw):
        before=tree(self.root/'in');out,r=f.audit(self.col,self.res,self.root/name,**kw)
        self.assertEqual(before,tree(self.root/'in'))
        return out,r
    def edit(self,change):
        r=json.loads(self.res.read_text());change(r);base.write_json(self.res,r)
    def policies(self,r,policy=None):
        return [p for p in r['completed_policies'] if policy is None or p['policy']==policy]


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class SupplementTests(Case):
    def test_partial_supplement_keeps_original_p22_and_adds_exit_cash_bh40(self):
        out,r=self.run_audit()
        source=json.loads(self.res.read_text())
        self.assertEqual(r['status'],'SUPPLEMENT_AUDITED_PARTIAL');self.assertEqual(r['supplement'],'POST_REGISTRATION_SUPPLEMENT_DESCRIPTIVE_ONLY')
        self.assertFalse(r['retroactive_success_criterion']);self.assertFalse(r['tool']['p22_frozen_verifier_executed'])
        self.assertEqual(r['original_p22']['status'],'VERIFIED_PARTIAL');self.assertEqual(r['excluded'],source['excluded'])
        c=r['cases'][0];ctx=self.policies(source)[0]['evaluation_context']
        self.assertEqual(c['original_p22_status'],source['cases'][0]['status'])
        bh=c['buy_hold_40'];self.assertEqual(bh['entry_open_utc'],ctx['execution_first_open'])
        self.assertEqual(bh['source'],'NEW_SUPPLEMENTAL_CALCULATION_P22_STORES_NO_BH40');self.assertNotIn('p19_buy_hold_40_net_pct',bh)
        # Independent hand formula for the BH40 supplement.
        o=D(bh['entry_reference_open']);close=D(bh['mark_price']);q=D(400000)/D('1.0005')/(o*D('1.0006'))
        self.assertTrue(base.close(D(bh['quantity']),q))
        self.assertTrue(base.close(D(bh['marked_return_pct']),((D(600000)+q*close)/D(1000000)-1)*100))
        exit_funds=q*close*D('0.9994');self.assertTrue(base.close(D(bh['hypothetical_exit_equity']),D(600000)+exit_funds*D('0.9995')))
        for name,key in zip(p21.POLICIES,('baseline','candidate')):
            row=c['policies'][name];audit=c['exit_audit'][name];orig=source['cases'][0][key]
            self.assertEqual(row['marked_return_pct'],orig['net_return_pct'])
            self.assertEqual(audit['original_unchanged']['realized_pnl'],orig['realized_pnl'])
            self.assertEqual(D(row['exit_vs_bh40_pp']),D(row['exit_return_pct'])-D(bh['hypothetical_exit_return_pct']))
            self.assertEqual(row['marked_vs_cash_pp'],row['marked_return_pct'])
            self.assertLess(D(row['exit_return_pct']),D(row['marked_return_pct']))  # Extra exit cost only, once.
        for name in ('results.json','supplement.csv','REPORT.md'):self.assertTrue((out/name).is_file())
        self.assertFalse((out/'INCOMPLETE').exists());self.assertIn('POST_REGISTRATION',(out/'REPORT.md').read_text())
        lines=f.summary(r,0);self.assertLessEqual(len(lines),8);self.assertIn('original=VERIFIED_PARTIAL',lines[0])

    def test_bh40_entry_ignores_candidate_signals_and_hand_negative_vs_cash(self):
        _,a=self.run_audit('a')
        def shift(r):
            for x in [r['cases'][0]['candidate']]+[p['result'] for p in self.policies(r,p21.POLICIES[1])]:
                x['open_position']['entry_first_at_utc']='2026-11-03T23:59:00+00:00'
        self.edit(shift);_,b=self.run_audit('b')
        self.assertEqual(a['cases'][0]['buy_hold_40'],b['cases'][0]['buy_hold_40'])
        flags=a['cases'][0]['flags']['exit'];self.assertTrue(flags['candidate_not_above_cash'])
        self.assertTrue(flags['candidate_not_above_bh40']);self.assertTrue(flags['candidate_negative_return'])

    def test_no_common_open_keeps_bh40_na(self):
        def none(r):
            for p in self.policies(r):p['evaluation_context']['execution_first_open']=None
        self.edit(none);_,r=self.run_audit()
        c=r['cases'][0];self.assertEqual(c['buy_hold_40'],{'status':'NA_NO_COMMON_EXECUTION_OPEN'})
        self.assertIsNone(c['policies'][p21.POLICIES[0]]['marked_vs_bh40_pp'])

    def test_original_contract_mismatches_fail_closed(self):
        r0=json.loads(self.res.read_text())
        both=lambda f2:lambda r:[f2(x) for x in [r['cases'][0]['baseline'],r['cases'][0]['candidate']]+[p['result'] for p in self.policies(r)]]
        cases=[('schema',lambda r:r.update(schema='P21_FIXED_44BPS_V1'),'P30_SCHEMA_MISMATCH'),
            ('status',lambda r:r.update(status='INCOMPLETE'),'P30_P22_NOT_VERIFIED'),
            ('insufficient',lambda r:r.update(status='INSUFFICIENT_REAL_DATA'),'P30_P22_NOT_VERIFIED'),
            ('failure',lambda r:r.update(failure_reason='X'),'P30_P22_NOT_VERIFIED'),
            ('protocol',lambda r:r.update(protocol_sha256='0'*64),'P30_P22_REGISTRATION_MISMATCH'),
            ('runner',lambda r:r.update(runner_sha256='0'*64),'P30_P22_REGISTRATION_MISMATCH'),
            ('freeze',lambda r:r.update(freeze_commit='0'*40),'P30_P22_REGISTRATION_MISMATCH'),
            ('collection',lambda r:r.update(collection_sha256='0'*64),'P30_COLLECTION_LINK_MISMATCH'),
            ('issues',lambda r:r['input_issues'].append({'reason':'COLLECTION_NOT_COMPLETED'}),'P30_P22_UNFINISHED'),
            ('unfinished',lambda r:r['unfinished_policies'].append({'day':p22.DAYS[0]}),'P30_P22_UNFINISHED'),
            ('dupcase',lambda r:r['cases'].append(deepcopy(r['cases'][0])),'P30_DUPLICATE_CASE'),
            ('missing',lambda r:r['excluded'].pop(),'P30_CASE_COVERAGE_MISMATCH'),
            ('nopolicy',lambda r:r['completed_policies'].pop(),'P30_POLICY_COVERAGE_MISMATCH'),
            ('conflict',lambda r:r['completed_policies'][0]['result'].update(fees='1'),'P30_PAIR_POLICY_CONFLICT'),
            ('context',lambda r:r['completed_policies'][1]['evaluation_context'].update(input_candles=1),'P30_CONTEXT_MISMATCH'),
            ('pair',lambda r:r['cases'][0].update(status='DEVELOPMENT_COMPARISON'),'P30_PAIR_MISMATCH'),
            ('cost',both(lambda x:x.update(costs={'fee_bps':'0','slippage_bps':'0','spread_bps':'0'})),'P28_COST_MISMATCH'),
            ('accounting',both(lambda x:x.update(cash='1')),'P28_CASH_EQUITY_MISMATCH'),
            ('hashes',lambda r:r['input_hashes'][p22.DAYS[1]].update({'plan.json':'0'*64}),'P30_INPUT_HASH_MISMATCH'),
            ('quality',lambda r:r['excluded'][0]['quality'].update(missing=9),'P30_EXCLUDED_MISMATCH'),
            ('mark',both(lambda x:x['open_position'].update(mark_at_utc='2026-11-03T23:00:00+00:00')),'P30_MARK_MISMATCH'),
            ('window',lambda r:[p.update(data_sha256='0'*64) for p in self.policies(r)],'P30_WINDOW_MISMATCH')]
        for n,(name,change,reason) in enumerate(cases):
            with self.subTest(name):
                self.reset();self.edit(change);out,r=self.run_audit(f'o{n}')
                self.assertEqual((r['status'],r['failure_reason']),('INCOMPLETE',reason))
                self.assertTrue((out/'INCOMPLETE').exists());self.assertFalse((out/'REPORT.md').exists())
        self.reset();self.assertEqual(json.loads(self.res.read_text()),r0)

    def test_collection_changes_markers_paths_and_limits(self):
        day=self.col/'data'/p22.DAYS[0]
        def link_data():shutil.move(str(self.col/'data'),self.root/'elsewhere');(self.col/'data').symlink_to(self.root/'elsewhere')
        cases=[('csv',lambda:(day/'KRW-XRP-1.csv').write_bytes(b'timestamp,open\n'),'FILE_SHA_MISMATCH'),
            ('collection',lambda:(self.col/'collection.json').write_bytes((self.col/'collection.json').read_bytes()+b' '),'P30_COLLECTION_LINK_MISMATCH'),
            ('protocol',lambda:(self.col/'protocol.json').write_bytes(b'{}'),'P22_INPUT_PROTOCOL_MISMATCH'),
            ('incomplete',lambda:(self.col/'INCOMPLETE').touch(),'P30_COLLECTION_INCOMPLETE'),
            ('dayincomplete',lambda:(day/'INCOMPLETE').touch(),'P30_DAY_INCOMPLETE'),
            ('account',lambda:(day/'session.json').write_text('{}'),'ACCOUNT_OR_HOLDOUT_PATH_FORBIDDEN'),
            ('datalink',link_data,'SYMLINK_FORBIDDEN')]
        for n,(name,change,reason) in enumerate(cases):
            with self.subTest(name):
                shutil.rmtree(self.root/'elsewhere',ignore_errors=True);self.reset();change()
                opened=[];opener=p28._open
                def record(path):opened.append(Path(path));return opener(path)
                with patch.object(p28,'_open',side_effect=record):_,r=f.audit(self.col,self.res,self.root/f'c{n}')
                self.assertEqual(r['failure_reason'],reason)
                for p in opened:base.safe_path(p)  # Everything read passed the full path policy first.
        self.reset();real=f.evaluate;csv_path=day/'KRW-XRP-1.csv'
        def touch(*a):
            value=real(*a);csv_path.write_bytes(csv_path.read_bytes()+b'\n');return value
        with patch.object(f,'evaluate',side_effect=touch):self.assertEqual(f.audit(self.col,self.res,self.root/'chg')[1]['failure_reason'],'P30_INPUT_CHANGED')
        self.reset()
        with patch.object(p28,'MAX_INPUT_BYTES',64):self.assertEqual(self.run_audit('big')[1]['failure_reason'],'P28_INPUT_SIZE')
        (self.root/'exists').mkdir()
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS_NO_OVERWRITE'):f.audit(self.col,self.res,self.root/'exists')
        (self.root/'rlink').symlink_to(self.res.parent)
        self.assertEqual(f.audit(self.col,self.root/'rlink'/'results.json',self.root/'rl')[1]['failure_reason'],'SYMLINK_FORBIDDEN')
        ticks=iter([0,0,0]+[61]*80)
        self.assertEqual(f.audit(self.col,self.res,self.root/'slow',clock=lambda:next(ticks))[1]['failure_reason'],'P30_TIME_LIMIT')
        with self.assertRaisesRegex(ValueError,'TIME_LIMIT'):f.audit(self.col,self.res,self.root/'long',seconds=61)
        with patch.object(f,'evaluate',side_effect=KeyboardInterrupt):out,r=f.audit(self.col,self.res,self.root/'stop')
        self.assertEqual(r['failure_reason'],'P30_INTERRUPTED');self.assertTrue((out/'INCOMPLETE').exists())

    def test_p21_only_audits_still_reject_p22_and_pins_unchanged(self):
        out,r=p28.audit(self.res,self.root/'p28');self.assertEqual(r['failure_reason'],'P28_SCHEMA_MISMATCH')
        _,r=p29.audit(self.col,self.res,self.root/'p29');self.assertEqual(r['status'],'INCOMPLETE')
        from tools import p22_isolated as iso
        self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])
        self.assertEqual(f.pins(),'20e892b5890cde5a84585897a6efb67570de7bef7e9bb542b1047c5453bb054a')
        with self.assertRaisesRegex(ValueError,'P22_FROZEN_CODE_CHANGED'):p22.frozen()  # Still fails closed; never bypassed.


class NotReadyTests(unittest.TestCase):
    def test_before_collection_window_reads_and_writes_nothing(self):
        with TemporaryDirectory() as temp,patch.object(p22,'utc_now',return_value=p22.END-pd.Timedelta(seconds=1)),\
                patch.object(base,'safe_path',side_effect=AssertionError('No path access')),\
                patch.object(p28,'_open',side_effect=AssertionError('No read')),\
                patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            out,r=f.audit('/nonexistent/p22','/nonexistent/results.json',Path(temp)/'never')
            self.assertIsNone(out);self.assertEqual(r['status'],'NOT_READY');self.assertEqual(r['files_read'],0)
            self.assertFalse((Path(temp)/'never').exists())
            self.assertEqual(f.summary(r,2)[0].split()[1],'NOT_READY')


@unittest.skipUnless(os.name=='posix','bash and P17 disposable accounts required')
class CommandTests(unittest.TestCase):
    """The whole documented block in a disposable git copy; time/synthetic only via a test shim."""
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name).resolve()
        files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0')
        repo=root/'repo'
        for name in filter(None,files):
            src=ROOT/name.decode()
            if src.is_file() and not src.is_symlink():
                dst=repo/name.decode();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        git=['git','-c','user.name=p30-test','-c','user.email=p30@test.invalid','-c','commit.gpgsign=false']
        for cmd in (['init','-q'],['add','-A'],['commit','-q','-m','snapshot']):subprocess.run(git+cmd,cwd=repo,check=True)
        cls.repo=repo;cls.shim=root/'shim.py'
        cls.shim.write_text('''import os,runpy,sys
sys.path.insert(0,os.getcwd())
from unittest.mock import patch
import pandas as pd
from backtesting import p22_forward as p22
from tests.test_p22_forward import FuturePandas
loader=p22.minute.load_partitioned_sample
def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
assert sys.argv[1]=='-m';module=sys.argv[2];sys.argv=[module]+sys.argv[3:]
with patch.object(p22,'utc_now',return_value=p22.END+pd.Timedelta(hours=3)),patch.object(p22.minute,'pd',FuturePandas()),\\
        patch.object(p22.minute,'load_partitioned_sample',side_effect=synthetic):
    runpy.run_module(module,run_name='__main__')
''')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_block(self,home,stale,interpreter):
        research=home/'research';research.mkdir(parents=True)
        shutil.copytree(STATE['fixture']/'collection',research/'p22-collection');shutil.copytree(STATE['fixture']/'analysis',research/'p22-analysis')
        block=doc_block();self.assertIn('.venv/bin/python -m backtesting.forward_result_audit',block)
        script=block.replace('.venv/bin/python',interpreter)
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','P22_ROOT','P22_RESULTS','P30_OUT')}
        env.update(HOME=str(home),PYTHON_DOTENV_DISABLED='1')
        if stale:env.update(P22_ROOT=str(home/'stale'),P22_RESULTS=str(home/'stale.json'),P30_OUT=str(home/'stale-out'))
        return subprocess.run(['bash','-c',script],cwd=self.repo,env=env,capture_output=True,text=True,timeout=180)

    def test_real_clock_cli_is_not_ready_before_window(self):
        if p22.utc_now()>=p22.END:self.skipTest('Collection window has opened; covered by the shim test')
        with TemporaryDirectory() as temp:
            home=Path(temp).resolve();done=self.run_block(home,True,f'"{sys.executable}"');lines=done.stdout.splitlines()
            self.assertEqual(done.returncode,2);self.assertLessEqual(len(lines),8);self.assertTrue(lines[0].startswith('P30 NOT_READY'))
            self.assertEqual(list((home/'research').glob('p30-*')),[]);self.assertFalse((home/'stale-out').exists())

    def test_full_block_clean_stale_and_dirty_with_test_only_clock(self):
        interpreter=f'"{sys.executable}" "{self.shim}"'
        for stale in (False,True):
            with self.subTest(stale=stale),TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,stale,interpreter);lines=done.stdout.splitlines()
                self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertLessEqual(len(lines),8)
                self.assertTrue(lines[0].startswith('P30 SUPPLEMENT_AUDITED_PARTIAL original=VERIFIED_PARTIAL pairs=1 excluded=5 exit=0'),lines)
                outs=list((home/'research').glob('p30-*'));self.assertEqual(len(outs),1);self.assertTrue((outs[0]/'results.json').is_file())
                self.assertFalse((home/'stale-out').exists());self.assertNotIn(str(home),done.stdout)
                self.assertNotIn('Traceback',done.stdout+done.stderr)
        readme=self.repo/'README.md';original=readme.read_bytes()
        try:
            readme.write_bytes(original+b'\nlocal change\n')
            with TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,False,interpreter)
                self.assertNotEqual(done.returncode,0);self.assertIn('STOP',done.stdout)
                self.assertEqual(list((home/'research').glob('p30-*')),[])
        finally:readme.write_bytes(original)


if __name__=='__main__':unittest.main()
