"""P26 manual PAPER continuation. SYNTHETIC public stream and clocks only; real PAPER engine, P17
costs and P9 accounting. Proves the account/state contract, not Upbit behaviour or profitability."""
import copy
import io
import json
import os
import unittest
from decimal import Decimal,localcontext
from unittest.mock import Mock,patch

from ops import paper_experiment as exp
from ops import paper_continuation as cont
from ops.paper_session import run_session
from services.tick_timing import TickTiming,TickTimingError,ACCOUNT_QUARANTINE_POLICY,discard_policy
import tests.test_p10h_realtime_activity as fixtures

POISON='POISON_P26'
WALL=1800000000


class AccountGuardTests(unittest.TestCase):
    def send(self,g,stamp,at,code='KRW-BTC'):
        return g.observe({'type':'ticker','code':code,'timestamp':WALL*1000+stamp,'trade_price':POISON},WALL+at,at)
    def guard(self,counts=None,hw=None):
        counts=counts or {};prior={'high_water_ms':hw or {},'counts':counts,'total':sum(counts.values())}
        return TickTiming(['KRW-BTC'],WALL,0,quarantine_regressions=True,account_prior=prior)

    def test_carried_budget_allows_only_the_remainder_without_refill(self):
        g=self.guard({'KRW-BTC':9});self.send(g,100,.1)
        self.assertIsNone(self.send(g,69,.2))  # 10th account-wide discard allowed.
        self.send(g,101,.3)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,70,.4)  # 11th blocked.
        r=g.report()['regression_discard']
        self.assertEqual((r['discarded_total'],r['account_prior_total'],r['account_discarded_total'],r['account_counts']),
                         (1,9,10,{'KRW-BTC':10}))

    def test_carried_high_water_quarantines_below_and_holds_equal_then_releases_on_advance(self):
        g=self.guard(hw={'KRW-BTC':WALL*1000+500})
        self.assertIsNone(self.send(g,400,.1))  # Below carried high-water: quarantined (counts toward account).
        self.assertIsNone(self.send(g,500,.2))  # Equal while quarantined: held, not forwarded.
        self.assertEqual(self.send(g,501,.3),'KRW-BTC');self.assertIsNone(g.awaiting_followup)
        g=self.guard(hw={'KRW-BTC':WALL*1000+500});self.send(g,400,.1)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.send(g,501,15.1)  # P25 pre-update watchdog kept.

    def test_prior_validation_and_policy_exclusivity(self):
        for bad in ({'high_water_ms':{},'counts':{'KRW-BTC':11},'total':11},{'high_water_ms':{},'counts':{'KRW-BTC':1},'total':2},
                    {'high_water_ms':{'KRW-ETH':1},'counts':{},'total':0},{'high_water_ms':{},'counts':{}}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                TickTiming(['KRW-BTC'],WALL,0,quarantine_regressions=True,account_prior=bad)
        with self.assertRaises(ValueError):TickTiming(['KRW-BTC'],WALL,0,account_prior={'high_water_ms':{},'counts':{},'total':0})
        with self.assertRaises(ValueError):discard_policy(account=True)
        self.assertIs(discard_policy(quarantine=True,account=True),ACCOUNT_QUARANTINE_POLICY)
        g=TickTiming(['KRW-BTC'],WALL,0,quarantine_regressions=True)
        self.assertNotIn('account_counts',g.report()['regression_discard'])  # P25 report unchanged.


class ReservationTests(unittest.TestCase):
    def marker(self,**over):
        block=cont.reserve('first',3600)
        m={'session_id':'a'*32,'previous_session':None,'phase':'STOPPED_VERIFIED','end_sha256':'f'*64,'p26':block,
           'p26_checkpoint':{'session_id':'a'*32,'state_sha256':'f'*64,'policy':cont.POLICY_ID,'run_index':1,
                             'high_water_ms':{'KRW-BTC':5},'counts':{'KRW-BTC':2},'total':2,'quarantined':[]},
           'regression_checkpoint':{'evidence':{'combined':{'discarded':2,'accepted':3,'markets':{'KRW-BTC':{'count':2}}}}}}
        m.update(over)
        if isinstance(m.get('p26_checkpoint'),dict) and 'digest' not in over.get('p26_checkpoint',{}):
            m['p26_checkpoint']=dict(m['p26_checkpoint']);m['p26_checkpoint'].pop('digest',None)
            m['p26_checkpoint']['digest']=cont.checkpoint_digest(m['p26_checkpoint'],m)
        return m

    def test_exact_second_and_run_limits(self):
        self.assertEqual(cont.reserve('resume',3600,self.marker(),'f'*64)['reserved_seconds_total'],7200)  # Exact cap.
        for seconds in (0,3601,1.5,True):
            with self.subTest(seconds=seconds),self.assertRaises(ValueError):cont.reserve('first',seconds)
        second=cont.reserve('resume',10,self.marker(),'f'*64)
        self.assertEqual((second['run_index'],second['rest_reserved_total'],second['prior']['total']),(2,128,2))
        third=self.marker(p26=second,session_id='b'*32)
        third['p26_checkpoint']=dict(third['p26_checkpoint'],session_id='b'*32,run_index=2)
        with self.assertRaisesRegex(ValueError,'P26_RUN_LIMIT_REACHED'):cont.reserve('resume',10,third,'f'*64)
        bad=copy.deepcopy(second);bad['reserved_seconds_total']=7201
        with self.assertRaises(ValueError):cont.validate_block(bad)

    def test_stale_tampered_unresolved_or_exhausted_checkpoints_block(self):
        cases={'stale_sha':('e'*64,{}),'other_session':('f'*64,{'p26_checkpoint':dict(self.marker()['p26_checkpoint'],session_id='c'*32)}),
               'lowered_count':('f'*64,{'p26_checkpoint':dict(self.marker()['p26_checkpoint'],counts={'KRW-BTC':1},total=1)}),
               'unresolved':('f'*64,{'p26_checkpoint':dict(self.marker()['p26_checkpoint'],quarantined=['KRW-BTC'])}),
               'missing':('f'*64,{'p26_checkpoint':None}),'not_stopped':('f'*64,{'phase':'PREPARATION_FAILED'}),
               'exhausted':('f'*64,{'p26_checkpoint':dict(self.marker()['p26_checkpoint'],counts={'KRW-BTC':10},total=10),
                    'regression_checkpoint':{'evidence':{'combined':{'discarded':10,'accepted':3,'markets':{'KRW-BTC':{'count':10}}}}}}),
               'no_evidence':('f'*64,{'regression_checkpoint':None})}
        for name,(current,over) in cases.items():
            with self.subTest(case=name),self.assertRaises(ValueError):cont.reserve('resume',10,self.marker(**over),current)


@unittest.skipUnless(os.name=='posix','Existing independent PAPER ownership is POSIX only')
class ContinuationSessionTests(unittest.TestCase):
    factory=fixtures.ActivityTests.factory
    make_stream=fixtures.ActivityTests.make_stream
    tearDown=fixtures.ActivityTests.tearDown  # Protected legacy/HALTED accounts and backups stay byte-identical.
    def setUp(self):
        fixtures.ActivityTests.setUp(self)
        for p in self.experiment.iterdir():
            if p.is_file():self.before[p]=p.read_bytes()
        self.before[exp.receipt_path(self.experiment)]=exp.receipt_path(self.experiment).read_bytes()
        self.legacy=self.experiment
        self.experiment=self.parent/'p26-account';self.state=self.experiment/exp.STATE
        self.assertEqual(exp.create(self.experiment,profile=cont.PROFILE_ID)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        backup=self.parent/'p26-initial-backup.json';backup.write_bytes(self.state.read_bytes());self.before[backup]=backup.read_bytes()
        self.prices=[];self.plan=None;self.runtime=False;self.now=0
        owner=self
        class Stream:
            def __init__(self,codes):self.codes=codes;self.pre=0;self.run=0
            def connect(self):pass
            def close(self):pass
            def get(self):
                key='run' if owner.runtime else 'pre'
                events=owner.plan[key];i=getattr(self,key);setattr(self,key,i+1)
                if i>=len(events):
                    if owner.runtime:owner.now=owner.plan['end'];owner.stop.set()
                    else:owner.now=1
                    raise TimeoutError()
                at,stamp,price=events[i];owner.now=at
                return {'type':'ticker','code':'KRW-BTC','stream_type':'REALTIME','timestamp':int(owner.wall*1000)+stamp,'trade_price':price}
        self.stream_factory=Stream
    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True
    def session(self,mode,plan,actions,seconds=30,finish=True,token=None):
        from trading.auto_trade import AutoTrade
        self.plan=plan;self.actions=actions;self.runtime=False;self.now=0;self.stop.clear()
        token=token or exp.begin(self.experiment,p26=mode,seconds=seconds)
        original=AutoTrade.process_market_data
        def observe(bot,t,data):self.prices.append(data['trade_price']);return original(bot,t,data)
        options={'session_id':token,'observe_realtime_seconds':1,'p26_continuation':mode}
        with patch.object(AutoTrade,'process_market_data',new=observe):
            r=run_session(self.state,seconds=seconds,stopped=self.stop.is_set,emit=self.emit,client=self.client,
                stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],experiment=options,
                wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return r,(exp.finish(self.experiment,token,r,record_summary=True) if finish else None),token
    # Run 1: observation with one -31 ms quarantine; runtime BUY at 10000, then a -31 ms poison and release.
    RUN1={'pre':[(.1,100,10000),(.2,69,POISON),(.3,101,10000),(.4,200,10000)],
          'run':[(1.1,1100,10000),(1.2,1069,POISON),(1.3,1300,10000)],'end':1.5}
    def run2(self,extra=()):
        # Wall moves +2 s; carried high-water is the old wall's +1300 ms (= new-relative -700 ms).
        return {'pre':[(.1,-750,POISON),(.2,100,10000),(.3,200,10000)],'run':[*extra,(1.1,1100,10010),(1.3,1300,10010)],'end':1.5}

    def first_then_resume(self,run2=None):
        r1,e1,t1=self.session('first',self.RUN1,['BUY'])
        self.assertEqual(e1['status'],'STOPPED_VERIFIED',r1);self.wall+=2
        r2,e2,t2=self.session('resume',run2 or self.run2(),['SELL'])
        return r1,e1,t1,r2,e2,t2

    def test_buy_stop_hold_resume_sell_reconciles_p17_costs_once(self):
        from ops.paper_bot import read_report
        r1,e1,t1=self.session('first',self.RUN1,['BUY'])
        self.assertEqual(e1['status'],'STOPPED_VERIFIED',r1);self.assertEqual(r1['orders'],1)
        mid=read_report(self.experiment)
        self.assertEqual(list(mid['summary']['positions']),['KRW-BTC']);self.assertEqual(mid['summary']['equity'],'N/A')
        self.assertEqual(mid['p26_account']['runs_used'],1);self.assertTrue(mid['p26_account']['resume_allowed'])
        self.assertEqual(mid['p26_account']['account_discards_total'],2)
        held=json.loads(self.state.read_bytes())['positions']['KRW-BTC']
        self.wall+=2;self.client.candles.reset_mock()
        r2,e2,t2=self.session('resume',self.run2(),['SELL'])
        self.assertEqual(e2['status'],'STOPPED_VERIFIED',r2)
        self.assertTrue(self.client.candles.called)  # Fresh completed candles prepared again on resume.
        self.assertIn('realtime_activity',r2)        # Fresh public observation passed again on resume.
        self.assertEqual(r2['orders'],2);saved=json.loads(self.state.read_bytes())
        self.assertEqual(len(saved['orders']),2);self.assertTrue(all(o['status']=='settled' for o in saved['orders'].values()))
        self.assertEqual(saved['positions'],{});self.assertEqual(saved['paper_experiment']['last_session_id'],t2)
        self.assertEqual(Decimal(held['quantity'])>0,True)
        rep=read_report(self.experiment)
        with localcontext() as ctx:
            ctx.prec=60
            expected=(Decimal(400000)/Decimal('1.0005')/Decimal(10006)*Decimal('10003.994')*Decimal('.9995'))-400000
        self.assertAlmostEqual(Decimal(rep['summary']['net_realized_pnl']),expected,delta=Decimal('1e-20'))
        self.assertAlmostEqual(Decimal(rep['summary']['cash']),Decimal(1000000)+expected,delta=Decimal('1e-12'))
        self.assertEqual((rep['summary']['cumulative_buys'],rep['summary']['cumulative_sells']),(1,1))  # No duplicate fill.
        self.assertGreater(Decimal(rep['summary']['fees']),0);self.assertEqual(rep['summary']['pending_orders'],0)
        self.assertNotIn(POISON,self.prices);self.assertNotIn(POISON,json.dumps(rep))
        self.assertEqual(r2['markets']['KRW-BTC']['last_price'],'10010')  # No run-1 mark reused.
        a=r2['p26_account']
        self.assertEqual((a['run_index'],a['reserved_seconds_total'],a['rest_reserved_total'],a['account_discards_prior'],
                          a['session_discards'],a['account_discards_total']),(2,60,128,2,1,3))
        self.assertEqual(rep['p26_account']['runs_used'],2);self.assertFalse(rep['p26_account']['resume_allowed'])

    def test_third_run_and_any_other_mode_or_promotion_is_blocked(self):
        self.first_then_resume()
        for kw in ({'p26':'resume','seconds':10},{'p26':'first','seconds':10},{},{'observation_discard':True,'quarantine':True},
                   {'observation_discard':True,'repeated_discard':True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):exp.begin(self.experiment,**kw)
        with self.assertRaisesRegex(ValueError,'P26_PROFILE_REQUIRED'):exp.begin(self.legacy,p26='first',seconds=10)
        fresh=self.parent/'p25-style';exp.create(fresh,cost_policy=cont.COST_INPUT)
        for mode in ('first','resume'):
            with self.assertRaisesRegex(ValueError,'P26_PROFILE_REQUIRED'):exp.begin(fresh,p26=mode,seconds=10)
        self.assertFalse(exp.first_use_path(fresh).exists())

    def test_resume_blocked_after_halt_preparation_failure_or_unresolved_quarantine(self):
        plans={'runtime_unresolved':dict(self.RUN1,run=[(1.1,1100,10000),(1.2,1069,POISON)]),
               'observation_unresolved':dict(self.RUN1,pre=[(.1,100,10000),(.2,69,POISON)])}
        for name,plan in plans.items():
            with self.subTest(case=name):
                self.setUp() if name!='runtime_unresolved' else None
                r,end,_=self.session('first',plan,['BUY'])
                self.assertIn(end['status'],('HALTED','PREPARATION_FAILED'))
                before=(self.experiment/'session.json').read_bytes()
                for mode in ('resume','first'):
                    with self.assertRaises(ValueError):exp.begin(self.experiment,p26=mode,seconds=10)
                self.assertEqual(before,(self.experiment/'session.json').read_bytes())  # Nothing new reserved.

    def test_reservation_consumed_before_network_and_never_refunded(self):
        self.client.candles.side_effect=AssertionError('network must not start')
        token=exp.begin(self.experiment,p26='first',seconds=30)
        m=exp.marker(self.experiment)
        self.assertEqual((m['p26']['run_index'],m['p26']['session_seconds'],m['p26']['rest_reserved_total']),(1,30,64))
        self.assertTrue(exp.first_use_path(self.experiment).exists())
        with self.assertRaises(ValueError):exp.begin(self.experiment,p26='first',seconds=30)  # In-progress: no second start.
        self.client.candles.side_effect=None
        self.stop.set()  # Early stop before observation completes.
        self.plan={'pre':[],'run':[],'end':1};self.runtime=False
        r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.emit,client=self.client,
            stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],
            experiment={'session_id':token,'observe_realtime_seconds':1,'p26_continuation':'first'},
            wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        exp.finish(self.experiment,token,r)
        for mode in ('first','resume'):
            with self.assertRaises(ValueError):exp.begin(self.experiment,p26=mode,seconds=30)

    def test_invalid_arguments_identity_or_owner_block_before_any_reservation(self):
        for kw in ({'p26':'first','seconds':3601},{'p26':'first','seconds':0},{'p26':'other','seconds':30},
                   {'p26':'first','seconds':30,'quarantine':True,'observation_discard':True},{'p26':'resume','seconds':30}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):exp.begin(self.experiment,**kw)
            self.assertFalse(exp.first_use_path(self.experiment).exists());self.assertFalse((self.experiment/'session.json').exists())
        from ops.paper_session import ExistingPaperStore
        owner=ExistingPaperStore(self.state,1_000_000);owner.acquire()
        try:
            with self.assertRaises(Exception):exp.begin(self.experiment,p26='first',seconds=30)  # Active owner.
        finally:owner.close()
        self.assertFalse(exp.first_use_path(self.experiment).exists())

    def test_runtime_rejects_mismatched_options_seconds_or_markers_before_network(self):
        token=exp.begin(self.experiment,p26='first',seconds=30);self.client.candles.reset_mock()
        for options,seconds,tickers in (({'p26_continuation':'resume'},30,['KRW-BTC']),({'p26_continuation':'first'},31,['KRW-BTC']),
                                        ({'p26_continuation':'first'},30,['KRW-ETH']),
                                        ({'p26_continuation':'first','quarantine_timestamp_regressions':True},30,['KRW-BTC'])):
            with self.subTest(options=options,seconds=seconds):
                try:
                    r=run_session(self.state,seconds=seconds,stopped=self.stop.is_set,emit=self.emit,client=self.client,
                        stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=tickers,
                        experiment={'session_id':token,'observe_realtime_seconds':1,**options},
                        wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
                    self.assertFalse(r['engine_started'])
                except ValueError:pass
                self.client.candles.assert_not_called()

    def test_resume_with_concurrent_or_stale_marker_is_blocked(self):
        r1,e1,t1=self.session('first',self.RUN1,['BUY'])
        m1=(self.experiment/'session.json').read_bytes()
        self.wall+=2;t2=exp.begin(self.experiment,p26='resume',seconds=30)
        with self.assertRaises(ValueError):exp.begin(self.experiment,p26='resume',seconds=30)  # IN_PROGRESS: concurrent resume.
        r2,e2,_=self.session('resume',self.run2(),['SELL'],token=t2)
        self.assertEqual(e2['status'],'STOPPED_VERIFIED')
        current=(self.experiment/'session.json').read_bytes()
        (self.experiment/'session.json').write_bytes(m1)  # Restore an old checkpoint to try to regain a run.
        with self.assertRaises(ValueError):exp.begin(self.experiment,p26='resume',seconds=30)
        (self.experiment/'session.json').write_bytes(current)

    def test_cli_and_init_require_explicit_fixed_profile(self):
        from ops.paper_bot import main as bot
        flag='--p26-first-run';base=['--connect-public','--seconds','30','--observe-realtime-seconds','1']
        bad=[[flag],base+[flag],base+['--tickers','KRW-ETH',flag],base+['--tickers','KRW-BTC',flag,'--p26-resume'],
             base+['--tickers','KRW-BTC',flag,'--quarantine-timestamp-regressions'],
             ['--connect-public','--seconds','30','--tickers','KRW-BTC',flag]]
        with patch.object(exp,'begin',side_effect=AssertionError('No reservation')):
            for args in bad:
                with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)
        target=self.parent/'p26-cli'
        with patch('sys.stdout',new_callable=io.StringIO) as out:self.assertEqual(bot(['init','--directory',str(target),'--profile','p26']),0)
        receipt=json.loads((target/'experiment.json').read_bytes())
        self.assertEqual((receipt['schema_version'],receipt['identity']['profile']['id']),(2,cont.PROFILE_ID))
        self.assertEqual(receipt['identity']['cost_policy'],cont.cost_policy())
        other=self.parent/'p26-wrong-cost'
        with patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(bot(['init','--directory',str(other),'--profile','p26','--fee-bps','4','--slippage-bps','5',
                                  '--full-spread-bps','2']),2)
        self.assertFalse(other.exists())
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):bot(['report','--directory',str(target),'--profile','p26'])
        with patch('sys.stdout',new_callable=io.StringIO) as out:bot(['check','--directory',str(target)])
        self.assertTrue(json.loads(out.getvalue())['p26_account']['first_run_allowed'])

    def test_eight_line_summary_separates_session_and_account_values(self):
        import tempfile
        from pathlib import Path
        from ops.paper_bot import read_report,view
        from tools.p26_paper_summary import main
        r1,e1,t1,r2,e2,t2=self.first_then_resume();report=read_report(self.experiment)
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'run.log';rep=Path(d)/'report.json'
            log.write_text('\n'.join(json.dumps(view(x)) for x in self.reports+[r2,e2])+'\n',encoding='utf-8')
            rep.write_text(json.dumps(report),encoding='utf-8')
            with patch('sys.stdout',new_callable=io.StringIO) as out:code=main([str(log),str(rep),'0','0'])
        text=out.getvalue();lines=text.splitlines()
        self.assertEqual(code,0);self.assertLessEqual(len(lines),8)
        for needle in ('run=2/2','reserved_s=60','remaining_s=7140','rest=128/128','discards session/account=1/3',
                       'buys/sells=1/1','costs=5/5/2','resume_allowed=False','public SELL settled=YES'):
            self.assertIn(needle,text)
        self.assertNotIn(POISON,text);self.assertNotIn(str(self.parent),text)

    def test_tampered_high_water_in_real_stopped_marker_is_rejected_before_reservation(self):
        """Review 5761233853: lowering or deleting the carried high-water must not pass resume."""
        r1,e1,t1=self.session('first',self.RUN1,['BUY']);self.assertEqual(e1['status'],'STOPPED_VERIFIED')
        path=self.experiment/'session.json';good=path.read_bytes()
        hw=json.loads(good)['p26_checkpoint']['high_water_ms']['KRW-BTC']
        for name,value in (('lowered',{'KRW-BTC':hw-1}),('tiny',{'KRW-BTC':1}),('deleted',{})):
            with self.subTest(case=name):
                bad=json.loads(good);bad['p26_checkpoint']['high_water_ms']=value;path.write_text(json.dumps(bad))
                before=(path.read_bytes(),self.state.read_bytes(),exp.first_use_path(self.experiment).read_bytes())
                with self.assertRaises(ValueError):exp.begin(self.experiment,p26='resume',seconds=30)
                self.assertEqual(before,(path.read_bytes(),self.state.read_bytes(),exp.first_use_path(self.experiment).read_bytes()))
        path.write_bytes(good)
        # The untampered checkpoint still carries normally: below/equal/advance behave as before.
        self.wall+=2;r2,e2,_=self.session('resume',self.run2(),['SELL']);self.assertEqual(e2['status'],'STOPPED_VERIFIED')
        self.assertEqual(r2['p26_account']['session_discards'],1)

    def test_documented_summary_command_runs_without_pythonpath(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        doc=(root/'docs/P26_MANUAL_PAPER_CONTINUATION.md').read_text(encoding='utf-8')
        self.assertNotIn('tools/p26_paper_summary.py',doc)
        self.assertEqual(doc.count('.venv/bin/python -m tools.p26_paper_summary '),2)
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'run.log';rep=Path(d)/'report.json'
            log.write_text(json.dumps({'status':'STOPPED','p26_account':{'run_index':1,'max_runs':2}})+'\n',encoding='utf-8')
            rep.write_text(json.dumps({'session_state':'STOPPED_VERIFIED','summary':{'cumulative_sells':0}}),encoding='utf-8')
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
            out=subprocess.run([sys.executable,'-m','tools.p26_paper_summary',str(log),str(rep),'0','0'],cwd=root,env=env,
                               capture_output=True,text=True,timeout=60)
        self.assertEqual(out.returncode,0,out.stderr);self.assertEqual(len(out.stdout.splitlines()),8)
        self.assertIn('public SELL settled=NO_NOT_VERIFIED',out.stdout)

    def test_rest_and_session_limits_reuse_existing_per_session_client_cap(self):
        import inspect
        from ops import paper_watch
        self.assertIn('max_calls=64 if experiment is not None',inspect.getsource(paper_watch._worker))
        self.assertEqual((cont.REST_PER_SESSION*cont.MAX_RUNS,cont.MAX_REST_TOTAL),(128,128))
        from services.tick_timing import DISCARD_SESSION_SECONDS
        self.assertEqual(DISCARD_SESSION_SECONDS,3600)  # P25/global 3600 s unchanged.


if __name__=='__main__':unittest.main()
