"""Finite REST->real analyzer->strategy->PAPER replay; synthetic, never a backtest."""
from decimal import Decimal as D
from email.utils import formatdate
from functools import lru_cache
import json
import logging
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch

import pandas as pd

FIXTURE=Path(__file__).parent/'fixtures/p14_upbit_minutes.json'


class MinuteFixture:
    def __init__(self,spec,case):
        self.spec=spec;self.case=spec['cases'][case];self.name=case;self.start=pd.Timestamp(spec['utc_start']).timestamp()
        self.anchor=int(self.start//60)*60;self.elapsed=0.;self.stop=threading.Event();self.requests=[];self.control_overrides=0
    def clock(self):return self.start+self.elapsed
    def price(self,minute,second):
        if self.name=='paper_round_trip':
            if minute<=-22:return D(10000)
            if -21<=minute<=-2:return D(10000-100*(minute+21))
            if minute==-1:return D(8200)
            return D(8200 if minute==0 and second<4 else 8610)
        return D(10000+10*(minute%2)) if second>=30 else D(10000)
    @lru_cache(maxsize=512)
    def trades(self,minute):
        model=self.spec['volume_model'];scale=D('.01') if minute==-1 and self.case.get('initial_completed_minute_volume')=='1' else D(1)
        events=[]
        for i in range(model['trades_per_full_minute']):
            second=D(i)*D(model['trade_interval_seconds'])
            quantity=D(model['first_trade_volume'] if i==0 else model['early_trade_volume'] if second<D(model['early_until_exclusive_seconds']) else model['late_trade_volume'])
            events.append((self.anchor+minute*60+float(second),self.price(minute,float(second)),quantity*scale))
        return tuple(events)
    def row(self,minute):
        start=self.anchor+minute*60;events=[t for t in self.trades(minute) if t[0]<=self.clock()]
        if not events:return None  # No fabricated no-trade candles.
        prices=[t[1] for t in events];stamp=pd.Timestamp(start,unit='s',tz='UTC')
        return {'market':self.spec['market'],'candle_date_time_utc':stamp.tz_localize(None).isoformat(),
            'candle_date_time_kst':stamp.tz_convert('Asia/Seoul').tz_localize(None).isoformat(),
            'opening_price':float(prices[0]),'high_price':float(max(prices)),'low_price':float(min(prices)),
            'trade_price':float(prices[-1]),'timestamp':int(events[-1][0]*1000),
            'candle_acc_trade_price':float(sum((p*q for _,p,q in events),D(0))),
            'candle_acc_trade_volume':float(sum((q for _,_,q in events),D(0))),'unit':1}
    def offsets(self):
        if 'tick_offsets_seconds' in self.case:return self.case['tick_offsets_seconds']
        if self.case['tick_count']==1:return [1.]
        n=self.case['tick_count'];return [float(D(i+1)*D(self.case['last_offset_seconds'])/n) for i in range(n)]
    def rows(self,params):
        params=dict(params)
        if self.case['query_control']=='ADD_EXCLUSIVE_TO_ONLY_WHEN_MISSING' and 'to' not in params:
            params['to']=pd.Timestamp(self.clock(),unit='s',tz='UTC').floor('min').isoformat();self.control_overrides+=1
        cutoff=pd.Timestamp(params['to']).timestamp() if 'to' in params else self.clock()+1
        current=int((self.clock()-self.anchor)//60)
        last=min(current,int((cutoff-self.anchor-1e-6)//60))
        rows=[self.row(minute) for minute in range(last,last-params['count'],-1)]
        assert all(r is not None for r in rows)
        self.requests.append({'elapsed':round(self.elapsed,6),'application_to':params.get('to') if self.case['query_control']=='NONE' else 'CONTROL_COMPLETED_QUERY',
            'response_latest_open_utc':rows[0]['candle_date_time_utc'],'response_latest_volume':rows[0]['candle_acc_trade_volume']})
        return rows
    def session(self):
        fixture=self
        class Response:
            status_code=200
            def __enter__(self):return self
            def __exit__(self,*_):pass
            @property
            def headers(self):return {'Date':formatdate(fixture.clock(),usegmt=True)}
            def iter_content(self,chunk_size):yield json.dumps(self.body,allow_nan=False).encode()
        class Session:
            def get(self,url,*,params,**kwargs):
                assert url=='https://api.upbit.com/v1/candles/minutes/1'
                r=Response();r.body=fixture.rows(params);return r
            def close(self):pass
        return Session()
    def stream(self):
        fixture=self
        class Stream:
            def __init__(self,codes):assert codes==[fixture.spec['market']];self.offsets=iter(fixture.offsets())
            def connect(self):pass
            def get(self):
                try:fixture.elapsed=next(self.offsets)
                except StopIteration:fixture.stop.set();raise TimeoutError()
                minute=int((fixture.clock()-fixture.anchor)//60);events=[t for t in fixture.trades(minute) if t[0]<=fixture.clock()]
                trade=events[-1]
                return {'type':'ticker','code':fixture.spec['market'],'trade_price':float(trade[1]),
                    'timestamp':int(fixture.clock()*1000),'trade_timestamp':int(trade[0]*1000)}
            def close(self):pass
        return Stream


def run_case(spec,name):
    from services.public_market import PublicMarketClient
    from ops import paper_experiment as exp
    from ops.paper_session import run_session
    from data_analyzer.analyzer import DataAnalyzer
    fixture=MinuteFixture(spec,name);audit=[];raw=DataAnalyzer.analyze.__wrapped__
    def record(analyzer,index=-1):
        result=raw(analyzer,index)  # Observation only: no signal/result/input substitution.
        frame=analyzer.df;closed_minute=int((fixture.clock()-fixture.anchor)//60)-1
        latest=fixture.row(closed_minute)
        audit.append({'elapsed':round(fixture.elapsed,6),'open_utc':frame.index[-1].tz_localize('Asia/Seoul').tz_convert('UTC').isoformat(),
            'cached_volume':float(frame.volume.iloc[-1]),'mean_20':float(frame.volume.rolling(20).mean().iloc[-1]),
            'latest_completed_volume_on_server':latest['candle_acc_trade_volume'],'action':result['action']})
        return result
    with tempfile.TemporaryDirectory(dir='/tmp') as temp:
        account=Path(temp).resolve()/'account';created=exp.create(account)
        assert created['status']=='CREATED_NEW_INDEPENDENT_EXPERIMENT',created['status']
        token=exp.begin(account)
        client=PublicMarketClient(session=fixture.session(),clock=fixture.clock,monotonic=lambda:fixture.elapsed,
            sleep=lambda _:None,max_calls=64,deadline=fixture.case['duration_seconds'],stopped=fixture.stop.is_set)
        with patch.object(DataAnalyzer.analyze,'__wrapped__',record),patch.object(socket.socket,'connect',side_effect=AssertionError('No external network')):
            result=run_session(account/exp.STATE,seconds=fixture.case['duration_seconds'],stopped=fixture.stop.is_set,emit=lambda _:None,
                client=client,stream_factory=fixture.stream(),tickers=[spec['market']],monotonic=lambda:fixture.elapsed,
                wall=fixture.clock,experiment={'session_id':token,'record_decisions':True})
        terminal=exp.finish(account,token,result,record_summary=True)
        counts=result['decision_trace']['counts'];blocks=result['decision_trace']['final_blocks']
        stale=[a for a in audit if a['elapsed']>=60 and a['elapsed']<300 and a['cached_volume']<a['mean_20']*.5
            and a['latest_completed_volume_on_server']==100]
        return {'fixture_origin':spec['origin'],'status':result['status'],'terminal':terminal['status'],'counts':counts,
            'volume_gate':blocks['VOLUME_GATE'],'buys':result.get('cumulative_buys'),'sells':result.get('cumulative_sells'),
            'net_realized_pnl':result.get('net_realized_pnl'),'cash':result.get('cash'),'fees':result.get('fees'),
            'pending_orders':result.get('pending_orders'),'post_validation':result.get('post_validation'),'rest_calls':client.calls,'requests':fixture.requests,
            'control_to_overrides':fixture.control_overrides,'analyzer_calls_observed':len(audit),
            'first_evaluation_input':audit[0] if audit else None,'last_evaluation_input':audit[-1] if audit else None,
            'stale_gate_despite_eligible_completed_volume':len(stale),'first_stale_gate':stale[0] if stale else None,
            'candle_evaluation':result.get('candle_evaluation')}


def violations(results):
    errors=[]
    for name in ('forming_only','repeated_1085'):
        if results[name]['counts']['strategy_evaluations']<=0:errors.append(name+': strategy_evaluations must be > 0 on normal completed candles')
        if results[name]['volume_gate']!=0:errors.append(name+': incomplete volume must not gate completed strategy inputs')
    if results['cache_only']['stale_gate_despite_eligible_completed_volume']!=0:errors.append('cache_only: old volume must not gate newer eligible completed candles')
    if results['legitimate_low_completed']['volume_gate']!=1 or results['legitimate_low_completed']['counts']['strategy_evaluations']!=0:
        errors.append('legitimate_low_completed: unchanged 50 percent gate must remain')
    if (results['paper_round_trip']['buys'],results['paper_round_trip']['sells'])!=(1,1):errors.append('paper_round_trip: existing strategy/PAPER buy and risk sell must settle')
    if any(r['terminal']!='STOPPED_VERIFIED' for r in results.values()):errors.append('session must be reconciled STOPPED_VERIFIED')
    return errors


def main():
    # Run with an explicit source root in a separate interpreter: no stale sys.modules.
    source=Path(sys.argv[1]).resolve();sys.path.insert(0,str(source));os.environ['PYTHON_DOTENV_DISABLED']='1';logging.disable(logging.CRITICAL)
    spec=json.loads(FIXTURE.read_text());results={name:run_case(spec,name) for name in spec['cases']}
    errors=violations(results)
    print(json.dumps({'schema':'P14_REPLAY_RESULT_V1','fixture_origin':spec['origin'],'cases':results,'violations':errors,'contract_passed':not errors},allow_nan=False))
    return 1 if errors else 0


if __name__=='__main__':raise SystemExit(main())
