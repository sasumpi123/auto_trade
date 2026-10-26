"""Offline regression-discard experiments; no actual account/market access."""
from decimal import Decimal
import io
import json
import os
import unittest
from unittest.mock import patch

from services.tick_timing import TickTiming,TickTimingError,MAX_REPORT_BYTES
from services.realtime_activity import Observation
from ops import paper_experiment as exp
from ops.paper_session import run_session
from trading.auto_trade import AutoTrade
from trading.execution import ExecutionEngine
import tests.test_p10h_realtime_activity as fixtures


class DiscardTimingTests(unittest.TestCase):
    def setUp(self):
        self.wall=1800000000;self.codes=['KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL'];self.make()
    def make(self,enabled=True):
        self.g=TickTiming(self.codes,self.wall,0,discard_small_regressions=enabled)
    def tick(self,t='KRW-BTC',offset=0,stamp=None):
        return {'type':'ticker','code':t,'timestamp':self.wall*1000+offset if stamp is None else stamp,'trade_price':'POISON_NOT_READ_BY_TIMING'}
    def send(self,offset=0,t='KRW-BTC',at=0):return self.g.observe(self.tick(t,offset),self.wall+at,at)
    def test_default_remains_strict_for_minus_one_two_and_five(self):
        for width in (1,2,5):
            self.make(False);self.send()
            with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(-width,at=.001)
            self.assertEqual(self.g.discarded,0)
    def test_one_two_and_five_discard_without_mutating_timestamp_or_watchdogs(self):
        for width in (1,2,5):
            self.make();self.send();before=(self.g.previous.copy(),self.g.progress.copy(),self.g.last_progress)
            self.assertIsNone(self.send(-width,at=.001))
            self.assertEqual(before,(self.g.previous,self.g.progress,self.g.last_progress))
            self.assertEqual(self.g.accepted,1);self.assertEqual(self.g.discarded,1)
            self.assertEqual(self.g.discards['KRW-BTC']['last_regression_ms'],width)
    def test_newer_followup_recovers_pending_report_but_not_budget(self):
        self.send();self.send(-2,at=.001);self.send(2,at=.002)
        self.assertTrue(self.g.discards['KRW-BTC']['followup_newer_received'])
        self.assertIsNone(self.g.awaiting_followup)
        with self.assertRaises(TickTimingError):self.send(1,at=.003)
        self.assertEqual(self.g.discarded,1)
    def test_equal_timestamp_does_not_count_as_newer_followup(self):
        self.send();self.send(-2,at=.001);self.send(0,at=.002)
        self.assertFalse(self.g.discards['KRW-BTC']['followup_newer_received'])
        self.assertEqual(self.g.progress['KRW-BTC'],0)
    def test_global_three_and_market_one_are_hard_caps_without_refill(self):
        for n,t in enumerate(self.codes[:3]):
            self.send(t=t,at=n);self.send(-2,t,at=n+.001);self.send(2,t,at=n+.002)
        self.send(t=self.codes[3],at=3)
        with self.assertRaises(TickTimingError):self.send(-1,self.codes[3],at=3.001)
        self.assertEqual(self.g.discarded,3)
    def test_no_second_discard_until_same_market_newer_followup(self):
        self.send();self.send(t='KRW-ETH');self.send(-2,at=.001)
        self.send(2,'KRW-ETH',at=.002)  # Other market does not resolve BTC uncertainty.
        with self.assertRaises(TickTimingError):self.send(1,'KRW-ETH',at=.003)
    def test_large_delayed_future_and_clock_errors_are_not_discardable(self):
        for offset,at,root in ((-6,.001,'PAPER_STALE_TICK'),(-60001,0,'PAPER_STALE_TICK'),
                               (5001,0,'PAPER_STALE_TICK'),(-2,6,'PAPER_CLOCK_DISCONTINUITY')):
            self.make();self.send()
            with self.assertRaisesRegex(TickTimingError,root):
                self.g.observe(self.tick(offset=offset),self.wall+(0 if at==6 else at),at)
            self.assertEqual(self.g.discarded,0)
    def test_first_discard_cannot_hide_already_expired_whole_stream(self):
        self.send()
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.send(-2,at=15)
        self.assertEqual(self.g.discarded,0)
    def test_repeated_discard_attempts_halt_without_extending_timers(self):
        self.send();self.send(-2,at=.001)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):self.send(-1,at=.002)
        self.assertEqual(self.g.progress['KRW-BTC'],0)
    def test_discard_then_silence_still_expires_watchdog(self):
        self.send();self.send(-2,at=.001)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.g.idle(self.wall+15,15)
    def test_active_other_market_cannot_hide_held_market_silence(self):
        self.g=TickTiming(self.codes[:2],self.wall,0,discard_small_regressions=True)
        self.send();self.send(-2,at=.001)
        for i in range(1,60):self.send(i*1000,'KRW-ETH',at=i)
        with self.assertRaisesRegex(TickTimingError,'PAPER_MARKET_STALE'):self.send(60000,'KRW-ETH',at=60)
        self.assertEqual(self.g.progress['KRW-BTC'],0)
    def test_no_discard_after_hour_even_if_injected_call_bypasses_supervisor(self):
        self.g=TickTiming(['KRW-BTC'],self.wall,0,discard_small_regressions=True)
        self.send(3600000,at=3600)
        with self.assertRaises(TickTimingError):self.send(3599998,at=3600.001)
    def test_report_bounded_numeric_and_no_raw_price(self):
        self.send();self.send(-5,at=.001)
        encoded=json.dumps(self.g.report(),allow_nan=False)
        self.assertLess(len(encoded.encode()),MAX_REPORT_BYTES)
        self.assertNotIn('POISON',encoded)
        self.assertFalse(self.g.report()['regression_discard']['markets']['KRW-BTC']['followup_newer_received'])
    def test_pretrade_observer_remains_strict(self):
        o=Observation(['KRW-BTC'],wall=lambda:self.wall,monotonic=lambda:0)
        o.guard.observe(self.tick(),self.wall,0,check_liveness=False)
        with self.assertRaises(TickTimingError):o.guard.observe(self.tick(offset=-2),self.wall,0,check_liveness=False)
    def test_cli_requires_explicit_connection_and_observation(self):
        for args in (['--discard-small-timestamp-regressions'],
                     ['--create','--discard-small-timestamp-regressions'],
                     ['--connect-public','--seconds','180','--discard-small-timestamp-regressions']):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)


@unittest.skipUnless(os.name=='posix','Private isolated account protocol is POSIX only; Windows BLOCKED')
class DiscardSessionTests(unittest.TestCase):
    factory=fixtures.ActivityTests.factory
    make_stream=fixtures.ActivityTests.make_stream
    tearDown=fixtures.ActivityTests.tearDown
    def setUp(self):
        fixtures.ActivityTests.setUp(self)
        # Third immutable HALTED fixture: represents the latest reported experiment.
        frozen=self.parent/'third-halted';self.assertEqual(exp.create(frozen)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        token=exp.begin(frozen);self.stamps=int(self.wall*1000)-60001
        self.client.top_tickers.return_value=['KRW-BTC']
        r=run_session(frozen/exp.STATE,seconds=30,stopped=self.stop.is_set,emit=lambda _:None,client=self.client,
            stream_factory=self.legacy_stream,analyzer_factory=self.factory,top=1,experiment={'session_id':token})
        self.assertTrue(r['halted']);exp.finish(frozen,token,r)
        for p in frozen.iterdir():
            if p.is_file():self.before[p]=p.read_bytes()
        for p in (exp.receipt_path(frozen),exp.first_use_path(frozen)):self.before[p]=p.read_bytes()
        backup=self.parent/'third-cold-backup.json';backup.write_bytes((frozen/exp.STATE).read_bytes());self.before[backup]=backup.read_bytes()
        self.stamps=None;self.now=0;self.stop.clear();self.reports=[];self.actions=['BUY','SELL']
        self.pool=['KRW-BTC'];self.client.top_tickers.return_value=self.pool
        self.runtime=False;self.inject_at={2:-2};self.price_poison='private-price-canary';self.normal_after=True
        self.runtime_seconds=5;self.strategy_prices=[];self.last_stamp=None;self.runtime_index=0
        self.base_stream=self.stream_factory
        def stream_factory(codes):
            s=self.base_stream(codes);base_get=s.get
            def get():
                data=base_get()
                if self.runtime:
                    self.runtime_index+=1
                    if self.runtime_index in self.inject_at:
                        data['timestamp']=self.last_stamp+self.inject_at[self.runtime_index]
                        data['trade_price']=self.price_poison
                    elif not self.normal_after and self.runtime_index>2:raise TimeoutError()
                    else:
                        self.last_stamp=data['timestamp'];data['trade_price']=10000 if self.runtime_index==1 else 10200
                return data
            s.get=get;return s
        self.stream_factory=stream_factory
    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True;self.runtime_started=self.now
    def run_i(self,enabled=True,seconds=150):
        token=exp.begin(self.experiment)
        options={'session_id':token,'observe_realtime_seconds':60,'discard_small_timestamp_regressions':enabled}
        original=AutoTrade.process_market_data
        def process(bot,t,data):
            self.strategy_prices.append(data['trade_price']);return original(bot,t,data)
        with patch.object(AutoTrade,'process_market_data',new=process):
            r=run_session(self.state,seconds=seconds,stopped=self.stop.is_set,emit=self.emit,client=self.client,
                stream_factory=self.stream_factory,analyzer_factory=self.factory,top=1,experiment=options,
                wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return r,exp.finish(self.experiment,token,r)
    def test_poisoned_regression_never_reaches_strategy_fills_or_marks_and_ledger_reconciles(self):
        r,end=self.run_i()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertNotIn(self.price_poison,self.strategy_prices)
        self.assertEqual(r['orders'],2);self.assertEqual(r['pending_orders'],0);self.assertFalse(r['halted'])
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],1)
        self.assertTrue(r['tick_timing']['regression_discard']['markets']['KRW-BTC']['followup_newer_received'])
        self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10200')
        saved=json.loads(self.state.read_bytes());self.assertNotIn(self.price_poison,json.dumps(saved))
        self.assertEqual(Decimal(saved['cash']),Decimal('1008000'))
        orders=saved['orders'].copy()
        check=exp.inspect_experiment(self.experiment);self.assertEqual(check['session_state'],'STOPPED_VERIFIED')
        self.assertEqual(orders,json.loads(self.state.read_bytes())['orders'])
    def test_default_halts_same_minus_two_and_preserves_first_buy(self):
        r,end=self.run_i(False)
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(r['orders'],1)
        self.assertEqual(end['status'],'HALTED');self.assertNotIn(self.price_poison,self.strategy_prices)
        with self.assertRaises(Exception):exp.begin(self.experiment)
    def test_large_regression_halts_even_with_opt_in(self):
        self.inject_at={2:-6};r,end=self.run_i()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertTrue(r['halted'])
        self.assertEqual(r['orders'],1);self.assertNotIn(self.price_poison,self.strategy_prices)
    def test_repeated_regression_halts_without_second_fill(self):
        self.inject_at={2:-2,3:-1};r,end=self.run_i()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(r['orders'],1)
        self.assertEqual(r['tick_timing']['regression_discard']['discarded_total'],1)
    def test_silence_after_discard_halts_held_position_without_stale_sell(self):
        self.normal_after=False;self.runtime_seconds=30;r,end=self.run_i()
        self.assertEqual(r['reason'],'PAPER_STREAM_TIMEOUT');self.assertEqual(r['orders'],1)
        self.assertGreater(Decimal(r['positions']['KRW-BTC']['quantity']),0)
        self.assertFalse(r['tick_timing']['regression_discard']['markets']['KRW-BTC']['followup_newer_received'])
        self.assertNotIn(self.price_poison,self.strategy_prices)
    def test_successful_session_cannot_reenable_policy_by_restarting_account(self):
        self.run_i();before=self.state.read_bytes();self.stop.clear();self.runtime=False;self.now=0
        r,end=self.run_i()
        self.assertFalse(r['engine_started']);self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(r['reason'],'PAPER_REGRESSION_EXPERIMENT_BLOCKED')
        self.assertEqual(end['status'],'PREPARATION_FAILED')
    def test_halted_experiment_cannot_be_started_with_new_flag(self):
        self.run_i(False);before=self.state.read_bytes()
        with self.assertRaises(Exception):self.run_i()
        self.assertEqual(before,self.state.read_bytes())

    def test_numeric_stale_price_cannot_trigger_take_profit_or_revaluation(self):
        self.price_poison=100000000;self.actions=['BUY'];self.normal_after=False;self.runtime_seconds=30
        r,end=self.run_i()
        self.assertEqual(r['reason'],'PAPER_STREAM_TIMEOUT');self.assertEqual(r['orders'],1)
        self.assertNotIn(self.price_poison,self.strategy_prices)
        self.assertEqual(r['markets']['KRW-BTC']['last_price'],'10000')
        self.assertGreater(Decimal(r['positions']['KRW-BTC']['quantity']),0)
    def test_first_execution_after_strict_success_is_not_eligible_for_opt_in(self):
        self.inject_at={};r,end=self.run_i(False);self.assertEqual(end['status'],'STOPPED_VERIFIED')
        before=self.state.read_bytes();self.stop.clear();self.runtime=False;self.now=0
        r,end=self.run_i(True)
        self.assertEqual(r['reason'],'PAPER_REGRESSION_EXPERIMENT_BLOCKED');self.assertFalse(r['engine_started'])
        self.assertEqual(self.state.read_bytes(),before)
    def test_accepted_followup_is_required_even_if_prices_never_trade(self):
        self.actions=[];r,end=self.run_i()
        self.assertEqual(r['orders'],0);self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual(r['session_ticks'],len(self.strategy_prices))
        self.assertEqual(r['tick_timing']['timing_accepted_total'],r['session_ticks'])
        self.assertEqual(r['tick_timing']['received_total'],r['session_ticks']+1)

    def test_stop_before_newer_followup_is_halted_not_verified_success(self):
        self.normal_after=False;self.runtime_seconds=1
        r,end=self.run_i()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED')
        self.assertFalse(r['tick_timing']['regression_discard']['markets']['KRW-BTC']['followup_newer_received'])
