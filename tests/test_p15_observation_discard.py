"""Synthetic continuation of the reported -2ms pair; no captured Mac tick stream."""
import inspect
from decimal import Decimal
import json
import os
import unittest
from unittest.mock import patch

from ops import paper_experiment as exp
from ops.paper_session import run_session
import tests.test_p10h_realtime_activity as fixtures


@unittest.skipUnless(os.name=='posix','Private PAPER account ownership requires POSIX')
class ObservationSessionTests(unittest.TestCase):
    factory=fixtures.ActivityTests.factory
    make_stream=fixtures.ActivityTests.make_stream
    tearDown=fixtures.ActivityTests.tearDown
    def setUp(self):
        fixtures.ActivityTests.setUp(self)
        self.wall=1793150493.794;self.now=0;self.runtime=False;self.runtime_seconds=2
        self.observation_enabled=True;self.width=2;self.followup=True;self.runtime_regression=False
        self.pool=['KRW-BTC'];self.actions=[];self.token=None
        self.before_state=self.state.read_bytes();owner=self
        class Stream:
            def __init__(self,codes):self.i=0;self.runtime_i=0
            def connect(self):pass
            def close(self):pass
            def get(self):
                if owner.runtime:
                    self.runtime_i+=1;owner.now+=.25
                    if owner.now>=owner.runtime_started+owner.runtime_seconds:owner.stop.set()
                    stamp=int((owner.wall+owner.now)*1000)
                    if owner.runtime_regression and self.runtime_i==2:stamp=self.last-2
                    self.last=stamp
                    return dict(type='ticker',code='KRW-BTC',stream_type='REALTIME',timestamp=stamp,trade_price=10000)
                self.i+=1
                if self.i<=24:
                    owner.now=self.i*.4 if self.i<24 else 9.609
                    stamp=1793150503403 if self.i==24 else int((owner.wall+owner.now)*1000)
                elif self.i==25:
                    owner.now=9.694;stamp=1793150503403-owner.width
                elif self.i==26 and owner.followup:
                    owner.now=9.8;stamp=int((owner.wall+owner.now)*1000)
                else:owner.now=10;raise TimeoutError()
                return dict(type='ticker',code='KRW-BTC',stream_type='REALTIME',timestamp=stamp,
                            trade_price='POISON_MUST_NOT_PARSE' if self.i==25 else 10000)
        self.stream_factory=Stream
    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True;self.runtime_started=self.now
    def run_case(self):
        # Enables the exact same future-option fixture against the fixed baseline too.
        kw={'observation_discard':self.observation_enabled} if 'observation_discard' in inspect.signature(exp.begin).parameters else {}
        self.token=exp.begin(self.experiment,**kw)
        options={'session_id':self.token,'observe_realtime_seconds':10,'discard_small_timestamp_regressions':True,
                 'discard_small_timestamp_regressions_during_observation':self.observation_enabled}
        r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.emit,client=self.client,
            stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],experiment=options,
            wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return r,exp.finish(self.experiment,self.token,r,record_summary=True)
    def test_existing_runtime_option_keeps_observation_strict(self):
        self.observation_enabled=False
        r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertFalse(r['engine_started'])
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(self.before_state,self.state.read_bytes())
        timing=r['realtime_activity']['tick_timing']
        self.assertEqual((timing['received_total'],timing['timing_accepted_total']),(25,24))
        self.assertFalse(timing['regression_discard']['enabled'])
        self.assertEqual(timing['samples'][-1]['timestamp_minus_previous_ms'],-2)
    def test_explicit_observation_option_discards_poison_and_starts_after_newer_tick(self):
        r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED',r)
        self.assertTrue(r['engine_started']);self.assertEqual(r['orders'],0)
        self.assertEqual(r['realtime_activity']['markets']['KRW-BTC']['realtime'],25)
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],1)
        self.assertNotIn('POISON',json.dumps(r));self.assertNotIn('POISON',self.state.read_text())
    def test_runtime_btc_budget_cannot_reset_after_observation(self):
        self.runtime_regression=True
        r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED')
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],1)
        self.assertTrue(r['halted']);self.assertEqual(r['orders'],0)
        from ops.paper_bot import read_report
        report=read_report(self.experiment)
        self.assertEqual(report['regression_evidence'],r['regression_evidence'])
        self.assertEqual(report['session_state'],'HALTED_NO_RESTART')
    def test_unresolved_end_preserves_ledger_and_failure_evidence_and_consumes_reservation(self):
        self.followup=False
        r,end=self.run_case()
        self.assertFalse(r['engine_started']);self.assertEqual(end['status'],'PREPARATION_FAILED')
        self.assertEqual(self.before_state,self.state.read_bytes())
        from ops.paper_bot import read_report
        report=read_report(self.experiment)
        self.assertEqual(report['regression_evidence']['observation']['discarded'],1)
        self.assertEqual(report['regression_evidence']['observation']['awaiting_followup'],'KRW-BTC')
        self.assertEqual(report['session_failure_or_stop_reason'],'PAPER_STALE_TICK')
        with self.assertRaisesRegex(ValueError,'PAPER_REGRESSION_EXPERIMENT_BLOCKED'):
            exp.begin(self.experiment,observation_discard=True)
        # Switching to the old runtime-only experiment must not replenish this account either.
        self.now=0;self.stop.clear();self.runtime=False;self.observation_enabled=False
        r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_REGRESSION_EXPERIMENT_BLOCKED')
        self.assertFalse(r['engine_started']);self.assertEqual(self.before_state,self.state.read_bytes())
    def test_status_projection_and_durable_report_match_and_no_price_leaks(self):
        r,end=self.run_case()
        from ops.paper_bot import read_report,view
        report=read_report(self.experiment)
        self.assertEqual(view(r)['regression_evidence'],report['regression_evidence'])
        e=report['regression_evidence']
        self.assertTrue(e['observation']['enabled']);self.assertTrue(e['runtime_enabled'])
        self.assertEqual(e['observation']['discarded'],1);self.assertEqual(e['runtime_discarded'],0)
        self.assertEqual(e['observation']['received'],26)
        self.assertGreater(e['combined']['received'],26)
        self.assertTrue(e['combined']['markets']['KRW-BTC']['followup_newer_received'])
        saved=exp.marker(self.experiment)['regression_checkpoint']
        self.assertEqual(saved['state_sha256'],exp.sha(self.state.read_bytes()))
        self.assertEqual(saved['session_id'],self.token)
        self.assertEqual(saved['before_sha256'],exp.sha(self.before_state))
        self.assertNotIn('trade_price',json.dumps(saved));self.assertNotIn('POISON',json.dumps(saved))
    def test_minus_six_is_still_preparation_failure_no_ledger_change(self):
        self.width=6
        r,end=self.run_case()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'PREPARATION_FAILED')
        self.assertEqual(r['regression_evidence']['observation']['discarded'],0)
        self.assertEqual(self.before_state,self.state.read_bytes())
    def test_marker_save_failure_prevents_any_public_preparation_and_retry(self):
        token=exp.begin(self.experiment,observation_discard=True)
        self.client.candles.reset_mock();self.client.top_tickers.reset_mock()
        with patch.object(exp,'save_marker',side_effect=OSError('synthetic disk full')):
            r=self.run_worker(token)
        self.assertFalse(r['engine_started']);self.client.candles.assert_not_called()
        self.client.top_tickers.assert_not_called();self.assertEqual(self.before_state,self.state.read_bytes())
        # Simulate lost child result: IN_PROGRESS + used receipt cannot grant another session.
        with self.assertRaises(ValueError):exp.begin(self.experiment,observation_discard=True)
    def run_worker(self,token):
        return run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.emit,client=self.client,
            stream_factory=self.stream_factory,analyzer_factory=self.factory,tickers=['KRW-BTC'],
            experiment={'session_id':token,'observe_realtime_seconds':10,'discard_small_timestamp_regressions':True,
                'discard_small_timestamp_regressions_during_observation':True},
            wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
    def test_same_session_token_cannot_restart_preparation_after_lost_result(self):
        token=exp.begin(self.experiment,observation_discard=True);self.followup=False
        first=self.run_worker(token);self.assertEqual(first['reason'],'PAPER_STALE_TICK')
        self.now=0;self.stop.clear();self.client.candles.reset_mock()
        second=self.run_worker(token)
        self.assertEqual(second['reason'],'PAPER_REGRESSION_EXPERIMENT_BLOCKED')
        self.client.candles.assert_not_called();self.assertEqual(self.before_state,self.state.read_bytes())
    def test_interrupt_after_discard_stays_unresolved_and_cannot_reenable(self):
        original=self.stream_factory
        def factory(codes):
            stream=original(codes);get=stream.get
            def receive():
                if stream.i>=25:self.stop.set()
                return get()
            stream.get=receive;return stream
        self.stream_factory=factory
        r,end=self.run_case()
        self.assertEqual(r['reason'],'ACTIVITY_OBSERVATION_INTERRUPTED');self.assertFalse(r['engine_started'])
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(self.before_state,self.state.read_bytes())
        self.assertEqual(r['regression_evidence']['observation']['awaiting_followup'],'KRW-BTC')
        with self.assertRaises(ValueError):exp.begin(self.experiment,observation_discard=True)
    def test_missing_session_after_reservation_cannot_be_reset(self):
        token=exp.begin(self.experiment,observation_discard=True)
        # File loss is injected only in this disposable synthetic directory.
        (self.experiment/'session.json').unlink()
        with self.assertRaisesRegex(ValueError,'UNKNOWN_SESSION_NOT_RESET'):exp.begin(self.experiment,observation_discard=True)
        self.assertEqual(self.before_state,self.state.read_bytes())
    def test_duplicate_owner_cannot_reserve_or_start_observation(self):
        from ops.paper_session import ExistingPaperStore
        owner=ExistingPaperStore(self.state,1_000_000);owner.acquire()
        try:
            with self.assertRaises(Exception):exp.begin(self.experiment,observation_discard=True)
        finally:owner.close()
        self.assertEqual(self.before_state,self.state.read_bytes())


    def test_process_exit_after_reservation_leaves_no_retry_authority(self):
        import subprocess,sys
        code="from ops.paper_experiment import begin; import os,sys; begin(sys.argv[1],observation_discard=True); os._exit(7)"
        child=subprocess.run([sys.executable,'-c',code,str(self.experiment)],capture_output=True,timeout=15)
        self.assertEqual(child.returncode,7,child.stderr.decode())
        self.assertEqual(self.before_state,self.state.read_bytes())
        with self.assertRaisesRegex(ValueError,'UNCERTAIN_SESSION_NO_AUTOMATIC_RESTART'):
            exp.begin(self.experiment,observation_discard=True)
    def test_observation_then_real_paper_buy_sell_never_sees_poison(self):
        from trading.auto_trade import AutoTrade
        from ops.paper_bot import read_report
        self.actions=['BUY','SELL'];prices=[];original=AutoTrade.process_market_data
        def record(bot,t,data):
            prices.append(data['trade_price']);return original(bot,t,data)
        with patch.object(AutoTrade,'process_market_data',new=record):r,end=self.run_case()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['orders'],2)
        self.assertNotIn('POISON_MUST_NOT_PARSE',prices)
        report=read_report(self.experiment)
        self.assertEqual(report['summary']['cumulative_buys'],1);self.assertEqual(report['summary']['cumulative_sells'],1)
        self.assertEqual(Decimal(report['summary']['net_realized_pnl']),0)
        self.assertEqual(report['summary']['pending_orders'],0)
    def test_changed_checkpoint_hash_or_raw_fields_are_not_reported(self):
        from ops.paper_bot import read_report
        self.run_case();raw=(self.experiment/'session.json').read_bytes();m=exp.marker(self.experiment)
        m['regression_checkpoint']['state_sha256']='0'*64
        (self.experiment/'session.json').write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError,'REGRESSION_EVIDENCE_UNVERIFIED'):read_report(self.experiment)
        (self.experiment/'session.json').write_bytes(raw)
        m=exp.marker(self.experiment);m['regression_checkpoint']['evidence']['raw_price']='MUST_NOT_REPORT'
        (self.experiment/'session.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError):read_report(self.experiment)
        self.assertNotIn('MUST_NOT_REPORT',self.state.read_text())


class ObservationGuardTests(unittest.TestCase):
    def setUp(self):
        self.wall=1800000000;self.now=0;self.stop=False
        self.codes=['KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL']
    def frame(self,code='KRW-BTC',stamp=100,price=100):
        return dict(type='ticker',code=code,stream_type='REALTIME',timestamp=self.wall*1000+stamp,trade_price=price)
    def observe(self,frames,enabled=True,seconds=2):
        from services.realtime_activity import Observation
        self.o=Observation(self.codes,wall=lambda:self.wall+self.now,monotonic=lambda:self.now,
            discard_small_regressions=enabled,clock_origin=(self.wall,0))
        owner=self
        class Stream:
            def get(self):
                if frames:
                    at,data=frames.pop(0);owner.now=at;return data
                owner.now=seconds;raise TimeoutError()
        return self.o.observe(Stream(),seconds,deadline=100,stopped=lambda:self.stop)
    def test_one_two_five_poison_is_not_parsed_or_counted_as_activity(self):
        from services.realtime_activity import positive
        for width in (1,2,5):
            with self.subTest(width=width),patch('services.realtime_activity.positive',wraps=positive) as parse:
                self.now=0
                o=self.observe([(0.1,self.frame()),(.2,self.frame(stamp=100-width,price='POISON')),
                                (.3,self.frame(stamp=101))])
                self.assertEqual(parse.call_count,2)
                self.assertEqual(o.stats['KRW-BTC']['realtime'],2)
                self.assertEqual(o.guard.progress['KRW-BTC'],.3)
                self.assertEqual(o.guard.discarded,1);self.assertTrue(o.completed)
    def test_equal_or_other_market_followup_cannot_start(self):
        from services.tick_timing import TickTimingError
        for follow in (self.frame(),self.frame('KRW-ETH',101)):
            self.now=0
            with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):
                self.observe([(.1,self.frame()),(.2,self.frame(stamp=98,price='POISON')),(.3,follow)])
            self.assertFalse(self.o.completed);self.assertEqual(self.o.guard.awaiting_followup,'KRW-BTC')
            self.assertEqual(self.o.guard.progress['KRW-BTC'],.1)
    def test_consecutive_and_repeated_market_regression_cannot_recharge(self):
        from services.tick_timing import TickTimingError
        for extra in ([self.frame(stamp=99)],[self.frame(stamp=101),self.frame(stamp=100)]):
            self.now=0;frames=[(.1,self.frame()),(.2,self.frame(stamp=98))]
            frames += [(.3+i*.1,f) for i,f in enumerate(extra)]
            with self.assertRaises(TickTimingError):self.observe(frames)
            self.assertEqual(self.o.guard.discarded,1)
    def test_global_budget_and_clock_origin_survive_transition(self):
        from services.tick_timing import TickTimingError
        frames=[]
        for i,t in enumerate(self.codes):
            frames.append((i*.3+.01,self.frame(t)))
            if i<3:frames.extend([(i*.3+.1,self.frame(t,98)),(i*.3+.2,self.frame(t,101))])
        o=self.observe(frames);guard=o.runtime_guard(self.codes,(self.wall,0),discard_small_regressions=True)
        self.assertIs(guard,o.guard);self.assertEqual(guard.discarded,3)
        self.assertEqual(guard.start_mono,0)
        with self.assertRaises(TickTimingError):guard.observe(self.frame('KRW-SOL',99),self.wall+2.1,2.1)
        self.assertEqual(o.report()['tick_timing']['regression_discard']['discarded_total'],3)
    def test_late_future_clock_and_idle_still_fail(self):
        from services.tick_timing import TickTimingError
        for frame in (self.frame(stamp=-60001),self.frame(stamp=6000)):
            self.now=0
            with self.assertRaises(TickTimingError):self.observe([(.1,frame)])
            self.assertEqual(self.o.guard.discarded,0)
        self.now=0;o=self.observe([(.1,self.frame()),(.2,self.frame(stamp=98)),(.3,self.frame(stamp=101))])
        with self.assertRaisesRegex(TickTimingError,'PAPER_CLOCK_DISCONTINUITY'):o.guard.clock(self.wall+20,2)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):o.guard.idle(self.wall+16,16)
    def test_cli_scope_validation_precedes_begin_or_network(self):
        import io
        base=['--connect-public','--seconds','30','--discard-small-timestamp-regressions-during-observation']
        bad=[[],['--observe-realtime-seconds','10'],['--discard-small-timestamp-regressions'],
             ['--observe-realtime-seconds','10','--discard-small-timestamp-regressions','--top','1'],
             ['--observe-realtime-seconds','10','--discard-small-timestamp-regressions','--eligible-top','1','--candidate-limit','2']]
        with patch.object(exp,'begin',side_effect=AssertionError('must not reserve')):
            for args in bad:
                with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(base+args)
    def test_evidence_rejects_arbitrary_strings_and_counts(self):
        from services.realtime_activity import timing_evidence,validate_regression_evidence
        self.observe([(.1,self.frame())]);r=timing_evidence(self.o.guard.report())
        value={'observation':r,'combined':r,'runtime_started':False,'runtime_enabled':True,'runtime_discarded':0}
        validate_regression_evidence(value)
        r['samples']=[{'secret':'must_not_leak'}]
        with self.assertRaises(ValueError):validate_regression_evidence(value)
