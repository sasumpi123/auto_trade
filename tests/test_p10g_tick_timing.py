"""Pure timing / Fake sockets and disposable ledgers. Never a real market call."""
import io
import json
import os
import signal
import socket
import threading
import time
import unittest
from unittest.mock import Mock,patch

from services.tick_timing import TickTiming,TickTimingError,MAX_REPORT_BYTES,MAX_SAMPLES,MAX_NUMBER,MAX_COUNTER
from ops.paper_watch import child_result,supervise
from ops.public_tick_diagnostic import monitor,main
from ops.paper_session import offline_report
from trading.execution import ExecutionEngine
import tests.test_p10c_paper_watch as fixtures
import tests.test_p10f_paper_experiment as experiment_fixtures
from ops import paper_experiment as exp


def _halted_worker(_state,_seconds,_top,_tickers,_stop,pipe):
    pipe.send({'status':'HALTED','reason':'PAPER_STALE_TICK','engine_started':True,
               'post_validation':'UNVERIFIED','tick_timing':{'samples':[{'market':'KRW-BTC','issue':'TIMESTAMP_REGRESSION',
                   'timestamp_minus_previous_ms':-1}]}})
    pipe.close()


def _diagnostic_stop_worker(_state,_seconds,_top,_tickers,stop,pipe):
    stop.wait(10);pipe.send({'status':'STOPPED','reason':'TIME_LIMIT_OR_USER_STOP','ledger_reads':0,'ledger_writes':0})
    pipe.close()


class TickTimingTests(unittest.TestCase):
    def setUp(self):self.now=1800000000;self.guard=TickTiming(['KRW-BTC','KRW-ETH'],self.now,0)
    def tick(self,delta=0,market='KRW-BTC',**extra):
        return {'type':'ticker','code':market,'trade_price':100,'timestamp':self.now*1000+delta,**extra}
    def issue(self,data,wall=None,mono=1):
        with self.assertRaises(TickTimingError):self.guard.observe(data,self.now if wall is None else wall,mono)
        return self.guard.report()['samples'][-1]

    def test_ordered_equal_and_independent_market_timestamps(self):
        self.guard.observe(self.tick(),self.now,0)
        self.guard.observe(self.tick(),self.now,1)
        self.guard.observe(self.tick(1,'KRW-ETH'),self.now,2)
        self.guard.observe(self.tick(2),self.now,3)
        r=self.guard.report();self.assertEqual(r['timing_accepted_total'],4)
        self.assertEqual(r['markets']['KRW-BTC']['equal_timestamp'],1)

    def test_missing_type_and_secret_value_are_sanitized(self):
        missing=self.tick();missing.pop('timestamp')
        self.assertEqual(self.issue(missing)['issue'],'TIMESTAMP_MISSING')
        for value in (None,True,1.5,'private-secret-canary',{},float('nan')):
            self.assertEqual(self.issue(self.tick(timestamp=value))['issue'],'TIMESTAMP_TYPE_INVALID')
        self.assertNotIn('private-secret-canary',json.dumps(self.guard.report(),allow_nan=False))

    def test_old_future_small_and_large_regressions_still_halt(self):
        self.guard.observe(self.tick(),self.now,0)
        for delta,code in ((-60001,'TIMESTAMP_DELAYED'),(5001,'TIMESTAMP_FUTURE'),(-1,'TIMESTAMP_REGRESSION'),(-5000,'TIMESTAMP_REGRESSION')):
            r=self.issue(self.tick(delta))
            self.assertEqual(r['issue'],code);self.assertEqual(r['timestamp_minus_previous_ms'],delta)
            self.assertEqual(r['local_receive_minus_timestamp_ms'],-delta)
        self.assertEqual(self.guard.accepted,1)

    def test_server_field_delta_is_not_claimed_to_be_server_receive_latency(self):
        self.guard.observe(self.tick(),self.now,0)
        sample=self.issue(self.tick(-1,trade_timestamp=self.now*1000-100))
        self.assertEqual(sample['timestamp_minus_trade_timestamp_ms'],99)
        self.assertIsNone(sample['server_relative_delay_ms'])
        self.assertIn('NOT_INDEPENDENTLY_VERIFIED',self.guard.report()['reference_clock'])

    def test_clock_jump_and_monotonic_regression_are_separate(self):
        self.assertEqual(self.issue(self.tick(),wall=self.now+7,mono=1)['issue'],'CLOCK_DISCONTINUITY')
        self.assertEqual(self.issue(self.tick(),mono=-1)['issue'],'CLOCK_DISCONTINUITY')

    def test_equal_timestamp_stream_does_not_refresh_progress_watchdog(self):
        self.guard=TickTiming(['KRW-BTC'],self.now,0)
        self.guard.observe(self.tick(),self.now,0)
        for i in range(1,15):self.guard.observe(self.tick(),self.now+i,i)
        self.assertEqual(self.issue(self.tick(),wall=self.now+15,mono=15)['issue'],'STREAM_IDLE')

    def test_active_other_market_cannot_hide_unupdated_market(self):
        for i in range(60):self.guard.observe(self.tick(i*1000),self.now+i,i)
        r=self.issue(self.tick(60000),wall=self.now+60,mono=60)
        self.assertEqual(r['issue'],'MARKET_IDLE');self.assertEqual(r['market'],'KRW-ETH');self.assertEqual(r['idle_ms'],60000)

    def test_absent_stream_and_invalid_envelope(self):
        with self.assertRaisesRegex(TickTimingError,'PAPER_STREAM_TIMEOUT'):self.guard.idle(self.now+15,15)
        for data in (None,{},self.tick(code=['private-secret-canary'])):
            self.assertEqual(self.issue(data)['issue'],'INVALID_ENVELOPE')
        self.assertNotIn('private-secret-canary',json.dumps(self.guard.report()))

    def test_numeric_report_and_sample_retention_are_bounded(self):
        g=TickTiming(['KRW-C'+str(i) for i in range(100)],self.now,0)
        for i in range(100):
            for _ in range(20):
                try:g.observe(self.tick(market='KRW-C'+str(i),timestamp=10**1000),self.now,0)
                except TickTimingError:pass
        r=g.report();self.assertEqual(len(r['samples']),MAX_SAMPLES)
        self.assertLessEqual(len(json.dumps(r).encode()),MAX_REPORT_BYTES)
        self.assertEqual(r['samples'][-1]['received_timestamp_ms'],MAX_NUMBER)
        g.markets['KRW-C0']['received']=MAX_COUNTER
        try:g.observe(self.tick(market='KRW-C0',timestamp='secret'),self.now,0)
        except TickTimingError:pass
        self.assertEqual(g.markets['KRW-C0']['received'],MAX_COUNTER)
        self.assertEqual(r['retained_log_files'],0)


class PublicDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.now=0;self.wall=1800000000;self.stop=False;self.index=0;self.data=[];self.closed=False
        owner=self
        class Stream:
            def connect(self):pass
            def close(self):owner.closed=True
            def get(self):
                owner.now+=1
                if owner.index>=len(owner.data):raise TimeoutError()
                item=owner.data[owner.index];owner.index+=1
                return item
        self.stream=lambda _:Stream()
    def tick(self,delta=0):return {'type':'ticker','code':'KRW-BTC','trade_price':100,'timestamp':self.wall*1000+delta}
    def run_monitor(self,seconds=5):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            return monitor(['KRW-BTC'],seconds,stream_factory=self.stream,stopped=lambda:self.stop,
                           wall=lambda:self.wall+self.now,monotonic=lambda:self.now)
    def test_normal_socket_diagnostic_never_accesses_ledger_or_trades(self):
        self.data=[self.tick(1000),self.tick(2000)]
        with patch('ops.paper_session.offline_report',side_effect=AssertionError('No ledger')):
            r=self.run_monitor()
        self.assertEqual(r['status'],'STOPPED');self.assertTrue(self.closed)
        for k in ('ledger_reads','ledger_writes','paper_orders','real_orders','strategy_calls','accounts_created','rest_requests'):
            self.assertEqual(r[k],0)
    def test_regression_and_receive_stall_are_distinct(self):
        self.data=[self.tick(1000),self.tick(999)]
        r=self.run_monitor();self.assertEqual(r['reason'],'PAPER_STALE_TICK')
        self.assertEqual(r['tick_timing']['samples'][-1]['issue'],'TIMESTAMP_REGRESSION')
        self.index=0;self.data=[];self.now=0;r=self.run_monitor(30)
        self.assertEqual(r['reason'],'PAPER_STREAM_TIMEOUT')
    def test_default_cli_is_offline_and_no_state_or_creation_option_exists(self):
        with patch('ops.paper_watch.supervise',side_effect=AssertionError('No process')):
            with patch('sys.stdout',new_callable=io.StringIO) as out:self.assertEqual(main([]),2)
        self.assertEqual(json.loads(out.getvalue())['status'],'DISABLED')
        for args in (['--state','private-canary'],['--create'],['--seconds','10'],['--connect-public','--seconds','601','--tickers','KRW-BTC']):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main(args)
    def test_public_supervisor_path_cannot_touch_paper_state(self):
        with patch('ops.paper_watch.offline_report',side_effect=AssertionError('No ledger path')):
            r=supervise(None,.1,0,['KRW-BTC'],emit=lambda _:None,worker=_diagnostic_stop_worker,grace=5)
        self.assertEqual(r['status'],'STOPPED');self.assertFalse(r['forced_termination'])
        self.assertEqual(r['after_close']['ledger_access'],'NOT_APPLICABLE_NO_LEDGER_DIAGNOSTIC')

    def test_message_and_output_limits_fail_closed(self):
        self.data=[self.tick(1000),self.tick(2000)]
        with patch('ops.public_tick_diagnostic.MAX_MESSAGES',2):r=self.run_monitor()
        self.assertEqual(r['status'],'HALTED');self.assertEqual(r['reason'],'DIAGNOSTIC_MESSAGE_LIMIT')
        with patch('ops.public_tick_diagnostic.MAX_OUTPUT_BYTES',1):
            with patch('sys.stdout',new_callable=io.StringIO) as output:self.assertEqual(main([]),2)
        self.assertEqual(json.loads(output.getvalue())['reason'],'DIAGNOSTIC_OUTPUT_LIMIT')

    @unittest.skipUnless(os.name=='posix','POSIX Ctrl+C/SIGHUP signal injection')
    def test_ctrl_c_and_ssh_hangup_stop_no_ledger_diagnostic_without_auto_restart(self):
        for sig in (signal.SIGINT,signal.SIGHUP):
            old=signal.getsignal(sig);sender=threading.Timer(.2,lambda:os.kill(os.getpid(),sig));sender.start()
            try:
                with patch('ops.paper_watch.offline_report',side_effect=AssertionError('No ledger')):
                    r=supervise(None,5,0,['KRW-BTC'],emit=lambda _:None,worker=_diagnostic_stop_worker,grace=5)
            finally:sender.join()
            self.assertEqual(signal.getsignal(sig),old);self.assertFalse(r['automatic_restart'])
            self.assertFalse(r['forced_termination']);self.assertEqual(r['status'],'STOPPED')

    def test_close_failure_does_not_replace_the_original_timestamp_failure(self):
        self.data=[self.tick(1000),self.tick(999)]
        original=self.stream
        def factory(codes):
            stream=original(codes)
            stream.close=Mock(side_effect=OSError('private-secret-canary'))
            return stream
        self.stream=factory
        r=self.run_monitor()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK')
        self.assertEqual(r['cleanup_error'],'PUBLIC_STREAM_CLOSE_UNCONFIRMED')
        self.assertNotIn('private-secret-canary',json.dumps(r))


class SupervisorTests(unittest.TestCase):
    def test_explicit_halted_reason_is_preserved_without_forced_stop_claim(self):
        child={'status':'HALTED','reason':'PAPER_STALE_TICK','post_validation':'UNVERIFIED'}
        r=child_result(child,0,False)
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(r['supervisor_reason'],'CHILD_REPORTED_HALTED')
        self.assertFalse(r['forced_termination']);self.assertEqual(r['post_validation'],'UNVERIFIED')
    def test_requested_termination_and_actual_signal_are_not_conflated(self):
        r=child_result(None,0,True);self.assertFalse(r['forced_termination'])
        self.assertEqual(r['reason'],'TERMINATION_REQUESTED_OUTCOME_UNCONFIRMED')
        exitcode=-signal.SIGKILL if os.name=='posix' else 1
        r=child_result(None,exitcode,True);self.assertEqual(r['forced_termination'],os.name=='posix')
        self.assertEqual(r['reason'],'FORCED_TERMINATION_CONFIRMED' if os.name=='posix' else 'TERMINATION_REQUESTED_OUTCOME_UNCONFIRMED')


class PaperIntegrationTests(unittest.TestCase):
    setUp=fixtures.PaperWatchTests.setUp
    factory=fixtures.PaperWatchTests.factory
    run_session=fixtures.PaperWatchTests.run_session
    def test_rejected_ticker_never_reaches_strategy(self):
        self.stamps=int(self.wall*1000)-60001
        with patch('trading.auto_trade.AutoTrade.process_market_data',side_effect=AssertionError('No stale strategy input')) as process:
            r=self.run_session()
        process.assert_not_called();self.assertEqual(r['orders'],0)
        self.assertEqual(r['tick_timing']['samples'][-1]['issue'],'TIMESTAMP_DELAYED')

    def test_small_regression_is_halted_before_second_strategy_call(self):
        original_factory=self.stream_factory
        def stream(codes):
            obj=original_factory(codes);get=obj.get
            def tick():
                data=get();data['timestamp']=int(self.wall*1000)-(1 if obj.index==2 else 0);return data
            obj.get=tick;return obj
        self.stream_factory=stream
        from trading.auto_trade import AutoTrade
        original=AutoTrade.process_market_data;seen=[]
        def process(bot,t,data):seen.append(data['timestamp']);return original(bot,t,data)
        with patch.object(AutoTrade,'process_market_data',new=process):r=self.run_session()
        self.assertEqual(len(seen),1);self.assertEqual(r['orders'],1)
        self.assertEqual(r['tick_timing']['received_total'],2);self.assertEqual(r['tick_timing']['timing_accepted_total'],1)
        self.assertEqual(r['tick_timing']['samples'][-1]['timestamp_minus_previous_ms'],-1)
    @unittest.skipUnless(os.name=='posix','Existing read-only ownership probe not certified on Windows')
    def test_zero_order_halted_ledger_reconciles_without_reset_or_start_permission(self):
        e=ExecutionEngine(self.state,1000000);e.open();e.halt('PAPER_SESSION_FAILURE');e.close()
        before=self.state.read_bytes()
        r=supervise(self.state,5,1,None,emit=lambda _:None,worker=_halted_worker)
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertFalse(r['forced_termination'])
        self.assertEqual(r['supervisor_reason'],'CHILD_REPORTED_HALTED')
        accounting=r['after_close']['halted_reconciliation']
        self.assertEqual(accounting['ledger'],'RECONCILED_READ_ONLY');self.assertEqual(accounting['confirmed_buys'],0)
        self.assertEqual(accounting['cash'],'1000000');self.assertFalse(accounting['restart_authorized'])
        self.assertIsNone(r['after_close']['summary']);self.assertEqual(self.state.read_bytes(),before)
    def test_normal_strategy_flow_keeps_timestamp_diagnostics_separate_from_order_counts(self):
        r=self.run_session()
        self.assertEqual(r['status'],'STOPPED');self.assertEqual(r['orders'],2)
        self.assertEqual(r['tick_timing']['received_total'],2)


@unittest.skipUnless(os.name=='posix','POSIX isolated experiment protocol')
class ExperimentHaltTests(unittest.TestCase):
    factory=experiment_fixtures.ExperimentTests.factory
    setUp=experiment_fixtures.ExperimentTests.setUp
    tearDown=experiment_fixtures.ExperimentTests.tearDown
    run_experiment=experiment_fixtures.ExperimentTests.run_experiment
    def test_experiment_finalization_keeps_child_timing_reason(self):
        self.stamps=int(self.wall*1000)-60001
        r,end=self.run_experiment()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['reason'],'PAPER_STALE_TICK')
        self.assertEqual(end['status'],'HALTED');self.assertFalse(end['automatic_restart'])
        before=self.state.read_bytes();self.assertEqual(offline_report(self.state)['halted_reconciliation']['ledger'],'RECONCILED_READ_ONLY')
        self.assertEqual(self.state.read_bytes(),before)
