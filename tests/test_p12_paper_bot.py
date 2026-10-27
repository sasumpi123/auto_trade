"""One short real-strategy/PAPER lifecycle, Fake public I/O; no backtesting."""
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from ops import paper_bot as cli
from ops import paper_experiment as exp
from ops.paper_session import run_session
from ops.paper_watch import supervise
from trading.execution import ExecutionEngine


def stopped_worker(state,seconds,top,tickers,stop,pipe):
    stop.wait(10)
    pipe.send({'status':'STOPPED','post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
    pipe.close()


class PortableCliTests(unittest.TestCase):
    def test_run_needs_explicit_public_option_and_cannot_create_implicitly(self):
        with patch.object(exp,'main',side_effect=AssertionError('No delegation')):
            for args in (['run'],['run','--create','--directory','unused']):
                with patch('sys.stdout',new_callable=io.StringIO) as out:self.assertEqual(cli.main(args),2)
                self.assertEqual(json.loads(out.getvalue())['network_calls'],0)
    def test_invalid_missing_args_no_reset_or_credentials_switch(self):
        for args in ([],['init'],['report','--reset'],['status','--api-key','unused']):
            with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):cli.main(args)
    @unittest.skipUnless(os.name=='nt','Windows private account remains blocked')
    def test_windows_init_never_creates_files(self):
        with patch('ops.paper_experiment.Path',side_effect=AssertionError('No filesystem')):
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                self.assertEqual(cli.main(['init','--directory','unused']),2)
        self.assertEqual(json.loads(out.getvalue())['status'],'BLOCKED')


@unittest.skipUnless(os.name=='posix','Mac/Linux private Unix account and IPC only; Windows BLOCKED')
class PaperBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.account=self.root/'paper'/'mvp01'
        original=socket.socket.connect
        def connect(s,address):
            if s.family!=socket.AF_UNIX:raise AssertionError('No external network')
            return original(s,address)
        block=patch.object(socket.socket,'connect',connect);block.start();self.addCleanup(block.stop)
        # Three protected HALTED fixture accounts are not reused as inputs.
        self.protected={}
        for i in range(3):
            p=self.root/f'old-{i}.json';p.write_bytes(b'HALTED private synthetic evidence')
            lock=Path(str(p)+'.lock');lock.write_bytes(b'keep lock')
            backup=self.root/f'backup-{i}.json';backup.write_bytes(p.read_bytes())
            self.protected.update({f:f.read_bytes() for f in (p,lock,backup)})
        self.assertEqual(cli.initialize(self.account)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
    def tearDown(self):
        for p,raw in self.protected.items():self.assertEqual(p.read_bytes(),raw)
    def command(self,*args):
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            code=cli.main([*args,'--directory',str(self.account)])
        return code,json.loads(out.getvalue())
    def request(self,control,command):
        box=[]
        thread=threading.Thread(target=lambda:box.append(cli.query(self.account,command)));thread.start()
        deadline=time.monotonic()+2;stop=False
        while thread.is_alive() and time.monotonic()<deadline:
            stop=control.poll() or stop;time.sleep(.001)
        thread.join(2);self.assertFalse(thread.is_alive());self.assertTrue(box)
        return box[0],stop
    def test_one_cli_buy_hold_sell_fee_shutdown_and_readback(self):
        self.assertEqual(self.command('check')[1]['status'],'READY_FOR_EXPLICIT_RUN')
        owner=self;observed=[];steps=[0];wall=time.time()
        class Public:
            calls=0
            clock=lambda _:wall+steps[0]
            monotonic=lambda _:steps[0]
            def candles(self,ticker):
                self.calls+=1
                closes=[10000.]*179+[10000.-100*i for i in range(20)]+[8200.]
                end=pd.Timestamp(wall,unit='s',tz='UTC').tz_convert('Asia/Seoul').tz_localize(None).floor('min')-pd.Timedelta(minutes=1)
                return pd.DataFrame({'open':closes,'high':[p+20 for p in closes],'low':[p-20 for p in closes],
                    'close':closes,'volume':100.,'value':1000000.},index=pd.date_range(end=end,periods=200,freq='min'))
            def close(self):pass
        class Stream:
            def __init__(self,tickers):owner.assertEqual(tickers,['KRW-BTC'])
            def connect(self):pass
            def get(self):
                steps[0]+=1
                # Real analyzer BUY -> unchanged cooldown HOLD -> existing +5% take profit.
                return {'type':'ticker','code':'KRW-BTC','trade_price':8200 if steps[0]<3 else 8610,
                    'timestamp':int((wall+steps[0])*1000)}
            def close(self):pass
        def offline_supervisor(state,seconds,top,tickers,*,emit,worker,outbox,control):
            stop=threading.Event();token=json.loads((self.account/'session.json').read_text())['session_id']
            def publish(event):
                control.publish(event);emit(event);observed.append(event)
                if event.get('status')=='RUNNING':
                    status,_=self.request(control,'STATUS')
                    self.assertEqual(status['summary']['orders'],event['orders'])
                    self.assertTrue(status['snapshot_fresh'])
                    if event['cumulative_sells']==1:
                        response,accepted=self.request(control,'STOP');self.assertTrue(accepted)
                        self.assertEqual(response['status'],'STOP_REQUESTED_NOT_YET_STOPPED');stop.set()
            return run_session(state,seconds=seconds,tickers=tickers,stopped=stop.is_set,emit=publish,
                client=Public(),stream_factory=Stream,monotonic=lambda:steps[0],wall=lambda:wall+steps[0],
                experiment={'session_id':token})
        with patch('ops.paper_watch.supervise',side_effect=offline_supervisor),patch('sys.stdout',new_callable=io.StringIO) as out:
            code=cli.main(['run','--directory',str(self.account),'--connect-public','--seconds','20','--tickers','KRW-BTC'])
        self.assertEqual(code,0,out.getvalue());self.assertFalse((self.account/cli.CONTROL).exists())
        held=next(r for r in observed if r.get('cumulative_buys')==1 and r.get('cumulative_sells')==0)
        self.assertEqual(Decimal(held['cash']),600000);self.assertGreater(Decimal(held['positions']['KRW-BTC']['quantity']),0)
        report=cli.read_report(self.account);summary=report['summary']
        self.assertEqual(report['session_state'],'STOPPED_VERIFIED');self.assertEqual(report['last_session']['session_ticks'],3)
        self.assertEqual(summary['cumulative_buys'],1);self.assertEqual(summary['cumulative_sells'],1)
        self.assertEqual(Decimal(summary['cash']),1020000);self.assertEqual(Decimal(summary['net_realized_pnl']),20000)
        self.assertEqual(Decimal(summary['fees']),0);self.assertEqual(Decimal(summary['net_return_percent']),2)
        self.assertEqual(summary['positions'],{});self.assertEqual(summary['pending_orders'],0)
        self.assertEqual([r['side'] for r in report['trades']],['buy','sell'])
        self.assertIn('CURRENT_PAPER_GENERATOR_ZERO_FEE',summary['fee_policy'])
        text=json.dumps(report);self.assertNotIn('identifier',text);self.assertNotIn('uuid',text)
        before={p.name:p.read_bytes() for p in self.account.iterdir()}
        self.assertEqual(self.command('status')[0],0);self.assertEqual(self.command('report')[0],0)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.account.iterdir()})
        self.assertFalse((self.account/'notifications').exists())
    def test_duplicate_init_does_not_reset_and_public_parent_is_not_chmodded(self):
        before=(self.account/exp.STATE).read_bytes();self.assertEqual(cli.initialize(self.account)['status'],'BLOCKED')
        self.assertEqual(before,(self.account/exp.STATE).read_bytes())
        self.account.parent.chmod(0o755)
        try:self.assertEqual(cli.initialize(self.account.parent/'other')['status'],'BLOCKED')
        finally:self.account.parent.chmod(0o700)
    def test_control_duplicate_stale_unknown_request_and_lost_progress(self):
        with cli.ControlServer(self.account) as control:
            with self.assertRaises(ValueError):
                with cli.ControlServer(self.account):pass
            r,_=self.request(control,'STATUS');self.assertEqual(r['summary']['status'],'PREPARING')
            control.updated-=20;r,_=self.request(control,'STATUS');self.assertFalse(r['snapshot_fresh'])
            r,stop=self.request(control,'RESET');self.assertEqual(r['status'],'BLOCKED');self.assertFalse(stop)
        path=self.account/cli.CONTROL;path.write_text('stale evidence')
        before=path.read_bytes()
        with self.assertRaises(ValueError):
            with cli.ControlServer(self.account):pass
        self.assertEqual(path.read_bytes(),before);self.assertEqual(self.command('status')[0],2);self.assertEqual(self.command('check')[0],2)
    def test_open_position_offline_value_na_and_halted_accounting_no_restart(self):
        state=self.account/exp.STATE;token=exp.begin(self.account)
        e=ExecutionEngine(state,1000000,real_trading=False);e._experiment_session=token;e.open()
        try:e.buy('KRW-BTC',10000,400000);e.halt('PAPER_SESSION_FAILURE')
        finally:e.close()
        before=state.read_bytes();r=cli.read_report(self.account)
        self.assertEqual(r['session_state'],'HALTED_NO_RESTART');self.assertEqual(r['summary']['unrealized_pnl'],'N/A')
        self.assertEqual(r['summary']['cumulative_buys'],1);self.assertFalse(r['execution_authorized'])
        self.assertEqual(self.command('check')[0],2);self.assertEqual(state.read_bytes(),before)
    def test_pending_corrupt_and_busy_account_do_not_initialize(self):
        state=self.account/exp.STATE;before=state.read_bytes();state.write_text('broken')
        self.assertEqual(self.command('report')[0],2);self.assertEqual(state.read_text(),'broken');state.write_bytes(before)
        token=exp.begin(self.account);e=ExecutionEngine(state,1000000);e._experiment_session=token;e.open()
        try:self.assertEqual(self.command('report')[0],2)
        finally:e.close()
    def test_existing_socket_symlink_is_not_followed_or_removed(self):
        target=self.root/'evidence';target.write_text('preserve');link=self.account/cli.CONTROL;link.symlink_to(target)
        with self.assertRaises(ValueError):
            with cli.ControlServer(self.account):pass
        self.assertEqual(target.read_text(),'preserve');self.assertTrue(link.is_symlink())
    def test_supervisor_control_hook_cooperatively_stops_real_child_process(self):
        replies=[]
        with cli.ControlServer(self.account) as control:
            thread=threading.Thread(target=lambda:replies.append(cli.query(self.account,'STOP')));thread.start()
            r=supervise(self.account/exp.STATE,5,1,['KRW-BTC'],emit=lambda _:None,worker=stopped_worker,control=control)
            thread.join(2)
        self.assertEqual(replies[0]['status'],'STOP_REQUESTED_NOT_YET_STOPPED')
        self.assertEqual(r['status'],'STOPPED');self.assertFalse(r['forced_termination'])
    def test_report_uses_nonzero_confirmed_fees_without_changing_generator(self):
        state=self.account/exp.STATE;token=exp.begin(self.account)
        e=ExecutionEngine(state,1000000);e._experiment_session=token;e.open()
        apply=e._apply_fill
        def receipt(order,value):
            value=dict(value);funds=sum(Decimal(t['funds']) for t in value['trades'])
            value['paid_fee']=str(funds*Decimal('.0005'));return apply(order,value)
        try:
            with patch.object(e,'_apply_fill',side_effect=receipt):
                e.buy('KRW-BTC',10000,400000);e.sell('KRW-BTC',10100)
        finally:e.close()
        terminal=exp.finish(self.account,token,{'status':'STOPPED','engine_started':True,'post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
        self.assertEqual(terminal['status'],'STOPPED_VERIFIED')
        r=cli.read_report(self.account,limit=1)
        self.assertEqual(r['trade_history_total'],2);self.assertEqual(r['trade_history_shown'],1)
        self.assertEqual(Decimal(r['summary']['fees']),402)
        self.assertEqual(Decimal(r['summary']['net_realized_pnl']),3598)
        self.assertEqual(Decimal(r['summary']['cash']),1003598)
    def test_supervisor_failure_leaves_uncertain_session_blocked_no_restart(self):
        with patch('ops.paper_watch.supervise',side_effect=OSError('private-canary')),patch('sys.stdout',new_callable=io.StringIO) as out:
            code=cli.main(['run','--directory',str(self.account),'--connect-public','--seconds','10','--tickers','KRW-BTC'])
        self.assertEqual(code,2);self.assertNotIn('private-canary',out.getvalue())
        self.assertEqual(json.loads((self.account/'session.json').read_text())['phase'],'IN_PROGRESS')
        before=(self.account/exp.STATE).read_bytes();self.assertEqual(self.command('check')[0],2)
        self.assertEqual((self.account/exp.STATE).read_bytes(),before)
