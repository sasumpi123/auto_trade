"""P23 STREAM_IDLE diagnostics. Synthetic/Fake I/O only: proves the record, not a network cause."""
import inspect
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from trading.execution import ExecutionEngine
from tests.test_p9b_paper_slack_e2e import SignalAnalyzer
from tests.test_p0_safety import TICKER
from ops.paper_session import run_session
from services import stream_diagnostics as sd
from services.tick_timing import TickTiming, TickTimingError

CANARY='private-token-canary-p23'


class FakeProbeStream:
    """Fake transport with the P23 probe surface; no socket."""
    def __init__(self,state='OPEN',pong=True,error=False,hang=False,close_during_wait=False,set_on_close=False):
        self.state=state;self.pong=pong;self.error=error;self.hang=hang;self.pings=0
        self.close_during_wait=close_during_wait;self.set_on_close=set_on_close;self.release=threading.Event()
    def connection_state(self):return self.state
    def send_ping(self):
        self.pings+=1
        if self.error:raise RuntimeError(CANARY)
        if self.hang:self.release.wait(30);return threading.Event()
        event=threading.Event()
        if self.pong:event.set()
        if self.close_during_wait:
            self.state='CLOSED'
            if self.set_on_close:event.set()
        return event


class ProbeUnitTests(unittest.TestCase):
    def test_pong_received_on_open_connection_is_confirmed(self):
        r=sd.probe(FakeProbeStream())
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('CONFIRMED','PONG_RECEIVED_CONNECTION_OPEN',True))
        self.assertEqual((r['connection_before'],r['connection_after']),('OPEN','OPEN'))

    def test_no_pong_is_unconfirmed_within_budget(self):
        started=time.monotonic();r=sd.probe(FakeProbeStream(pong=False),budget=.2)
        self.assertLess(time.monotonic()-started,.6)
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','NO_PONG_WITHIN_BUDGET',True))

    def test_connection_close_is_never_counted_as_pong(self):
        for set_on_close in (False,True):
            with self.subTest(set_on_close=set_on_close):
                r=sd.probe(FakeProbeStream(pong=False,close_during_wait=True,set_on_close=set_on_close),budget=.2)
                self.assertEqual(r['pong'],'UNCONFIRMED');self.assertEqual(r['connection_after'],'CLOSED')
        r=sd.probe(FakeProbeStream(state='CLOSING'))
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','CONNECTION_NOT_OPEN',False))

    def test_send_error_is_fixed_code_without_exception_text(self):
        s=FakeProbeStream(error=True);r=sd.probe(s)
        self.assertEqual((r['pong'],r['probe_reason']),('ERROR','PING_SEND_FAILED'))
        self.assertNotIn(CANARY,json.dumps(r))

    def test_blocking_send_is_abandoned_at_two_second_budget(self):
        s=FakeProbeStream(hang=True);self.addCleanup(s.release.set)
        started=time.monotonic();r=sd.probe(s)
        elapsed=time.monotonic()-started
        self.assertLessEqual(elapsed,sd.BUDGET_SECONDS+.3);self.assertGreaterEqual(elapsed,sd.BUDGET_SECONDS-.05)
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','PING_SEND_NOT_COMPLETED_WITHIN_BUDGET',None))
        self.assertEqual(sd.BUDGET_SECONDS,2.0)

    def test_cancel_unsupported_and_unknown_state(self):
        r=sd.probe(FakeProbeStream(),cancelled=lambda:True)
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('CANCELLED','STOP_REQUESTED',False))
        self.assertEqual(sd.probe(object())['pong'],'UNSUPPORTED');self.assertEqual(sd.probe(None)['pong'],'UNSUPPORTED')
        class Broken(FakeProbeStream):
            def connection_state(self):raise RuntimeError(CANARY)
        r=sd.probe(Broken())
        self.assertEqual((r['connection_before'],r['pong']),('UNKNOWN','UNCONFIRMED'))

    def test_validate_fails_closed_and_record_is_small(self):
        self.assertEqual(sd.validate({'schema':sd.SCHEMA,'status':'TRIGGERED','trade_price':1})['status'],'UNAVAILABLE')
        self.assertEqual(sd.validate(None)['status'],'UNAVAILABLE')
        self.assertEqual(sd.validate(sd.not_triggered()),{'schema':sd.SCHEMA,'status':'NOT_TRIGGERED'})


class MarketStreamProbeTests(unittest.TestCase):
    """Real websockets 17 sync client against a loopback-only server (no external network)."""
    def test_real_library_pong_and_closed_state(self):
        from websockets.sync.server import serve
        from websockets.sync.client import connect
        from services.market_stream import MarketStream
        server=serve(lambda ws:[None for _ in ws],'127.0.0.1',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            port=server.socket.getsockname()[1]
            stream=MarketStream([TICKER],timeout=1)
            stream.socket=connect(f'ws://127.0.0.1:{port}',ping_interval=None,legacy=True)
            self.assertEqual(stream.connection_state(),'OPEN')
            r=sd.probe(stream)
            self.assertEqual((r['pong'],r['connection_after']),('CONFIRMED','OPEN'))
            stream.socket.close()
            self.assertEqual(stream.connection_state(),'CLOSED')
            r=sd.probe(stream)
            self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','CONNECTION_NOT_OPEN',False))
            stream.socket=None;self.assertEqual(stream.connection_state(),'UNKNOWN')
        finally:
            server.shutdown();thread.join(5)


class SessionDiagnosticsTests(unittest.TestCase):
    """Real PAPER engine and ledger; scripted Fake stream and clocks."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'paper-state.json'
        e=ExecutionEngine(self.state,1_000_000);e.open();e.close()
        self.stop=threading.Event();self.base=time.time();self.now=0.0;self.reports=[];self.actions=[]
        self.script=[];self.after_probe_gets=0;self.probe_seen=None
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        blocker.start();self.addCleanup(blocker.stop)

    def factory(self,ticker):
        a=SignalAnalyzer(ticker);a.actions=self.actions.copy();return a

    def stream_class(self,probe=None):
        owner=self
        class Stream:
            def __init__(self,codes):owner.stream=self;self.closed=False
            def connect(self):pass
            def get(self):
                if owner.probe_seen is not None:owner.after_probe_gets+=1
                if not owner.script:owner.stop.set();raise TimeoutError()
                kind,at,offset=owner.script.pop(0);owner.now=at
                if kind=='timeout':raise TimeoutError()
                return {'type':'ticker','code':TICKER,'trade_price':10000,'timestamp':int(owner.base*1000)+offset}
            def close(self):self.closed=True
        if probe is None:return Stream
        class ProbeStream(Stream):
            def connection_state(self):return 'CLOSED' if self.closed else probe.state
            def send_ping(self):
                saved=json.loads(owner.state.read_text(encoding='utf-8'))
                owner.probe_seen={'halt_reason':saved.get('halt_reason'),'orders':len(saved['orders']),'closed':self.closed}
                return probe.send_ping()
        return ProbeStream

    def session(self,stream,seconds=30):
        self.now=0.0
        return run_session(self.state,seconds=seconds,stopped=self.stop.is_set,emit=self.reports.append,
            client=Mock(),stream_factory=stream,analyzer_factory=self.factory,tickers=[TICKER],
            monotonic=lambda:self.now,wall=lambda:self.base+self.now)

    def silent_gap(self,gap):
        # Two advancing ticks, then only receive timeouts (1 s polls) until the gap.
        # Poll phase is not aligned to the last advance, so the first poll past 15 s can be 15.093 s.
        self.script=[('tick',1.0,0),('tick',2.0,1000)]
        self.script+=[('timeout',round(2.0+gap-k,6),0) for k in range(int(gap),-1,-1) if gap-k>0]

    def test_synthetic_15093ms_silent_gap_records_recv_timeout(self):
        self.silent_gap(15.093)
        r=self.session(self.stream_class())
        self.assertEqual((r['status'],r['reason']),('HALTED','PAPER_STREAM_TIMEOUT'))
        d=r['stream_idle_diagnostics']
        self.assertEqual((d['status'],d['trigger_path'],d['message_kind']),('TRIGGERED','RECV_TIMEOUT',None))
        self.assertAlmostEqual(d['idle_ms'],15093.0,places=3)
        self.assertAlmostEqual(d['since_last_message_ms'],15093.0,places=3)
        self.assertAlmostEqual(d['since_last_advance_ms'],15093.0,places=3)
        self.assertEqual(d['counters'],{'stream_messages':2,'timing_received':2,'timing_accepted':2,
            'equal_timestamp':0,'discarded':0,'ignored_non_strategy':0})
        self.assertEqual((d['pong'],d['probe_reason']),('UNSUPPORTED','STREAM_PROBE_UNSUPPORTED'))
        self.assertEqual(d['order'],'ENGINE_HALT_CONFIRMED_BEFORE_PROBE')
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['halt_reason'],'PAPER_SESSION_FAILURE')
        self.assertLessEqual(len(json.dumps(d)),sd.MAX_RECORD_BYTES)
        self.assertNotIn('10000',json.dumps(d))

    def test_equal_timestamp_repeats_are_distinguished_from_no_receive(self):
        self.script=[('tick',1.0,0),('tick',2.0,1000)]+[('tick',2.0+i,1000) for i in range(1,16)]
        r=self.session(self.stream_class())
        self.assertEqual(r['reason'],'PAPER_STREAM_TIMEOUT')
        d=r['stream_idle_diagnostics']
        self.assertEqual((d['trigger_path'],d['message_kind']),('RECEIVED_MESSAGE_CHECK','STRATEGY_TICKER'))
        self.assertEqual(d['since_last_message_ms'],0.0);self.assertAlmostEqual(d['idle_ms'],15000.0)
        self.assertEqual(d['counters']['equal_timestamp'],15);self.assertEqual(d['counters']['stream_messages'],17)

    def test_pong_confirmed_unconfirmed_and_closed_after_halt(self):
        cases=(('OPEN',True,'CONFIRMED'),('OPEN',False,'UNCONFIRMED'),('CLOSED',True,'UNCONFIRMED'))
        original=self.state.read_bytes()
        for state,pong,expected in cases:
            with self.subTest(state=state,pong=pong):
                self.state.write_bytes(original);self.stop.clear();self.probe_seen=None;self.after_probe_gets=0
                self.silent_gap(15.093);probe=FakeProbeStream(state=state,pong=pong)
                with patch.object(sd,'BUDGET_SECONDS',.2),patch.object(sd.diagnose,'__kwdefaults__',{**sd.diagnose.__kwdefaults__,'budget':.2}):
                    r=self.session(self.stream_class(probe))
                d=r['stream_idle_diagnostics']
                self.assertEqual((r['status'],r['reason'],d['pong']),('HALTED','PAPER_STREAM_TIMEOUT',expected))
                if state=='OPEN':
                    self.assertEqual(self.probe_seen,{'halt_reason':'PAPER_SESSION_FAILURE','orders':0,'closed':False})
                else:self.assertIsNone(self.probe_seen)
                self.assertEqual(self.after_probe_gets,0);self.assertTrue(self.stream.closed)

    def test_halt_precedes_probe_and_no_strategy_or_order_after_halt(self):
        self.actions=['BUY'];self.silent_gap(15.093);probe=FakeProbeStream()
        calls=[]
        original=__import__('trading.auto_trade',fromlist=['AutoTrade']).AutoTrade.process_market_data
        def spy(bot,t,data):calls.append(self.probe_seen);return original(bot,t,data)
        with patch('trading.auto_trade.AutoTrade.process_market_data',spy):
            r=self.session(self.stream_class(probe))
        self.assertEqual(self.probe_seen['halt_reason'],'PAPER_SESSION_FAILURE')
        self.assertEqual(self.probe_seen['orders'],r['orders']);self.assertEqual(r['orders'],1)
        self.assertTrue(all(c is None for c in calls))  # Every strategy call happened before the probe.
        self.assertEqual(r['pending_orders'],0);self.assertTrue(r['halted'])

    def test_diagnostic_exception_or_timeout_keeps_original_reason(self):
        original=self.state.read_bytes()
        with patch.object(sd,'diagnose',side_effect=RuntimeError(CANARY)):
            self.silent_gap(15.093);r=self.session(self.stream_class())
        self.assertEqual((r['status'],r['reason'],r['stream_idle_diagnostics']['status']),('HALTED','PAPER_STREAM_TIMEOUT','UNAVAILABLE'))
        self.assertNotIn(CANARY,json.dumps(self.reports))
        self.state.write_bytes(original);self.stop.clear();self.probe_seen=None
        probe=FakeProbeStream(hang=True);self.addCleanup(probe.release.set)
        self.silent_gap(15.093);started=time.monotonic();r=self.session(self.stream_class(probe))
        self.assertLessEqual(time.monotonic()-started,sd.BUDGET_SECONDS+1.5)
        d=r['stream_idle_diagnostics']
        self.assertEqual((r['status'],r['reason'],d['pong'],d['probe_reason']),
            ('HALTED','PAPER_STREAM_TIMEOUT','UNCONFIRMED','PING_SEND_NOT_COMPLETED_WITHIN_BUDGET'))
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['halt_reason'],'PAPER_SESSION_FAILURE')

    def test_normal_stop_and_other_halts_are_not_triggered(self):
        self.silent_gap(14.0);self.script=self.script[:-1]
        r=self.session(self.stream_class())
        self.assertEqual(r['status'],'STOPPED');self.assertEqual(r['stream_idle_diagnostics'],sd.not_triggered())
        self.stop.clear();self.script=[('tick',1.0,0),('tick',2.0,-61_000)]
        r=self.session(self.stream_class())
        self.assertEqual(r['status'],'HALTED');self.assertNotEqual(r['reason'],'PAPER_STREAM_TIMEOUT')
        self.assertEqual(r['stream_idle_diagnostics'],sd.not_triggered())

    def test_fifteen_second_policy_unchanged(self):
        g=TickTiming([TICKER],1000.0,0.0)
        g.idle(1014.999,14.999)
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):g.idle(1015.0,15.0)
        self.silent_gap(14.999);self.script=self.script[:-1]+[('timeout',2.0+14.999,0)]
        self.assertEqual(self.session(self.stream_class())['status'],'STOPPED')

    def test_terminal_view_carries_allowlisted_record_and_supervisor_grace_unchanged(self):
        from ops.paper_bot import view
        from ops.paper_watch import supervise
        self.silent_gap(15.093);r=self.session(self.stream_class())
        self.assertEqual(view(r)['stream_idle_diagnostics'],r['stream_idle_diagnostics'])
        self.assertEqual(set(r['stream_idle_diagnostics']),sd.KEYS)
        self.assertEqual(inspect.signature(supervise).parameters['grace'].default,15)

    def test_p22_frozen_sources_unchanged(self):
        from backtesting.p22_forward import frozen
        self.assertEqual(frozen()['schema'],'P22_FROZEN_FORWARD_V1')


class SummaryToolTests(unittest.TestCase):
    def test_eight_line_summary_without_raw_lines(self):
        from tools.p23_stream_idle_summary import main
        import contextlib,io
        with tempfile.TemporaryDirectory() as d:
            log=Path(d)/'run.log';report=Path(d)/'report.json'
            diag={'schema':sd.SCHEMA,'status':'TRIGGERED','trigger_path':'RECV_TIMEOUT','idle_ms':15093.055,'pong':'UNCONFIRMED'}
            log.write_text('noise '+CANARY+'\n'+json.dumps({'status':'RUNNING','last_price':'99999'})+'\n'
                +json.dumps({'status':'HALTED','reason':'PAPER_STREAM_TIMEOUT','stream_idle_diagnostics':diag})+'\n',encoding='utf-8')
            report.write_text(json.dumps({'session_state':'HALTED_NO_RESTART','summary':{'cumulative_buys':0}}),encoding='utf-8')
            out=io.StringIO()
            with contextlib.redirect_stdout(out):code=main([str(log),str(report),'2','0'])
        text=out.getvalue();lines=text.splitlines()
        self.assertEqual(code,0);self.assertLessEqual(len(lines),8)
        self.assertIn('PAPER_STREAM_TIMEOUT',text);self.assertIn('15093.055',text);self.assertIn('RECV_TIMEOUT',text)
        self.assertNotIn(CANARY,text);self.assertNotIn('99999',text);self.assertNotIn(d,text)


if __name__=='__main__':unittest.main()
