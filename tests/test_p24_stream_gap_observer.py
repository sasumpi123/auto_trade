"""P24 observer. Synthetic clocks/streams and loopback only; proves the record, never an Upbit cause."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ops import stream_gap_observer as obs

PRICE_CANARY=987654321
ROOT=Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):self.t=0.0
    def mono(self):return self.t
    def wall(self):return 1_800_000_000.0+self.t
    def sleep(self,s):self.t=round(self.t+s,6)


def tick(stamp,kind='REALTIME'):
    return {'type':'ticker','code':'KRW-BTC','timestamp':stamp,'stream_type':kind,'trade_price':PRICE_CANARY}


class FakeStream:
    """Scripted: list of (due_time, message | 'CLOSE' | 'ERROR'). get() never blocks."""
    def __init__(self,clock,script,*,close_error=False,connect_error=False):
        self.clock=clock;self.script=list(script);self.closed=False;self.close_error=close_error;self.connect_error=connect_error
    def connect(self):
        if self.connect_error:raise OSError('private-token-canary')
    def connection_state(self):return 'CLOSED' if self.closed else 'OPEN'
    def get(self):
        if self.script and self.script[0][0]<=self.clock.t:
            _,item=self.script.pop(0)
            if item=='CLOSE':
                from websockets.exceptions import ConnectionClosed
                from websockets.frames import Close
                self.closed=True;raise ConnectionClosed(None,Close(1011,'keepalive ping timeout'))
            if item=='ERROR':raise ValueError('private-token-canary')
            return item
        raise TimeoutError()
    def close(self):
        self.closed=True
        if self.close_error:raise OSError('private-token-canary')


def steady(start,end,step=1.0,stamp0=1000,equal=False):
    out=[];t=start;i=0
    while t<end:
        out.append((round(t,6),tick(stamp0 if equal else stamp0+i*1000)));t+=step;i+=1
    return out


class ObserveTests(unittest.TestCase):
    def run_observe(self,scripts,budget=120,stopped=lambda:False,**kw):
        clock=Clock();captures=[];streams=[]
        def factory(codes):
            self.assertEqual(codes,['KRW-BTC'])
            spec=scripts[len(streams)];s=FakeStream(clock,spec.get('script',[]),close_error=spec.get('close_error',False),
                connect_error=spec.get('connect_error',False));streams.append(s);return s
        r=obs.observe(budget,stream_factory=factory,stopped=stopped,monotonic=clock.mono,wall=clock.wall,
                      sleep=clock.sleep,on_capture=captures.append,**kw)
        self.streams=streams;self.clock=clock
        return r,captures

    def assertNoSecrets(self,r):
        raw=json.dumps(r)
        self.assertNotIn(str(PRICE_CANARY),raw);self.assertNotIn('private-token-canary',raw)

    def test_both_connections_gap_then_recover(self):
        both=steady(0,10)+steady(30,60,stamp0=50_000)
        r,caps=self.run_observe([{'script':both},{'script':list(both)}])
        self.assertEqual((r['status'],r['reason']),('REPRODUCED','RECOVERY_WINDOW_COMPLETE'))
        self.assertEqual(len(caps),1);self.assertEqual(caps[0],r['capture']);self.assertTrue(r['capture_persisted'])
        c=r['capture']
        self.assertAlmostEqual(c['at_elapsed_ms'],24000.0,delta=60)  # last receive at 9 s + 15 s
        for name in ('C1','C2'):
            self.assertEqual(c['connections'][name]['gap_kind'],'NO_RECEIVE')
            self.assertGreaterEqual(c['connections'][name]['since_last_receive_ms'],15000)
            rec=r['connections'][name]['recovery']
            self.assertEqual(rec['classification'],'RECEIVE_AND_ADVANCE_RESUMED')
            self.assertAlmostEqual(rec['first_receive_after_capture_ms'],6000,delta=60)
        self.assertEqual(r['recovery_window_observed_ms'],30000.0)
        self.assertEqual(r['pong']['status'],'UNMEASURED');self.assertEqual(r['reconnections'],0)
        self.assertTrue(all(s.closed for s in self.streams));self.assertNoSecrets(r)

    def test_only_one_connection_gaps(self):
        r,_=self.run_observe([{'script':steady(0,5)+steady(60,90,stamp0=90_000)},{'script':steady(0,90)}])  # C1 resumes after window
        self.assertEqual(r['status'],'REPRODUCED')
        self.assertEqual(r['capture']['connections']['C1']['gap_kind'],'NO_RECEIVE')
        self.assertEqual(r['capture']['connections']['C2']['gap_kind'],'NONE')
        self.assertEqual(r['connections']['C1']['recovery']['classification'],'NOT_RECOVERED_WITHIN_WINDOW')
        self.assertEqual(r['connections']['C2']['recovery']['classification'],'NOT_GAPPED_AT_CAPTURE')
        self.assertFalse(r['connections']['C2']['recovery']['gap_during_window'])

    def test_equal_timestamps_are_no_advance_not_no_receive(self):
        script=steady(0,2)+steady(2,80,stamp0=1000+1000,equal=True)
        r,_=self.run_observe([{'script':script},{'script':list(script)}])
        c=r['capture']['connections']['C1']
        self.assertEqual(c['gap_kind'],'NO_ADVANCE_WITH_RECEIVE')
        self.assertLess(c['since_last_receive_ms'],1100);self.assertGreaterEqual(c['since_last_advance_ms'],15000)
        self.assertGreater(c['equal_timestamp'],10)
        self.assertEqual(r['connections']['C1']['recovery']['classification'],'RECEIVE_ONLY_NO_ADVANCE')

    def test_threshold_boundary_matches_operating_rule(self):
        r,_=self.run_observe([{'script':steady(0,1.5)+steady(15.95,80,stamp0=9000)},{'script':steady(0,80)}],budget=70)
        self.assertEqual(r['status'],'NOT_REPRODUCED')  # 14.95 s gap only.
        self.assertLess(r['connections']['C1']['max_receive_gap_ms'],15000)
        r,_=self.run_observe([{'script':steady(0,1.5)+steady(16.05,90,stamp0=9000)},{'script':steady(0,90)}],budget=80)  # 15.05 s
        self.assertEqual(r['status'],'REPRODUCED')
        self.assertEqual(obs.GAP_SECONDS,15);self.assertEqual(obs.RECOVERY_SECONDS,30)

    def test_regression_invalid_and_snapshot_counts(self):
        script=[(0,tick(5000)),(0.5,tick(4997)),(1,tick(6000,'SNAPSHOT')),(1.5,{'type':'other'}),(2,tick('x'))]+steady(3,80,stamp0=10_000)
        r,_=self.run_observe([{'script':script},{'script':steady(0,80)}],budget=70)
        c=r['connections']['C1']
        self.assertEqual((c['regressed'],c['snapshot'],c['invalid']),(1,1,2));self.assertNoSecrets(r)

    def test_not_reproduced_when_no_gap(self):
        r,caps=self.run_observe([{'script':steady(0,100)},{'script':steady(0,100)}],budget=60)
        self.assertEqual((r['status'],r['reason'],r['capture']),('NOT_REPRODUCED','NOT_REPRODUCED_DEADLINE',None))
        self.assertEqual(caps,[])

    def test_connection_close_before_gap_is_incomplete_with_codes(self):
        r,_=self.run_observe([{'script':steady(0,5)+[(5.5,'CLOSE')]},{'script':steady(0,80)}])
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','CONNECTION_ENDED_BEFORE_GAP'))
        c=r['connections']['C1']
        self.assertEqual((c['end_reason'],c['close_sent_code'],c['keepalive_timeout_close']),('CONNECTION_CLOSED',1011,True))
        self.assertTrue(all(s.closed for s in self.streams))

    def test_read_error_connect_failure_and_cleanup_failure(self):
        r,_=self.run_observe([{'script':[(1,'ERROR')]},{'script':steady(0,80)}])
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','CONNECTION_ENDED_BEFORE_GAP'));self.assertNoSecrets(r)
        r,_=self.run_observe([{'script':[]},{'connect_error':True}])
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','CONNECT_FAILED'));self.assertNoSecrets(r)
        self.assertTrue(self.streams[0].closed)
        r,_=self.run_observe([{'script':steady(0,100),'close_error':True},{'script':steady(0,100)}],budget=60)
        self.assertEqual((r['status'],r['cleanup_error']),('INCOMPLETE','STREAM_CLOSE_UNCONFIRMED'));self.assertNoSecrets(r)

    def test_stop_request_and_deadline_truncation_are_incomplete(self):
        flag=[0]
        def stopped():flag[0]+=1;return flag[0]>50
        r,_=self.run_observe([{'script':steady(0,100)},{'script':steady(0,100)}],stopped=stopped)
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','STOP_REQUESTED'))
        r,caps=self.run_observe([{'script':steady(0,40)},{'script':steady(0,40)}],budget=60)
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','RECOVERY_WINDOW_TRUNCATED'))
        self.assertEqual(len(caps),1);self.assertLess(r['recovery_window_observed_ms'],30000)

    def test_capture_persist_failure_is_incomplete(self):
        clock=Clock()
        def factory(_):return FakeStream(clock,steady(0,5)+steady(30,80,stamp0=90_000))
        def fail(_):raise OSError('disk')
        r=obs.observe(120,stream_factory=factory,monotonic=clock.mono,wall=clock.wall,sleep=clock.sleep,on_capture=fail)
        self.assertEqual((r['status'],r['capture_persisted']),('INCOMPLETE',False))


def _worker_ok(seconds,stop,pipe):
    pipe.send({'schema':obs.SCHEMA,'kind':'GAP_CAPTURED','at_elapsed_ms':1.0,'connections':{}})
    pipe.send({'schema':obs.SCHEMA,'kind':'FINAL','status':'REPRODUCED','reason':'RECOVERY_WINDOW_COMPLETE','capture':None})
    pipe.close()


def _worker_hang(seconds,stop,pipe):
    import signal
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
    pipe.send({'schema':obs.SCHEMA,'kind':'GAP_CAPTURED','at_elapsed_ms':2.0,'connections':{}})
    while True:time.sleep(.1)  # Ignores stop: supervisor must kill after grace.


def _worker_cooperative(seconds,stop,pipe):
    stop.wait(10)
    pipe.send({'schema':obs.SCHEMA,'kind':'FINAL','status':'INCOMPLETE','reason':'STOP_REQUESTED'});pipe.close()


def _worker_silent(seconds,stop,pipe):pipe.close()


class SupervisorTests(unittest.TestCase):
    def test_normal_result_and_capture_written_in_order(self):
        rows=[];r=obs.supervise(5,write=rows.append,worker=_worker_ok,grace=2)
        self.assertEqual([m['kind'] for m in rows],['GAP_CAPTURED','FINAL'])
        self.assertEqual((r['status'],r['forced_termination'],r['capture']['at_elapsed_ms']),('REPRODUCED',False,1.0))

    def test_hung_worker_is_killed_incomplete_and_capture_preserved(self):
        rows=[];started=time.monotonic();r=obs.supervise(1,write=rows.append,worker=_worker_hang,grace=1)
        self.assertLess(time.monotonic()-started,12)
        self.assertEqual((r['status'],r['reason'],r['forced_termination']),('INCOMPLETE','FORCED_TERMINATION',True))
        self.assertEqual(r['capture']['at_elapsed_ms'],2.0);self.assertEqual(rows[0]['kind'],'GAP_CAPTURED')

    def test_deadline_stop_and_missing_result(self):
        r=obs.supervise(1,write=lambda _:None,worker=_worker_cooperative,grace=5)
        self.assertEqual((r['status'],r['reason'],r['forced_termination']),('INCOMPLETE','STOP_REQUESTED',False))
        r=obs.supervise(5,write=lambda _:None,worker=_worker_silent,grace=1)
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','WORKER_RESULT_MISSING'))

    def test_write_failure_is_incomplete(self):
        def fail(_):raise OSError('disk')
        r=obs.supervise(5,write=fail,worker=_worker_ok,grace=2)
        self.assertEqual((r['status'],r['reason']),('INCOMPLETE','PRIVATE_OUTPUT_UNVERIFIED'))


class LoopbackTests(unittest.TestCase):
    """Real websockets 17 sync client through MarketStream against a loopback-only server."""
    def test_gap_recovery_and_clean_close_over_loopback(self):
        from websockets.sync.server import serve
        import websockets.sync.client as client
        from services.market_stream import MarketStream
        subscriptions=[];release=threading.Event()
        def handler(ws):
            subscriptions.append(json.loads(ws.recv()))
            for i in range(3):ws.send(json.dumps(tick(1000+i)))
            release.wait(5)
            for i in range(20):ws.send(json.dumps(tick(5000+i)));time.sleep(.05)
            try:
                for _ in ws:pass
            except Exception:pass
        server=serve(handler,'127.0.0.1',0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.socket.getsockname()[1];real=client.connect;seen=[]
        def redirected(url,**kw):
            seen.append((url,kw));return real(f'ws://127.0.0.1:{port}',legacy=True,**kw)
        try:
            starts=[]
            def capture(c):starts.append(c);threading.Timer(.1,release.set).start()
            with patch('websockets.sync.client.connect',redirected):
                started=time.monotonic()
                r=obs.observe(8,stream_factory=lambda codes:MarketStream(codes,timeout=0),on_capture=capture,gap=.4,recovery=1.0)
                elapsed=time.monotonic()-started
        finally:
            release.set();server.shutdown();thread.join(5)
        self.assertLess(elapsed,6)
        self.assertEqual((r['status'],r['reason'],r['cleanup_error']),('REPRODUCED','RECOVERY_WINDOW_COMPLETE',None))
        self.assertEqual(len(starts),1)
        classes=[r['connections'][n]['recovery']['classification'] for n in ('C1','C2')]
        self.assertIn('RECEIVE_AND_ADVANCE_RESUMED',classes)  # Real timing: the other may not have reached 0.4 s yet.
        self.assertTrue(set(classes)<={'RECEIVE_AND_ADVANCE_RESUMED','NOT_GAPPED_AT_CAPTURE'})
        for name in ('C1','C2'):
            c=r['connections'][name]
            self.assertEqual(c['connection_state'],'UNKNOWN')  # MarketStream.close() released the socket.
            self.assertEqual(c['applied_settings'],{'ping_interval':20,'ping_timeout':10,'close_timeout':2})
        self.assertEqual(len(seen),2)
        for url,kw in seen:
            self.assertEqual(url,obs.OPERATING_SETTINGS['url'])
            self.assertEqual(kw,{'open_timeout':10,'close_timeout':2,'ping_interval':20,'ping_timeout':10})
        self.assertEqual(subscriptions[0][1],{'type':'ticker','codes':['KRW-BTC'],'isOnlyRealtime':True})
        self.assertEqual(r['library_version'],__import__('websockets').__version__)
        self.assertNotIn(str(PRICE_CANARY),json.dumps(r))


class IsolationAndCliTests(unittest.TestCase):
    def test_no_account_or_trading_modules_are_imported(self):
        code=('import sys,json;import ops.stream_gap_observer as o;from services.market_stream import MarketStream;'
              'bad=[m for m in sys.modules if m.split(".")[0] in ("trading","strategies","data_analyzer","config","backtesting","automation","pyupbit","slack_sdk") '
              'or m.startswith("ops.paper") or m in ("ops.local_preflight","services.public_market","services.tick_timing")];print(json.dumps(bad))')
        out=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,env={**os.environ,'PYTHON_DOTENV_DISABLED':'1'})
        self.assertEqual(out.returncode,0,out.stderr);self.assertEqual(json.loads(out.stdout),[])
        source=(ROOT/'ops/stream_gap_observer.py').read_text(encoding='utf-8')
        for word in ('state_store','ExecutionEngine','paper_session','REAL_TRADING','--directory'):self.assertNotIn(word,source)

    def test_disabled_without_explicit_connection_and_no_network(self):
        out=io.StringIO()
        with patch.object(socket.socket,'connect',side_effect=AssertionError('network')),contextlib.redirect_stdout(out):
            self.assertEqual(obs.main([]),2)
        self.assertEqual(json.loads(out.getvalue())['status'],'DISABLED')
        with patch.object(obs,'supervise',side_effect=AssertionError('no run')),contextlib.redirect_stderr(io.StringIO()):
            for argv in (['--seconds','60'],['--connect-public','--seconds','3301','--output-dir','/tmp/x'],
                         ['--connect-public','--seconds','59','--output-dir','/tmp/x'],['--connect-public','--seconds','60']):
                with self.subTest(argv=argv),self.assertRaises(SystemExit):obs.main(argv)

    def test_output_directory_must_be_private_empty_and_outside_project(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'out';p.mkdir(mode=0o700)
            self.assertEqual(obs.private_directory(p),p.resolve())
            (p/'x').write_text('x')
            with self.assertRaisesRegex(ValueError,'OUTPUT_NOT_EMPTY'):obs.private_directory(p)
            q=Path(d)/'open';q.mkdir();q.chmod(0o755)
            with self.assertRaisesRegex(ValueError,'OUTPUT_NOT_PRIVATE'):obs.private_directory(q)
            link=Path(d)/'link';link.symlink_to(p)
            with self.assertRaisesRegex(ValueError,'SYMLINK'):obs.private_directory(link)
        with self.assertRaisesRegex(ValueError,'PROJECT'):obs.private_directory(ROOT/'docs')

    def test_cli_writes_private_files_and_prints_eight_lines(self):
        fake={'schema':obs.SCHEMA,'kind':'FINAL','status':'NOT_REPRODUCED','reason':'NOT_REPRODUCED_DEADLINE','capture':None,
              'connections':{'C1':{'received':3},'C2':{'received':3}},'pong':obs.PONG}
        def supervise(seconds,*,write):write(fake);return dict(fake)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'out';p.mkdir(mode=0o700);out=io.StringIO()
            with patch.object(obs,'supervise',supervise),contextlib.redirect_stdout(out):
                code=obs.main(['--connect-public','--seconds','60','--output-dir',str(p)])
            lines=out.getvalue().splitlines()
            self.assertEqual(code,0);self.assertLessEqual(len(lines),8);self.assertIn('NOT_REPRODUCED',lines[0])
            for name in (obs.EVENTS,obs.RESULT):
                self.assertEqual((p/name).stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads((p/obs.RESULT).read_text())['status'],'NOT_REPRODUCED')
            self.assertNotIn(d,out.getvalue())
        incomplete=dict(fake,status='INCOMPLETE',reason='FORCED_TERMINATION')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'out';p.mkdir(mode=0o700)
            with patch.object(obs,'supervise',lambda s,*,write:dict(incomplete)),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(obs.main(['--connect-public','--seconds','60','--output-dir',str(p)]),2)

    def test_operating_settings_mirror_market_stream(self):
        from unittest.mock import Mock
        from services.market_stream import MarketStream
        connection=Mock()
        with patch('websockets.sync.client.connect',return_value=connection) as connect:
            MarketStream(['KRW-BTC'],timeout=0).connect()
        s=obs.OPERATING_SETTINGS
        connect.assert_called_once_with(s['url'],open_timeout=s['open_timeout'],close_timeout=s['close_timeout'],
                                        ping_interval=s['ping_interval'],ping_timeout=s['ping_timeout'])
        sub=json.loads(connection.send.call_args.args[0])[1]
        self.assertEqual((sub['type'],sub['isOnlyRealtime']),(s['subscription_type'],s['isOnlyRealtime']))
        self.assertFalse(hasattr(MarketStream,'send_ping'))

    def test_p22_frozen_sources_unchanged(self):
        from backtesting.p22_forward import frozen
        self.assertEqual(frozen()['schema'],'P22_FROZEN_FORWARD_V1')


if __name__=='__main__':unittest.main()
