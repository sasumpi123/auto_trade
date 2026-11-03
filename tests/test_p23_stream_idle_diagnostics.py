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
    """Fake transport exposing a ping that would block forever; P23 must never call it."""
    def __init__(self,state='OPEN'):
        self.state=state;self.pings=0;self.release=threading.Event()
    def connection_state(self):return self.state
    def send_ping(self):
        self.pings+=1;self.release.wait(30);return threading.Event()


class LockedSocket:
    """Mimics websockets 17.4 sync: ping() and close() share protocol_mutex; sendall under it blocks."""
    class State:
        def __init__(self,name):self.name=name
    def __init__(self):
        self.protocol_mutex=threading.Lock();self.state=self.State('OPEN');self.pings=0;self.release=threading.Event()
    def ping(self,*a,**k):
        self.pings+=1
        with self.protocol_mutex:self.release.wait(30)  # Blocked sendall while holding the mutex.
        return threading.Event()
    def close(self):
        with self.protocol_mutex:self.state=self.State('CLOSED')


class ProbeUnitTests(unittest.TestCase):
    def test_open_connection_reports_unsupported_without_sending(self):
        s=FakeProbeStream();self.addCleanup(s.release.set)
        started=time.monotonic();r=sd.probe(s)
        self.assertLess(time.monotonic()-started,.1);self.assertEqual(s.pings,0)
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNSUPPORTED','LIVE_PING_SEND_NOT_BOUNDED',False))
        self.assertEqual((r['connection_before'],r['connection_after']),('OPEN','OPEN'))
        self.assertFalse(any(t.name.startswith('p23') for t in threading.enumerate()))

    def test_closed_or_closing_connection_is_never_a_pong(self):
        for state in ('CLOSING','CLOSED','UNKNOWN'):
            with self.subTest(state=state):
                r=sd.probe(FakeProbeStream(state=state))
                self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','CONNECTION_NOT_OPEN',False))

    def test_cancel_unsupported_and_broken_state(self):
        s=FakeProbeStream();r=sd.probe(s,cancelled=lambda:True)
        self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('CANCELLED','STOP_REQUESTED',False))
        self.assertEqual(sd.probe(object())['pong'],'UNSUPPORTED');self.assertEqual(sd.probe(None)['pong'],'UNSUPPORTED')
        class Broken(FakeProbeStream):
            def connection_state(self):raise RuntimeError(CANARY)
        r=sd.probe(Broken())
        self.assertEqual((r['connection_before'],r['pong']),('UNKNOWN','UNCONFIRMED'));self.assertNotIn(CANARY,json.dumps(r))
        def boom():raise RuntimeError(CANARY)
        r=sd.probe(FakeProbeStream(),cancelled=boom)
        self.assertEqual((r['pong'],r['probe_reason']),('ERROR','PROBE_FAILED'));self.assertNotIn(CANARY,json.dumps(r))

    def test_validate_fails_closed_and_record_is_small(self):
        self.assertEqual(sd.validate({'schema':sd.SCHEMA,'status':'TRIGGERED','trade_price':1})['status'],'UNAVAILABLE')
        self.assertEqual(sd.validate(None)['status'],'UNAVAILABLE')
        self.assertEqual(sd.validate(sd.not_triggered()),{'schema':sd.SCHEMA,'status':'NOT_TRIGGERED'})


class MarketStreamProbeTests(unittest.TestCase):
    def test_locked_blocking_sender_cannot_delay_close(self):
        """Review regression: a ping holding protocol_mutex would block close(); P23 never pings."""
        from services.market_stream import MarketStream
        stream=MarketStream([TICKER],timeout=1);sock=LockedSocket();stream.socket=sock
        self.addCleanup(sock.release.set)
        self.assertFalse(hasattr(stream,'send_ping'))
        r=sd.probe(stream)
        self.assertEqual((r['pong'],r['probe_reason'],sock.pings),('UNSUPPORTED','LIVE_PING_SEND_NOT_BOUNDED',0))
        self.assertTrue(sock.protocol_mutex.acquire(blocking=False));sock.protocol_mutex.release()
        started=time.monotonic();stream.close()
        self.assertLess(time.monotonic()-started,.1);self.assertIsNone(stream.socket)

    def test_real_websockets_close_after_probe(self):
        """websockets 17 sync client against a loopback-only server (no external network)."""
        from websockets.sync.server import serve
        from websockets.sync.client import connect
        from services.market_stream import MarketStream
        server=serve(lambda ws:[None for _ in ws],'127.0.0.1',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            port=server.socket.getsockname()[1]
            stream=MarketStream([TICKER],timeout=1)
            stream.socket=connect(f'ws://127.0.0.1:{port}',ping_interval=None,legacy=True)
            connection=stream.socket
            self.assertEqual(stream.connection_state(),'OPEN')
            r=sd.probe(stream)
            self.assertEqual((r['pong'],r['ping_sent'],connection.pending_pings),('UNSUPPORTED',False,{}))
            self.assertTrue(connection.protocol_mutex.acquire(blocking=False));connection.protocol_mutex.release()
            started=time.monotonic();stream.close()
            self.assertLess(time.monotonic()-started,1.0)
            self.assertEqual(connection.state.name,'CLOSED');self.assertEqual(stream.connection_state(),'UNKNOWN')
            stream.socket=connection
            r=sd.probe(stream)
            self.assertEqual((r['pong'],r['probe_reason'],r['ping_sent']),('UNCONFIRMED','CONNECTION_NOT_OPEN',False))
        finally:
            server.shutdown();thread.join(5)


class SessionDiagnosticsTests(unittest.TestCase):
    """Real PAPER engine and ledger; scripted Fake stream and clocks."""
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)/'paper-state.json'
        e=ExecutionEngine(self.state,1_000_000);e.open();e.close()
        self.stop=threading.Event();self.base=time.time();self.now=0.0;self.reports=[];self.actions=[]
        self.script=[];self.after_probe_gets=0;self.probe_seen=None;self.lock=threading.Lock()
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
            def connection_state(owner_stream):
                saved=json.loads(owner.state.read_text(encoding='utf-8'))
                if owner.probe_seen is None:
                    owner.probe_seen={'halt_reason':saved.get('halt_reason'),'orders':len(saved['orders']),'closed':owner_stream.closed}
                return 'CLOSED' if owner_stream.closed else probe.state
            def send_ping(self):
                with owner.lock:return probe.send_ping()  # Blocked send holding the close lock.
            def close(owner_stream):
                # Same lock a blocked websockets sender would hold; bounded so a regression fails, not hangs.
                acquired=owner.lock.acquire(timeout=1)
                if acquired:owner.lock.release()
                owner_stream.closed=acquired
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

    def test_probe_close_and_terminal_emission_complete_without_forced_termination(self):
        cases=(('OPEN','UNSUPPORTED','LIVE_PING_SEND_NOT_BOUNDED'),('CLOSED','UNCONFIRMED','CONNECTION_NOT_OPEN'))
        original=self.state.read_bytes()
        for state,pong,reason in cases:
            with self.subTest(state=state):
                self.state.write_bytes(original);self.stop.clear();self.probe_seen=None;self.after_probe_gets=0;self.reports=[]
                self.silent_gap(15.093);probe=FakeProbeStream(state=state);self.addCleanup(probe.release.set)
                started=time.monotonic();r=self.session(self.stream_class(probe))
                self.assertLess(time.monotonic()-started,sd.BUDGET_SECONDS)
                d=r['stream_idle_diagnostics']
                self.assertEqual((r['status'],r['reason'],d['pong'],d['probe_reason'],d['ping_sent']),
                    ('HALTED','PAPER_STREAM_TIMEOUT',pong,reason,False))
                self.assertEqual(probe.pings,0);self.assertTrue(self.stream.closed)  # close got the lock.
                self.assertEqual(self.probe_seen,{'halt_reason':'PAPER_SESSION_FAILURE','orders':0,'closed':False})
                self.assertEqual(self.after_probe_gets,0)
                self.assertEqual(self.reports[-1]['stream_idle_diagnostics'],d)  # Final event was emitted.
                self.assertNotIn('cleanup_error',r)

    def test_cancellation_or_deadline_after_halt_still_closes_and_emits(self):
        probe=FakeProbeStream();self.addCleanup(probe.release.set);flag=[False]
        from trading.execution import ExecutionEngine as Engine
        original=Engine.halt
        def halt(engine,reason):original(engine,reason);flag[0]=True  # Supervisor stop/deadline right after HALT.
        self.silent_gap(15.093)
        with patch.object(Engine,'halt',halt):
            r=run_session(self.state,seconds=30,stopped=lambda:flag[0],emit=self.reports.append,client=Mock(),
                stream_factory=self.stream_class(probe),analyzer_factory=self.factory,tickers=[TICKER],
                monotonic=lambda:self.now,wall=lambda:self.base+self.now)
        d=r['stream_idle_diagnostics']
        self.assertEqual((r['status'],r['reason'],d['pong'],d['probe_reason']),('HALTED','PAPER_STREAM_TIMEOUT','CANCELLED','STOP_REQUESTED'))
        self.assertTrue(self.stream.closed);self.assertEqual(self.reports[-1]['stream_idle_diagnostics'],d);self.assertEqual(probe.pings,0)

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

    def test_diagnostic_exception_keeps_original_reason_and_cleanup(self):
        original=self.state.read_bytes()
        with patch.object(sd,'diagnose',side_effect=RuntimeError(CANARY)):
            self.silent_gap(15.093);r=self.session(self.stream_class())
        self.assertEqual((r['status'],r['reason'],r['stream_idle_diagnostics']['status']),('HALTED','PAPER_STREAM_TIMEOUT','UNAVAILABLE'))
        self.assertNotIn(CANARY,json.dumps(self.reports))
        self.assertTrue(self.stream.closed);self.assertEqual(self.reports[-1]['stream_idle_diagnostics']['status'],'UNAVAILABLE')
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['halt_reason'],'PAPER_SESSION_FAILURE')
        del original

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
