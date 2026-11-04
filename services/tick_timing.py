"""Bounded numeric ticker diagnostics. No I/O, raw payloads, prices or credentials."""
from collections import deque
from dataclasses import dataclass
import copy
import math

from services.public_market import MARKET

MAX_MARKETS=100
MAX_SAMPLES=16
MAX_COUNTER=2_147_483_647
MAX_NUMBER=10**16
MAX_REPORT_BYTES=65_536
SMALL_REGRESSION_MS=5
MAX_DISCARDS_PER_MARKET=1
MAX_DISCARDS_PER_SESSION=3
DISCARD_SESSION_SECONDS=3600
ISSUES=('TIMESTAMP_MISSING','TIMESTAMP_TYPE_INVALID','TIMESTAMP_DELAYED','TIMESTAMP_FUTURE',
        'TIMESTAMP_REGRESSION','CLOCK_DISCONTINUITY','STREAM_IDLE','MARKET_IDLE','INVALID_ENVELOPE')


@dataclass(frozen=True)
class DiscardPolicy:
    identifier: str
    enabled: bool
    max_per_market: int
    max_per_session: int
    max_width_ms: int | None = SMALL_REGRESSION_MS
    max_session_seconds: int = DISCARD_SESSION_SECONDS
    quarantine: bool = False  # P25: any validated regression is quarantined until a same-market advance.

    def evidence(self):
        result={'id':self.identifier,'max_per_market':self.max_per_market,'max_per_session':self.max_per_session,
                'max_width_ms':self.max_width_ms,'max_session_seconds':self.max_session_seconds}
        if self.quarantine:result['release']=QUARANTINE_RELEASE  # Absent for every pre-P25 policy (unchanged evidence).
        return result


STRICT_POLICY=DiscardPolicy('STRICT',False,1,3)
P15_POLICY=DiscardPolicy('P15_SINGLE_DISCARD',True,1,3)
REPEAT_POLICY=DiscardPolicy('P18_BOUNDED_REPEAT_V1',True,10,30)
QUARANTINE_RELEASE='SAME_MARKET_TIMESTAMP_ABOVE_HIGH_WATER'
# P25: no width limit (the width is not re-decided by an arbitrary ms value). Budgets are the
# same fixed P18 caps; they are operating limits, not statistically proven safe values.
QUARANTINE_POLICY=DiscardPolicy('P25_QUARANTINE_UNTIL_ADVANCE_V1',True,10,30,max_width_ms=None,quarantine=True)
# P26: the same quarantine rules, but the 10/market and 30 caps are ACCOUNT-wide across the two
# allowed P26 sessions (carried, never refilled). The per-session 3600 s discard window is unchanged.
ACCOUNT_QUARANTINE_POLICY=DiscardPolicy('P26_ACCOUNT_QUARANTINE_V1',True,10,30,max_width_ms=None,quarantine=True)
BOUND_POLICIES={REPEAT_POLICY.identifier:REPEAT_POLICY,QUARANTINE_POLICY.identifier:QUARANTINE_POLICY,
                ACCOUNT_QUARANTINE_POLICY.identifier:ACCOUNT_QUARANTINE_POLICY}


def discard_policy(enabled=False,repeated=False,quarantine=False,account=False):
    if (type(enabled) is not bool or type(repeated) is not bool or type(quarantine) is not bool or type(account) is not bool
            or (repeated and not enabled) or (quarantine and (enabled or repeated)) or (account and not quarantine)):
        raise ValueError('INVALID_REGRESSION_POLICY')  # P25/P26 are exclusive: never mixed with P15/P18 flags.
    if account:return ACCOUNT_QUARANTINE_POLICY
    if quarantine:return QUARANTINE_POLICY
    return REPEAT_POLICY if repeated else P15_POLICY if enabled else STRICT_POLICY


def numeric(value):
    if type(value) is int:return max(-MAX_NUMBER,min(MAX_NUMBER,value))
    if type(value) is not float or not math.isfinite(value):return None
    return round(max(-MAX_NUMBER,min(MAX_NUMBER,value)),3)


def field_type(data,key):
    if key not in data:return 'missing'
    t=type(data[key])
    return t.__name__ if t in (int,float,str,bool,type(None)) else 'other'


class TickTimingError(ValueError):pass


class TickTiming:
    def __init__(self,markets,wall,monotonic,*,clock_origin=None,discard_small_regressions=False,
                 discard_repeated_small_regressions=False,quarantine_regressions=False,account_prior=None):
        if (not 1<=len(markets)<=MAX_MARKETS or len(set(markets))!=len(markets)
                or any(not isinstance(t,str) or not MARKET.fullmatch(t) for t in markets)):
            raise ValueError('INVALID_TIMING_MARKETS')
        self.start_wall,self.start_mono=clock_origin if clock_origin is not None else (wall,monotonic)
        self.previous={};self.progress={t:monotonic for t in markets};self.last_progress=monotonic
        self.markets={t:{'received':0,'timing_accepted':0,'equal_timestamp':0,
                         'issues':{k:0 for k in ISSUES}} for t in markets}
        self.global_issues={k:0 for k in ISSUES};self.samples=deque(maxlen=MAX_SAMPLES)
        self._policy=discard_policy(discard_small_regressions,discard_repeated_small_regressions,quarantine_regressions,
                                    account_prior is not None)
        self.discarded=0;self.awaiting_followup=None;self.quarantined=set();self.held=0
        self.prior_counts={};self.prior_total=0
        if account_prior is not None:
            # P26: carried, already-validated account checkpoint. Only budget and high-water carry over;
            # an unresolved quarantine can never be carried (a checkpoint with one is rejected upstream).
            if (type(account_prior) is not dict or set(account_prior)!={'high_water_ms','counts','total'}
                    or any(t not in self.markets for t in (*account_prior['high_water_ms'],*account_prior['counts']))
                    or any(type(v) is not int for v in (*account_prior['high_water_ms'].values(),*account_prior['counts'].values()))
                    or type(account_prior['total']) is not int or account_prior['total']!=sum(account_prior['counts'].values())
                    or any(not 0<=v<=self._policy.max_per_market for v in account_prior['counts'].values())
                    or not 0<=account_prior['total']<=self._policy.max_per_session):
                raise ValueError('INVALID_ACCOUNT_PRIOR')
            self.prior_counts=dict(account_prior['counts']);self.prior_total=account_prior['total']
            self.previous.update(account_prior['high_water_ms'])
        self.discards={t:{'count':0,'max_regression_ms':0,'last_regression_ms':None,
                          'followup_newer_received':None} for t in markets}
        self.received=0;self.accepted=0

    @property
    def policy(self):return self._policy

    @property
    def discard_enabled(self):return self._policy.enabled

    def increment(self,mapping,key):mapping[key]=min(MAX_COUNTER,mapping[key]+1)

    def fail(self,code,market=None,**numbers):
        target=self.markets[market]['issues'] if market in self.markets else self.global_issues
        self.increment(target,code)
        sample={'issue':code,'market':market if market in self.markets else 'ALL_OR_UNKNOWN'}
        # Fixed allowlist: no response body, unknown keys, invalid string values or prices.
        sample.update(numbers);self.samples.append(sample)
        root={'CLOCK_DISCONTINUITY':'PAPER_CLOCK_DISCONTINUITY','STREAM_IDLE':'PAPER_STREAM_TIMEOUT',
              'MARKET_IDLE':'PAPER_MARKET_STALE','INVALID_ENVELOPE':'PAPER_INVALID_TICK'}.get(code,'PAPER_STALE_TICK')
        raise TickTimingError(root)

    def clock(self,wall,mono,market=None,received=None):
        try:
            delta=(wall-self.start_wall)-(mono-self.start_mono)
            valid=all(type(v) in (int,float) and math.isfinite(v) for v in (wall,mono,delta))
            if not valid or mono<self.start_mono or abs(delta)>5:
                self.fail('CLOCK_DISCONTINUITY',market,wall_minus_monotonic_ms=numeric(delta*1000),
                          wall_elapsed_ms=numeric((wall-self.start_wall)*1000),
                          monotonic_elapsed_ms=numeric((mono-self.start_mono)*1000),
                          received_timestamp_ms=numeric(received),previous_timestamp_ms=numeric(self.previous.get(market)))
        except (TypeError,OverflowError):
            self.fail('CLOCK_DISCONTINUITY',market,wall_minus_monotonic_ms=None)

    def idle(self,wall,mono):
        self.clock(wall,mono)
        if mono-self.last_progress>=15:
            self.fail('STREAM_IDLE',idle_ms=numeric((mono-self.last_progress)*1000))
        for t,at in self.progress.items():
            if mono-at>=60:self.fail('MARKET_IDLE',t,idle_ms=numeric((mono-at)*1000))

    def observe(self,data,wall,mono,*,check_liveness=True):
        self.received=min(MAX_COUNTER,self.received+1)
        if (not isinstance(data,dict) or data.get('type')!='ticker'
                or not isinstance(data.get('code'),str) or data['code'] not in self.markets):
            self.fail('INVALID_ENVELOPE')
        t=data['code'];m=self.markets[t];self.increment(m,'received');self.clock(wall,mono,t,data.get('timestamp'))
        stamp=data.get('timestamp');previous=self.previous.get(t);trade=data.get('trade_timestamp')
        details={'timestamp_type':field_type(data,'timestamp'),'trade_timestamp_type':field_type(data,'trade_timestamp'),
                 'received_timestamp_ms':numeric(stamp),'previous_timestamp_ms':numeric(previous),
                 'local_receive_wall_ms':numeric(wall*1000),'server_relative_delay_ms':None}
        if 'timestamp' not in data:self.fail('TIMESTAMP_MISSING',t,**details)
        if type(stamp) is not int:self.fail('TIMESTAMP_TYPE_INVALID',t,**details)
        details.update(local_receive_minus_timestamp_ms=numeric(wall*1000-stamp),
                       timestamp_minus_previous_ms=numeric(stamp-previous) if previous is not None else None,
                       timestamp_minus_trade_timestamp_ms=numeric(stamp-trade) if type(trade) is int else None)
        if stamp<wall*1000-60_000:self.fail('TIMESTAMP_DELAYED',t,**details)
        if stamp>wall*1000+5000:self.fail('TIMESTAMP_FUTURE',t,**details)
        if previous is not None and stamp<previous:
            width=previous-stamp;row=self.discards[t]
            if self.policy.quarantine:
                # Validated (market/type/int/delay/future/clock above) but below the same-market
                # high-water mark: drop at input. No price, high-water, progress or accepted update.
                if (self.prior_counts.get(t,0)+row['count']<self.policy.max_per_market
                        and self.prior_total+self.discarded<self.policy.max_per_session
                        and 0<=mono-self.start_mono<self.policy.max_session_seconds):
                    self.idle(wall,mono)  # An already-expired watchdog wins over a discard.
                    row.update(count=row['count']+1,max_regression_ms=max(row['max_regression_ms'],width),
                               last_regression_ms=width,followup_newer_received=False)
                    self.discarded+=1;self.quarantined.add(t);self.awaiting_followup=min(self.quarantined)
                    self.samples.append({'issue':'REGRESSION_QUARANTINED','market':t,**details})
                    return None
                self.fail('TIMESTAMP_REGRESSION',t,**details)
            if (self.discard_enabled and width<=self.policy.max_width_ms
                    and row['count']<self.policy.max_per_market and self.discarded<self.policy.max_per_session
                    and self.awaiting_followup is None and 0<=mono-self.start_mono<self.policy.max_session_seconds):
                # Do not advance previous/progress/last_progress or return a market/price.
                # Already-expired watchdogs take precedence over granting a discard.
                self.idle(wall,mono)
                row.update(count=row['count']+1,max_regression_ms=max(row['max_regression_ms'],width),
                           last_regression_ms=width,followup_newer_received=False)
                self.discarded+=1;self.awaiting_followup=t
                self.samples.append({'issue':'SMALL_REGRESSION_DISCARDED','market':t,**details})
                return None
            self.fail('TIMESTAMP_REGRESSION',t,**details)
        if self.policy.quarantine and check_liveness and t in self.quarantined and stamp>previous:
            # P25 runtime release: check the PRE-update 15 s/60 s watchdogs before any high-water,
            # progress, release or price forwarding, so a delayed follow-up cannot erase an expired deadline.
            # (Legacy modes and the pre-trade observation's measurement contract keep their old order.)
            self.idle(wall,mono)
        if previous==stamp:
            self.increment(m,'equal_timestamp')
            if t in self.quarantined:
                # Non-advancing price while quarantined: never forwarded and never a release.
                if check_liveness:self.idle(wall,mono)
                self.held=min(MAX_COUNTER,self.held+1)
                return None
        else:self.progress[t]=mono;self.last_progress=mono
        self.previous[t]=stamp
        # Equal timestamps keep the legacy acceptance rule, but cannot extend liveness.
        if check_liveness:self.idle(wall,mono)
        if t in self.quarantined and stamp>previous:
            # Release only by a valid same-market timestamp above the high-water mark.
            self.discards[t]['followup_newer_received']=True;self.quarantined.discard(t)
            self.awaiting_followup=min(self.quarantined) if self.quarantined else None
        elif self.awaiting_followup==t and previous is not None and stamp>previous:
            self.discards[t]['followup_newer_received']=True;self.awaiting_followup=None
        self.increment(m,'timing_accepted');self.accepted=min(MAX_COUNTER,self.accepted+1)
        return t

    def report(self):
        result=self._report()
        if self.policy.quarantine:
            result['regression_discard'].update(quarantined_markets=sorted(self.quarantined),quarantine_held=self.held,
                release=QUARANTINE_RELEASE)
        if self.policy is ACCOUNT_QUARANTINE_POLICY:
            counts={t:self.prior_counts.get(t,0)+r['count'] for t,r in self.discards.items()}
            result['regression_discard'].update(account_prior_total=self.prior_total,
                account_discarded_total=self.prior_total+self.discarded,account_counts=counts,
                high_water_ms={t:v for t,v in self.previous.items() if t in self.markets})
        return result

    def _report(self):
        return {'received_total':self.received,'timing_accepted_total':self.accepted,
                'markets':copy.deepcopy(self.markets),'global_issues':dict(self.global_issues),'samples':list(self.samples),
                'max_samples':MAX_SAMPLES,'max_report_bytes':MAX_REPORT_BYTES,'retained_log_files':0,
                'reference_clock':'LOCAL_WALL_NOT_INDEPENDENTLY_VERIFIED_SERVER_TIME',
                'server_relative_delay_ms':None,
                'regression_policy':self.policy.identifier if self.policy in BOUND_POLICIES.values() else
                    'EXPERIMENTAL_DISCARD_SMALL_REGRESSIONS' if self.discard_enabled else 'HALT_NO_TOLERANCE_OR_PRICE_FALLBACK',
                'regression_discard':{'enabled':self.discard_enabled,'policy_id':self.policy.identifier,'discarded_total':self.discarded,
                    'markets':copy.deepcopy(self.discards),'awaiting_followup':self.awaiting_followup,
                    'max_width_ms':self.policy.max_width_ms,'max_per_market':self.policy.max_per_market,
                    'max_per_session':self.policy.max_per_session,'max_session_seconds':self.policy.max_session_seconds,
                    'budget_scope':'FIRST_EXECUTION_ONLY_MAX_ONE_HOUR_NO_REFILL_OR_REENABLE',
                    'prices_forwarded_from_discarded_messages':0},
                'counter_limit':MAX_COUNTER,'numeric_limit':MAX_NUMBER}
