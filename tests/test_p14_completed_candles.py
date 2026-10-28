"""Fake bounded public REST/WebSocket through real PAPER adapter and engine."""
from decimal import Decimal
from email.utils import formatdate
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

from ops import paper_experiment as exp,paper_bot as cli
from ops.paper_session import run_session,validate_candle_report
from services.public_market import PublicMarketClient,PublicDataError


class CompletedCandleContractTests(unittest.TestCase):
    def client(self,now=1800000010.):
        c=PublicMarketClient(clock=lambda:now);self.addCleanup(c.close);return c
    def rows(self,cutoff):
        return [{'market':'KRW-BTC','unit':1,'candle_date_time_utc':(cutoff-pd.Timedelta(minutes=i+1)).tz_localize(None).isoformat(),
            'opening_price':100,'high_price':101,'low_price':99,'trade_price':100,
            'candle_acc_trade_volume':100,'candle_acc_trade_price':10000} for i in range(200)]
    def test_exact_exclusive_utc_cutoff_excludes_forming_low_volume_bar(self):
        c=self.client();cutoff=pd.Timestamp(c.clock(),unit='s',tz='UTC').floor('min');observed=[]
        def get(endpoint,params):
            observed.append(params);self.assertEqual(pd.Timestamp(params['to']),cutoff)
            return self.rows(pd.Timestamp(params['to']))
        c.get=get;f=c.candles('KRW-BTC')
        self.assertEqual(observed,[{'market':'KRW-BTC','count':200,'to':cutoff.isoformat()}])
        self.assertEqual(f.index[-1].tz_localize('Asia/Seoul').tz_convert('UTC'),cutoff-pd.Timedelta(minutes=1))
        self.assertEqual(f.volume.iloc[-1],100)
    def test_current_candle_old_latest_and_ambiguous_timezone_are_rejected(self):
        c=self.client();cutoff=pd.Timestamp(c.clock(),unit='s',tz='UTC').floor('min')
        for variant in ('forming','missing_latest','kst_in_utc_field','nat'):
            rows=self.rows(cutoff)
            if variant=='forming':rows=self.rows(cutoff+pd.Timedelta(minutes=1))
            if variant=='missing_latest':rows=self.rows(cutoff-pd.Timedelta(minutes=1))
            if variant=='kst_in_utc_field':rows[0]['candle_date_time_utc']='2027-01-15T17:00:00+09:00'
            if variant=='nat':rows[0]['candle_date_time_utc']='NaT'
            c.get=lambda *_,r=rows:r
            with self.subTest(variant=variant),self.assertRaises(PublicDataError):c.candles('KRW-BTC')
    def test_candle_report_is_typed_and_does_not_echo_unknown_fields(self):
        with self.assertRaises(ValueError):validate_candle_report({'KRW-BTC':{'raw':'private-canary'}})
    def test_invalid_local_clock_stops_before_request(self):
        c=self.client(float('nan'))
        with patch.object(c,'get',side_effect=AssertionError('No request')):
            with self.assertRaisesRegex(PublicDataError,'PUBLIC_RESPONSE_CLOCK_UNVERIFIED'):c.candles('KRW-BTC')


@unittest.skipUnless(os.name=='posix','Existing private PAPER account requires Mac/Linux')
class CompletedCandleRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.account=self.root/'paper';cli.initialize(self.account)
        self.start=(time.time()//60)*60+10;self.now=0.;self.stop=threading.Event();self.reports=[];self.requests=[]
        self.offsets=[1,2,3];self.prices=[10000]*3;self.buy=False;self.low=False;self.gap=False;self.ignore_to=False
        self.date_offset=0;self.regress=False;self.cancel_refresh=False;owner=self
        class Response:
            status_code=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            @property
            def headers(self):return {'Date':formatdate(owner.start+owner.now+owner.date_offset,usegmt=True)}
            def iter_content(self,chunk_size):yield json.dumps(self.rows).encode()
        class Session:
            def get(self,url,*,params,**kwargs):
                owner.requests.append(params.copy())
                if owner.cancel_refresh and len(owner.requests)==2:owner.stop.set()
                # To-less old query would include the low-volume in-progress candle.
                cutoff=pd.Timestamp(params['to']) if params.get('to') else pd.Timestamp(owner.start+owner.now,unit='s',tz='UTC').floor('min')+pd.Timedelta(minutes=1)
                if owner.ignore_to:cutoff+=pd.Timedelta(minutes=1)
                closes=[10000.]*179+[10000.-100*i for i in range(20)]+[8200.] if owner.buy else [10000.+10*(i%2) for i in range(200)]
                rows=[]
                current=pd.Timestamp(owner.start+owner.now,unit='s',tz='UTC').floor('min')
                for i in range(200):
                    stamp=cutoff-pd.Timedelta(minutes=i+1);p=closes[-1-i]
                    volume=1 if stamp==current or (owner.low and i==0) else 100
                    if owner.gap and i>=10:stamp-=pd.Timedelta(minutes=1)
                    rows.append({'market':'KRW-BTC','unit':1,'candle_date_time_utc':stamp.tz_localize(None).isoformat(),
                        'opening_price':p,'high_price':p+20,'low_price':p-20,'trade_price':p,
                        'candle_acc_trade_volume':volume,'candle_acc_trade_price':volume*p})
                r=Response();r.rows=rows;return r
            def close(self):pass
        self.client=PublicMarketClient(session=Session(),clock=lambda:self.start+self.now,monotonic=lambda:self.now,
            sleep=lambda _:None,max_calls=64,deadline=120,stopped=self.stop.is_set)
        self.protected={}
        for i in range(3):
            for suffix in ('.json','.lock','.backup.json'):
                p=self.root/(f'old-{i}'+suffix);p.write_bytes(b'protected HALTED synthetic evidence');self.protected[p]=p.read_bytes()
        block=patch.object(socket.socket,'connect',side_effect=AssertionError('No external network'))
        block.start();self.addCleanup(block.stop)
    def tearDown(self):
        for p,raw in self.protected.items():self.assertEqual(p.read_bytes(),raw)
    def execute(self):
        owner=self;token=exp.begin(self.account)
        class Stream:
            def __init__(self,codes):self.i=0
            def connect(self):pass
            def get(self):
                if self.i==len(owner.offsets):owner.stop.set();raise TimeoutError()
                i=self.i;self.i+=1;owner.now=owner.offsets[i]
                stamp=int((owner.start+owner.now)*1000)
                if owner.regress and i==1:stamp=int((owner.start+owner.offsets[0])*1000)-4
                return {'type':'ticker','code':'KRW-BTC','trade_price':owner.prices[i],'timestamp':stamp}
            def close(self):pass
        r=run_session(self.account/exp.STATE,seconds=120,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=Stream,tickers=['KRW-BTC'],monotonic=lambda:self.now,wall=lambda:self.start+self.now,
            experiment={'session_id':token,'record_decisions':True})
        terminal=exp.finish(self.account,token,r,record_summary=True)
        return r,terminal
    def test_forming_low_volume_does_not_block_valid_completed_hold_evaluation(self):
        r,end=self.execute();self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertGreater(r['decision_trace']['counts']['strategy_evaluations'],0)
        basis=r['candle_evaluation']['KRW-BTC'];self.assertEqual(basis['volume'],100)
        self.assertEqual(basis['mean_volume_20_completed'],100);self.assertEqual(basis['threshold_fraction'],.5)
        self.assertEqual(r['decision_trace']['final_blocks']['VOLUME_GATE'],0)
        self.assertEqual(cli.read_report(self.account)['candle_evaluation'],r['candle_evaluation'])
    def test_actually_low_completed_volume_keeps_original_fifty_percent_hold(self):
        self.low=True;r,end=self.execute();self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual(r['decision_trace']['counts']['strategy_evaluations'],0)
        self.assertEqual(r['decision_trace']['final_blocks']['VOLUME_GATE'],1)
        b=r['candle_evaluation']['KRW-BTC'];self.assertEqual(b['volume'],1)
        self.assertEqual(b['mean_volume_20_completed'],95.05);self.assertEqual(b['skipped_same_candle'],2)
    def test_new_completed_minute_refreshes_once_and_evaluates_again(self):
        self.offsets=[1,2,3,51,52];self.prices=[10000]*5;r,end=self.execute()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['decision_trace']['counts']['strategy_evaluations'],2)
        self.assertEqual(len(self.requests),2);self.assertEqual(self.client.calls,2)
        self.assertEqual(r['candle_evaluation']['KRW-BTC']['skipped_same_candle'],3)
        self.assertEqual(pd.Timestamp(self.requests[1]['to'])-pd.Timestamp(self.requests[0]['to']),pd.Timedelta(minutes=1))
    def test_many_same_bar_ticks_only_one_strategy_buy_and_no_duplicate_orders(self):
        self.buy=True;self.offsets=[1+i*.1 for i in range(30)];self.prices=[8200]*30;r,end=self.execute()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['cumulative_buys'],1)
        self.assertEqual(r['decision_trace']['counts']['analyzer_calls'],1);self.assertEqual(self.client.calls,1)
        self.assertEqual(r['candle_evaluation']['KRW-BTC']['skipped_same_candle'],29)
    def test_take_profit_is_immediate_between_candle_refreshes(self):
        self.buy=True;self.prices=[8200,8200,8610];r,end=self.execute()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual((r['cumulative_buys'],r['cumulative_sells']),(1,1))
        self.assertEqual(r['decision_trace']['risk_priority']['TAKE_PROFIT'],1);self.assertEqual(self.client.calls,1)
        self.assertEqual(Decimal(r['net_realized_pnl']),20000)
    def test_averaging_then_stop_loss_are_immediate_before_next_candle(self):
        self.buy=True;self.prices=[8200,7700,7000];r,end=self.execute()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual((r['cumulative_buys'],r['cumulative_sells']),(2,1))
        self.assertEqual(r['decision_trace']['risk_priority']['STOP_OR_AVERAGE'],2);self.assertEqual(self.client.calls,1)
    def test_gap_and_server_clock_error_preserve_preparation_ledger(self):
        self.gap=True;before=(self.account/exp.STATE).read_bytes();r,end=self.execute()
        self.assertEqual(r['status'],'HALTED');self.assertFalse(r['engine_started'])
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual((self.account/exp.STATE).read_bytes(),before)
        self.account=self.root/'clock';cli.initialize(self.account);self.stop.clear();self.gap=False;self.date_offset=60
        before=(self.account/exp.STATE).read_bytes();r,end=self.execute()
        self.assertEqual(r['reason'],'PUBLIC_RESPONSE_CLOCK_UNVERIFIED');self.assertEqual((self.account/exp.STATE).read_bytes(),before)
    def test_budget_exhaustion_on_next_bar_halts_without_extra_request(self):
        self.client.max_calls=1;self.offsets=[1,51];self.prices=[10000,10000];r,end=self.execute()
        self.assertEqual(r['reason'],'PUBLIC_REQUEST_BUDGET');self.assertEqual(r['status'],'HALTED')
        self.assertEqual(end['status'],'HALTED');self.assertEqual(len(self.requests),1)
        self.assertEqual(cli.read_report(self.account)['session_state'],'HALTED_NO_RESTART')
    def test_timestamp_regression_keeps_existing_halt_and_no_second_evaluation(self):
        self.regress=True;r,end=self.execute()
        self.assertEqual(r['reason'],'PAPER_STALE_TICK');self.assertEqual(end['status'],'HALTED')
        self.assertEqual(r['decision_trace']['counts']['strategy_evaluations'],1)
        self.assertEqual(r['decision_trace']['timestamp_stats']['issues']['TIMESTAMP_REGRESSION'],1)

    def test_user_stop_during_refresh_does_not_become_data_failure_or_halt(self):
        self.cancel_refresh=True;self.offsets=[1,51];self.prices=[10000,10000]
        r,end=self.execute()
        self.assertEqual(r['status'],'STOPPED');self.assertFalse(r['halted'])
        self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual(r['decision_trace']['counts']['strategy_evaluations'],1)
