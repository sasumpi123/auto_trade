"""P35 synthetic-only tests: protocol schema, commit-pinned registration gate, worktree isolation, P33 reproduction.

No real data, network, schedule or account. Git repositories, anchors and future clocks exist only in temp dirs.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal as D
import json,os,shutil,socket,subprocess,sys,time,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch

import pandas as pd

from backtesting import p19_baseline as base
from backtesting import p20_attribution as p20
from backtesting import p33_forward as p33
from backtesting import p35_registered_eval as p35
from tests import test_p33_forward as t33

ROOT=Path(__file__).resolve().parents[1]
GIT=['git','-c','user.name=p35-test','-c','user.email=p35@test.invalid','-c','commit.gpgsign=false','-c','init.defaultBranch=master']
REL='registrations/p33_reproduction/protocol.json'


def git(root,*args,date=None,check=True):
    env={**os.environ,**({'GIT_COMMITTER_DATE':date,'GIT_AUTHOR_DATE':date} if date else {})}
    return subprocess.run(GIT+list(args),cwd=root,check=check,env=env,capture_output=True,text=True)


def p33_as_p35(code_commit,manifest_sha,/,**changes):
    """The registered P33 protocol moved into the P35 schema (same folds=days, markets, costs, rule)."""
    old=json.loads(p33.normalized(p33.PROTOCOL))
    value={'schema':p35.SCHEMA,'id':'p33_reproduction','question':old['question'],
        'strategy':{'id':'consensus','label':'consensus_2of3','params':{'quorum':2}},
        'folds':[{'id':d,'start':d+'T00:00:00+00:00','end_exclusive':(pd.Timestamp(d)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')+'T00:00:00+00:00'} for d in old['days']],
        'markets':old['markets'],'unit_minutes':old['unit_minutes'],'costs_bps':old['costs_bps'],'initial_cash':old['initial_cash'],
        'position_fraction':old['position_fraction'],'warmup_candles':old['warmup_candles'],'baselines':p35.BASELINES,'comparison':p35.COMPARISON,
        'decision_rule':{'kind':p35.DECISION_KIND,'min_valid':10,'min_trade_cases':6,'min_both_exceed_markets':2,'both_exceed_fraction':'0.6',
            'scope':old['decision_rule']['scope']},
        'registration_deadline_exclusive':old['registration_deadline_exclusive'],'analysis_not_before_utc':old['collection_not_before_utc'],
        'max_cases':old['max_cases'],'analysis_seconds':old['analysis_seconds'],'code_commit':code_commit,'input_manifest_sha256':manifest_sha,
        'missing_candles':'quarantine the fold/market (PARTIAL); no interpolation, re-collection or extension','adopt_strategy':False}
    value.update(changes);return value


def code_files():
    out=subprocess.run(['git','-C',str(ROOT),'ls-files','-z'],capture_output=True,check=True).stdout.decode().split('\0')
    names={n for n in out if n and n.split('/')[0] not in ('research','tests','docs','automation')}
    return sorted(names|{'backtesting/p35_registered_eval.py'})


def make_repo(root,*,code=False,code_date='2026-11-07T20:00:00+00:00',anchor_date='2026-11-07T22:00:00+00:00',
        reference='synthetic test approval',anchor=True,changes=None,manifest_sha='e'*64):
    """Disposable full-history repo: a code commit, then a reviewed registration commit, remote master at the tip."""
    root=Path(root);root.mkdir(parents=True);git(root,'init','-q')
    if code:
        for name in code_files():
            target=root/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    else:(root/'code.txt').write_text('fixed evaluation code\n')
    git(root,'add','-A');git(root,'commit','-q','-m','code',date=code_date)
    commit=git(root,'rev-parse','HEAD').stdout.strip()
    proto=root/REL;proto.parent.mkdir(parents=True)
    proto.write_text(json.dumps(p33_as_p35(commit,manifest_sha,**(changes or {})),indent=2))
    if anchor:
        (proto.parent/'anchor.json').write_text(json.dumps({'schema':p35.ANCHOR_SCHEMA,'protocol_sha256':p35.sha(proto),'approval_reference':reference}))
    git(root,'add','-A');git(root,'commit','-q','-m','register',date=anchor_date)
    git(root,'update-ref',p35.REMOTE_MASTER,'HEAD')
    return root,commit


class Gate(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
    def tearDown(self):self.t.cleanup()
    def at(self,instant,repo):
        ps=[patch.object(p35,'utc_now',return_value=pd.Timestamp(instant)),patch.object(p35,'CHECKOUT',Path(repo).resolve())]
        for x in ps:x.start()
        self.addCleanup(lambda:[x.stop() for x in reversed(ps)])


class SchemaTests(unittest.TestCase):
    def test_p33_copy_validates_and_unsupported_conditions_fail_closed(self):
        ok=p33_as_p35('a'*40,'b'*64);self.assertEqual(p35.validate(deepcopy(ok))['max_cases'],14)
        cases={'P35_UNIT_NOT_SUPPORTED_BY_REVIEWED_PATH':{'unit_minutes':15},'P35_UNIT_INVALID':{'unit_minutes':5},
            'P35_CONDITION_NOT_SUPPORTED_BY_REVIEWED_PATH':{'costs_bps':{'fee_bps':'5','slippage_bps':'0','spread_bps':'2'}},
            'P35_STRATEGY_NOT_REGISTERED':{'strategy':{'id':'new_alpha','label':'x','params':{}}},
            'P35_STRATEGY_LABEL_MISMATCH':{'strategy':{'id':'consensus','label':'consensus_2of3','params':{'quorum':3}}},
            'P35_CASE_LIMIT_INVALID':{'max_cases':13},'P35_CODE_COMMIT_INVALID':{'code_commit':'HEAD'},
            'P35_INPUT_MANIFEST_INVALID':{'input_manifest_sha256':'x'},'P35_TIME_LIMIT_INVALID':{'analysis_seconds':601},
            'P35_DECISION_RULE_INVALID':{'decision_rule':{**ok['decision_rule'],'both_exceed_fraction':0.6}},
            'P35_PROTOCOL_INVALID':{'adopt_strategy':True}}
        for code,change in cases.items():
            with self.subTest(code=code),self.assertRaisesRegex(ValueError,code):p35.validate({**deepcopy(ok),**change})
        folds=deepcopy(ok['folds']);folds[1]['start']=folds[0]['start']
        with self.assertRaisesRegex(ValueError,'P35_FOLDS_INVALID'):p35.validate({**deepcopy(ok),'folds':folds})  # Overlap.
        with self.assertRaisesRegex(ValueError,'P35_PROTOCOL_INVALID'):p35.validate({**deepcopy(ok),'extra':1})


class RegistrationTests(Gate):
    def test_unregistered_blocks_without_network_or_files_and_missed_is_permanent(self):
        repo,_=make_repo(self.root/'r',anchor=False);self.at('2026-11-07T00:00:00Z',repo)
        with patch.object(base,'fresh',side_effect=AssertionError('No files')),patch.object(p35,'worktree',side_effect=AssertionError('No worktree')),\
                patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            self.assertEqual(p35.status(REL)['status'],'NOT_REGISTERED')
            r=p35.analyze(REL,self.root/'in',self.root/'out');self.assertEqual((r['status'],r['files_written'],r['network_calls']),('NOT_REGISTERED',0,0))
            with patch('sys.stdout'):self.assertEqual(p35.main(['status','--protocol',REL]),0)
        self.assertFalse((self.root/'out').exists())
        self.at('2026-11-08T00:00:00Z',repo);self.assertEqual(p35.status(REL)['status'],'MISSED_REGISTRATION')

    def test_anchored_before_deadline_registers_then_ready_only_after_not_before(self):
        repo,commit=make_repo(self.root/'r')
        self.at('2026-11-14T23:59:59Z',repo);s=p35.status(REL);self.assertEqual((s['status'],s['registered'],s['code_commit']),('NOT_READY',True,commit))
        self.at('2026-11-15T00:00:00Z',repo);self.assertEqual(p35.status(REL)['status'],'READY')
        reg=p35.registration(REL);self.assertEqual(reg['registration_id'],p35.sha(repo/REL.replace('protocol.json','anchor.json')))

    def test_late_untracked_modified_or_unanchored_registration_is_rejected(self):
        late,_=make_repo(self.root/'late',anchor_date='2026-11-08T00:00:00+00:00');self.at('2026-11-15T01:00:00Z',late)
        with self.assertRaisesRegex(ValueError,'P35_MISSED_REGISTRATION'):p35.registration(REL)
        repo,_=make_repo(self.root/'r');self.at('2026-11-15T01:00:00Z',repo)
        proto=repo/REL;anchor=proto.with_name('anchor.json');saved=anchor.read_bytes()
        anchor.write_bytes(saved+b'\n')
        with self.assertRaisesRegex(ValueError,'P35_REGISTRATION_MODIFIED'):p35.registration(REL)
        anchor.write_bytes(saved);original=proto.read_bytes();proto.write_bytes(original.replace(b'"0.6"',b'"0.5"'))
        with self.assertRaisesRegex(ValueError,'P35_PROTOCOL_NOT_ANCHORED'):p35.registration(REL)  # Changed after approval.
        proto.write_bytes(original);git(repo,'rm','-q','--cached',str(anchor.relative_to(repo)));git(repo,'commit','-q','-m','drop',date='2026-11-07T23:00:00+00:00')
        git(repo,'update-ref',p35.REMOTE_MASTER,'HEAD')
        with self.assertRaisesRegex(ValueError,'P35_REGISTRATION_UNTRACKED'):p35.registration(REL)

    def test_code_commit_must_exist_on_remote_master_before_registration(self):
        repo,_=make_repo(self.root/'unknown',changes={'code_commit':'f'*40});self.at('2026-11-15T01:00:00Z',repo)
        with self.assertRaisesRegex(ValueError,'P35_CODE_COMMIT_UNKNOWN'):p35.registration(REL)
        repo=self.root/'side';repo.mkdir();git(repo,'init','-q');(repo/'a').write_text('a');git(repo,'add','-A');git(repo,'commit','-q','-m','base',date='2026-11-07T19:00:00+00:00')
        git(repo,'checkout','-q','-b','side');(repo/'b').write_text('b');git(repo,'add','-A');git(repo,'commit','-q','-m','side',date='2026-11-07T19:30:00+00:00')
        side=git(repo,'rev-parse','HEAD').stdout.strip();git(repo,'checkout','-q','master')
        proto=repo/REL;proto.parent.mkdir(parents=True);proto.write_text(json.dumps(p33_as_p35(side,'e'*64)))
        (proto.parent/'anchor.json').write_text(json.dumps({'schema':p35.ANCHOR_SCHEMA,'protocol_sha256':p35.sha(proto),'approval_reference':'x'}))
        git(repo,'add','-A');git(repo,'commit','-q','-m','register',date='2026-11-07T22:00:00+00:00');git(repo,'update-ref',p35.REMOTE_MASTER,'HEAD')
        self.at('2026-11-15T01:00:00Z',repo)
        with self.assertRaisesRegex(ValueError,'P35_CODE_COMMIT_NOT_ON_MASTER'):p35.registration(REL)
        after,_=make_repo(self.root/'after',code_date='2026-11-07T23:00:00+00:00');self.at('2026-11-15T01:00:00Z',after)
        with self.assertRaisesRegex(ValueError,'P35_CODE_COMMIT_AFTER_REGISTRATION'):p35.registration(REL)

    def test_history_must_be_full_and_registration_on_remote_master(self):
        repo,commit=make_repo(self.root/'r');self.at('2026-11-15T01:00:00Z',repo)
        git(repo,'update-ref',p35.REMOTE_MASTER,commit)  # Remote master does not contain the registration commit.
        with self.assertRaisesRegex(ValueError,'P35_REGISTRATION_NOT_ON_MASTER'):p35.registration(REL)
        git(repo,'update-ref','-d',p35.REMOTE_MASTER)
        with self.assertRaisesRegex(ValueError,'P35_HISTORY_UNVERIFIED'):p35.registration(REL)
        git(repo,'update-ref',p35.REMOTE_MASTER,'HEAD');self.assertEqual(p35.registration(REL)['status'],'REGISTERED')
        shallow=self.root/'shallow';git(self.root,'clone','-q','--depth','1',repo.as_uri(),str(shallow))
        self.at('2026-11-15T01:00:00Z',shallow)
        with self.assertRaisesRegex(ValueError,'P35_HISTORY_UNVERIFIED'):p35.registration(REL)  # Never success, never MISSED.

    def test_cli_arguments_are_fixed(self):
        for argv in (['status'],['status','--protocol',REL,'--input','x'],['analyze','--protocol',REL,'--input','x'],['collect','--protocol',REL]):
            with self.subTest(argv=argv),patch('sys.stderr'),self.assertRaises(SystemExit):p35.main(argv)


def fake_row(market,status,close=None,bh=None,exit_=None,bh_exit=None):
    trades=status=='TRADES'
    def side(mine,bench):return {'return_pct':mine if trades else None,'vs_cash':p35.outcome(mine,'0') if trades else None,
        'vs_bh40':p35.outcome(mine,bench) if trades and bench is not None else None}
    return {'market':market,'status':status,'primary_close':side(close,bh),'secondary_exit':side(exit_,bh_exit)}


class TallyTests(unittest.TestCase):
    RULE=p33_as_p35('a'*40,'b'*64)['decision_rule']
    def test_ties_no_trades_and_partial_denominators(self):
        rows=[fake_row('KRW-BTC','TRADES','0','0','-1','-1'),fake_row('KRW-XRP','TRADES','1.0','1.00','1','0'),
            fake_row('KRW-BTC','NO_TRADES'),fake_row('KRW-XRP','TRADES','2',None,'2',None)]
        t=p35.tally(rows,[{'fold':'f','market':'KRW-BTC'}],14,self.RULE);c=t['primary_close']
        self.assertEqual((t['valid'],t['trade_cases'],t['no_trade_cases'],t['quarantined'],t['unfinished']),(4,3,1,1,9))
        self.assertEqual(c['vs_cash'],{'EXCEED':2,'TIE':1,'BELOW':0,'NA':0});self.assertEqual(c['vs_bh40'],{'EXCEED':0,'TIE':2,'BELOW':0,'NA':1})
        self.assertEqual((c['both_exceed'],c['denominator_trade_cases']),(0,3))  # TIE and NA never count as a win.
        self.assertEqual(t['secondary_exit']['both_exceed'],1)

    def test_decision_matches_p33_rule_on_every_boundary(self):
        for n in range(0,15):
            for wins in range(0,n+1):
                for markets in (('KRW-BTC',),('KRW-BTC','KRW-XRP')):
                    rows=[fake_row(markets[i%len(markets)],'TRADES','1','0','1','0') if i<wins else fake_row('KRW-BTC','TRADES','-1','0','-1','0') for i in range(n)]
                    rows+=[fake_row('KRW-XRP','NO_TRADES')]*(10-n if n<10 else 0)
                    with self.subTest(n=n,wins=wins,markets=markets):
                        mine=p35.tally(rows,[],14,self.RULE);old=p33.tally(rows,[],14)
                        for key in ('decision','valid','trade_cases','no_trade_cases','primary_close','secondary_exit'):self.assertEqual(mine[key],old[key])


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class ReproductionTests(t33.Registered):
    """Same synthetic P33 collection through the P33 runner and through the P35 schema copy."""
    def p35(self,source,name,snapshot=None):
        snap=self.root/(name+'-input')
        if not snap.exists():
            shutil.copytree(source/'data',snap/'data')
            folds=p33_as_p35('a'*40,'b'*64)['folds']
            base.write_json(snap/'snapshot.json',snapshot or p35.snapshot_record(snap,folds))
        p=p35.validate(p33_as_p35('a'*40,p35.raw_sha(snap/'snapshot.json')))
        out=base.fresh(self.root/name)
        return snap,out,p35.evaluate(p,snap,out,protocol_sha256='c'*64,code_commit='a'*40,registration_id='d'*64)

    def test_p33_reproduction_matches_case_decimals_and_decision(self):
        self.collect();source=self.root/'collection';_,old=self.analyze(source)
        _,out,new=self.p35(source,'p35')
        self.assertEqual((new['status'],old['status']),('VERIFIED_PARTIAL','VERIFIED_PARTIAL'))
        self.assertEqual(len(new['cases']),len(old['cases']));self.assertGreater(len(new['cases']),0)
        for a,b in zip(new['cases'],old['cases']):
            a=dict(a);b=dict(b);self.assertEqual((a.pop('fold'),a['market']),(b.pop('day'),b['market']))
            self.assertEqual(a,b)  # Every Decimal string, window, BH40, exit audit and comparison identical.
        self.assertEqual([(e['fold'],e['market'],e['quality']) for e in new['excluded']],[(e['day'],e['market'],e['quality']) for e in old['excluded']])
        for key in ('scheduled','valid','quarantined','unfinished','trade_cases','no_trade_cases','primary_close','secondary_exit','decision'):
            self.assertEqual(new['tally'][key],old['tally'][key])
        self.assertEqual(new['unfinished'],[]);self.assertEqual(new['tally']['decision'],'INSUFFICIENT_SAMPLE_HOLD')
        for name in ('REPORT.md','cases.csv','results.json'):self.assertTrue((out/name).is_file())
        lines,code=p35.summary(new);self.assertLessEqual(len(lines),8);self.assertEqual(code,0)

    def test_input_manifest_fold_and_file_mismatches_fail_closed(self):
        self.collect();source=self.root/'collection'
        snap,_,r=self.p35(source,'ok');self.assertEqual(r['status'],'VERIFIED_PARTIAL')
        p=p35.validate(p33_as_p35('a'*40,'0'*64))
        def run(name):
            out=base.fresh(self.root/name);return p35.evaluate(p,snap,out,protocol_sha256='c'*64,code_commit='a'*40,registration_id='d'*64)
        self.assertEqual(run('manifest')['failure_reason'],'P35_INPUT_MANIFEST_MISMATCH')
        csv_path=snap/'data'/p33.DAYS[0]/'KRW-XRP-1.csv';csv_path.write_bytes(csv_path.read_bytes()+b'\n')
        p=p35.validate(p33_as_p35('a'*40,p35.raw_sha(snap/'snapshot.json')))
        self.assertEqual(run('csv')['failure_reason'],'FILE_SHA_MISMATCH')
        record=json.loads((snap/'snapshot.json').read_text());record['folds'][0]['manifest_sha256']='0'*64
        base.write_json(snap/'snapshot.json',record);p=p35.validate(p33_as_p35('a'*40,p35.raw_sha(snap/'snapshot.json')))
        self.assertEqual(run('fold')['failure_reason'],'P35_FOLD_MANIFEST_CHANGED')
        with self.assertRaisesRegex(ValueError,'P32_OUTPUT_INSIDE_INPUT'):p35.p32.require_outside(snap,snap/'out')


@unittest.skipUnless(os.name=='posix','process groups and worktrees are exercised on POSIX')
class WorktreeTests(Gate):
    """The real outer path: gate, git worktree at code_commit, isolated interpreter, cleanup. Real-source checks stay on."""
    def test_evaluation_runs_inside_code_commit_worktree_independent_of_working_tree(self):
        now=pd.Timestamp.now(tz='UTC').floor('D');day=(now-pd.Timedelta(days=2)).strftime('%Y-%m-%d')
        snap=self.root/'snapshot';(snap).mkdir()
        p20.base.minute.collect(snap/'data'/day,list(p33.MARKETS),day+'T00:00:00+00:00',(pd.Timestamp(day)+pd.Timedelta(days=1)).isoformat()+'+00:00',
            connect_public=True,client=t33.factory(day))
        fold=[{'id':day,'start':day+'T00:00:00+00:00','end_exclusive':(pd.Timestamp(day)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')+'T00:00:00+00:00'}]
        base.write_json(snap/'snapshot.json',p35.snapshot_record(snap,fold))
        ts=lambda d:(now-pd.Timedelta(days=d)).isoformat()
        repo,commit=make_repo(self.root/'repo',code=True,code_date=ts(5),anchor_date=ts(4),manifest_sha=p35.raw_sha(snap/'snapshot.json'),
            changes={'folds':fold,'max_cases':2,'registration_deadline_exclusive':ts(3),'analysis_not_before_utc':ts(3),'analysis_seconds':120})
        (repo/'backtesting/p32_consensus_cost_comparison.py').write_text('raise SystemExit(99)\n')  # Uncommitted edit never reaches it.
        self.at(now.isoformat(),repo)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            r=p35.analyze(REL,snap,self.root/'out')
        # The frozen code ran its unchanged real-source check (synthetic rejected), proving it executed at code_commit.
        self.assertEqual((r['status'],r['failure_reason'],r['exit_code']),('INCOMPLETE','REAL_SOURCE_UNVERIFIED',2))
        self.assertEqual((r['code_commit'],r['network_calls']),(commit,0))
        source_root=Path(r['runner']['source_root'])
        self.assertTrue(source_root.name=='code' and source_root.parent.name.startswith('p35-worktree-'))
        self.assertFalse(source_root.exists());self.assertTrue((self.root/'out'/'INCOMPLETE').exists())
        self.assertEqual(len(git(repo,'worktree','list','--porcelain').stdout.split('worktree '))-1,1)  # Cleaned up.
        self.assertIn('SystemExit(99)',(repo/'backtesting/p32_consensus_cost_comparison.py').read_text())

    def test_time_limit_kills_the_isolated_process_group(self):
        started=time.monotonic()
        self.assertIsNone(p35.run_isolated([sys.executable,'-c','import time;time.sleep(30)'],self.root,1))
        self.assertLess(time.monotonic()-started,10)
        self.assertEqual(p35.run_isolated([sys.executable,'-c','raise SystemExit(3)'],self.root,10),3)


class BoundaryTests(unittest.TestCase):
    def test_p22_p33_frozen_and_registered_state_untouched(self):
        from tools import p22_isolated as iso
        p33.protocol();self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])
        self.assertNotIn('backtesting/p35_registered_eval.py',json.loads(p33.normalized(p33.PROTOCOL))['code_sha256'])


if __name__=='__main__':unittest.main()
