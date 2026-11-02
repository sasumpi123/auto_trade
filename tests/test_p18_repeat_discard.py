"""Bounded-repeat synthetic timestamps only; no captured live account data."""
import inspect
import unittest

from services.tick_timing import TickTiming,TickTimingError


class RepeatTimingTests(unittest.TestCase):
    def guard(self,repeat):
        # Run this same success contract on the baseline before the new option exists.
        kw={'discard_repeated_small_regressions':repeat} if 'discard_repeated_small_regressions' in inspect.signature(TickTiming).parameters else {}
        return TickTiming(['KRW-BTC'],1800000000,0,discard_small_regressions=True,**kw)
    def frame(self,stamp):
        return {'type':'ticker','code':'KRW-BTC','timestamp':1800000000000+stamp,'trade_price':'POISON_NOT_A_PRICE'}
    def send(self,g,stamp,at):return g.observe(self.frame(stamp),1800000000+at,at)
    def test_p15_still_halts_on_second_separated_minus_two(self):
        g=self.guard(False)
        self.send(g,100,.1);self.assertIsNone(self.send(g,98,.2));self.send(g,101,.3);self.send(g,200,.4)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(g,198,.5)
        self.assertEqual(g.discarded,1)
    def test_new_policy_survives_two_separated_minus_two_with_newer_followups(self):
        g=self.guard(True)
        self.send(g,100,.1);self.assertIsNone(self.send(g,98,.2));self.send(g,101,.3);self.send(g,200,.4)
        self.assertIsNone(self.send(g,198,.5));self.send(g,201,.6)
        self.assertEqual(g.discarded,2);self.assertEqual(g.accepted,4)
        self.assertIsNone(g.awaiting_followup);self.assertEqual(g.previous['KRW-BTC'],1800000000201)
    def test_widths_and_immutable_policy_selection_preserve_old_modes(self):
        from dataclasses import FrozenInstanceError
        from services.tick_timing import REPEAT_POLICY
        for width in (1,2,5):
            g=self.guard(True);self.send(g,100,.1)
            before=(dict(g.previous),dict(g.progress),g.last_progress,g.accepted)
            self.assertIsNone(self.send(g,100-width,.2))
            self.assertEqual(before,(g.previous,g.progress,g.last_progress,g.accepted))
        g=self.guard(True);self.send(g,100,.1)
        with self.assertRaises(TickTimingError):self.send(g,94,.2)
        with self.assertRaises(FrozenInstanceError):g.policy.max_per_market=100
        with self.assertRaises(AttributeError):g.policy=REPEAT_POLICY
        with self.assertRaises(ValueError):TickTiming(['KRW-BTC'],1800000000,0,discard_repeated_small_regressions=True)
        strict=TickTiming(['KRW-BTC'],1800000000,0);self.send(strict,100,.1)
        with self.assertRaises(TickTimingError):self.send(strict,98,.2)
    def test_market_tenth_allowed_eleventh_blocked_without_refund(self):
        g=self.guard(True)
        for n in range(10):
            at=n*.1;stamp=100+n*10
            self.send(g,stamp,at);self.assertIsNone(self.send(g,stamp-2,at+.01));self.send(g,stamp+1,at+.02)
        self.assertEqual(g.discarded,10);self.assertIsNone(g.awaiting_followup)
        with self.assertRaises(TickTimingError):self.send(g,190,.999)
        self.assertEqual(g.discarded,10)
    def test_session_thirtieth_allowed_thirty_first_blocked(self):
        codes=['KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL']
        g=TickTiming(codes,1800000000,0,discard_small_regressions=True,discard_repeated_small_regressions=True)
        def send(t,stamp,at):return g.observe({'type':'ticker','code':t,'timestamp':1800000000000+stamp},1800000000+at,at)
        for i,t in enumerate(codes[:3]):
            for n in range(10):
                at=(i*10+n)*.1;stamp=100+n*10
                send(t,stamp,at);self.assertIsNone(send(t,stamp-2,at+.01));send(t,stamp+1,at+.02)
        send(codes[3],100,3.1)
        with self.assertRaises(TickTimingError):send(codes[3],98,3.2)
        self.assertEqual(g.discarded,30);self.assertEqual(len(g.samples),16)
        r=g.report()['regression_discard'];self.assertEqual((r['max_per_market'],r['max_per_session']),(10,30))
    def test_pending_equal_and_other_market_do_not_resolve_or_refresh(self):
        g=self.guard(True);self.send(g,100,.1);self.send(g,98,.2);self.send(g,100,.3)
        self.assertEqual(g.awaiting_followup,'KRW-BTC');self.assertEqual(g.progress['KRW-BTC'],.1)
        with self.assertRaises(TickTimingError):self.send(g,99,.4)
        g=TickTiming(['KRW-BTC','KRW-ETH'],1800000000,0,discard_small_regressions=True,discard_repeated_small_regressions=True)
        self.send(g,100,.1);self.send(g,98,.2)
        g.observe({'type':'ticker','code':'KRW-ETH','timestamp':1800000000300},1800000000.3,.3)
        self.assertEqual(g.awaiting_followup,'KRW-BTC')
    def test_expiry_delay_future_and_clock_errors_remain_fail_closed(self):
        for stamp,at,wall_delta,root in ((-60001,.1,.1,'PAPER_STALE_TICK'),(6000,.1,.1,'PAPER_STALE_TICK'),
                (98,15.1,15.1,'PAPER_STREAM_TIMEOUT'),(98,6,0,'PAPER_CLOCK_DISCONTINUITY')):
            g=self.guard(True);self.send(g,100,.1)
            with self.assertRaisesRegex(TickTimingError,root):g.observe(self.frame(stamp),1800000000+wall_delta,at)
            self.assertEqual(g.discarded,0)
        g=self.guard(True);self.send(g,3600000,3600)
        with self.assertRaises(TickTimingError):self.send(g,3599998,3600.001)
        g=self.guard(True);self.send(g,100,.1);self.send(g,98,.2)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):g.idle(1800000015.1,15.1)
    def test_other_market_cannot_hide_market_idle_after_discard(self):
        g=TickTiming(['KRW-BTC','KRW-ETH'],1800000000,0,discard_small_regressions=True,discard_repeated_small_regressions=True)
        self.send(g,100,.1);self.send(g,98,.2);self.send(g,101,.3)
        for n in range(1,61):g.observe({'type':'ticker','code':'KRW-ETH','timestamp':1800000000000+n*1000},1800000000+n,n)
        with self.assertRaisesRegex(TickTimingError,'PAPER_MARKET_STALE'):
            g.observe({'type':'ticker','code':'KRW-ETH','timestamp':1800000061000},1800000061,61)


import os
import json
from decimal import Decimal,localcontext
from unittest.mock import patch,Mock
from ops import paper_experiment as exp
from ops.paper_session import run_session
import tests.test_p10h_realtime_activity as fixtures


@unittest.skipUnless(os.name=='posix','Existing independent PAPER ownership is POSIX only')
class RepeatSessionTests(unittest.TestCase):
    factory=fixtures.ActivityTests.factory
    make_stream=fixtures.ActivityTests.make_stream
    tearDown=fixtures.ActivityTests.tearDown
    def setUp(self):
        fixtures.ActivityTests.setUp(self)
        # Preserve an existing legacy account and backup in addition to inherited HALTED fixtures.
        for p in self.experiment.iterdir():
            if p.is_file():self.before[p]=p.read_bytes()
        self.before[exp.receipt_path(self.experiment)]=exp.receipt_path(self.experiment).read_bytes()
        self.experiment=self.parent/'repeat-cost';self.state=self.experiment/exp.STATE
        self.policy={'schema_version':1,'fee_bps':'5','slippage_bps':'5','full_spread_bps':'2'}
        self.assertEqual(exp.create(self.experiment,cost_policy=self.policy)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        self.initial=self.state.read_bytes();backup=self.parent/'repeat-initial-backup.json';backup.write_bytes(self.initial)
        self.before[backup]=backup.read_bytes()
        self.actions=['BUY','SELL'];self.now=0;self.runtime=False;self.phase='split';self.poison='POISON_NO_PRICE'
        self.missing_followup=False;self.interrupt=False;self.width=2;self.token=None;self.prices=[]
        owner=self
        class Stream:
            def __init__(self,codes):self.codes=codes;self.pre=0;self.run=0
            def connect(self):
                used=json.loads(exp.first_use_path(owner.experiment).read_bytes());m=exp.marker(owner.experiment)
                if owner.new_mode:
                    owner.assertEqual(used['discard_policy'],'P18_BOUNDED_REPEAT_V1')
                    owner.assertEqual(m['discard_policy'],used['discard_policy'])
            def close(self):pass
            def get(self):
                if not owner.runtime:
                    self.pre+=1;i=self.pre
                    events=[(.1,100,10000),(.2,100-owner.width,owner.poison),(.3,101,10000),(.4,200,10000)]
                    if owner.phase=='observation':events +=[(.5,200-owner.width,owner.poison)] + ([] if owner.missing_followup else [(.6,201,10000)])
                    if i>len(events):owner.now=1;raise TimeoutError()
                    at,stamp,price=events[i-1]
                    if owner.interrupt and i==3:owner.stop.set()
                else:
                    self.run+=1;i=self.run
                    events=[(1.1,1100,10000)]
                    if owner.phase=='split':events += [(1.2,1100-owner.width,owner.poison)]
                    if not owner.missing_followup:events +=[(1.3,1300,10010),(1.4,1400,10010)]
                    if i>len(events):owner.now=1.5;owner.stop.set();raise TimeoutError()
                    at,stamp,price=events[i-1]
                owner.now=at
                return {'type':'ticker','code':'KRW-BTC','stream_type':'REALTIME','timestamp':int(owner.wall*1000)+stamp,
                        'trade_price':price}
        self.stream_factory=Stream
    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True
    def run_case(self,new=True,finish=True):
        self.new_mode=new;self.token=exp.begin(self.experiment,observation_discard=True,repeated_discard=new)
        return self.worker(self.token,new,finish)
    def worker(self,token,new=True,finish=True):
        from trading.auto_trade import AutoTrade
        original=AutoTrade.process_market_data
        def observe(bot,t,data):
            self.prices.append(data['trade_price']);return original(bot,t,data)
        options={'session_id':token,'observe_realtime_seconds':1,'discard_small_timestamp_regressions':True,
            'discard_small_timestamp_regressions_during_observation':True,'discard_repeated_small_timestamp_regressions':new}
        with patch.object(AutoTrade,'process_market_data',new=observe):
            r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.emit,client=self.client,
                stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],experiment=options,
                wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return r,exp.finish(self.experiment,token,r,record_summary=True) if finish else None
    def test_cost_session_observation_runtime_share_policy_and_reconcile_fees(self):
        from ops.paper_bot import read_report,view
        r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED',r);self.assertEqual(r['orders'],2)
        e=r['regression_evidence'];self.assertEqual(e['observation']['discarded'],1)
        self.assertEqual(e['combined']['discarded'],2);self.assertEqual(e['runtime_discarded'],1)
        self.assertEqual(e['combined']['policy']['max_per_market'],10)
        self.assertEqual(e['combined']['policy']['max_per_session'],30)
        self.assertTrue(e['combined']['markets']['KRW-BTC']['followup_newer_received'])
        self.assertNotIn(self.poison,self.prices);self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10010')
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],2)
        self.assertEqual(r['tick_timing']['timing_accepted_total'],r['session_ticks']+3)
        self.assertLessEqual(len(r['tick_timing']['samples']),16)
        saved=self.state.read_bytes();report=read_report(self.experiment)
        self.assertEqual(report['regression_evidence'],view(r)['regression_evidence'])
        self.assertEqual(saved,self.state.read_bytes());self.assertEqual(report['summary']['positions'],{})
        with localcontext() as ctx:
            ctx.prec=60
            expected=(Decimal(400000)/Decimal('1.0005')/Decimal(10006)*Decimal('10003.994')*Decimal('.9995'))-400000
        self.assertAlmostEqual(Decimal(report['summary']['net_realized_pnl']),expected,delta=Decimal('1e-20'))
        self.assertGreater(Decimal(report['summary']['fees']),0)
        self.assertNotIn(self.poison,json.dumps(report));self.assertEqual(report['summary']['pending_orders'],0)
    def test_p15_still_halts_second_regression_in_same_cost_session(self):
        r,end=self.run_case(False);self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED')
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],1)
        self.assertEqual(r['orders'],1)
    def test_two_observation_discards_skip_poison_price_parsing(self):
        from services.realtime_activity import positive
        self.phase='observation'
        with patch('services.realtime_activity.positive',wraps=positive) as parse:r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['regression_evidence']['observation']['discarded'],2)
        self.assertEqual(parse.call_count,4);self.assertNotIn(self.poison,[c.args[0] for c in parse.call_args_list])
    def test_numeric_poison_cannot_reach_risk_orders_or_valuation(self):
        self.poison=100000000;r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['orders'],2)
        self.assertNotIn(self.poison,self.prices);self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10010')
    def test_unresolved_runtime_followup_stays_halted_and_no_restart(self):
        self.missing_followup=True;r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED')
        self.assertEqual(r['orders'],1)
        with self.assertRaises(ValueError):exp.begin(self.experiment,repeated_discard=True,observation_discard=True)
    def test_preparation_failure_cannot_refill_or_downgrade(self):
        self.phase='observation';self.missing_followup=True;r,end=self.run_case()
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertFalse(r['engine_started'])
        self.assertEqual(self.initial,self.state.read_bytes())
        before=(self.experiment/'session.json').read_bytes()
        for kw in ({},{'observation_discard':True},{'observation_discard':True,'repeated_discard':True}):
            with self.assertRaises(ValueError):exp.begin(self.experiment,**kw)
            self.assertEqual(before,(self.experiment/'session.json').read_bytes())
    def test_interruption_consumes_first_attempt_without_refund(self):
        self.interrupt=True;r,end=self.run_case()
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(self.initial,self.state.read_bytes())
        self.assertEqual(r['reason'],'ACTIVITY_OBSERVATION_INTERRUPTED')
        with self.assertRaises(ValueError):exp.begin(self.experiment)
    def test_reservation_policy_loss_or_downgrade_blocks_before_network(self):
        self.new_mode=True;token=exp.begin(self.experiment,observation_discard=True,repeated_discard=True)
        used_path=exp.first_use_path(self.experiment);marker_path=self.experiment/'session.json'
        used_raw=used_path.read_bytes();marker_raw=marker_path.read_bytes()
        self.client.candles.reset_mock()
        for target,raw in ((used_path,used_raw),(marker_path,marker_raw)):
            for value in (None,'P15_SINGLE_DISCARD'):
                bad=json.loads(raw)
                if value is None:del bad['discard_policy']
                else:bad['discard_policy']=value
                target.write_text(json.dumps(bad));before=self.state.read_bytes()
                r,_=self.worker(token,finish=False)
                self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()
                self.assertEqual(before,self.state.read_bytes());target.write_bytes(raw)
        r,_=self.worker(token,new=False,finish=False)
        self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()
    def test_lost_result_same_token_and_lost_files_cannot_regrant_attempt(self):
        self.phase='observation';self.missing_followup=True;r,_=self.run_case(finish=False)
        self.assertFalse(r['engine_started']);self.now=0;self.stop.clear();self.client.candles.reset_mock()
        r,_=self.worker(self.token,finish=False);self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()
        (self.experiment/'session.json').unlink()
        with self.assertRaises(ValueError):exp.begin(self.experiment,observation_discard=True,repeated_discard=True)
    def test_path_replacement_does_not_create_a_fresh_experiment(self):
        import shutil
        self.interrupt=True;self.run_case()
        shutil.rmtree(self.experiment)  # Disposable synthetic leaf, no user files.
        self.assertEqual(exp.create(self.experiment,cost_policy=self.policy)['status'],'BLOCKED')
        self.assertFalse(self.experiment.exists())
    def test_policy_reserved_before_connection_cannot_restart_after_process_death(self):
        import subprocess,sys
        code="from ops.paper_experiment import begin; import sys,os; begin(sys.argv[1],observation_discard=True,repeated_discard=True); os._exit(9)"
        r=subprocess.run([sys.executable,'-c',code,str(self.experiment)],capture_output=True,timeout=15)
        self.assertEqual(r.returncode,9,r.stderr.decode());self.assertEqual(self.initial,self.state.read_bytes())
        for kw in ({},{'observation_discard':True},{'observation_discard':True,'repeated_discard':True}):
            with self.assertRaises(ValueError):exp.begin(self.experiment,**kw)
    def test_explicit_policy_evidence_rejects_caps_loss_or_raw_payload(self):
        import copy
        from services.realtime_activity import validate_regression_evidence
        r,end=self.run_case();value=r['regression_evidence'];validate_regression_evidence(value)
        for mutate in ('caps','loss','raw','float','mismatch'):
            bad=copy.deepcopy(value)
            if mutate=='caps':bad['combined']['policy']['max_per_market']=11
            elif mutate=='loss':del bad['combined']['policy']
            elif mutate=='float':bad['combined']['policy']['max_per_market']=10.0
            elif mutate=='mismatch':del bad['observation']['policy']
            else:bad['combined']['raw_payload']='MUST_NOT_LEAK'
            with self.assertRaises(ValueError):validate_regression_evidence(bad)


class RepeatOptionTests(unittest.TestCase):
    def test_missing_contradictory_or_non_network_options_fail_before_reservation(self):
        import io
        flag='--discard-repeated-small-timestamp-regressions'
        base=['--connect-public','--seconds','30','--observe-realtime-seconds','1','--tickers','KRW-BTC']
        bad=[[flag],['--create',flag],base+[flag],base+[flag,'--discard-small-timestamp-regressions'],
             base+[flag,'--discard-small-timestamp-regressions-during-observation'],
             ['--connect-public','--seconds','30','--observe-realtime-seconds','1','--top','1',flag,
              '--discard-small-timestamp-regressions','--discard-small-timestamp-regressions-during-observation']]
        with patch.object(exp,'begin',side_effect=AssertionError('No reservation')):
            for args in bad:
                with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)
    def test_runtime_programmatic_flag_mismatch_fails_before_client_or_engine(self):
        from ops.paper_session import run_session
        for options in ({'discard_repeated_small_timestamp_regressions':True},
                        {'discard_repeated_small_timestamp_regressions':1},
                        {'discard_small_timestamp_regressions':True,'observe_realtime_seconds':1,
                         'discard_repeated_small_timestamp_regressions':True}):
            client=Mock();stream=Mock()
            with self.assertRaises(ValueError):run_session('not-opened',seconds=30,stopped=lambda:False,emit=lambda _:None,
                client=client,stream_factory=stream,tickers=['KRW-BTC'],experiment=options)
            client.candles.assert_not_called();client.top_tickers.assert_not_called();stream.assert_not_called()
    def test_observation_guard_transition_keeps_same_policy_counters_and_origin(self):
        from services.realtime_activity import Observation
        now=[0];wall=1800000000
        o=Observation(['KRW-BTC'],wall=lambda:wall+now[0],monotonic=lambda:now[0],
                      clock_origin=(wall,0),discard_small_regressions=True,discard_repeated_small_regressions=True)
        rows=iter([(.1,100),(.2,98),(.3,101),(.4,200),(.5,198),(.6,201)])
        class Stream:
            def get(self):
                try:at,stamp=next(rows)
                except StopIteration:now[0]=1;raise TimeoutError()
                now[0]=at
                return {'type':'ticker','code':'KRW-BTC','stream_type':'REALTIME',
                        'timestamp':wall*1000+stamp,'trade_price':100}
        o.observe(Stream(),1,deadline=30,stopped=lambda:False)
        g=o.runtime_guard(['KRW-BTC'],(wall,0),discard_small_regressions=True,discard_repeated_small_regressions=True)
        self.assertIs(g,o.guard);self.assertEqual(g.discarded,2);self.assertEqual(g.start_mono,0)
        from services.realtime_activity import ActivityError
        with self.assertRaises(ActivityError):o.runtime_guard(['KRW-BTC'],(wall,0),discard_small_regressions=True)
