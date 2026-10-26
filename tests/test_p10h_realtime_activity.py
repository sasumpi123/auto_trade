"""Fake-only pre-trade activity; both protected HALTED accounts stay immutable."""
import io
import json
import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import Mock,patch

from services.realtime_activity import Observation,ActivityError,MAX_START_DELAY
from services.tick_timing import TickTimingError
from ops import paper_experiment as exp
from ops.paper_session import run_session,ExistingPaperStore
from ops.public_tick_diagnostic import monitor
import tests.test_p10f_paper_experiment as fixtures

MARKETS=['KRW-BLAST','KRW-BABY','KRW-IRYS','KRW-T','KRW-ORDER','KRW-XRP','KRW-CAP']


class OptionTests(unittest.TestCase):
    def test_activity_never_implicitly_enabled_and_invalid_combinations_fail(self):
        with patch('ops.paper_watch.supervise',side_effect=AssertionError('No network')):
            for args in (['--observe-realtime-seconds','60'],['--select-realtime-active'],
                         ['--connect-public','--seconds','180','--top','7','--select-realtime-active'],
                         ['--connect-public','--seconds','180','--eligible-top','7','--candidate-limit','10','--select-realtime-active'],
                         ['--connect-public','--seconds','60','--observe-realtime-seconds','60'],
                         ['--connect-public','--seconds','180','--observe-realtime-seconds','61']):
                with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.now=0;self.wall=1800000000;self.stop=False;self.index=0;self.kind='REALTIME';self.repeat=False
        self.markets=MARKETS.copy();self.error=None;owner=self
        class Stream:
            def get(self):
                owner.now+=.1
                if owner.error is not None:raise owner.error
                t=owner.markets[owner.index%len(owner.markets)];owner.index+=1
                return {'type':'ticker','code':t,'stream_type':owner.kind,'trade_price':100,
                        'timestamp':int((owner.wall+(.1 if owner.repeat else owner.now))*1000)}
        self.stream=Stream()
    def observe(self,seconds=60,deadline=100):
        self.o=Observation(MARKETS,wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        return self.o.observe(self.stream,seconds,deadline=deadline,stopped=lambda:self.stop)
    def test_all_markets_have_finite_first_receive_and_end_age(self):
        o=self.observe();r=o.report()
        self.assertTrue(all(o.active(t) for t in MARKETS))
        self.assertTrue(all(0<v['first_realtime_ms']<1000 for v in r['markets'].values()))
        self.assertGreaterEqual(r['window_elapsed_ms'],60000)
        self.now+=20;self.assertEqual(r['window_elapsed_ms'],o.report()['window_elapsed_ms'])
        self.assertTrue(all(v['status']=='REALTIME_ACTIVITY_OBSERVED' for v in o.report()['markets'].values()))
    def test_snapshot_messages_never_count_as_realtime(self):
        self.kind='SNAPSHOT';o=self.observe()
        self.assertTrue(all(v['snapshot']>0 and v['realtime']==0 for v in o.stats.values()))
        self.assertTrue(all(not o.active(t) for t in MARKETS))
    def test_unknown_message_class_is_incomplete_not_accepted(self):
        self.kind=None
        with self.assertRaisesRegex(ActivityError,'ACTIVITY_STREAM_TYPE_UNKNOWN'):self.observe()
        self.assertFalse(self.o.completed)
        self.assertTrue(all(v['status']=='INCOMPLETE_UNKNOWN' for v in self.o.report()['markets'].values()))
    def test_repeated_same_timestamp_does_not_pass_recent_progress_rule(self):
        self.repeat=True;o=self.observe()
        self.assertTrue(all(v['realtime']>0 for v in o.stats.values()))
        self.assertTrue(all(not o.active(t) for t in MARKETS))
        self.assertEqual(o.exclusion(MARKETS[0]),'REALTIME_ACTIVITY_NOT_RECENT_AT_OBSERVATION_END')
    def test_connection_close_is_observed_but_no_message_cause_remains_unknown(self):
        from websockets.exceptions import ConnectionClosedOK
        from websockets.frames import Close
        self.error=ConnectionClosedOK(Close(1000,'private-secret-canary'),Close(1000,'private-secret-canary'),True)
        with self.assertRaisesRegex(ActivityError,'ACTIVITY_CONNECTION_FAILED'):self.observe()
        r=self.o.report();self.assertEqual(r['close_code'],1000);self.assertIn('CLOSE_OBSERVED',r['connection_end'])
        self.assertNotIn('private-secret-canary',json.dumps(r))
    def test_deadline_message_limit_and_stop_cannot_create_a_pass(self):
        with self.assertRaisesRegex(ActivityError,'ACTIVITY_DEADLINE_INSUFFICIENT'):self.observe(deadline=60)
        self.now=0
        with patch('services.realtime_activity.MAX_MESSAGES',2):
            with self.assertRaisesRegex(ActivityError,'ACTIVITY_MESSAGE_LIMIT'):self.observe()
        self.now=0;self.stop=True
        with self.assertRaisesRegex(ActivityError,'ACTIVITY_OBSERVATION_INTERRUPTED'):self.observe()
    def test_runtime_guard_seeds_observed_timestamp_and_evidence_expires(self):
        o=self.observe();g=o.runtime_guard(MARKETS,(self.wall,0))
        self.assertEqual(g.previous,o.guard.previous)
        first=MARKETS[0]
        data={'type':'ticker','code':first,'trade_price':1,'timestamp':o.guard.previous[first]-1}
        with self.assertRaisesRegex(TickTimingError,'PAPER_STALE_TICK'):g.observe(data,self.wall+self.now,self.now)
        self.now+=6
        with self.assertRaisesRegex(ActivityError,'ACTIVITY_START_EVIDENCE_EXPIRED'):o.validate_start(MARKETS)
    def test_public_diagnostic_labels_snapshot_and_first_receive_separately(self):
        owner=self
        class Stream:
            def connect(self):pass
            def close(self):pass
            def get(self):
                owner.now+=1
                return {'type':'ticker','code':'KRW-BTC','stream_type':'SNAPSHOT' if owner.now==1 else 'REALTIME',
                        'timestamp':int((owner.wall+owner.now)*1000),'trade_price':100}
        r=monitor(['KRW-BTC'],3,stream_factory=lambda _:Stream(),stopped=lambda:False,
                  wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        self.assertEqual(r['stream_activity']['KRW-BTC']['snapshot'],1)
        self.assertEqual(r['stream_activity']['KRW-BTC']['labeled_realtime'],1)
        self.assertEqual(r['stream_activity']['KRW-BTC']['first_receive_ms'],1000)
        self.assertEqual(r['ledger_reads'],0);self.assertEqual(r['strategy_calls'],0)

    def test_no_frames_does_not_assert_healthy_transport_or_absent_trades(self):
        self.error=TimeoutError();o=self.observe()
        self.assertEqual(o.report()['transport_observation'],'NO_MESSAGES_CONNECTION_OR_ACTIVITY_UNKNOWN')
        self.assertTrue(all(v['status']=='NO_REALTIME_TICKS_DURING_OBSERVATION' for v in o.report()['markets'].values()))


@unittest.skipUnless(os.name=='posix','Existing private experiment/cold ownership protocol is POSIX only')
class ActivityTests(unittest.TestCase):
    factory=fixtures.ExperimentTests.factory
    def setUp(self):
        fixtures.ExperimentTests.setUp(self)
        self.legacy_stream=self.stream_factory
        # Make a second protected, HALTED experimental account using Fake data only.
        self.frozen=self.parent/'frozen-experiment'
        self.assertEqual(exp.create(self.frozen)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        token=exp.begin(self.frozen);old=self.state;self.state=self.frozen/exp.STATE
        self.stamps=int(self.wall*1000)-60001
        run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=lambda _:None,client=self.client,
            stream_factory=self.legacy_stream,analyzer_factory=self.factory,top=1,experiment={'session_id':token})
        self.before[self.state]=self.state.read_bytes();self.before[exp.receipt_path(self.frozen)]=exp.receipt_path(self.frozen).read_bytes()
        self.before[self.frozen/'session.json']=(self.frozen/'session.json').read_bytes()
        self.state=old;self.stamps=None;self.now=0;self.stop.clear();self.reports=[]
        self.client.top_tickers.reset_mock();self.client.candles.reset_mock()
        self.before[self.frozen/(exp.STATE+'.lock')]=(self.frozen/(exp.STATE+'.lock')).read_bytes()
        frozen_backup=self.parent/'frozen-backup.json';frozen_backup.write_bytes((self.frozen/exp.STATE).read_bytes())
        self.before[frozen_backup]=frozen_backup.read_bytes()
        self.client.clock=lambda:self.wall+self.now
        self.actions=[];self.missing=set();self.runtime_missing=set();self.kind='REALTIME'
        self.mode_error=None;self.runtime=False;self.created_streams=[];self.observe_seconds=60
        self.runtime_seconds=5;self.pool=MARKETS.copy();self.client.top_tickers.return_value=self.pool
        self.tokens=[];owner=self
        class Stream:
            def __init__(self,codes):self.codes=codes.copy();self.index=0;self.closed=False;self.gets=0
            def connect(self):
                if owner.mode_error=='connect':raise OSError('private-secret-canary')
            def close(self):self.closed=True
            def get(self):
                owner.now+=.25;self.gets+=1
                if owner.mode_error=='read' and owner.now>2:raise OSError('private-secret-canary')
                if owner.mode_error=='interrupt' and owner.now>2:owner.stop.set()
                if owner.runtime and owner.now>=owner.runtime_started+owner.runtime_seconds:owner.stop.set()
                absent=owner.runtime_missing if owner.runtime else owner.missing
                codes=[t for t in self.codes if t not in absent]
                if not codes:raise TimeoutError()
                t=codes[self.index%len(codes)];self.index+=1
                return {'type':'ticker','code':t,'stream_type':owner.kind,'trade_price':10000,
                        'timestamp':int((owner.wall+owner.now)*1000)}
        self.stream_factory=lambda codes:self.make_stream(Stream,codes)

    def make_stream(self,cls,codes):
        stream=cls(codes);self.created_streams.append(stream);return stream

    tearDown=fixtures.ExperimentTests.tearDown

    def emit(self,r):
        self.reports.append(r)
        if r['status']=='STARTING':self.runtime=True;self.runtime_started=self.now

    def run_h(self,*,top=7,eligible=None,limit=None,replace=False,observe=60,seconds=150,finish=True):
        token=exp.begin(self.experiment);self.tokens.append(token)
        options={'session_id':token,'eligible_top':eligible,'candidate_limit':limit,
                 'observe_realtime_seconds':observe,'select_realtime_active':replace}
        r=run_session(self.state,seconds=seconds,stopped=self.stop.is_set,emit=self.emit,client=self.client,
            stream_factory=self.stream_factory,analyzer_factory=self.factory,top=top,experiment=options,
            wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
        terminal=exp.finish(self.experiment,token,r) if finish else None
        return r,terminal

    def preparation_report(self):return [r['selection'] for r in self.reports if r['status']=='PREPARATION_FAILED'][-1]

    def hold_btc(self):
        self.client.top_tickers.return_value=['KRW-BTC'];self.actions=['BUY']
        token=exp.begin(self.experiment)
        r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=lambda _:None,client=self.client,
            stream_factory=self.legacy_stream,analyzer_factory=self.factory,top=1,experiment={'session_id':token},
            wall=lambda:self.wall,monotonic=lambda:self.now)
        self.assertEqual(exp.finish(self.experiment,token,r)['status'],'STOPPED_VERIFIED')
        self.now=0;self.stop.clear();self.actions=[];self.reports=[];self.client.top_tickers.return_value=self.pool

    def test_seven_one_silent_for_full_sixty_seconds_is_not_inferred_api_failure(self):
        self.missing={'KRW-BLAST'};before=self.state.read_bytes()
        with patch.object(ExistingPaperStore,'save',side_effect=AssertionError('No PAPER write')) as save:r,end=self.run_h()
        save.assert_not_called();self.assertFalse(r['engine_started']);self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(end['status'],'PREPARATION_FAILED')
        activity=self.preparation_report()['realtime_activity'];blast=activity['markets']['KRW-BLAST']
        self.assertEqual(blast['status'],'NO_REALTIME_TICKS_DURING_OBSERVATION');self.assertEqual(blast['realtime'],0)
        self.assertGreaterEqual(blast['no_progress_ms'],60000);self.assertTrue(activity['completed'])
        self.assertTrue(all(activity['markets'][t]['realtime']>0 for t in MARKETS[1:]))

    def test_all_seven_realtime_reuses_one_connection_without_observation_price_orders(self):
        r,end=self.run_h()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['orders'],0)
        self.assertEqual(len(self.created_streams),1);self.assertTrue(self.created_streams[0].closed)
        report=[e['selection'] for e in self.reports if e['status']=='DATA_PREPARED'][-1]
        self.assertEqual(report['final_strategy_markets'],MARKETS);self.assertEqual(report['wire_subscription'],MARKETS)
        self.assertLessEqual(report['realtime_activity']['last_start_check_delay_ms'] or 0,5000)
        self.assertTrue(all(v['first_realtime_ms'] is not None for v in r['realtime_activity']['markets'].values()))
        self.assertEqual(r['realtime_activity']['window_elapsed_ms'],60000)

    def test_snapshot_only_never_supplies_realtime_evidence(self):
        self.kind='SNAPSHOT';before=self.state.read_bytes();r,end=self.run_h()
        self.assertFalse(r['engine_started']);self.assertEqual(end['status'],'PREPARATION_FAILED')
        self.assertEqual(self.state.read_bytes(),before)
        stats=self.preparation_report()['realtime_activity']['markets']
        self.assertTrue(all(v['snapshot']>0 and v['realtime']==0 and v['first_realtime_ms'] is None for v in stats.values()))

    def test_explicit_ranked_replacement_happens_only_before_start_with_wire_universe_reported(self):
        self.pool=MARKETS+['KRW-BTC'];self.client.top_tickers.return_value=self.pool;self.missing={'KRW-BLAST'}
        r,end=self.run_h(eligible=7,limit=8,replace=True)
        self.assertEqual(end['status'],'STOPPED_VERIFIED')
        report=[v['selection'] for v in self.reports if v['status']=='DATA_PREPARED'][-1]
        self.assertEqual(report['final_strategy_markets'],self.pool[1:]);self.assertTrue(report['ranking_deviation_declared'])
        self.assertEqual(report['final_subscription'],self.pool);self.assertEqual(len(self.created_streams),1)
        self.assertNotIn('KRW-BLAST',r['markets'])
        self.assertEqual(report['excluded'][0]['reason'],'NO_REALTIME_TICKS_DURING_OBSERVATION')

    def test_eligible_top_without_replacement_flag_fails_instead_of_changing_membership(self):
        self.pool=MARKETS+['KRW-BTC'];self.client.top_tickers.return_value=self.pool;self.missing={'KRW-BLAST'}
        before=self.state.read_bytes();r,end=self.run_h(eligible=7,limit=8)
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(self.created_streams[0].codes,MARKETS)

    def test_insufficient_active_candidates_do_not_expand_pool_or_write_state(self):
        self.missing={'KRW-BLAST'};before=self.state.read_bytes();r,end=self.run_h(eligible=7,limit=7,replace=True)
        self.assertEqual(r['reason'],'INSUFFICIENT_REALTIME_ACTIVE_CANDIDATES')
        self.assertEqual(self.state.read_bytes(),before);self.client.top_tickers.assert_called_once_with(7)

    def test_held_silent_market_is_mandatory_and_not_replaced(self):
        self.hold_btc();self.missing={'KRW-BTC'};before=self.state.read_bytes()
        r,end=self.run_h(eligible=7,limit=7,replace=True)
        self.assertEqual(r['reason'],'HELD_REALTIME_ACTIVITY_UNAVAILABLE');self.assertEqual(end['status'],'PREPARATION_FAILED')
        self.assertEqual(before,self.state.read_bytes());self.assertIn('KRW-BTC',self.created_streams[0].codes)

    def test_after_start_held_market_still_halts_after_sixty_seconds_without_stale_sell(self):
        self.hold_btc();self.runtime_missing={'KRW-BTC'};self.runtime_seconds=75
        r,end=self.run_h(seconds=180)
        self.assertTrue(r['engine_started']);self.assertEqual(r['reason'],'PAPER_MARKET_STALE');self.assertTrue(r['halted'])
        self.assertEqual(r['orders'],1);self.assertEqual(end['status'],'HALTED')
        self.assertGreater(float(r['positions']['KRW-BTC']['quantity']),0)
        with self.assertRaises(Exception):exp.begin(self.experiment)

    def test_subscription_and_read_failure_are_not_marked_as_no_market_trades(self):
        for error in ('connect','read'):
            self.mode_error=error;self.now=0;before=self.state.read_bytes();r,end=self.run_h()
            self.assertEqual(r['reason'],'ACTIVITY_CONNECTION_FAILED');self.assertEqual(end['status'],'PREPARATION_FAILED')
            self.assertEqual(before,self.state.read_bytes());self.assertNotIn('private-secret-canary',json.dumps(r))
            report=self.preparation_report()['realtime_activity'];self.assertFalse(report['completed'])
            self.assertTrue(all(v['status']=='INCOMPLETE_UNKNOWN' for v in report['markets'].values()))

    def test_unknown_stream_type_never_counts_as_realtime(self):
        self.kind='UNKNOWN';before=self.state.read_bytes();r,end=self.run_h()
        self.assertEqual(r['reason'],'ACTIVITY_STREAM_TYPE_UNKNOWN');self.assertFalse(r['engine_started'])
        self.assertEqual(self.state.read_bytes(),before)

    def test_short_remaining_deadline_does_not_truncate_observation_into_a_pass(self):
        before=self.state.read_bytes();r,end=self.run_h(seconds=60)
        self.assertEqual(r['reason'],'ACTIVITY_DEADLINE_INSUFFICIENT');self.assertEqual(before,self.state.read_bytes())
        self.assertFalse(r['engine_started'])

    def test_stop_request_during_observation_preserves_ledger_for_ctrl_c_or_ssh_paths(self):
        for cause in ('SIGINT','SIGHUP'):
            self.mode_error='interrupt';self.now=0;self.stop.clear();before=self.state.read_bytes()
            r,end=self.run_h()
            self.assertEqual(r['reason'],'ACTIVITY_OBSERVATION_INTERRUPTED');self.assertFalse(r['engine_started'])
            self.assertEqual(self.state.read_bytes(),before);self.assertEqual(end['status'],'PREPARATION_FAILED')

    def test_evidence_expiry_at_first_save_prevents_any_ledger_write(self):
        real=ExistingPaperStore.save;triggered=[False]
        def delayed(store,saved):
            if store.before_first_save is not None and not triggered[0]:
                triggered[0]=True;self.now+=MAX_START_DELAY+1
            return real(store,saved)
        before=self.state.read_bytes()
        with patch.object(ExistingPaperStore,'save',new=delayed):r,end=self.run_h()
        self.assertEqual(r['reason'],'ACTIVITY_START_EVIDENCE_EXPIRED');self.assertFalse(r['engine_started'])
        self.assertEqual(self.state.read_bytes(),before);self.assertEqual(end['status'],'PREPARATION_FAILED')

    def test_duplicate_start_blocks_before_observation(self):
        token=exp.begin(self.experiment);before=self.state.read_bytes()
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(before,self.state.read_bytes());self.assertEqual(len(self.created_streams),0)

    def test_unselected_wire_messages_are_ignored_without_changing_fixed_universe(self):
        self.pool=MARKETS+['KRW-BTC','KRW-ETH'];self.client.top_tickers.return_value=self.pool
        r,end=self.run_h(eligible=7,limit=9,replace=True)
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['fixed_strategy_markets'],MARKETS)
        self.assertEqual(r['wire_subscription'],self.pool);self.assertGreater(r['ignored_non_strategy_or_snapshot_messages'],0)
        self.assertEqual(len(self.created_streams),1)
