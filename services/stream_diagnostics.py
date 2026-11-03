"""P23 bounded STREAM_IDLE diagnostics. Observation only; never strategy, order or valuation input.

The record is a small fixed allowlist: no raw packet, price, credential, URL or exception text.
It runs only after the engine HALT is confirmed and never reconnects, restarts or clears a halt.
It never sends on the live connection (see ``probe``), so the existing close/cleanup is unaffected.
"""
import json
import math
import time

SCHEMA='P23_STREAM_IDLE_DIAGNOSTICS_V1'
BUDGET_SECONDS=2.0
MAX_RECORD_BYTES=2048
MAX_COUNTER=2_147_483_647
MAX_MS=10**12
PATHS=('RECV_TIMEOUT','RECEIVED_MESSAGE_CHECK','OTHER')
MESSAGE_KINDS=('STRATEGY_TICKER','NON_STRATEGY_OR_SNAPSHOT',None)
CONNECTION_STATES=('OPEN','CLOSING','CLOSED','UNKNOWN')
PONG_RESULTS=('CONFIRMED','UNCONFIRMED','UNSUPPORTED','CANCELLED','ERROR')
PROBE_REASONS=('LIVE_PING_SEND_NOT_BOUNDED','CONNECTION_NOT_OPEN','STREAM_PROBE_UNSUPPORTED',
    'STOP_REQUESTED','ENGINE_HALT_UNCONFIRMED','PROBE_FAILED')
COUNTERS=('stream_messages','timing_received','timing_accepted','equal_timestamp','discarded','ignored_non_strategy')
INTERPRETATION='DIAGNOSTIC_ONLY_NOT_ROOT_CAUSE_UNCONFIRMED_PONG_IS_NOT_NETWORK_FAILURE_PROOF'
KEYS={'schema','status','trigger_path','message_kind','idle_ms','since_last_message_ms','since_last_advance_ms',
      'advance_reference','counters','counter_scope','connection_before','connection_after','pong','ping_sent',
      'probe_reason','probe_elapsed_ms','probe_budget_ms','order','interpretation'}


def not_triggered():
    return {'schema':SCHEMA,'status':'NOT_TRIGGERED'}


def unavailable():
    return {'schema':SCHEMA,'status':'UNAVAILABLE','interpretation':INTERPRETATION}


def _ms(value):
    if type(value) not in (int,float) or not math.isfinite(value):return None
    return round(max(-MAX_MS,min(MAX_MS,float(value))),3)


def _state(stream):
    reader=getattr(stream,'connection_state',None)
    if not callable(reader):return 'UNKNOWN'
    try:value=reader()
    except Exception:return 'UNKNOWN'
    return value if value in CONNECTION_STATES else 'UNKNOWN'


def probe(stream,*,cancelled=lambda:False,budget=BUDGET_SECONDS,clock=time.monotonic):
    """Non-blocking connection check. Never sends a ping on the live connection.

    websockets 17 sync ``ping()`` and ``close()`` share ``protocol_mutex`` and ``sendall()`` runs
    while it is held, so a ping whose send blocks cannot be bounded or cancelled without delaying
    the existing ``stream.close()`` and terminal evidence. P23 therefore reports UNSUPPORTED
    instead of starting an unbounded sender: no thread, no lock, no wait. ``budget`` only caps the
    recorded elapsed time; nothing here waits for it.
    """
    started=clock()
    result={'connection_before':'UNKNOWN','connection_after':'UNKNOWN','pong':'UNSUPPORTED',
            'ping_sent':False,'probe_reason':'STREAM_PROBE_UNSUPPORTED'}
    def finish(pong,reason):
        result.update(pong=pong,probe_reason=reason,ping_sent=False,connection_after=_state(stream))
        result['probe_elapsed_ms']=_ms(min(budget,max(0.0,clock()-started))*1000)
        return result
    try:
        if not callable(getattr(stream,'connection_state',None)):return finish('UNSUPPORTED','STREAM_PROBE_UNSUPPORTED')
        result['connection_before']=_state(stream)
        if cancelled():return finish('CANCELLED','STOP_REQUESTED')
        if result['connection_before']!='OPEN':return finish('UNCONFIRMED','CONNECTION_NOT_OPEN')
        return finish('UNSUPPORTED','LIVE_PING_SEND_NOT_BOUNDED')
    except Exception:
        return finish('ERROR','PROBE_FAILED')


def counters(timing,*,stream_messages,ignored_non_strategy):
    report=timing.report()
    def bounded(v):return v if type(v) is int and 0<=v<=MAX_COUNTER else None
    equal=sum(m['equal_timestamp'] for m in report['markets'].values())
    discarded=report['regression_discard']['discarded_total']
    return {'stream_messages':bounded(stream_messages),'timing_received':bounded(report['received_total']),
            'timing_accepted':bounded(report['timing_accepted_total']),'equal_timestamp':bounded(min(MAX_COUNTER,equal)),
            'discarded':bounded(discarded),'ignored_non_strategy':bounded(ignored_non_strategy)}


def stream_idle_sample(timing):
    """The last fixed-allowlist TickTiming sample when the failure was STREAM_IDLE, else None."""
    samples=getattr(timing,'samples',None)
    if not samples:return None
    last=samples[-1]
    return last if isinstance(last,dict) and last.get('issue')=='STREAM_IDLE' else None


def diagnose(*,timing,stream,check,last_message_at,stream_messages,ignored_non_strategy,halt_confirmed,
             cancelled=lambda:False,budget=BUDGET_SECONDS,clock=time.monotonic):
    """Build the record after HALT. ``check`` is (path, monotonic_at_check, message_kind)."""
    sample=stream_idle_sample(timing)
    if sample is None or not isinstance(check,tuple) or len(check)!=3:return unavailable()
    path,at,kind=check
    record={'schema':SCHEMA,'status':'TRIGGERED','trigger_path':path if path in PATHS else 'OTHER',
        'message_kind':kind if kind in MESSAGE_KINDS else None,'idle_ms':_ms(sample.get('idle_ms')),
        'since_last_message_ms':_ms((at-last_message_at)*1000) if last_message_at is not None else None,
        'since_last_advance_ms':_ms((at-timing.last_progress)*1000),
        'advance_reference':'TICK_TIMING_LAST_PROGRESS_OR_GUARD_START',
        'counters':counters(timing,stream_messages=stream_messages,ignored_non_strategy=ignored_non_strategy),
        'counter_scope':'TICK_TIMING_GUARD_CUMULATIVE_AND_RUNTIME_LOOP',
        'probe_budget_ms':int(budget*1000),'interpretation':INTERPRETATION}
    if halt_confirmed is not True:
        record.update(connection_before=_state(stream),connection_after=_state(stream),pong='UNSUPPORTED',
                      ping_sent=False,probe_reason='ENGINE_HALT_UNCONFIRMED',probe_elapsed_ms=0.0,
                      order='ENGINE_HALT_UNCONFIRMED_PROBE_SKIPPED')
    else:
        record.update(probe(stream,cancelled=cancelled,budget=budget,clock=clock))
        record['order']='ENGINE_HALT_CONFIRMED_BEFORE_PROBE'
    return validate(record)


def validate(record):
    """Fail closed to a fixed shape; anything else becomes UNAVAILABLE rather than leaking data."""
    if type(record) is not dict or record.get('schema')!=SCHEMA:return unavailable()
    if record.get('status')=='NOT_TRIGGERED' and set(record)=={'schema','status'}:return dict(record)
    if record.get('status')=='UNAVAILABLE' and set(record)=={'schema','status','interpretation'}:return dict(record)
    if set(record)!=KEYS or record['status']!='TRIGGERED':return unavailable()
    ok=(record['trigger_path'] in PATHS and record['message_kind'] in MESSAGE_KINDS
        and record['connection_before'] in CONNECTION_STATES and record['connection_after'] in CONNECTION_STATES
        and record['pong'] in PONG_RESULTS and record['probe_reason'] in PROBE_REASONS
        and record['ping_sent'] is False and record['interpretation']==INTERPRETATION
        and record['order'] in ('ENGINE_HALT_CONFIRMED_BEFORE_PROBE','ENGINE_HALT_UNCONFIRMED_PROBE_SKIPPED')
        and record['advance_reference']=='TICK_TIMING_LAST_PROGRESS_OR_GUARD_START'
        and record['counter_scope']=='TICK_TIMING_GUARD_CUMULATIVE_AND_RUNTIME_LOOP'
        and type(record['probe_budget_ms']) is int and 0<=record['probe_budget_ms']<=int(BUDGET_SECONDS*1000)
        and all(record[k] is None or (type(record[k]) is float and math.isfinite(record[k]))
                for k in ('idle_ms','since_last_message_ms','since_last_advance_ms','probe_elapsed_ms'))
        and type(record['counters']) is dict and set(record['counters'])==set(COUNTERS)
        and all(v is None or (type(v) is int and 0<=v<=MAX_COUNTER) for v in record['counters'].values()))
    if not ok or len(json.dumps(record,allow_nan=False))>MAX_RECORD_BYTES:return unavailable()
    return json.loads(json.dumps(record))
