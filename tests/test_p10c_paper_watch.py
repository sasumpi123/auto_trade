"""Real PAPER engine, existing locks and Fake public I/O; never a real exchange call."""
import copy
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
from unittest.mock import Mock, patch

from trading.execution import ExecutionEngine
from tests.test_p9b_paper_slack_e2e import SignalAnalyzer
from tests.test_p0_safety import TICKER, OTHER
from ops.paper_session import run_session, offline_report, public_analyzer_factory
from ops.paper_watch import main, supervise


def _hang_worker(state,seconds,top,tickers,stop,pipe):
    pipe.send({'status':'RUNNING'})
    while True:time.sleep(.1)


def _cooperative_worker(state,seconds,top,tickers,stop,pipe):
    stop.wait(10)
    pipe.send({'status':'STOPPED','post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
    pipe.close()


class PaperWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.state=self.root/'paper-state.json'
        e=ExecutionEngine(self.state,1_000_000);e.open();e.close()
        self.stop=threading.Event();self.wall=time.time();self.now=0;self.reports=[]
        self.client=Mock();self.client.top_tickers.return_value=[TICKER]
        self.actions=['BUY','SELL'];self.stream_error=None;self.stamps=None
        owner=self
        class Stream:
            def __init__(self,codes):self.index=0;self.codes=codes;owner.stream=self
            def connect(self):pass
            def get(self):
                owner.now+=1;self.index+=1
                if owner.stream_error is not None:raise owner.stream_error
                if self.index==3:owner.stop.set()
                return {'type':'ticker','code':TICKER,'trade_price':10000 if self.index==1 else 10200,
                        'timestamp':owner.stamps if owner.stamps is not None else int(owner.wall*1000)}
            def close(self):self.closed=True
        self.stream_factory=Stream
        self.blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'))
        self.blocker.start();self.addCleanup(self.blocker.stop)

    def factory(self,ticker):
        analyzer=SignalAnalyzer(ticker);analyzer.actions=self.actions.copy();return analyzer

    def run_session(self,**kw):
        return run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=self.factory,
            monotonic=lambda:self.now,wall=lambda:self.wall,**kw)

    def test_signal_confirmed_ledger_summary_normal_shutdown_and_restart(self):
        result=self.run_session()
        self.assertEqual(result['status'],'STOPPED')
        self.assertEqual(result['post_validation'],'VERIFIED_UNDER_OWNER_LOCK')
        self.assertEqual(result['orders'],2);self.assertEqual(result['pending_orders'],0)
        self.assertEqual(result['session_ticks'],2);self.assertEqual(__import__('decimal').Decimal(result['cash']),1008000)
        self.assertFalse(result['halted']);self.assertTrue(self.stream.closed)
        before=json.loads(self.state.read_text(encoding='utf-8'))
        self.stop.clear();self.now=0;self.actions=[]
        second=self.run_session()
        self.assertEqual(second['orders'],2);self.assertEqual(second['session_ticks'],2)
        self.assertEqual(before['orders'],json.loads(self.state.read_text(encoding='utf-8'))['orders'])
        self.assertEqual(second['historical_ticks'],'NOT_RECORDED')

    def test_missing_state_or_lock_never_creates_or_initializes(self):
        self.state.unlink()
        with self.assertRaises(Exception):self.run_session()
        self.assertFalse(self.state.exists());self.client.top_tickers.assert_not_called()

    def test_busy_state_owner_stops_before_discovery_without_changing_disk(self):
        engine=ExecutionEngine(self.state,1_000_000);engine.open()
        try:
            before=self.state.read_bytes();r=self.run_session()
            self.assertEqual(r['status'],'HALTED');self.assertEqual(before,self.state.read_bytes())
            self.assertNotIn('cash',r);self.client.top_tickers.assert_not_called()
        finally:engine.close()

    def test_halted_pending_unknown_and_cash_mismatch_never_reset(self):
        original=json.loads(self.state.read_text(encoding='utf-8'))
        for issue in ('halt','pending','unknown','cash'):
            state=copy.deepcopy(original)
            if issue=='halt':state['halt_reason']='private-token-canary'
            elif issue=='cash':state['cash']='999'
            else:state['orders']={'id':{'identifier':'id','ticker':TICKER,'side':'bid','averaging':False,
                'uuid':None,'status':'intent' if issue=='pending' else 'UNKNOWN','value':'10000'}}
            self.state.write_text(json.dumps(state),encoding='utf-8');before=self.state.read_bytes()
            with self.subTest(issue=issue):
                self.assertEqual(self.run_session()['status'],'HALTED')
                self.assertEqual(before,self.state.read_bytes())
        self.client.top_tickers.assert_not_called()
        self.assertNotIn('private-token-canary',json.dumps(self.reports))

    def test_restored_held_market_is_not_silently_dropped_from_subscription(self):
        # Use first session to retain BTC, then select another ticker in the next session.
        self.actions=['BUY'];self.run_session();self.stop.clear();self.now=0;self.actions=[]
        self.client.top_tickers.return_value=[OTHER]
        r=self.run_session()
        self.assertEqual(set(self.stream.codes),{TICKER,OTHER})
        self.assertIn(TICKER,r['positions'])

    def test_network_failure_halts_and_does_not_disclose_error(self):
        self.stream_error=ValueError('private-token-canary')
        r=self.run_session()
        self.assertEqual(r['status'],'HALTED');self.assertTrue(r['halted'])
        self.assertNotIn('private-token-canary',json.dumps(self.reports))
        self.assertEqual(json.loads(self.state.read_text(encoding='utf-8'))['halt_reason'],'PAPER_SESSION_FAILURE')

    def test_stream_idle_timeout_and_stale_future_ticks_halt(self):
        original=self.state.read_bytes()
        for issue in ('timeout','old','future'):
            self.state.write_bytes(original);self.now=0;self.stop.clear()
            self.stream_error=TimeoutError() if issue=='timeout' else None
            self.stamps=int((self.wall+(-61 if issue=='old' else 6))*1000) if issue!='timeout' else None
            r=self.run_session()
            self.assertEqual(r['status'],'HALTED')
            self.assertEqual(r['orders'],0)

    def test_short_idle_poll_does_not_halt_before_time_limit(self):
        self.stream_error=TimeoutError()
        r=run_session(self.state,seconds=3,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=self.factory,
            monotonic=lambda:self.now,wall=lambda:self.wall)
        self.assertEqual(r['status'],'STOPPED');self.assertFalse(r['halted'])

    def test_user_stop_during_public_warmup_is_graceful_no_fill(self):
        def factory(t):
            a=self.factory(t)
            def stop():self.stop.set();raise ValueError('request cancelled')
            a.update_data=stop
            return a
        r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=factory)
        self.assertEqual(r['status'],'STOPPED');self.assertEqual(r['orders'],0)
        self.assertEqual(r['post_validation'],'VERIFIED_UNDER_OWNER_LOCK')

    def test_explicit_tickers_skip_network_discovery(self):
        self.run_session(tickers=[TICKER]);self.client.top_tickers.assert_not_called()

    def test_disk_failure_blocks_successful_post_validation(self):
        from trading.state_store import StateStore
        original=StateStore.save
        count=[0]
        def fail(store,state):
            count[0]+=1
            if count[0]>=2:raise OSError('private-disk-canary')
            return original(store,state)
        with patch.object(StateStore,'save',fail):r=self.run_session()
        self.assertEqual(r['status'],'HALTED')
        self.assertNotEqual(r['post_validation'],'VERIFIED_UNDER_OWNER_LOCK')
        self.assertNotIn('private-disk-canary',json.dumps(self.reports))

    def test_public_adapter_uses_same_indicators_and_signal_bodies_without_slack(self):
        from tests.test_backtesting import candles
        from data_analyzer.analyzer import DataAnalyzer
        frame=candles(200);frame['value']=1000
        frame['close']=[100+(i%13) for i in range(200)]
        client=Mock();client.candles.return_value=frame
        a=public_analyzer_factory(client)(TICKER);a.update_data()
        b=DataAnalyzer(TICKER);b.df=frame.copy();DataAnalyzer.calculate_indicators.__wrapped__(b)
        import pandas as pd
        pd.testing.assert_frame_equal(a.df,b.df)
        with patch('utils.decorators.notification_service.send_error_alert',side_effect=AssertionError('No Slack')):
            self.assertEqual(a.analyze()['action'],DataAnalyzer.analyze.__wrapped__(b)['action'])

    def test_cli_default_and_invalid_options_cannot_start_worker(self):
        with patch('ops.paper_watch.supervise',side_effect=AssertionError('must not start')),patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(main(['--state',str(self.state)]),2)
            for argv in (['--connect-public'],['--seconds','10'],['--connect-public','--seconds','3601'],
                         ['--connect-public','--seconds','10','--tickers',TICKER,TICKER]):
                with self.assertRaises(SystemExit):main(argv)

    def test_cli_refuses_live_slack_or_automation_flags(self):
        r=offline_report(self.state)
        r['preflight']['checks']['paper_slack_automation_off']='BLOCKED'
        with patch('ops.paper_watch.offline_report',return_value=r),patch('ops.paper_watch.supervise') as worker,patch('sys.stdout',new=io.StringIO()):
            self.assertEqual(main(['--connect-public','--seconds','10','--state',str(self.state)]),2)
            worker.assert_not_called()

    def test_default_import_and_dry_run_never_import_config_or_connect(self):
        code="""import socket,sys
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('No network'))
from ops.paper_watch import main
assert 'config' not in sys.modules
assert main(['--state','runtime/not-created.json'])==2
assert 'config' not in sys.modules
"""
        result=subprocess.run([sys.executable,'-X','utf8','-c',code],capture_output=True,text=True,encoding='utf-8',timeout=20)
        self.assertEqual(result.returncode,0)
        self.assertFalse(Path('runtime/not-created.json').exists())

    def test_supervisor_kills_unresponsive_worker_without_automatic_restart(self):
        output=[]
        started=time.monotonic()
        r=supervise(self.state,.2,10,None,emit=output.append,worker=_hang_worker,grace=.2)
        self.assertLess(time.monotonic()-started,12)
        self.assertEqual(r['status'],'HALTED');self.assertFalse(r['automatic_restart'])
        self.assertEqual(r['recovery_action'],'READ_ONLY_RECONCILIATION_REQUIRED')


    def test_supervisor_ctrl_c_requests_cooperative_stop_and_restores_handler(self):
        import signal
        old=signal.getsignal(signal.SIGINT)
        before=self.state.read_bytes()
        timer=threading.Timer(.3,lambda:signal.raise_signal(signal.SIGINT))
        timer.start()
        try:
            r=supervise(self.state,30,10,None,emit=lambda _:None,worker=_cooperative_worker,grace=10)
        finally:timer.cancel();timer.join(3)
        self.assertIs(signal.getsignal(signal.SIGINT),old)
        self.assertFalse(r['automatic_restart']);self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(r['status'],'STOPPED' if os.name=='posix' else 'HALTED')

    def test_public_rate_limit_has_fixed_diagnostic_no_retry(self):
        from services.public_market import PublicDataError
        self.client.top_tickers.side_effect=PublicDataError('PUBLIC_RATE_LIMIT')
        r=self.run_session()
        self.assertEqual(r['status'],'HALTED');self.assertEqual(r['reason'],'PUBLIC_RATE_LIMIT')
        self.assertEqual(self.client.top_tickers.call_count,1)
        self.assertEqual(r['orders'],0)

    def test_unexpected_websocket_message_type_never_trades(self):
        original=self.stream_factory
        def factory(codes):
            stream=original(codes);get=stream.get
            def bad():
                data=get();data['type']='unexpected';return data
            stream.get=bad;return stream
        self.stream_factory=factory
        r=self.run_session()
        self.assertEqual(r['status'],'HALTED');self.assertEqual(r['orders'],0)

    def test_clock_rollback_halts_before_processing_next_order(self):
        original=self.stream_factory
        def factory(codes):
            stream=original(codes);get=stream.get
            def jump():
                if stream.index==1:self.wall-=10
                return get()
            stream.get=jump;return stream
        self.stream_factory=factory
        r=self.run_session()
        self.assertEqual(r['status'],'HALTED');self.assertEqual(r['orders'],1)
        self.assertEqual(r['reason'],'PAPER_CLOCK_DISCONTINUITY')

    def test_one_silent_market_halts_even_when_other_market_is_active(self):
        original=self.stream_factory
        self.client.top_tickers.return_value=[TICKER,OTHER]
        def factory(codes):
            stream=original(codes);get=stream.get
            def slow():
                self.now+=30;self.wall+=31
                return get()
            stream.get=slow;return stream
        self.stream_factory=factory
        r=run_session(self.state,seconds=120,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=self.factory,
            monotonic=lambda:self.now,wall=lambda:self.wall)
        self.assertEqual(r['status'],'HALTED');self.assertEqual(r['orders'],1)
        self.assertEqual(r['reason'],'PAPER_MARKET_STALE')

    def test_multiple_discovered_markets_trade_and_account_without_seed_ticker_assumption(self):
        self.client.top_tickers.return_value=[TICKER,OTHER]
        owner=self
        class Stream:
            def __init__(self,codes):self.index=0;owner.stream=self
            def connect(self):pass
            def get(self):
                self.index+=1;owner.now+=1
                if self.index==5:owner.stop.set()
                return {'type':'ticker','code':OTHER if self.index%2 else TICKER,
                        'trade_price':10000 if self.index<=2 else 10200,'timestamp':int(owner.wall*1000)}
            def close(self):self.closed=True
        self.stream_factory=Stream
        r=self.run_session()
        self.assertEqual(r['status'],'STOPPED');self.assertEqual(r['orders'],4)
        self.assertEqual(r['pending_orders'],0);self.assertEqual(r['session_ticks'],4)
        self.assertEqual(__import__('decimal').Decimal(r['cash']),1016000)
        self.assertEqual(set(r['markets']),{TICKER,OTHER})
