"""P33 synthetic-only tests: proposal gates, registration, collection limits, reviewed P32 evaluation, tally.

No real data, network, schedule or account. Future clock and registration receipts exist only inside tests.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal as D
import json,os,shutil,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch

import pandas as pd

from backtesting import benchmark_audit as p29
from backtesting import p19_baseline as base
from backtesting import p20_attribution as p20
from backtesting import p21_entry_filter as p21
from backtesting import p32_consensus_cost_comparison as p32
from backtesting import p33_forward as p33
from backtesting.data import demo_candles
from strategies.signals import ConsensusStrategy

ROOT=Path(__file__).resolve().parents[1]
BEGIN,END='<!-- P33_COMMAND_BEGIN -->','<!-- P33_COMMAND_END -->'
FUTURE=p33.END+pd.Timedelta(hours=1)
ORIGINAL_LOADER=base.minute.load_partitioned_sample


class FuturePandas:
    """Only the existing loader's wall-clock check is moved for synthetic tests (as in the P22 tests)."""
    class Timestamp:
        def __new__(cls,*args,**kwargs):return pd.Timestamp(*args,**kwargs)
        @staticmethod
        def now(*args,**kwargs):return p33.END+pd.Timedelta(hours=2)
    def __getattr__(self,name):return getattr(pd,name)


def receipt(path,**changes):
    value={'schema':'P33_REGISTRATION_V1','protocol_sha256':p33.sha(p33.PROTOCOL),'approved':True,
        'approval_reference':'synthetic test approval','protocol_commit':'a'*40,'observed_remote_head':'a'*40,
        'remote_repository':'sasumpi123/auto_trade','observed_at_utc':'2026-11-07T23:00:00+00:00',**changes}
    Path(path).write_text(json.dumps(value));return path


GIT=['git','-c','user.name=p33-test','-c','user.email=p33@test.invalid','-c','commit.gpgsign=false']


def anchored_checkout(root,*,commit_date='2026-11-07T22:00:00+00:00',reference='synthetic test approval',**changes):
    """Disposable git checkout holding a committed receipt + anchor (what a reviewed registration PR would add)."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if not (root/'.git').exists():subprocess.run(GIT+['init','-q'],cwd=root,check=True)
    receipt(root/'p33_registration.json',**changes)
    anchor={'schema':'P33_REGISTRATION_ANCHOR_V1','stage':'REGISTERED','receipt_sha256':p33.sha(root/'p33_registration.json'),
        'approval_reference':reference}
    (root/'p33_registration_anchor.json').write_text(json.dumps(anchor))
    env={**os.environ,'GIT_COMMITTER_DATE':commit_date,'GIT_AUTHOR_DATE':commit_date}
    subprocess.run(GIT+['add','-A'],cwd=root,check=True);subprocess.run(GIT+['commit','-q','-m','register'],cwd=root,check=True,env=env)
    return root


def patched(*managers):
    stack=ExitStack()
    for m in managers:stack.enter_context(m)
    return stack


def use_checkout(root):
    root=Path(root).resolve()
    return [patch.object(p33,'CHECKOUT',root),patch.object(p33,'ANCHOR',root/'p33_registration_anchor.json'),
            patch.object(p33,'REGISTRATION',root/'p33_registration.json')]


COMPLETE={(p33.DAYS[0],'KRW-XRP'),(p33.DAYS[1],'KRW-BTC')}


def factory(day):
    frame=demo_candles(1440);frame.index=pd.date_range(p33.day_start(day),periods=1440,freq='min')
    class FakeRest:
        def get(self,endpoint,params):
            f=frame if (day,params['market']) in COMPLETE else frame.drop(frame.index[100])
            f=f.loc[f.index<pd.Timestamp(params['to'])].iloc[-params['count']:]
            return [{'market':params['market'],'unit':1,'candle_date_time_utc':t.tz_localize(None).isoformat(),
                'opening_price':float(r.open),'high_price':float(r.high),'low_price':float(r.low),
                'trade_price':float(r.close),'candle_acc_trade_volume':float(r.volume)} for t,r in f.iloc[::-1].iterrows()]
        def close(self):pass
    return FakeRest()


def tree(path):return {p.relative_to(path):p.read_bytes() for p in sorted(Path(path).rglob('*')) if p.is_file()}


class Registered(unittest.TestCase):
    """Synthetic registration + future clock; every guard is a test-only patch, never a CLI option."""
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        def synthetic(path,**kwargs):return ORIGINAL_LOADER(path,allow_synthetic=True,**kwargs)
        self.checkout=anchored_checkout(self.root/'checkout')
        self.patches=[patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),*use_checkout(self.checkout),
            patch.object(p33,'utc_now',return_value=FUTURE+pd.Timedelta(hours=1)),
            patch.object(base.minute,'pd',FuturePandas()),
            patch.object(base.minute,'load_partitioned_sample',side_effect=synthetic)]
        for x in self.patches:x.start()
    def tearDown(self):
        for x in reversed(self.patches):x.stop()
        self.t.cleanup()
    def collect(self,name='collection'):
        import time
        started=time.monotonic()
        def clock():return FUTURE.timestamp()+time.monotonic()-started
        return p33.collect(self.root/name,connect_public=True,clock=clock,client_factory=factory)
    def analyze(self,source,name='analysis'):
        out=base.fresh(self.root/name);p33.work(source,out,Mock());r=p20.read_results(out/'results.json')
        r['elapsed_seconds']=0;p33.write_report(out,r);return out,r


class RegistrationTests(unittest.TestCase):
    """Review 5796192064: a receipt alone never registers; only the reviewed, committed anchor can."""
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
    def tearDown(self):self.t.cleanup()
    def at(self,instant,root):
        ps=[patch.object(p33,'utc_now',return_value=pd.Timestamp(instant)),*use_checkout(root)]
        for x in ps:x.start()
        self.addCleanup(lambda:[x.stop() for x in reversed(ps)])

    def unanchored(self):
        root=self.root/'proposal';root.mkdir()
        (root/'p33_registration_anchor.json').write_text(json.dumps({'schema':'P33_REGISTRATION_ANCHOR_V1','receipt_sha256':None,'approval_reference':None}))
        return root

    def test_proposal_blocks_and_local_retroactive_receipt_never_opens_gate(self):
        root=self.unanchored()
        with patched(patch.object(p33,'utc_now',return_value=p33.START-pd.Timedelta(days=1)),*use_checkout(root)):
            self.assertEqual(p33.status()['status'],'PROPOSED_NOT_REGISTERED')
            with patch.object(p33.minute,'collect',side_effect=AssertionError('No request')),\
                    patch.object(base,'fresh',side_effect=AssertionError('No files')):
                self.assertEqual(p33.collect('/nonexistent',connect_public=True)['status'],'NOT_REGISTERED')
                self.assertEqual(p33.analyze('/nonexistent','/nonexistent2')[1]['status'],'NOT_REGISTERED')
        with patched(patch.object(p33,'utc_now',return_value=pd.Timestamp('2026-11-16T00:00:00Z')),*use_checkout(root)):
            self.assertEqual(p33.registration()['status'],'MISSED_REGISTRATION')
            receipt(root/'p33_registration.json',approval_reference='unverified')  # Reviewer's retroactive local receipt.
            reg=p33.registration();self.assertEqual(reg['status'],'MISSED_REGISTRATION');self.assertTrue(reg['receipt_present_but_unanchored'])
            self.assertIsNotNone(p33.gate()[1]);self.assertEqual(p33.status()['status'],'MISSED_REGISTRATION')
            receipt(root/'p33_registration.json',observed_at_utc='2027-01-01T00:00:00+00:00')
            self.assertIsNotNone(p33.gate()[1])

    def test_anchored_committed_receipt_registers_and_every_bypass_is_rejected(self):
        good=anchored_checkout(self.root/'good');self.at('2026-11-07T23:30:00Z',good)
        reg=p33.registration();self.assertEqual(reg['status'],'REGISTERED');self.assertEqual(reg['registration_id'],p33.sha(good/'p33_registration.json'))
        cases=[('late anchor commit',dict(commit_date='2026-11-08T00:00:00+00:00'),'P33_MISSED_REGISTRATION'),
            ('observed at deadline',dict(observed_at_utc='2026-11-08T00:00:00+00:00'),'P33_MISSED_REGISTRATION'),
            ('observed in the future',dict(observed_at_utc='2026-11-07T23:50:00+00:00'),'P33_REGISTRATION_FROM_FUTURE'),
            ('reference mismatch',dict(reference='other approval'),'P33_REGISTRATION_UNVERIFIED'),
            ('protocol digest',dict(protocol_sha256='0'*64),'P33_REGISTRATION_UNVERIFIED'),
            ('not approved',dict(approved=False),'P33_REGISTRATION_UNVERIFIED'),
            ('commit != head',dict(observed_remote_head='b'*40),'P33_REGISTRATION_UNVERIFIED')]
        for n,(name,kw,reason) in enumerate(cases):
            with self.subTest(name):
                root=anchored_checkout(self.root/f'c{n}',**kw);self.at('2026-11-07T23:30:00Z',root)
                with self.assertRaisesRegex(ValueError,reason):p33.registration()
                with self.assertRaisesRegex(ValueError,reason):p33.gate()  # The gate never opens.

    def test_modified_untracked_or_swapped_receipt_and_anchor_are_rejected(self):
        root=anchored_checkout(self.root/'m');self.at('2026-11-07T23:30:00Z',root)
        saved=(root/'p33_registration.json').read_bytes()
        receipt(root/'p33_registration.json',approval_reference='synthetic test approval',protocol_commit='c'*40,observed_remote_head='c'*40)
        with self.assertRaisesRegex(ValueError,'P33_RECEIPT_NOT_ANCHORED'):p33.registration()  # Tampered bytes.
        (root/'p33_registration.json').write_bytes(saved)
        anchor=json.loads((root/'p33_registration_anchor.json').read_text());anchor['note']='local edit'
        (root/'p33_registration_anchor.json').write_text(json.dumps(anchor))
        with self.assertRaisesRegex(ValueError,'P33_REGISTRATION_MODIFIED'):p33.registration()  # Uncommitted anchor edit.
        subprocess.run(GIT+['checkout','--','p33_registration_anchor.json'],cwd=root,check=True)
        subprocess.run(GIT+['rm','-q','--cached','p33_registration.json'],cwd=root,check=True)
        with self.assertRaisesRegex(ValueError,'P33_REGISTRATION_UNTRACKED'):p33.registration()  # Untracked receipt.
        (root/'p33_registration.json').unlink()
        with self.assertRaisesRegex(ValueError,'P33_RECEIPT_NOT_ANCHORED'):p33.registration()

    def test_frozen_sources_and_conditions(self):
        root=anchored_checkout(self.root/'f');self.at('2026-11-07T23:30:00Z',root);real=p33.sha
        with patch.object(p33,'sha',side_effect=lambda f:'0'*64 if Path(f).name=='signals.py' else real(f)):
            with self.assertRaisesRegex(ValueError,'P33_FROZEN_CODE_CHANGED'):p33.registration()
        changed=json.loads(p33.PROTOCOL.read_text());changed['costs_bps']['fee_bps']='4'
        other=self.root/'p.json';other.write_text(json.dumps(changed))
        with patch.object(p33,'PROTOCOL',other),self.assertRaisesRegex(ValueError,'P33_PROTOCOL_CONDITIONS_CHANGED'):p33.protocol()

    def test_real_checkout_state_is_consistent_either_way(self):
        """Passes whether or not the real checkout is registered (proposal now, registered after the reviewed PR)."""
        state=p33.status()['status'];anchor=json.loads(p33.ANCHOR.read_text())
        if anchor['receipt_sha256'] is None:self.assertIn(state,('PROPOSED_NOT_REGISTERED','MISSED_REGISTRATION'))
        else:self.assertEqual(state,'REGISTERED');self.assertEqual(p33.sha(p33.REGISTRATION),anchor['receipt_sha256'])

    def test_protocol_pins_current_sources_and_p22_frozen_untouched(self):
        proto=p33.protocol()
        self.assertIn('backtesting/p33_forward.py',proto['code_sha256']);self.assertIn('backtesting/p32_consensus_cost_comparison.py',proto['code_sha256'])
        self.assertEqual(proto['status'],'PROPOSED_FOR_REVIEW_NOT_REGISTERED');self.assertEqual(proto['other_policies'][:4],'NONE')
        from tools import p22_isolated as iso
        self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])


class GateTests(Registered):
    def test_registered_but_before_end_is_not_ready_with_no_access(self):
        with patch.object(p33,'utc_now',return_value=p33.END-pd.Timedelta(seconds=1)),\
                patch.object(p33.minute,'collect',side_effect=AssertionError('No request')),\
                patch.object(base,'fresh',side_effect=AssertionError('No files')):
            r=p33.collect(self.root/'c',connect_public=True);self.assertEqual((r['status'],r['network_calls'],r['files_written']),('NOT_READY',0,0))
            self.assertEqual(p33.analyze(self.root/'c',self.root/'a')[1]['status'],'NOT_READY')
        self.assertEqual(p33.collect(self.root/'c')['status'],'OFFLINE_PLAN_ONLY');self.assertFalse((self.root/'c').exists())

    def test_collection_clock_before_window_is_blocked(self):
        out=p33.collect(self.root/'early',connect_public=True,clock=lambda:(p33.END-pd.Timedelta(minutes=1)).timestamp(),client_factory=factory)
        self.assertEqual((out['status'],out['failure_reason']),('INCOMPLETE','P33_CLOCK_BEFORE_COLLECTION_WINDOW'))


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class PipelineTests(Registered):
    def test_collect_then_evaluate_matches_reviewed_tools_and_tallies(self):
        collection=self.collect();self.assertEqual(collection['status'],'PARTIAL_REAL_DATA')
        self.assertLessEqual(collection['network_calls'],p33.MAX_CALLS_TOTAL)
        source=self.root/'collection';before=tree(source)
        out,r=self.analyze(source)
        self.assertEqual(tree(source),before)
        self.assertEqual(r['status'],'VERIFIED_PARTIAL');self.assertEqual(len(r['cases']),2);self.assertEqual(len(r['excluded']),12)
        self.assertEqual(r['unfinished'],[]);self.assertEqual(r['policy'],'consensus_2of3')
        for c in r['cases']:
            _,frames,_=ORIGINAL_LOADER(source/'data'/c['day'],allow_synthetic=True,quarantine_gaps=True)
            frame,windows=base.prepare(frames[c['market']],60)
            direct=p32.strategy_case(frame,windows,c['market'],ConsensusStrategy(quorum=2))
            expected=p21.compact(direct,direct['attribution'],filtered=False)
            for key in ('net_return_pct','fees','mdd_pct','fills','final_equity','realized_pnl','unrealized_pnl','open_position'):
                self.assertEqual(c['result'][key],json.loads(json.dumps(expected[key])))  # Same numbers as the reviewed P32 path.
            bh=p29.buy_hold_40_core(frame,direct['execution_first_open'],c['market'],self.root/f"bh-{c['day']}",prefix='P33')
            self.assertEqual(c['buy_hold_40']['marked_return_pct'],bh['marked_return_pct'])
            self.assertEqual(c['buy_hold_40']['entry_open_utc'],direct['execution_first_open'])  # Same window, not the first BUY.
            if D(c['open_position']['quantity'])>0:
                self.assertLess(D(c['secondary_exit']['return_pct']),D(c['primary_close']['return_pct']))  # Terminal unwind cost.
        t=r['tally'];self.assertEqual((t['scheduled'],t['valid'],t['quarantined'],t['unfinished']),(14,2,12,0))
        self.assertEqual(t['decision'],'INSUFFICIENT_SAMPLE_HOLD')
        for name in ('REPORT.md','cases.csv'):self.assertTrue((out/name).is_file())
        lines,code=p33.summary(r);self.assertLessEqual(len(lines),8);self.assertEqual(code,0)

    def test_hash_registry_and_path_mismatches_fail_closed(self):
        self.collect();source=self.root/'collection'
        csv_path=source/'data'/p33.DAYS[0]/'KRW-XRP-1.csv';orig=csv_path.read_bytes()
        csv_path.write_bytes(orig+b'\n');_,r=self.analyze(source,'bad');self.assertEqual(r['failure_reason'],'FILE_SHA_MISMATCH')
        csv_path.write_bytes(orig)
        record=json.loads((source/'collection.json').read_text())
        bad=deepcopy(record);bad['protocol_sha256']='0'*64;base.write_json(source/'collection.json',bad)
        _,r=self.analyze(source,'reg');self.assertEqual(r['failure_reason'],'P33_INPUT_REGISTRATION_MISMATCH')
        bad=deepcopy(record);bad['network_calls']=999;base.write_json(source/'collection.json',bad)
        _,r=self.analyze(source,'calls');self.assertEqual(r['failure_reason'],'P33_COLLECTION_REGISTRY_MISMATCH')
        base.write_json(source/'collection.json',record)
        proto=source/'protocol.json';saved=proto.read_bytes();proto.write_bytes(saved+b' ')
        _,r=self.analyze(source,'proto');self.assertEqual(r['failure_reason'],'P33_INPUT_PROTOCOL_MISMATCH');proto.write_bytes(saved)
        anchored_checkout(self.checkout,protocol_commit='d'*40,observed_remote_head='d'*40)  # A different approval.
        _,r=self.analyze(source,'rereg');self.assertEqual(r['failure_reason'],'P33_INPUT_REGISTRATION_MISMATCH')
        with self.assertRaisesRegex(ValueError,'P32_OUTPUT_INSIDE_INPUT'):p33.analyze(source,source/'analysis')
        self.assertFalse((source/'analysis').exists())
        (self.root/'exists').mkdir()
        with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS_NO_OVERWRITE'):p33.collect(self.root/'exists',connect_public=True,client_factory=factory)


def fake_row(market,status,close=None,bh=None,exit_=None,bh_exit=None):
    trades=status=='TRADES'
    def side(mine,bench):return {'return_pct':mine if trades else None,'vs_cash':p33.outcome(mine,'0') if trades else None,
        'vs_bh40':p33.outcome(mine,bench) if trades and bench is not None else None}
    return {'day':'d','market':market,'status':status,'primary_close':side(close,bh),'secondary_exit':side(exit_,bh_exit)}


class TallyTests(unittest.TestCase):
    def test_unrounded_comparison_ties_and_no_trades(self):
        self.assertEqual(p33.outcome('0.0000000000000001','0'),'EXCEED');self.assertEqual(p33.outcome('1.50','1.5'),'TIE')
        self.assertEqual(p33.outcome('-0.1','0'),'BELOW');self.assertIsNone(p33.outcome(None,'0'))
        rows=[fake_row('KRW-BTC','TRADES','1','1','0.9','0.9'),fake_row('KRW-XRP','NO_TRADES'),fake_row('KRW-XRP','TRADES','0','-1','-0.1',None)]
        t=p33.tally(rows,[])
        self.assertEqual((t['trade_cases'],t['no_trade_cases']),(2,1))
        self.assertEqual(t['primary_close']['vs_bh40'],{'EXCEED':1,'TIE':1,'BELOW':0,'NA':0})  # Tie (1 vs 1) is never a win.
        self.assertEqual(t['primary_close']['vs_cash'],{'EXCEED':1,'TIE':1,'BELOW':0,'NA':0})
        self.assertEqual(t['primary_close']['both_exceed'],0);self.assertEqual(t['secondary_exit']['vs_bh40'],{'EXCEED':0,'TIE':1,'BELOW':0,'NA':1})

    def test_decision_rule_boundaries(self):
        win=lambda m:fake_row(m,'TRADES','2','1','1.9','0.9');lose=lambda m:fake_row(m,'TRADES','-1','1','-1.1','0.9')
        nt=lambda m:fake_row(m,'NO_TRADES')
        self.assertEqual(p33.tally([win('KRW-BTC')]*9,[])['decision'],'INSUFFICIENT_SAMPLE_HOLD')
        self.assertEqual(p33.tally([win('KRW-BTC')]*5+[nt('KRW-XRP')]*7,[])['decision'],'INSUFFICIENT_SAMPLE_HOLD')
        self.assertEqual(p33.tally([win('KRW-BTC')]*8+[lose('KRW-XRP')]*4,[])['decision'],'CONCENTRATED_OR_ABSENT_HOLD')
        self.assertEqual(p33.tally([win('KRW-BTC')]*4+[win('KRW-XRP')]*2+[lose('KRW-XRP')]*4,[])['decision'],'FURTHER_VALIDATION_WARRANTED')
        self.assertEqual(p33.tally([win('KRW-BTC')]*3+[win('KRW-XRP')]*2+[lose('KRW-XRP')]*5,[])['decision'],'NOT_SUPPORTED')
        t=p33.tally([win('KRW-BTC')]*10,[{'day':'x','market':'KRW-XRP'}]*2)
        self.assertEqual((t['valid'],t['quarantined'],t['unfinished']),(10,2,2))


class CliTests(unittest.TestCase):
    def test_cli_arguments_are_fixed(self):
        for args in (['status','--output','x'],['collect'],['collect','--output','x','--input','y'],['analyze','--input','x'],
                     ['analyze','--input','x','--output','y','--connect-public'],['collect','--output','x','--days','1']):
            with self.subTest(args=args),patch('sys.stderr'),self.assertRaises(SystemExit):p33.main(args)

    @unittest.skipUnless(os.name=='posix','bash required')
    def test_documented_block_real_state_registered_copy_and_dirty(self):
        doc=(ROOT/'docs/P33_CONSENSUS_FORWARD_PROPOSAL.md').read_text(encoding='utf-8')
        body=doc.split(BEGIN,1)[1].split(END,1)[0].strip();self.assertTrue(body.startswith('```bash\n') and body.endswith('\n```'))
        block=body[len('```bash\n'):-len('\n```')].replace('.venv/bin/python',f'"{sys.executable}"')
        def run(repo):
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')};env['PYTHON_DOTENV_DISABLED']='1'
            return subprocess.run(['bash','-c',block],cwd=repo,env=env,capture_output=True,text=True,timeout=120)
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();repo=root/'repo'
            files=subprocess.run(['git','ls-files','-co','--exclude-standard','-z'],cwd=ROOT,capture_output=True,check=True).stdout.split(b'\0')
            for name in filter(None,files):
                src=ROOT/name.decode()
                if src.is_file() and not src.is_symlink():
                    dst=repo/name.decode();dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
            old='2026-11-01T00:00:00+00:00';env={**os.environ,'GIT_COMMITTER_DATE':old,'GIT_AUTHOR_DATE':old}
            for cmd in (['init','-q'],['add','-A'],['commit','-q','-m','snapshot']):subprocess.run(GIT+cmd,cwd=repo,check=True,env=env)
            expected=p33.status()['status']  # The real checkout's own state, whatever it is.
            done=run(repo);lines=done.stdout.splitlines();self.assertLessEqual(len(lines),8)
            self.assertTrue(lines[0].startswith('P33 '+expected),done.stdout+done.stderr)
            self.assertEqual(done.returncode,0 if expected in ('PROPOSED_NOT_REGISTERED','REGISTERED') else 2)
            if json.loads((repo/'backtesting/p33_registration_anchor.json').read_text())['receipt_sha256'] is None:
                receipt(repo/'backtesting/p33_registration.json',observed_at_utc='2026-11-01T00:00:00+00:00')
                anchor={'schema':'P33_REGISTRATION_ANCHOR_V1','stage':'REGISTERED','approval_reference':'synthetic test approval',
                    'receipt_sha256':p33.sha(repo/'backtesting/p33_registration.json')}
                (repo/'backtesting/p33_registration_anchor.json').write_text(json.dumps(anchor))
                for cmd in (['add','-A'],['commit','-q','-m','register']):subprocess.run(GIT+cmd,cwd=repo,check=True,env=env)
            done=run(repo);lines=done.stdout.splitlines()
            self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertTrue(lines[0].startswith('P33 REGISTERED'),lines)
            (repo/'README.md').write_bytes((repo/'README.md').read_bytes()+b'\nchange\n')
            done=run(repo);self.assertNotEqual(done.returncode,0);self.assertIn('STOP',done.stdout)


if __name__=='__main__':unittest.main()
