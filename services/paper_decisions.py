"""Bounded, memory-only PAPER decision observations. No orders, files or raw reasons."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import math
import time

LIMIT=2_147_483_647
MAX_MARKETS=30
MAX_BYTES=65_536
COUNTS=('stream_messages_received','timing_accepted','analyzer_calls','analyzer_returns',
        'strategy_attempts','strategy_evaluations','strategy_BUY','strategy_SELL','strategy_HOLD',
        'decision_BUY','decision_SELL','decision_HOLD','buy_attempts','sell_attempts',
        'buy_engine_attempts','sell_engine_attempts','buy_fills','sell_fills','analyzer_failures','strategy_failures','order_errors')
SKIPS=('VOLUME_GATE','COOLDOWN','RISK_PRIORITY','ANALYZER_DATA_FAILURE','MARKET_VALIDATION')
BLOCKS=('VOLUME_GATE','COOLDOWN','NO_ACTIONABLE_SIGNAL','BUY_ALREADY_HELD','SELL_WITHOUT_POSITION',
        'MAX_POSITIONS','CASH_BELOW_MINIMUM','VALUE_BELOW_MINIMUM','ENGINE_REJECTED','ORDER_ERROR')
CONSTRAINTS=('MAX_POSITIONS','CASH_BELOW_MINIMUM','VALUE_BELOW_MINIMUM','REQUEST_EXCEEDS_CASH')
RISK=('TAKE_PROFIT','STOP_OR_AVERAGE')
ACTIONS=('BUY','SELL','HOLD')
STAGES=('ATTEMPT','ENGINE_ATTEMPT','FILLED','BLOCKED','ERROR')
REASONS=(*BLOCKS,*RISK,'SIGNAL','UNKNOWN')


def utc_stamp(value):
    try:return datetime.fromtimestamp(value,timezone.utc).isoformat() if value is not None else None
    except (ValueError,OverflowError,OSError):return None


def zeros(keys):return dict.fromkeys(keys,0)
def row():return {'counts':zeros(COUNTS),'final_blocks':zeros(BLOCKS),'last_decision':None,'last_strategy_result':None,'last_order':None}


def observe(owner,event,market,**fields):
    observer=getattr(owner,'decision_observer',None)
    if observer is not None:observer.record(event,market,**fields)


class DecisionCounters:
    def __init__(self,wall=time.time):
        self.wall=wall;self.total=row();self.markets={};self.skips=zeros(SKIPS);self.constraints=zeros(CONSTRAINTS)
        self.pairs=zeros([a+'+'+b for i,a in enumerate(CONSTRAINTS) for b in CONSTRAINTS[i+1:]])
        self.overlaps=0;self.risk=zeros(RISK);self.data=zeros(('MARKET_VALIDATION','CANDLE_PREPARATION_OR_REFRESH'))
        self.saturated=False;self.failed=False;self.gates={};self.attempted=set();self.gate_conditions=zeros(('VOLUME_GATE','COOLDOWN','BOTH'))
    def stamp(self):
        try:
            n=float(self.wall());return n if math.isfinite(n) and 0<=n<=10**12 else None
        except Exception:return None
    def inc(self,mapping,key):
        if mapping[key]==LIMIT:self.saturated=True
        else:mapping[key]+=1
    def record(self,event,market=None,**fields):
        # Observation faults must not alter strategy/order control flow or invent valid statistics.
        try:self._record(event,market,fields)
        except Exception:self.failed=True
    def _record(self,event,market,f):
        if market is not None:
            from services.public_market import MARKET
            if not isinstance(market,str) or not MARKET.fullmatch(market):raise ValueError()
            if market not in self.markets:
                if len(self.markets)>=MAX_MARKETS:raise ValueError()
                self.markets[market]=row()
        rows=[self.total]+([self.markets[market]] if market is not None else [])
        def count(key):
            for r in rows:self.inc(r['counts'],key)
        def block(reason):
            for r in rows:self.inc(r['final_blocks'],reason)
        def last(which,value):
            stamp=self.stamp()
            for r in rows:r[which]=dict(value,at_epoch_seconds=stamp,at_utc=utc_stamp(stamp),market=market)
        if event in COUNTS:
            count(event)
            if event=='analyzer_calls':self.gates.pop(market,None);self.attempted.discard(market)
            if event=='strategy_attempts':self.attempted.add(market)
        elif event=='gate':
            code=f['reason'];self.inc(self.skips,code);block(code);self.gates[market]=code
        elif event=='gate_conditions':
            for k in ('VOLUME_GATE','COOLDOWN'):
                if f.get(k) is True:self.inc(self.gate_conditions,k)
            if f.get('VOLUME_GATE') is True and f.get('COOLDOWN') is True:self.inc(self.gate_conditions,'BOTH')
        elif event=='strategy_result':
            action=f['action']
            if action not in ACTIONS:raise ValueError()
            count('strategy_evaluations');count('strategy_'+action)
            last('last_strategy_result',{'action':action,'reason':'SIGNAL'})
        elif event=='decision':
            action=f['action'];code=self.gates.get(market,'NO_ACTIONABLE_SIGNAL' if action=='HOLD' else 'SIGNAL')
            if action not in ACTIONS or code not in REASONS:raise ValueError()
            count('analyzer_returns');count('decision_'+action)
            if code=='NO_ACTIONABLE_SIGNAL':block(code)
            last('last_decision',{'action':action,'reason':code})
        elif event=='block':block(f['reason'])
        elif event=='risk':
            self.inc(self.risk,f['reason']);self.inc(self.skips,'RISK_PRIORITY')
        elif event=='analyzer_error':
            count('analyzer_failures')
            if market not in self.attempted and market not in self.gates:self.inc(self.skips,'ANALYZER_DATA_FAILURE')
            elif market in self.attempted:count('strategy_failures')
        elif event=='constraints':
            active=[k for k in CONSTRAINTS if f.get(k) is True]
            for k in active:self.inc(self.constraints,k)
            if len(active)>1:
                if self.overlaps<LIMIT:self.overlaps+=1
                else:self.saturated=True
            for i,a in enumerate(active):
                for b in active[i+1:]:self.inc(self.pairs,a+'+'+b)
        elif event=='order':
            side=f['side'];stage=f['stage'];reason=f.get('reason','SIGNAL')
            if side not in ('BUY','SELL') or stage not in STAGES or reason not in REASONS:raise ValueError()
            key={'ATTEMPT':'attempts','ENGINE_ATTEMPT':'engine_attempts','FILLED':'fills'}.get(stage)
            if key:count(side.lower()+'_'+key)
            elif stage=='BLOCKED':block(reason)
            elif stage=='ERROR':count('order_errors');block('ORDER_ERROR')
            last('last_order',{'side':side,'stage':stage,'reason':reason})
        elif event=='data_error':
            code=f['reason'];self.inc(self.data,code)
            if code=='MARKET_VALIDATION':self.inc(self.skips,'MARKET_VALIDATION')
        else:raise ValueError()
    def snapshot(self,timing=None):
        timing_stats=None
        if timing is not None:
            from services.tick_timing import ISSUES
            timing_stats={'issues':{k:min(LIMIT,timing.global_issues[k]+sum(r['issues'][k] for r in timing.markets.values())) for k in ISSUES},
                'latest_guard_timestamp_ms':dict(timing.previous)}
        return {'schema':'P13_PAPER_DECISIONS_V1','availability':'UNVERIFIED' if self.failed else 'RECORDED',
            'counter_limit':LIMIT,'saturated':self.saturated,'timestamp_stats':timing_stats,**deepcopy(self.total),'markets':deepcopy(self.markets),
            'strategy_not_evaluated':dict(self.skips),'gate_conditions_overlapping':dict(self.gate_conditions),'candidate_constraints_overlapping':dict(self.constraints),
            'attempts_with_multiple_constraints':self.overlaps,'constraint_pairs_overlapping':dict(self.pairs),
            'risk_priority':dict(self.risk),'data_failures':dict(self.data)}


def validate_trace(value):
    """Reject unknown fields/types/raw strings before persistence or report output."""
    if len(json.dumps(value,allow_nan=False).encode())>MAX_BYTES:raise ValueError('DECISION_TRACE_TOO_LARGE')
    expected=DecisionCounters().snapshot()
    if not isinstance(value,dict) or set(value)!=set(expected) or value['schema']!=expected['schema']:raise ValueError('DECISION_TRACE_SCHEMA')
    if value['availability'] not in ('RECORDED','UNVERIFIED') or type(value['saturated']) is not bool or value['counter_limit']!=LIMIT:raise ValueError('DECISION_TRACE_STATUS')
    def counters(got,keys):
        if type(got) is not dict or set(got)!=set(keys) or any(type(v) is not int or not 0<=v<=LIMIT for v in got.values()):raise ValueError('DECISION_TRACE_COUNTER')
    def last(got,order=False):
        if got is None:return
        keys={'side','stage','reason','at_epoch_seconds','at_utc','market'} if order else {'action','reason','at_epoch_seconds','at_utc','market'}
        if type(got) is not dict or set(got)!=keys or got['reason'] not in REASONS:raise ValueError('DECISION_TRACE_LAST')
        from services.public_market import MARKET
        if got['market'] is not None and (not isinstance(got['market'],str) or not MARKET.fullmatch(got['market'])):raise ValueError('DECISION_TRACE_MARKET')
        t=got['at_epoch_seconds']
        if t is not None and (type(t) not in (int,float) or not math.isfinite(t) or not 0<=t<=10**12):raise ValueError('DECISION_TRACE_TIME')
        if got['at_utc']!=utc_stamp(t):raise ValueError('DECISION_TRACE_TIME')
        if (order and (got['side'] not in ('BUY','SELL') or got['stage'] not in STAGES)) or (not order and got['action'] not in ACTIONS):raise ValueError('DECISION_TRACE_ACTION')
    def validate_row(r):
        if type(r) is not dict or set(r)!=set(row()):raise ValueError('DECISION_TRACE_ROW')
        counters(r['counts'],COUNTS);counters(r['final_blocks'],BLOCKS);last(r['last_decision']);last(r['last_strategy_result']);last(r['last_order'],True)
    validate_row({k:value[k] for k in row()})
    from services.public_market import MARKET
    if type(value['markets']) is not dict or len(value['markets'])>MAX_MARKETS:raise ValueError('DECISION_TRACE_MARKETS')
    for market,r in value['markets'].items():
        if not MARKET.fullmatch(market):raise ValueError('DECISION_TRACE_MARKET')
        validate_row(r)
    for k,keys in (('gate_conditions_overlapping',expected['gate_conditions_overlapping']),('strategy_not_evaluated',SKIPS),('candidate_constraints_overlapping',CONSTRAINTS),
                   ('constraint_pairs_overlapping',expected['constraint_pairs_overlapping']),('risk_priority',RISK),('data_failures',expected['data_failures'])):
        counters(value[k],keys)
    timing=value['timestamp_stats']
    if timing is not None:
        from services.tick_timing import ISSUES
        if type(timing) is not dict or set(timing)!={'issues','latest_guard_timestamp_ms'}:raise ValueError('DECISION_TRACE_TIMING')
        counters(timing['issues'],ISSUES)
        previous=timing['latest_guard_timestamp_ms']
        if type(previous) is not dict or len(previous)>MAX_MARKETS:raise ValueError('DECISION_TRACE_TIMING')
        for t,stamp in previous.items():
            if not MARKET.fullmatch(t) or type(stamp) is not int or not 0<=stamp<=10**16:raise ValueError('DECISION_TRACE_TIMING')
    n=value['attempts_with_multiple_constraints']
    if type(n) is not int or not 0<=n<=LIMIT:raise ValueError('DECISION_TRACE_OVERLAP')
    return deepcopy(value)
