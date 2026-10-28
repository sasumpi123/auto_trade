"""Synthetic continuation of the reported -2ms pair; no captured Mac tick stream."""
import inspect
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
