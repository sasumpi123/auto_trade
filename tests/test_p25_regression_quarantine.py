"""P25 regression quarantine. Only the -31 ms width comes from the user's report; every surrounding
tick, price and follow-up here is SYNTHETIC. Proves the input contract, not Upbit behaviour."""
import copy
import inspect
import io
import json
import os
import unittest
from decimal import Decimal,localcontext
from unittest.mock import Mock,patch

from services.tick_timing import (TickTiming,TickTimingError,discard_policy,REPEAT_POLICY,QUARANTINE_POLICY,
                                  BOUND_POLICIES,STRICT_POLICY,P15_POLICY)

WALL=1800000000
POISON='POISON_NO_PRICE_P25'


def frame(stamp,code='KRW-BTC',price=POISON):
    return {'type':'ticker','code':code,'timestamp':WALL*1000+stamp,'trade_price':price}


class QuarantineTimingTests(unittest.TestCase):
    def guard(self,markets=('KRW-BTC',)):return TickTiming(list(markets),WALL,0,quarantine_regressions=True)
    def p18(self):return TickTiming(['KRW-BTC'],WALL,0,discard_small_regressions=True,discard_repeated_small_regressions=True)
    def send(self,g,stamp,at,code='KRW-BTC'):return g.observe(frame(stamp,code),WALL+at,at)

    def test_minus_31ms_halts_p18_but_is_quarantined_then_released_by_newer_followup(self):
        g=self.p18();self.send(g,1000,.1)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,969,.2)  # Existing P18 contract.
        self.assertEqual(g.discarded,0)
        g=self.guard();self.assertEqual(self.send(g,1000,.1),'KRW-BTC')
        before=(dict(g.previous),dict(g.progress),g.last_progress,g.accepted)
        self.assertIsNone(self.send(g,969,.2))
        self.assertEqual(before,(g.previous,g.progress,g.last_progress,g.accepted))  # High-water/watchdog untouched.
        self.assertEqual((g.awaiting_followup,g.discarded),('KRW-BTC',1))
        self.assertEqual(self.send(g,1001,.3),'KRW-BTC')
        self.assertIsNone(g.awaiting_followup);self.assertTrue(g.discards['KRW-BTC']['followup_newer_received'])
        self.assertEqual(g.discards['KRW-BTC']['max_regression_ms'],31)

    def test_width_is_not_a_decision_but_validity_checks_still_apply(self):
        def late():return TickTiming(['KRW-BTC'],WALL+40,40,quarantine_regressions=True)  # Starts 40 s in.
        for width in (1,5,6,31,500,30_000):
            with self.subTest(width=width):
                g=late();self.send(g,40_100,40.1)
                self.assertIsNone(self.send(g,40_100-width,40.2));self.assertEqual(g.discarded,1)
        g=late();self.send(g,40_100,40.1)  # Too old in absolute terms: the delay check fires before quarantine.
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,40_200-60_001,40.2)
        self.assertEqual(g.discarded,0)
        for stamp,at,wall_delta,root in ((6000,.1,.1,'PAPER_STALE_TICK'),(98,6,0,'PAPER_CLOCK_DISCONTINUITY')):
            g=self.guard();self.send(g,100,.05)
            with self.assertRaisesRegex(TickTimingError,root):g.observe(frame(stamp),WALL+wall_delta,at)
            self.assertEqual(g.discarded,0)
        g=self.guard()
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):g.observe(dict(frame(100),timestamp=1.5),WALL+.1,.1)

    def test_equal_timestamp_and_other_market_do_not_release_or_forward(self):
        g=self.guard(('KRW-BTC','KRW-ETH'));self.send(g,1000,.1);self.send(g,969,.2)
        self.assertIsNone(self.send(g,1000,.3))  # Equal to high-water: held, not a release.
        self.assertEqual(g.held,1);self.assertEqual(g.progress['KRW-BTC'],.1)
        self.assertEqual(self.send(g,1300,.4,'KRW-ETH'),'KRW-ETH')  # Other market flows; not BTC's release.
        self.assertEqual(g.awaiting_followup,'KRW-BTC');self.assertEqual(g.report()['regression_discard']['quarantined_markets'],['KRW-BTC'])
        self.assertEqual(self.send(g,1001,.5),'KRW-BTC');self.assertIsNone(g.awaiting_followup)

    def test_repeated_regressions_budget_market_ten_session_thirty_no_refund(self):
        g=self.guard()
        self.send(g,100,.01)
        for n in range(10):self.assertIsNone(self.send(g,69-n,.02+n*.01))  # Repeated while quarantined.
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,50,.2)
        self.assertEqual(g.discarded,10)
        codes=['KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL'];g=self.guard(codes)
        for i,t in enumerate(codes[:3]):
            for n in range(10):
                at=(i*10+n)*.01+.001;stamp=100+n*10
                self.send(g,stamp,at,t);self.assertIsNone(self.send(g,stamp-31,at+.002,t));self.send(g,stamp+1,at+.004,t)
        self.send(g,100,.5,codes[3])
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,69,.6,codes[3])
        self.assertEqual(g.discarded,30)

    def test_expired_watchdog_and_session_window_take_precedence(self):
        g=self.guard();self.send(g,100,.1)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.send(g,69,15.1)
        self.assertEqual(g.discarded,0)
        g=self.guard();self.send(g,3_599_000,3599.999);self.assertIsNone(self.send(g,3_598_969,3599.9995))
        g=self.guard();self.send(g,3_600_000,3600)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,3_599_969,3600.0005)
        g=self.guard();self.send(g,100,.1);self.send(g,69,.2)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):g.idle(WALL+15.1,15.1)
        g=TickTiming(['KRW-BTC','KRW-ETH'],WALL,0,quarantine_regressions=True);self.send(g,100,.1);self.send(g,69,.2)
        for n in range(1,61):self.send(g,n*1000,n,'KRW-ETH')
        with self.assertRaisesRegex(TickTimingError,'PAPER_MARKET_STALE'):self.send(g,61_000,61,'KRW-ETH')

    def test_release_tick_cannot_erase_expired_15s_watchdog(self):
        """Review 5748275358: a delayed advancing follow-up must not reset an already-expired deadline."""
        for at,ok in ((15.099,True),(15.1,False),(15.101,False)):
            with self.subTest(at=at):
                g=self.guard();self.send(g,100,.1);self.assertIsNone(self.send(g,69,.2))
                if ok:
                    self.assertEqual(self.send(g,101,at),'KRW-BTC');self.assertIsNone(g.awaiting_followup)
                else:
                    before=(dict(g.previous),dict(g.progress),g.last_progress,g.accepted)
                    with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.send(g,101,at)
                    self.assertEqual(before,(g.previous,g.progress,g.last_progress,g.accepted))  # Nothing refreshed/forwarded.
                    self.assertEqual(g.awaiting_followup,'KRW-BTC')

    def test_release_tick_cannot_erase_expired_60s_market_watchdog_with_other_market_active(self):
        for at,ok in ((60.099,True),(60.1,False),(60.101,False)):
            with self.subTest(at=at):
                g=self.guard(('KRW-BTC','KRW-ETH'));self.send(g,100,.1);self.assertIsNone(self.send(g,69,.2))
                for n in range(1,61):self.assertEqual(self.send(g,n*1000,n-.5,'KRW-ETH'),'KRW-ETH')  # Global healthy.
                if ok:self.assertEqual(self.send(g,60_000,at),'KRW-BTC')
                else:
                    with self.assertRaisesRegex(TickTimingError,'PAPER_MARKET_STALE'):self.send(g,60_000,at)
                    self.assertEqual(g.awaiting_followup,'KRW-BTC');self.assertEqual(g.progress['KRW-BTC'],.1)

    def test_observation_measurement_contract_and_old_modes_keep_legacy_ordering(self):
        g=self.guard();self.send(g,100,.1);g.observe(frame(69),WALL+.2,.2,check_liveness=False)
        self.assertEqual(g.observe(frame(101),WALL+20,20,check_liveness=False),'KRW-BTC')  # Observation disables liveness.
        for kw in ({},{'discard_small_regressions':True},{'discard_small_regressions':True,'discard_repeated_small_regressions':True}):
            g=TickTiming(['KRW-BTC'],WALL,0,**kw);self.send(g,100,.1)
            self.assertEqual(self.send(g,101,15.1),'KRW-BTC')  # Unchanged legacy order: refresh, then check.

    def test_policy_is_exclusive_immutable_and_old_evidence_unchanged(self):
        from dataclasses import FrozenInstanceError
        for args in ((True,False,True),(True,True,True),(False,True,True),(False,False,1)):
            with self.assertRaises(ValueError):discard_policy(*args)
        with self.assertRaises(ValueError):TickTiming(['KRW-BTC'],WALL,0,discard_small_regressions=True,quarantine_regressions=True)
        with self.assertRaises(FrozenInstanceError):QUARANTINE_POLICY.max_per_market=100
        self.assertEqual(REPEAT_POLICY.evidence(),{'id':'P18_BOUNDED_REPEAT_V1','max_per_market':10,'max_per_session':30,
            'max_width_ms':5,'max_session_seconds':3600})
        self.assertEqual(QUARANTINE_POLICY.evidence(),{'id':'P25_QUARANTINE_UNTIL_ADVANCE_V1','max_per_market':10,
            'max_per_session':30,'max_width_ms':None,'max_session_seconds':3600,'release':'SAME_MARKET_TIMESTAMP_ABOVE_HIGH_WATER'})
        self.assertEqual(set(BOUND_POLICIES),{'P18_BOUNDED_REPEAT_V1','P25_QUARANTINE_UNTIL_ADVANCE_V1','P26_ACCOUNT_QUARANTINE_V1'})
        self.assertEqual((discard_policy(),discard_policy(True),discard_policy(True,True)),(STRICT_POLICY,P15_POLICY,REPEAT_POLICY))

    def test_unselected_modes_behave_exactly_as_before(self):
        for kw,expect_discard in (({},False),({'discard_small_regressions':True},True),
                                  ({'discard_small_regressions':True,'discard_repeated_small_regressions':True},True)):
            g=TickTiming(['KRW-BTC'],WALL,0,**kw);self.send(g,100,.1)
            if expect_discard:self.assertIsNone(self.send(g,98,.2))
            else:
                with self.assertRaises(TickTimingError):self.send(g,98,.2)
            r=g.report()['regression_discard']
            self.assertNotIn('quarantined_markets',r);self.assertNotIn('release',r)
            g=TickTiming(['KRW-BTC'],WALL,0,**kw);self.send(g,100,.1)
            with self.assertRaises(TickTimingError):self.send(g,69,.2)  # -31 ms still halts every old mode.
            g=TickTiming(['KRW-BTC'],WALL,0,**kw);self.send(g,100,.1);self.assertEqual(self.send(g,100,.2),'KRW-BTC')  # Equal accepted.

    def test_observation_and_runtime_share_one_guard_and_evidence_validates(self):
        from services.realtime_activity import Observation,ActivityError,timing_evidence,validate_regression_evidence
        now=[0]
        o=Observation(['KRW-BTC'],wall=lambda:WALL+now[0],monotonic=lambda:now[0],clock_origin=(WALL,0),quarantine_regressions=True)
        rows=iter([(.1,100),(.2,69),(.3,101)])
        class Stream:
            def get(self):
                try:at,stamp=next(rows)
                except StopIteration:now[0]=1;raise TimeoutError()
                now[0]=at;return dict(frame(stamp,price=100),stream_type='REALTIME')
        o.observe(Stream(),1,deadline=30,stopped=lambda:False)
        g=o.runtime_guard(['KRW-BTC'],(WALL,0),quarantine_regressions=True)
        self.assertIs(g,o.guard);self.assertEqual(g.discarded,1)
        for kw in ({},{'discard_small_regressions':True},{'discard_small_regressions':True,'discard_repeated_small_regressions':True}):
            with self.assertRaises(ActivityError):o.runtime_guard(['KRW-BTC'],(WALL,0),**kw)
        ev=timing_evidence(g.report());self.assertEqual(ev['policy'],QUARANTINE_POLICY.evidence())
        value={'observation':ev,'combined':ev,'runtime_started':True,'runtime_enabled':True,'runtime_discarded':0}
        validate_regression_evidence(value)
        for mutate in ('width_cap','release','downgrade','raw'):
            bad=copy.deepcopy(value)
            if mutate=='width_cap':bad['combined']['policy']['max_width_ms']=5
            elif mutate=='release':del bad['observation']['policy']['release']
            elif mutate=='downgrade':bad['combined']['policy']=REPEAT_POLICY.evidence()
            else:bad['combined']['raw']=POISON
            with self.assertRaises(ValueError):validate_regression_evidence(bad)


from ops import paper_experiment as exp
from ops.paper_session import run_session
import tests.test_p10h_realtime_activity as fixtures


@unittest.skipUnless(os.name=='posix','Existing independent PAPER ownership is POSIX only')
class QuarantineSessionTests(unittest.TestCase):
    """Fake public stream -> real PAPER engine -> P17 5/5/2 bps -> P9 accounting."""
    factory=fixtures.ActivityTests.factory
    make_stream=fixtures.ActivityTests.make_stream
    tearDown=fixtures.ActivityTests.tearDown  # Verifies protected legacy/HALTED accounts and backups unchanged.
    def setUp(self):
        fixtures.ActivityTests.setUp(self)
        for p in self.experiment.iterdir():
            if p.is_file():self.before[p]=p.read_bytes()
        self.before[exp.receipt_path(self.experiment)]=exp.receipt_path(self.experiment).read_bytes()
        self.experiment=self.parent/'p25-cost';self.state=self.experiment/exp.STATE
        self.policy={'schema_version':1,'fee_bps':'5','slippage_bps':'5','full_spread_bps':'2'}
        self.assertEqual(exp.create(self.experiment,cost_policy=self.policy)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        self.initial=self.state.read_bytes();backup=self.parent/'p25-initial-backup.json';backup.write_bytes(self.initial)
        self.before[backup]=backup.read_bytes()
        self.actions=['BUY','SELL'];self.now=0;self.runtime=False;self.phase='split';self.width=31
        self.missing_followup=False;self.interrupt=False;self.token=None;self.prices=[];self.bound='P25_QUARANTINE_UNTIL_ADVANCE_V1'
        owner=self
        class Stream:
            def __init__(self,codes):self.codes=codes;self.pre=0;self.run=0
            def connect(self):
                used=json.loads(exp.first_use_path(owner.experiment).read_bytes());m=exp.marker(owner.experiment)
                owner.assertEqual(used.get('discard_policy'),owner.bound);owner.assertEqual(m.get('discard_policy'),owner.bound)
            def close(self):pass
            def get(self):
                if not owner.runtime:
                    self.pre+=1;i=self.pre
                    events=[(.1,100,10000),(.2,100-owner.width,POISON),(.3,100,POISON),(.35,101,10000),(.4,200,10000)]
                    if owner.phase=='observation':events+=[(.5,200-owner.width,POISON)]+([] if owner.missing_followup else [(.6,201,10000)])
                    if i>len(events):owner.now=1;raise TimeoutError()
                    at,stamp,price=events[i-1]
                    if owner.interrupt and i==3:owner.stop.set()
                else:
                    self.run+=1;i=self.run
                    events=[(1.1,1100,10000)]
                    if owner.phase=='split':events+=[(1.2,1100-owner.width,POISON),(1.25,1100,POISON)]
                    if owner.phase=='late_followup':events+=[(1.2,1100-owner.width,POISON),(16.1,1300,10010)]
                    if not owner.missing_followup and owner.phase!='late_followup':events+=[(1.3,1300,10010),(1.4,1400,10010)]
                    if i>len(events):owner.now=1.5;owner.stop.set();raise TimeoutError()
                    at,stamp,price=events[i-1]
                owner.now=at
                return {'type':'ticker','code':'KRW-BTC','stream_type':'REALTIME','timestamp':int(owner.wall*1000)+stamp,'trade_price':price}
        self.stream_factory=Stream
    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True
    def options(self,token,**extra):
        return {'session_id':token,'observe_realtime_seconds':1,'quarantine_timestamp_regressions':True,**extra}
    def worker(self,token,options=None,finish=True):
        from trading.auto_trade import AutoTrade
        original=AutoTrade.process_market_data
        def observe(bot,t,data):
            self.prices.append(data['trade_price']);return original(bot,t,data)
        with patch.object(AutoTrade,'process_market_data',new=observe):
            r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.emit,client=self.client,
                stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],
                experiment=options or self.options(token),wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return r,exp.finish(self.experiment,token,r,record_summary=True) if finish else None
    def run_case(self,finish=True):
        self.token=exp.begin(self.experiment,observation_discard=True,quarantine=True)
        return self.worker(self.token,finish=finish)

    def test_minus_31ms_paper_trades_with_costs_and_reconciles_without_poison(self):
        from ops.paper_bot import read_report,view
        r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED',r);self.assertEqual(r['orders'],2);self.assertEqual(r['pending_orders'],0)
        e=r['regression_evidence']
        self.assertEqual((e['observation']['discarded'],e['combined']['discarded'],e['runtime_discarded']),(1,2,1))
        self.assertEqual(e['combined']['policy'],QUARANTINE_POLICY.evidence())
        self.assertEqual(e['combined']['markets']['KRW-BTC']['width_ms'],31)
        self.assertTrue(e['combined']['markets']['KRW-BTC']['followup_newer_received']);self.assertIsNone(e['combined']['awaiting_followup'])
        self.assertNotIn(POISON,self.prices);self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10010')
        rd=r['tick_timing']['regression_discard']
        self.assertEqual((rd['policy_id'],rd['quarantine_held'],rd['quarantined_markets']),('P25_QUARANTINE_UNTIL_ADVANCE_V1',2,[]))
        saved=self.state.read_bytes();report=read_report(self.experiment)
        self.assertEqual(report['regression_evidence'],view(r)['regression_evidence']);self.assertEqual(saved,self.state.read_bytes())
        self.assertEqual(report['summary']['positions'],{});self.assertEqual(report['summary']['pending_orders'],0)
        with localcontext() as ctx:
            ctx.prec=60
            expected=(Decimal(400000)/Decimal('1.0005')/Decimal(10006)*Decimal('10003.994')*Decimal('.9995'))-400000
        self.assertAlmostEqual(Decimal(report['summary']['net_realized_pnl']),expected,delta=Decimal('1e-20'))
        self.assertGreater(Decimal(report['summary']['fees']),0);self.assertNotIn(POISON,json.dumps(report))

    def test_eight_line_summary_from_actual_session_outputs(self):
        import tempfile
        from pathlib import Path
        from ops.paper_bot import read_report,view
        from tools.p25_paper_summary import main
        r,end=self.run_case();report=read_report(self.experiment)
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'run.log';rep=Path(d)/'report.json'
            log.write_text('\n'.join(json.dumps(view(x)) for x in self.reports+[r,end])+'\n',encoding='utf-8')
            rep.write_text(json.dumps(report),encoding='utf-8');out=io.StringIO()
            with patch('sys.stdout',out):code=main([str(log),str(rep),'0','0'])
        lines=out.getvalue().splitlines()
        self.assertEqual(code,0);self.assertLessEqual(len(lines),8)
        text=out.getvalue()
        for needle in ('P25_QUARANTINE_UNTIL_ADVANCE_V1','market/session/seconds=10/30/3600','width_limit=None',
                       'discarded obs/runtime/total=1/1/2','followup=True','buys/sells=1/1','fee/slip/spread_bps=5/5/2'):
            self.assertIn(needle,text)
        self.assertNotIn(POISON,text);self.assertNotIn(str(self.parent),text)

    def test_delayed_get_returning_followup_after_15s_halts_without_forwarding(self):
        self.phase='late_followup';r,end=self.run_case()
        self.assertEqual((r['status'],r['reason'],end['status']),('HALTED','PAPER_STREAM_TIMEOUT','HALTED'))
        self.assertEqual(r['orders'],1)  # Only the earlier BUY; the late follow-up never reached strategy/orders.
        self.assertNotIn(10010,self.prices);self.assertNotIn(POISON,self.prices)
        self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10000')
        self.assertEqual(r['tick_timing']['regression_discard']['quarantined_markets'],['KRW-BTC'])

    def test_same_minus_31ms_still_halts_existing_p18_mode(self):
        self.bound='P18_BOUNDED_REPEAT_V1'
        token=exp.begin(self.experiment,observation_discard=True,repeated_discard=True)
        options={'session_id':token,'observe_realtime_seconds':1,'discard_small_timestamp_regressions':True,
                 'discard_small_timestamp_regressions_during_observation':True,'discard_repeated_small_timestamp_regressions':True}
        r,end=self.worker(token,options)
        self.assertEqual((r['reason'],end['status']),('PAPER_STALE_TICK','PREPARATION_FAILED'))
        self.assertEqual(self.initial,self.state.read_bytes());self.assertNotIn(POISON,self.prices)

    def test_numeric_poison_never_reaches_risk_orders_or_valuation(self):
        global POISON
        old=POISON;POISON=987654321
        try:
            r,end=self.run_case()
            self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertNotIn(987654321,self.prices)
            self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10010')
        finally:POISON=old

    def test_two_observation_quarantines_skip_poison_price_parsing(self):
        from services.realtime_activity import positive
        self.phase='observation'
        with patch('services.realtime_activity.positive',wraps=positive) as parse:r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['regression_evidence']['observation']['discarded'],2)
        self.assertNotIn(POISON,[c.args[0] for c in parse.call_args_list])

    def test_unresolved_runtime_quarantine_halts_without_restart(self):
        self.missing_followup=True;r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED');self.assertEqual(r['orders'],1)
        for kw in ({},{'observation_discard':True},{'observation_discard':True,'quarantine':True}):
            with self.assertRaises(ValueError):exp.begin(self.experiment,**kw)

    def test_unresolved_observation_quarantine_is_preparation_failure_without_refill(self):
        self.phase='observation';self.missing_followup=True;r,end=self.run_case()
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertFalse(r['engine_started'])
        self.assertEqual(self.initial,self.state.read_bytes())
        before=(self.experiment/'session.json').read_bytes()
        for kw in ({},{'observation_discard':True},{'observation_discard':True,'quarantine':True},
                   {'observation_discard':True,'repeated_discard':True}):
            with self.assertRaises(ValueError):exp.begin(self.experiment,**kw)
            self.assertEqual(before,(self.experiment/'session.json').read_bytes())

    def test_interruption_consumes_first_attempt(self):
        self.interrupt=True;r,end=self.run_case()
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(r['reason'],'ACTIVITY_OBSERVATION_INTERRUPTED')
        with self.assertRaises(ValueError):exp.begin(self.experiment,observation_discard=True,quarantine=True)

    def test_binding_loss_mismatch_or_mixing_blocks_before_network(self):
        token=exp.begin(self.experiment,observation_discard=True,quarantine=True)
        used_path=exp.first_use_path(self.experiment);marker_path=self.experiment/'session.json'
        used_raw=used_path.read_bytes();marker_raw=marker_path.read_bytes()
        self.client.candles.reset_mock()
        for target,raw in ((used_path,used_raw),(marker_path,marker_raw)):
            for value in (None,'P18_BOUNDED_REPEAT_V1','P15_SINGLE_DISCARD'):
                bad=json.loads(raw)
                if value is None:del bad['discard_policy']
                else:bad['discard_policy']=value
                target.write_text(json.dumps(bad));before=self.state.read_bytes()
                r,_=self.worker(token,finish=False)
                self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()
                self.assertEqual(before,self.state.read_bytes());target.write_bytes(raw)
        p18={'session_id':token,'observe_realtime_seconds':1,'discard_small_timestamp_regressions':True,
             'discard_small_timestamp_regressions_during_observation':True,'discard_repeated_small_timestamp_regressions':True}
        for options in (p18,{'session_id':token,'observe_realtime_seconds':1}):  # Downgrade attempts.
            r,_=self.worker(token,options,finish=False)
            self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()

    def test_used_or_existing_accounts_get_no_new_policy_or_budget(self):
        token=exp.begin(self.experiment)
        with self.assertRaises(ValueError):exp.begin(self.experiment,observation_discard=True,quarantine=True)
        r,_=self.worker(token,finish=False)  # A plain session token cannot carry the P25 policy.
        self.assertFalse(r['engine_started'])
        fresh=self.parent/'p25-fresh'
        self.assertEqual(exp.create(fresh,cost_policy=self.policy)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        for kw in ({'quarantine':True},{'quarantine':True,'observation_discard':True,'repeated_discard':True},
                   {'quarantine':1,'observation_discard':True}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):exp.begin(fresh,**kw)
            self.assertFalse(exp.first_use_path(fresh).exists())  # Invalid selection reserves nothing.

    def test_persisted_evidence_cannot_be_downgraded(self):
        from ops.paper_bot import read_report
        r,end=self.run_case();self.assertEqual(end['status'],'STOPPED_VERIFIED')
        m=exp.marker(self.experiment)
        for mutate in ('drop','p18'):
            bad=copy.deepcopy(m)
            for stage in ('observation','combined'):
                if mutate=='drop':del bad['regression_checkpoint']['evidence'][stage]['policy']
                else:bad['regression_checkpoint']['evidence'][stage]['policy']=REPEAT_POLICY.evidence()
            (self.experiment/'session.json').write_text(json.dumps(bad))
            with self.assertRaises(ValueError):read_report(self.experiment)
        (self.experiment/'session.json').write_text(json.dumps(m));read_report(self.experiment)


class QuarantineOptionTests(unittest.TestCase):
    def test_cli_requires_explicit_exclusive_observed_fixed_ticker_selection(self):
        flag='--quarantine-timestamp-regressions'
        base=['--connect-public','--seconds','30','--observe-realtime-seconds','1','--tickers','KRW-BTC']
        bad=[[flag],['--create',flag],['--connect-public','--seconds','30','--tickers','KRW-BTC',flag],
             ['--connect-public','--seconds','30','--observe-realtime-seconds','1','--top','1',flag],
             base+[flag,'--discard-small-timestamp-regressions'],base+[flag,'--discard-small-timestamp-regressions-during-observation'],
             base+[flag,'--discard-small-timestamp-regressions','--discard-small-timestamp-regressions-during-observation',
                   '--discard-repeated-small-timestamp-regressions'],
             ['--connect-public','--seconds','3601','--observe-realtime-seconds','1','--tickers','KRW-BTC',flag]]
        with patch.object(exp,'begin',side_effect=AssertionError('No reservation')):
            for args in bad:
                with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)
        seen={}
        def begin(p,**kw):seen.update(kw);raise ValueError('stop here')
        with patch.object(exp,'begin',side_effect=begin),patch.object(exp,'directory',side_effect=lambda v:v),\
             patch('sys.stdout',new_callable=io.StringIO):
            self.assertEqual(exp.main(base+[flag,'--directory','/nonexistent-p25']),2)
        self.assertEqual(seen,{'observation_discard':True,'quarantine':True})

    def test_programmatic_mixing_or_missing_preconditions_fail_before_client_or_engine(self):
        for options in ({'quarantine_timestamp_regressions':True},{'quarantine_timestamp_regressions':1,'observe_realtime_seconds':1},
                        {'quarantine_timestamp_regressions':True,'observe_realtime_seconds':1,'discard_small_timestamp_regressions':True},
                        {'quarantine_timestamp_regressions':True,'observe_realtime_seconds':1,'eligible_top':1}):
            client=Mock();stream=Mock()
            with self.assertRaises(ValueError):run_session('not-opened',seconds=30,stopped=lambda:False,emit=lambda _:None,
                client=client,stream_factory=stream,tickers=['KRW-BTC'],experiment=options)
            client.candles.assert_not_called();stream.assert_not_called()
        client=Mock()
        with self.assertRaises(ValueError):run_session('not-opened',seconds=3601,stopped=lambda:False,emit=lambda _:None,client=client,
            stream_factory=Mock(),tickers=['KRW-BTC'],experiment={'quarantine_timestamp_regressions':True,'observe_realtime_seconds':1})
        with self.assertRaises(ValueError):run_session('not-opened',seconds=30,stopped=lambda:False,emit=lambda _:None,client=client,
            stream_factory=Mock(),tickers=None,experiment={'quarantine_timestamp_regressions':True,'observe_realtime_seconds':1})


if __name__=='__main__':unittest.main()
