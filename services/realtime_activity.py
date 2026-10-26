"""Bounded, pre-trade observation; server-labeled realtime evidence, not trade proof."""
import time

from services.public_market import positive
from services.tick_timing import TickTiming,numeric

MAX_MARKETS=30
MAX_MESSAGES=100_000
MAX_START_DELAY=5
MAX_PROGRESS_AGE=10
ACTIVITY_ERRORS=frozenset({'ACTIVITY_OBSERVATION_INTERRUPTED','ACTIVITY_DEADLINE_INSUFFICIENT',
    'ACTIVITY_CONNECTION_FAILED','ACTIVITY_STREAM_TYPE_UNKNOWN','ACTIVITY_INVALID_MESSAGE',
    'ACTIVITY_MESSAGE_LIMIT','ACTIVITY_START_EVIDENCE_EXPIRED','ACTIVITY_OBSERVATION_FAILED',
    'INSUFFICIENT_REALTIME_ACTIVE_CANDIDATES','HELD_REALTIME_ACTIVITY_UNAVAILABLE','REALTIME_ACTIVITY_REQUIRED'})


class ActivityError(ValueError):pass


class Observation:
    def __init__(self,markets,*,wall=time.time,monotonic=time.monotonic):
        if not 1<=len(markets)<=MAX_MARKETS:raise ActivityError('ACTIVITY_OBSERVATION_FAILED')
        self.wall,self.monotonic=wall,monotonic
        self.start=monotonic();self.started_wall=wall();self.end=None
        self.guard=TickTiming(markets,self.started_wall,self.start)
        self.stats={t:{'realtime':0,'snapshot':0,'unknown_type':0,'first_receive_ms':None,
                       'first_realtime_ms':None,'last_realtime_receive':None} for t in markets}
        self.received=0;self.completed=False;self.connection_end='UNKNOWN';self.close_code=None;self.start_check_delay_ms=None

    def observe(self,stream,seconds,*,stopped,deadline):
        if type(seconds) is not int or not 1<=seconds<=60:raise ActivityError('ACTIVITY_OBSERVATION_FAILED')
        if deadline-self.start<seconds+MAX_START_DELAY:raise ActivityError('ACTIVITY_DEADLINE_INSUFFICIENT')
        target=self.start+seconds
        try:
            while self.monotonic()<target:
                if stopped() or self.monotonic()>=deadline:raise ActivityError('ACTIVITY_OBSERVATION_INTERRUPTED')
                try:data=stream.get()
                except TimeoutError:
                    self.guard.clock(self.wall(),self.monotonic());continue
                if stopped() or self.monotonic()>=deadline:raise ActivityError('ACTIVITY_OBSERVATION_INTERRUPTED')
                if self.monotonic()>=target:break  # Frames read after the window are not evidence/prices.
                self.received+=1
                if self.received>=MAX_MESSAGES:raise ActivityError('ACTIVITY_MESSAGE_LIMIT')
                if (not isinstance(data,dict) or data.get('type')!='ticker' or not isinstance(data.get('code'),str)
                        or data['code'] not in self.stats):raise ActivityError('ACTIVITY_INVALID_MESSAGE')
                t=data['code'];m=self.stats[t];now=self.monotonic()
                if m['first_receive_ms'] is None:m['first_receive_ms']=numeric((now-self.start)*1000)
                self.guard.clock(self.wall(),now,t)
                kind=data.get('stream_type')
                if kind=='SNAPSHOT':m['snapshot']+=1;continue
                if kind!='REALTIME':
                    m['unknown_type']+=1;raise ActivityError('ACTIVITY_STREAM_TYPE_UNKNOWN')
                # Only the pre-trade observer disables liveness rejection, so zero-tick
                # candidates can be measured for the FULL window. Runtime never does.
                self.guard.observe(data,self.wall(),now,check_liveness=False);positive(data.get('trade_price'))
                m['realtime']+=1;m['last_realtime_receive']=now
                if m['first_realtime_ms'] is None:m['first_realtime_ms']=numeric((now-self.start)*1000)
            self.guard.clock(self.wall(),self.monotonic())
            self.end=self.monotonic();self.completed=True
            self.connection_end='OBSERVATION_COMPLETED_CONNECTION_RETAINED'
        except Exception as exc:
            self.end=self.monotonic()
            from websockets.exceptions import ConnectionClosed
            if isinstance(exc,ConnectionClosed):
                self.connection_end='WEBSOCKET_CLOSE_OBSERVED_CAUSE_UNKNOWN'
                code=getattr(getattr(exc,'rcvd',None),'code',None)
                self.close_code=code if type(code) is int else None
                raise ActivityError('ACTIVITY_CONNECTION_FAILED') from None
            if isinstance(exc,(ActivityError,ValueError)):
                self.connection_end='OBSERVATION_INTERRUPTED_OR_VALIDATION_FAILED';raise
            self.connection_end='READ_OR_CONNECTION_FAILURE_CAUSE_UNKNOWN'
            raise ActivityError('ACTIVITY_CONNECTION_FAILED') from None
        return self

    def active(self,t,now=None):
        now=self.monotonic() if now is None else now
        return (self.completed and self.stats[t]['realtime']>0
                and 0<=now-self.guard.progress[t]<=MAX_PROGRESS_AGE)

    def exclusion(self,t):
        return ('NO_REALTIME_TICKS_DURING_OBSERVATION' if self.stats[t]['realtime']==0
                else 'REALTIME_ACTIVITY_NOT_RECENT_AT_OBSERVATION_END')

    def validate_start(self,selected):
        now=self.monotonic()
        self.guard.clock(self.wall(),now)
        self.start_check_delay_ms=numeric((now-self.end)*1000) if self.end is not None else None
        if (not self.completed or self.end is None or not 0<=now-self.end<=MAX_START_DELAY
                or any(self.stats[t]['realtime']==0 or not 0<=now-self.guard.progress[t]<15 for t in selected)):
            raise ActivityError('ACTIVITY_START_EVIDENCE_EXPIRED')

    def runtime_guard(self,selected,clock_origin):
        self.validate_start(selected)
        g=TickTiming(selected,self.wall(),self.monotonic(),clock_origin=clock_origin)
        g.previous={t:self.guard.previous[t] for t in selected}
        g.progress={t:self.guard.progress[t] for t in selected};g.last_progress=max(g.progress.values())
        return g

    def report(self):
        now=self.monotonic();assessment=self.end if self.end is not None else now;rows={}
        for t,stats in self.stats.items():
            m={k:v for k,v in stats.items() if k!='last_realtime_receive'}
            m['last_realtime_receive_age_ms']=(numeric((assessment-stats['last_realtime_receive'])*1000)
                                              if stats['last_realtime_receive'] is not None else None)
            m['no_progress_ms']=numeric((assessment-self.guard.progress[t])*1000)
            m['status']=('REALTIME_ACTIVITY_OBSERVED' if self.active(t,assessment) else self.exclusion(t)) if self.completed else 'INCOMPLETE_UNKNOWN'
            rows[t]=m
        transport=('INCOMPLETE_UNKNOWN' if not self.completed else
                   'NO_MESSAGES_CONNECTION_OR_ACTIVITY_UNKNOWN' if self.received==0 else
                   'SOME_REALTIME_LABELED_MESSAGES_NOT_PER_MARKET_HEALTH_PROOF' if any(v['realtime'] for v in self.stats.values()) else
                   'SNAPSHOT_ONLY_REALTIME_SUBSCRIPTION_UNVERIFIED')
        return {'transport_observation':transport,'completed':self.completed,'window_elapsed_ms':numeric((assessment-self.start)*1000),
                'evidence_age_at_report_ms':numeric((now-self.end)*1000) if self.end is not None else None,
                'last_start_check_delay_ms':self.start_check_delay_ms,
                'received_messages':self.received,'markets':rows,'connection_end':self.connection_end,
                'close_code':self.close_code,'max_start_delay_ms':MAX_START_DELAY*1000,
                'max_progress_age_at_observation_end_ms':MAX_PROGRESS_AGE*1000,
                'classification_basis':'SERVER_STREAM_TYPE_REALTIME_NOT_TRADE_OR_API_HEALTH_PROOF',
                'wire_request_isOnlyRealtime':True,'official_parameter_contract':'PENDING_EXTERNAL_DOC_VERIFICATION',
                'tick_timing':self.guard.report(),'max_messages':MAX_MESSAGES,'retained_log_files':0}
