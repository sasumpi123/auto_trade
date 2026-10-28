"""Fake public messages through actual analyzer/AutoTrade/PAPER; no research runs."""
from copy import deepcopy
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

from services.paper_decisions import DecisionCounters,validate_trace,LIMIT,MAX_BYTES
from ops import paper_bot as cli,paper_experiment as exp
from ops.paper_session import run_session,public_analyzer_factory
from trading.auto_trade import AutoTrade
from trading.state_store import StateStore


class DecisionCounterTests(unittest.TestCase):
    def test_bounds_schema_saturation_and_secret_rejection(self):
        c=DecisionCounters(wall=lambda:100)
        for i in range(30):
            t=f'KRW-T{i}';c.record('analyzer_calls',t);c.record('strategy_result',t,action='HOLD');c.record('decision',t,action='HOLD')
        report=c.snapshot();self.assertLess(len(json.dumps(report).encode()),MAX_BYTES)
        self.assertEqual(validate_trace(report),report)
        c.total['counts']['analyzer_calls']=LIMIT;c.record('analyzer_calls','KRW-T0')
        self.assertTrue(c.snapshot()['saturated']);self.assertEqual(c.total['counts']['analyzer_calls'],LIMIT)
        c.record('analyzer_calls','KRW-NEW');self.assertEqual(c.snapshot()['availability'],'UNVERIFIED')
        for mutate in (lambda r:r.update(raw='private-canary'),lambda r:r['last_decision'].update(reason='private-canary'),
                       lambda r:r['counts'].update(analyzer_calls=True)):
            r=deepcopy(report);mutate(r)
            with self.assertRaises(ValueError):validate_trace(r)
    def bot(self,cash):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        bot=AutoTrade(start_cash=cash,tickers=['KRW-BTC'],state_path=Path(temp.name)/'synthetic.json',real_trading=False)
        bot.initialize();self.addCleanup(bot.stop);bot.decision_observer=DecisionCounters();return bot
    def test_cash_minimum_and_max_positions_overlap_is_not_multiple_final_blocks(self):
        for cash,final in ((1000,'CASH_BELOW_MINIMUM'),(10000,'VALUE_BELOW_MINIMUM')):
            b=self.bot(cash);self.assertFalse(b.buy_coin('KRW-BTC',10000))
            r=b.decision_observer.snapshot();self.assertEqual(r['final_blocks'][final],1)
            self.assertEqual(r['counts']['buy_attempts'],1);self.assertEqual(r['counts']['buy_engine_attempts'],0)
        b=self.bot(1000);b.engine.buy('KRW-BTC',100,10);b.engine.buy('KRW-ETH',100,10)
        self.assertFalse(b.buy_coin('KRW-XRP',100));r=b.decision_observer.snapshot()
        self.assertEqual(r['final_blocks']['MAX_POSITIONS'],1)
        self.assertEqual(r['final_blocks']['CASH_BELOW_MINIMUM'],0)
        self.assertEqual(r['attempts_with_multiple_constraints'],1)
        self.assertEqual(r['candidate_constraints_overlapping']['CASH_BELOW_MINIMUM'],1)
        self.assertEqual(r['candidate_constraints_overlapping']['VALUE_BELOW_MINIMUM'],1)
    def test_engine_refusal_is_not_a_fill_and_sell_has_no_invented_minimum_gate(self):
        b=self.bot(10000);b.engine.buy('KRW-BTC',100,4000)
        self.assertFalse(b.buy_coin('KRW-BTC',90,amount=7000))
        r=b.decision_observer.snapshot();self.assertEqual(r['counts']['buy_engine_attempts'],1)
        self.assertEqual(r['final_blocks']['ENGINE_REJECTED'],1);self.assertEqual(r['counts']['buy_fills'],0)
        self.assertEqual(r['candidate_constraints_overlapping']['REQUEST_EXCEEDS_CASH'],1)
        self.assertTrue(b.sell_coin('KRW-BTC',1));self.assertEqual(b.decision_observer.snapshot()['counts']['sell_fills'],1)


@unittest.skipUnless(os.name=='posix','Existing isolated account and Unix IPC require Mac/Linux; Windows BLOCKED')
class RuntimeDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.account=self.root/'account';cli.initialize(self.account)
        self.now=0;self.wall=(time.time()//60)*60+10;self.stop=threading.Event();self.reports=[];self.frames='HOLD';self.low=False;self.cooldown=False
        self.errors={};self.prices=[10000]*3;self.stamps=None;self.before={}
        for i in range(3):
            p=self.root/f'protected-{i}.json';p.write_bytes(b'HALTED private protected evidence')
            backup=self.root/f'backup-{i}.json';backup.write_bytes(p.read_bytes())
            lock=self.root/f'protected-{i}.lock';lock.write_bytes(b'original lock')
            self.before.update({x:x.read_bytes() for x in (p,backup,lock)})
        original=socket.socket.connect
        def connect(s,addr):
            if s.family!=socket.AF_UNIX:raise AssertionError('No external network')
            return original(s,addr)
        block=patch.object(socket.socket,'connect',connect);block.start();self.addCleanup(block.stop)
    def tearDown(self):
        for p,raw in self.before.items():self.assertEqual(p.read_bytes(),raw)
    def client(self):
        owner=self
        class Public:
            calls=0
            clock=lambda _:owner.wall+owner.now
            monotonic=lambda _:owner.now
            def candles(self,t):
                self.calls+=1
                if owner.errors:raise next(iter(owner.errors.values()))
                closes=([10000.]*179+[10000.-100*i for i in range(20)]+[8200.] if owner.frames=='BUY'
                    else [10000.+10*(i%2) for i in range(200)])
                end=pd.Timestamp(owner.wall,unit='s',tz='UTC').tz_convert('Asia/Seoul').tz_localize(None).floor('min')-pd.Timedelta(minutes=1)
                f=pd.DataFrame({'open':closes,'high':[p+20 for p in closes],'low':[p-20 for p in closes],
                    'close':closes,'volume':100.,'value':1000000.},index=pd.date_range(end=end,periods=200,freq='min'))
                if owner.low:f.iloc[-1,f.columns.get_loc('volume')]=1
                return f
            def close(self):pass
        return Public()
    def execute(self,*,emit=None):
        owner=self;client=self.client();token=exp.begin(self.account)
        class Stream:
            def __init__(self,tickers):self.index=0
            def connect(self):pass
            def get(self):
                if self.index==len(owner.prices):owner.stop.set();raise TimeoutError()
                owner.now+=1;i=self.index;self.index+=1
                return {'type':'ticker','code':'KRW-BTC','trade_price':owner.prices[i],
                    'timestamp':owner.stamps[i] if owner.stamps is not None else int((owner.wall+owner.now)*1000)}
            def close(self):pass
        factory=public_analyzer_factory(client,wall=client.clock)
        def analyzer(t):
            a=factory(t)
            if self.cooldown:a.last_signal='BUY';a.last_signal_time=self.wall
            return a
        r=run_session(self.account/exp.STATE,seconds=100,stopped=self.stop.is_set,emit=emit or self.reports.append,
            client=client,stream_factory=Stream,analyzer_factory=analyzer,monotonic=lambda:self.now,wall=lambda:self.wall+self.now,
            tickers=['KRW-BTC'],experiment={'session_id':token,'record_decisions':True})
        terminal=exp.finish(self.account,token,r,record_summary=True)
        return r,terminal
    def test_received_ticks_hold_only_has_real_evaluations_no_orders(self):
        r,end=self.execute();d=r['decision_trace'];c=d['counts']
        self.assertEqual(d['availability'],'RECORDED');self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual((c['stream_messages_received'],c['timing_accepted'],c['analyzer_calls'],c['strategy_evaluations']),(3,3,1,1))
        self.assertEqual(c['strategy_HOLD'],1);self.assertEqual(c['decision_HOLD'],1)
        self.assertEqual(d['final_blocks']['NO_ACTIONABLE_SIGNAL'],1);self.assertEqual(c['buy_attempts'],0)
        self.assertEqual(d['last_strategy_result']['action'],'HOLD');self.assertIsNotNone(d['last_decision']['at_epoch_seconds'])
        self.assertEqual(cli.read_report(self.account)['decision_trace'],d)
    def test_buy_conditions_but_volume_gate_never_calls_or_fabricates_strategy_buy(self):
        self.frames='BUY';self.low=True;r,_=self.execute();d=r['decision_trace'];c=d['counts']
        self.assertEqual(c['analyzer_calls'],1);self.assertEqual(c['strategy_evaluations'],0)
        self.assertEqual(c['strategy_BUY'],0);self.assertEqual(c['decision_HOLD'],1)
        self.assertEqual(d['strategy_not_evaluated']['VOLUME_GATE'],1);self.assertEqual(d['final_blocks']['VOLUME_GATE'],1)
        self.assertIsNone(d['last_strategy_result']);self.assertEqual(d['last_decision']['reason'],'VOLUME_GATE')
    def test_cooldown_and_overlapping_volume_have_one_terminal_gate(self):
        self.frames='BUY';self.cooldown=True;r,_=self.execute();d=r['decision_trace']
        self.assertEqual(d['strategy_not_evaluated']['COOLDOWN'],1);self.assertEqual(d['counts']['strategy_evaluations'],0)
        # A second new test account; never reset or reuse the just-finished account.
        self.account=self.root/'second';cli.initialize(self.account);self.stop.clear();self.now=0;self.low=True
        r,_=self.execute();d=r['decision_trace']
        self.assertEqual(d['gate_conditions_overlapping']['BOTH'],1)
        self.assertEqual(d['final_blocks']['VOLUME_GATE'],1);self.assertEqual(d['final_blocks']['COOLDOWN'],0)
    def test_real_buy_hold_take_profit_trace_matches_ipc_and_persistent_report(self):
        self.frames='BUY';self.prices=[8200,8200,8610];snapshots=[]
        with cli.ControlServer(self.account) as control:
            def emit(event):
                control.publish(event);self.reports.append(event)
                if event.get('status')=='STOPPED':
                    box=[];thread=threading.Thread(target=lambda:box.append(cli.query(self.account,'STATUS')));thread.start()
                    end=time.monotonic()+2
                    while thread.is_alive() and time.monotonic()<end:control.poll();time.sleep(.001)
                    thread.join(2);self.assertTrue(box);snapshots.append(box[0]['summary']['decision_trace'])
            r,end=self.execute(emit=emit)
        d=r['decision_trace'];c=d['counts'];self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual((c['buy_attempts'],c['buy_fills'],c['sell_attempts'],c['sell_fills']),(1,1,1,1))
        self.assertEqual(c['analyzer_calls'],1);self.assertEqual(c['strategy_evaluations'],1)
        self.assertEqual((c['decision_BUY'],c['decision_HOLD'],c['decision_SELL']),(1,0,0))
        self.assertEqual(d['risk_priority']['TAKE_PROFIT'],1);self.assertEqual(d['strategy_not_evaluated']['RISK_PRIORITY'],1)
        self.assertEqual(d['last_order']['stage'],'FILLED');self.assertEqual(d['last_order']['side'],'SELL')
        report=cli.read_report(self.account);self.assertEqual(snapshots,[d]);self.assertEqual(report['decision_trace'],d)
        self.assertEqual(Decimal(report['summary']['cash']),1020000);self.assertEqual(report['summary']['cumulative_buys'],1)
        self.assertEqual(Decimal(report['summary']['net_realized_pnl']),20000)
        self.assertEqual(report['decision_trace_scope'],'STOPPED_VERIFIED_LAST_SESSION_ONLY')
    def test_regression_halts_and_retains_only_owned_partial_observation(self):
        self.stamps=[int((self.wall+1)*1000),int((self.wall+1)*1000)-4];self.prices=[10000,10000]
        r,end=self.execute();d=r['decision_trace']
        self.assertEqual(r['status'],'HALTED');self.assertEqual(end['status'],'HALTED')
        self.assertEqual(d['counts']['stream_messages_received'],2);self.assertEqual(d['counts']['timing_accepted'],1)
        self.assertEqual(d['timestamp_stats']['issues']['TIMESTAMP_REGRESSION'],1)
        report=cli.read_report(self.account);self.assertEqual(report['session_state'],'HALTED_NO_RESTART')
        self.assertEqual(report['decision_trace'],d);self.assertIn('NOT_SUCCESS',report['decision_trace_scope'])
        before=(self.account/exp.STATE).read_bytes()
        self.assertEqual(exp.inspect_experiment(self.account)['status'],'BLOCKED')
        self.assertEqual((self.account/exp.STATE).read_bytes(),before)
        self.assertEqual(exp.marker(self.account)['phase'],'IN_PROGRESS')
    def test_missing_old_statistics_na_and_bad_metadata_not_echoed(self):
        self.assertEqual(cli.read_report(self.account)['decision_trace'],'N/A_NOT_RECORDED')
        self.execute();p=self.account/'session.json';m=json.loads(p.read_text());m.pop('decision_trace');p.write_text(json.dumps(m))
        self.assertEqual(cli.read_report(self.account)['decision_trace'],'N/A_NOT_RECORDED')
        m['decision_trace']={'raw':'secret-canary'};p.write_text(json.dumps(m))
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(cli.main(['report','--directory',str(self.account)]),2)
        self.assertNotIn('secret-canary',out.getvalue())
    def test_hold_ticks_do_not_add_ledger_saves(self):
        paths=[];save=StateStore.save
        def record(store,state):paths.append(store.path);return save(store,state)
        with patch.object(StateStore,'save',record):self.execute()
        first=sum(p.name==exp.STATE for p in paths)
        self.account=self.root/'many';cli.initialize(self.account);self.stop.clear();self.now=0;self.prices=[10000]*40;paths.clear()
        with patch.object(StateStore,'save',record):r,_=self.execute()
        self.assertEqual(sum(p.name==exp.STATE for p in paths),first)
        self.assertEqual(r['decision_trace']['counts']['strategy_evaluations'],1)

class AdditionalDecisionTests(unittest.TestCase):
    def test_actual_buy_while_held_and_sell_without_position_are_final_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            for action in ('BUY','SELL'):
                bot=AutoTrade(start_cash=100000,tickers=['KRW-BTC'],state_path=Path(tmp)/(action+'.json'),real_trading=False)
                bot.initialize()
                try:
                    if action=='BUY':bot.engine.buy('KRW-BTC',10000,10000)
                    c=DecisionCounters();bot.decision_observer=c;a=bot.analyzers['KRW-BTC'];a.decision_observer=c
                    f=pd.DataFrame({'close':10000.,'volume':100.,'rsi':50.,'macd':0.,'macd_signal':0.,
                        'bb_lower':9000.,'bb_middle':10000.,'bb_upper':11000.},index=range(20))
                    f.loc[18:19,'rsi']=[20.,25.] if action=='BUY' else [80.,75.];a.df=f
                    bot.process_market_data('KRW-BTC',{'trade_price':10000})
                    r=c.snapshot();reason='BUY_ALREADY_HELD' if action=='BUY' else 'SELL_WITHOUT_POSITION'
                    self.assertEqual(r['counts']['strategy_'+action],1);self.assertEqual(r['final_blocks'][reason],1)
                    self.assertEqual(r['counts'][action.lower()+'_attempts'],0);self.assertEqual(r['last_order']['stage'],'BLOCKED')
                finally:bot.stop()
    def test_maximal_counter_snapshot_stays_bounded(self):
        c=DecisionCounters(wall=lambda:10**12)
        for i in range(30):
            t=f'KRW-SYNTH{i}';c.record('analyzer_calls',t);c.record('strategy_result',t,action='HOLD')
            c.record('decision',t,action='HOLD');c.record('order',t,side='BUY',stage='BLOCKED',reason='VALUE_BELOW_MINIMUM')
        r=c.snapshot()
        for item in [r,*r['markets'].values()]:
            for group in ('counts','final_blocks'):
                for key in item[group]:item[group][key]=LIMIT
        self.assertLess(len(json.dumps(r).encode()),MAX_BYTES);self.assertEqual(validate_trace(r),r)
