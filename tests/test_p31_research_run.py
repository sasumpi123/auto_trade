"""P31 tests: single manual P22 research run orchestration. No real network, collection or user data.

Fixture: the unchanged frozen P22 collect/work inside the isolated registered tree (tests/p30_fixture_gen.py).
Orchestration uses an injected fake executor (a run() parameter, never a CLI option) that copies that
fixture and runs the real P30 audit in-process with the same test-only clock move as the P22 tests.
"""
import json,os,shutil,signal,socket,stat,subprocess,sys,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtesting import forward_result_audit as f
from backtesting import p19_baseline as base
from backtesting import p22_forward as p22
from ops import research_run as rr
from tests.test_p22_forward import FuturePandas

ROOT=Path(__file__).resolve().parents[1]
BEGIN,END='<!-- P31_COMMAND_BEGIN -->','<!-- P31_COMMAND_END -->'
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


def clock_patches():
    loader=p22.minute.load_partitioned_sample
    def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
    return [patch.object(p22,'utc_now',return_value=FUTURE),patch.object(p22.minute,'pd',FuturePandas()),
            patch.object(p22.minute,'load_partitioned_sample',side_effect=synthetic)]


def arg(argv,name):return Path(argv[argv.index(name)+1])


class FakeExecutor:
    """Stands in for the isolated child processes; records exact argv and call order."""
    def __init__(self,fixture,results=None,raise_at=None):
        self.fixture=fixture;self.calls=[];self.results=results or {};self.raise_at=raise_at
    def __call__(self,step,argv,log,timeout):
        self.calls.append((step,list(argv),timeout));Path(log).write_text(f'{step} log\n')
        if self.raise_at==step:raise KeyboardInterrupt
        mode=self.results.get(step,'ok')
        if mode=='timeout':return 'TIMEOUT',None
        if step=='collect':
            if mode!='none':shutil.copytree(self.fixture/'collection',arg(argv,'--output'))
            if mode=='incomplete':
                p=arg(argv,'--output')/'collection.json';r=json.loads(p.read_text());r['status']='INCOMPLETE';p.write_text(json.dumps(r))
            return 'EXITED',0 if mode=='ok' else 2
        if step=='analyze':
            if mode=='ok':shutil.copytree(self.fixture/'analysis',arg(argv,'--output'));return 'EXITED',0
            out=arg(argv,'--output');out.mkdir();(out/'results.json').write_text('{"status":"INCOMPLETE","cases":[]}');return 'EXITED',2
        if mode=='fail':return 'EXITED',2
        _,r=f.audit(arg(argv,'--p22-root'),arg(argv,'--results'),arg(argv,'--output'))
        return 'EXITED',0 if r['status'] in ('SUPPLEMENT_AUDITED','SUPPLEMENT_AUDITED_PARTIAL') else 2


class Base(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        self.patches=[patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))]+clock_patches()
        for x in self.patches:x.start()
        self.before=tree(STATE['fixture'])
    def tearDown(self):
        for x in reversed(self.patches):x.stop()
        self.assertEqual(self.before,tree(STATE['fixture']))  # Fixture originals never modified.
        self.t.cleanup()


@unittest.skipUnless(os.name=='posix','process groups and P17 disposable accounts require POSIX')
class OrchestrationTests(Base):
    def test_ordered_once_each_exact_argv_and_partial_completion(self):
        ex=FakeExecutor(STATE['fixture']);run=self.root/'run'
        out,r=rr.run(run,connect_public=True,executor=ex)
        self.assertEqual([c[0] for c in ex.calls],['collect','analyze','supplement'])
        exe=sys.executable;iso=str(rr.ROOT/'tools/p22_isolated.py')
        self.assertEqual(ex.calls[0][1],[exe,iso,'collect','--connect-public','--output',str(run/'collection')])
        self.assertEqual(ex.calls[1][1],[exe,iso,'analyze','--input',str(run/'collection'),'--output',str(run/'analysis')])
        self.assertEqual(ex.calls[2][1],[exe,'-m','backtesting.forward_result_audit','--p22-root',str(run/'collection'),
            '--results',str(run/'analysis'/'results.json'),'--output',str(run/'supplement')])
        self.assertEqual([c[2] for c in ex.calls],[420,660,90])
        self.assertEqual(r['status'],'COMPLETED_PARTIAL_DATA');self.assertEqual(rr.code_of(r),0)  # Never named full success.
        saved=json.loads((run/'run.json').read_text());self.assertEqual(saved['status'],'COMPLETED_PARTIAL_DATA')
        self.assertEqual(saved['kind'],'RESEARCH_RUN_RECORD_NOT_PAPER_LEDGER_OR_APPROVAL')
        steps={s['step']:s for s in saved['steps']}
        self.assertEqual((steps['collect']['result'],steps['collect']['requests']),('PARTIAL_REAL_DATA',48))
        self.assertEqual((steps['analyze']['result'],steps['analyze']['pairs'],steps['analyze']['excluded']),('VERIFIED_PARTIAL',1,5))
        self.assertEqual(steps['supplement']['result'],'SUPPLEMENT_AUDITED_PARTIAL')
        self.assertEqual(set(saved['sources_sha256']),set(rr.SOURCES));self.assertEqual(len(steps['analyze']['sha256']),64)
        self.assertFalse((run/'INCOMPLETE').exists())
        self.assertEqual(stat.S_IMODE(run.stat().st_mode),0o700);self.assertEqual(stat.S_IMODE((run/'run.json').stat().st_mode)&0o077,0)
        lines=rr.summary(r,out);self.assertLessEqual(len(lines),8)
        self.assertTrue(lines[0].startswith('P31 COMPLETED_PARTIAL_DATA exit=0'));self.assertIn('close% L/44/BH=',lines[1])
        self.assertTrue(all(str(self.root) not in l for l in lines))

    def test_collect_failures_analysis_failure_supplement_failure_never_exit_zero(self):
        cases=[({'collect':'none'},['collect'],'P31_NO_COLLECTION_TO_ANALYZE'),
            ({'collect':'incomplete','analyze':'fail'},['collect','analyze'],'P31_ANALYSIS_NOT_VERIFIED'),
            ({'analyze':'fail'},['collect','analyze'],'P31_ANALYSIS_NOT_VERIFIED'),
            ({'supplement':'fail'},['collect','analyze','supplement'],None)]
        for n,(results,called,reason) in enumerate(cases):
            with self.subTest(results=results):
                ex=FakeExecutor(STATE['fixture'],results);out,r=rr.run(self.root/f'r{n}',connect_public=True,executor=ex)
                self.assertEqual([c[0] for c in ex.calls],called)  # Each at most once: no retry/resume/refill.
                self.assertEqual(r['status'],'FAILED');self.assertEqual(rr.code_of(r),2)
                if reason:self.assertIn(reason,[s.get('reason') for s in r['steps']])
                self.assertTrue((out/'INCOMPLETE').exists())
                self.assertLessEqual(len(rr.summary(r,out)),8)

    def test_timeout_interrupt_and_total_budget(self):
        ex=FakeExecutor(STATE['fixture'],{'analyze':'timeout'});_,r=rr.run(self.root/'t',connect_public=True,executor=ex)
        self.assertEqual(r['status'],'TIMEOUT');self.assertEqual([c[0] for c in ex.calls],['collect','analyze'])
        ex=FakeExecutor(STATE['fixture'],raise_at='analyze');out,r=rr.run(self.root/'i',connect_public=True,executor=ex)
        self.assertEqual(r['status'],'INTERRUPTED');self.assertEqual([c[0] for c in ex.calls],['collect','analyze'])
        saved=json.loads((out/'run.json').read_text());self.assertEqual(saved['steps'][0]['result'],'PARTIAL_REAL_DATA')
        self.assertTrue((out/'collection'/'collection.json').is_file())  # Completed work preserved.
        ticks=iter([0,0,0,rr.TOTAL_SECONDS+1]+[rr.TOTAL_SECONDS+2]*20)
        ex=FakeExecutor(STATE['fixture']);_,r=rr.run(self.root/'b',connect_public=True,executor=ex,clock=lambda:next(ticks))
        self.assertEqual(r['status'],'BUDGET_EXCEEDED');self.assertEqual(rr.code_of(r),2)
        self.assertEqual([c[0] for c in ex.calls],['collect'])

    def test_paths_accounts_and_existing_outputs_are_rejected_before_any_child(self):
        account=self.root/'acct';account.mkdir();(account/'experiment.json').write_text('{"status":"HALTED"}')
        (account/'backup.json').write_text('{}');(self.root/'exists').mkdir();(self.root/'link').symlink_to(self.root/'exists')
        before=tree(self.root/'acct')
        for target,reason in ((self.root/'exists','OUTPUT_EXISTS_NO_OVERWRITE'),(self.root/'link'/'new','SYMLINK_FORBIDDEN'),
                (account/'new','ACCOUNT_OR_HOLDOUT_PATH_FORBIDDEN'),(ROOT/'research'/'p31-never','PRIVATE_OUTPUT_OUTSIDE_CHECKOUT_REQUIRED')):
            with self.subTest(reason=reason):
                ex=FakeExecutor(STATE['fixture'])
                with self.assertRaisesRegex(ValueError,reason):rr.run(target,connect_public=True,executor=ex)
                self.assertEqual(ex.calls,[])
        self.assertEqual(before,tree(self.root/'acct'));self.assertFalse((ROOT/'research'/'p31-never').exists())


class GateTests(unittest.TestCase):
    def test_not_ready_and_plan_only_touch_nothing(self):
        guards=[patch.object(base,'safe_path',side_effect=AssertionError('No path')),patch.object(base,'fresh',side_effect=AssertionError('No output')),
            patch.object(rr,'preflight',side_effect=AssertionError('No preflight')),patch.object(rr,'spawn',side_effect=AssertionError('No child')),
            patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))]
        for g in guards:g.start()
        try:
            def boom(*a):raise AssertionError('No executor')
            with patch.object(p22,'utc_now',return_value=p22.END-pd.Timedelta(seconds=1)):
                for connect in (False,True):
                    out,r=rr.run('/nonexistent/x',connect_public=connect,executor=boom)
                    self.assertIsNone(out);self.assertEqual(r['status'],'NOT_READY');self.assertEqual(rr.code_of(r),2)
            with patch.object(p22,'utc_now',return_value=FUTURE):
                out,r=rr.run('/nonexistent/x',executor=boom);self.assertEqual(r['status'],'OFFLINE_PLAN_ONLY')
                self.assertEqual((r['network_calls'],r['files_written'],r['children_started']),(0,0,0))
                self.assertLessEqual(len(rr.summary(r)),8)
        finally:
            for g in reversed(guards):g.stop()

    def test_cli_has_no_clock_synthetic_retry_or_command_options(self):
        for args in (['p22','--directory','x','--clock','1'],['p22','--directory','x','--allow-synthetic'],
                     ['p22','--directory','x','--retry'],['other','--directory','x'],['p22']):
            with self.subTest(args=args),patch('sys.stderr'),self.assertRaises(SystemExit):rr.main(args)
        self.assertRaises(ValueError,rr.argv_for,'shell',Path('/x'))

    def test_pins_and_previous_tools_unchanged(self):
        from tools import p22_isolated as iso
        self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py']);self.assertEqual(rr.preflight(),['ops/paper_experiment.py'])
        with self.assertRaisesRegex(ValueError,'P22_FROZEN_CODE_CHANGED'):p22.frozen()


@unittest.skipUnless(os.name=='posix','process groups require POSIX')
class SpawnTests(unittest.TestCase):
    def test_exit_code_log_timeout_group_kill_and_bounded_log(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve()
            outcome,code=rr.spawn([sys.executable,'-c','import sys;print("x");sys.exit(3)'],root/'a.log',30)
            self.assertEqual((outcome,code),('EXITED',3));self.assertEqual((root/'a.log').read_text().strip(),'x')
            script=('import subprocess,sys,time;'
                f'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);open({str(root/"pid")!r},"w").write(str(p.pid));'
                'sys.stdout.flush();time.sleep(60)')
            t0=time.monotonic();outcome,code=rr.spawn([sys.executable,'-c',script],root/'b.log',2)
            self.assertEqual(outcome,'TIMEOUT');self.assertLess(time.monotonic()-t0,30);self.assertNotEqual(code,0)
            grandchild=int((root/'pid').read_text())
            def alive(pid):
                try:os.kill(pid,0)
                except ProcessLookupError:return False
                stat_file=Path(f'/proc/{pid}/stat')  # Killed but unreaped (zombie) under a non-reaping PID 1.
                return not (stat_file.exists() and stat_file.read_text().rsplit(')',1)[1].split()[0]=='Z')
            deadline=time.monotonic()+10
            while alive(grandchild) and time.monotonic()<deadline:time.sleep(0.1)
            self.assertFalse(alive(grandchild))  # Own process group fully stopped, grandchild included.
            with patch.object(rr,'MAX_LOG_BYTES',100):
                rr.spawn([sys.executable,'-c','print("y"*5000)'],root/'c.log',30)
            self.assertLessEqual((root/'c.log').stat().st_size,100+len(b'[truncated]\n'))


@unittest.skipUnless(os.name=='posix','bash required')
class CommandTests(unittest.TestCase):
    """Whole documented block in a disposable git copy. The real CLI would now connect to the public API,
    so only a test shim replaces the child executor (fixture copy + in-process P30) and the clock."""
    @classmethod
    def setUpClass(cls):
        cls.temp=TemporaryDirectory();root=Path(cls.temp.name).resolve()
        files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0')
        repo=root/'repo'
        for name in filter(None,files):
            src=ROOT/name.decode()
            if src.is_file() and not src.is_symlink():
                dst=repo/name.decode();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        git=['git','-c','user.name=p31-test','-c','user.email=p31@test.invalid','-c','commit.gpgsign=false']
        for cmd in (['init','-q'],['add','-A'],['commit','-q','-m','snapshot']):subprocess.run(git+cmd,cwd=repo,check=True)
        cls.repo=repo;cls.shim=root/'shim.py'
        cls.shim.write_text(f'''import os,runpy,socket,sys
sys.path.insert(0,os.getcwd())
from pathlib import Path
from unittest.mock import patch
from tests.test_p31_research_run import FakeExecutor,clock_patches
from ops import research_run as rr
ex=FakeExecutor(Path({str(STATE['fixture'])!r}))
assert sys.argv[1]=='-m' and sys.argv[2]=='ops.research_run';sys.argv=['ops.research_run']+sys.argv[3:]
ps=[patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),
    patch.object(rr,'spawn',side_effect=lambda argv,log,timeout:ex(Path(log).stem,argv,log,timeout))]+clock_patches()
for p in ps:p.start()
try:code=rr.main(sys.argv[1:])
finally:
    for p in reversed(ps):p.stop()
print('SHIM_CALLS',[c[0] for c in ex.calls],file=sys.stderr);raise SystemExit(code)
''')
    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def block(self):
        doc=(ROOT/'docs/P31_RESEARCH_RUN.md').read_text(encoding='utf-8')
        body=doc.split(BEGIN,1)[1].split(END,1)[0].strip()
        self.assertTrue(body.startswith('```bash\n') and body.endswith('\n```'))
        return body[len('```bash\n'):-len('\n```')]

    def run_block(self,home,stale,interpreter):
        (home/'research').mkdir(parents=True)
        script=self.block();self.assertIn('.venv/bin/python -m ops.research_run p22',script)
        script=script.replace('.venv/bin/python',interpreter)
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','RUN_ROOT')}
        env.update(HOME=str(home),PYTHON_DOTENV_DISABLED='1')
        if stale:env['RUN_ROOT']=str(home/'stale-run')
        return subprocess.run(['bash','-c',script],cwd=self.repo,env=env,capture_output=True,text=True,timeout=300)

    def test_full_block_clean_stale_and_dirty(self):
        interpreter=f'"{sys.executable}" "{self.shim}"'
        for stale in (False,True):
            with self.subTest(stale=stale),TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,stale,interpreter);lines=done.stdout.splitlines()
                self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertLessEqual(len(lines),8)
                self.assertTrue(lines[0].startswith('P31 COMPLETED_PARTIAL_DATA exit=0'),lines)
                self.assertIn("SHIM_CALLS ['collect', 'analyze', 'supplement']",done.stderr)
                runs=list((home/'research').glob('p22-run-*'));self.assertEqual(len(runs),1)
                self.assertTrue((runs[0]/'run.json').is_file());self.assertFalse((home/'stale-run').exists())
                self.assertNotIn(str(home),done.stdout);self.assertNotIn('Traceback',done.stdout+done.stderr)
        readme=self.repo/'README.md';original=readme.read_bytes()
        try:
            readme.write_bytes(original+b'\nlocal change\n')
            with TemporaryDirectory() as temp:
                home=Path(temp).resolve();done=self.run_block(home,False,interpreter)
                self.assertNotEqual(done.returncode,0);self.assertIn('STOP',done.stdout)
                self.assertEqual(list((home/'research').glob('p22-run-*')),[]);self.assertNotIn('SHIM_CALLS',done.stderr)
        finally:readme.write_bytes(original)

    def test_real_cli_without_connect_public_is_plan_only(self):
        with TemporaryDirectory() as temp:
            home=Path(temp).resolve()
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')};env['PYTHON_DOTENV_DISABLED']='1'
            done=subprocess.run([sys.executable,'-m','ops.research_run','p22','--directory',str(home/'plan')],cwd=self.repo,env=env,
                capture_output=True,text=True,timeout=60)
            lines=done.stdout.splitlines();self.assertLessEqual(len(lines),8);self.assertFalse((home/'plan').exists())
            if p22.utc_now()<p22.END:self.assertEqual((done.returncode,lines[0].split()[1]),(2,'NOT_READY'))
            else:self.assertEqual((done.returncode,lines[0].split()[1]),(0,'OFFLINE_PLAN_ONLY'))


if __name__=='__main__':unittest.main()
