"""P24 account-free public stream gap/recovery observer. KRW-BTC ticker on two independent connections.

No account path, ledger, engine, strategy, order, Slack or REST. Network only with --connect-public.
No reconnect or retry. Never sends on the observed connections (no diagnostic ping): Pong is UNMEASURED.
Records only counters, enums and local timings; never raw packets, prices or credentials.
"""
import argparse
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import time

SCHEMA='P24_STREAM_GAP_OBSERVATION_V1'
MARKETS=('KRW-BTC',)
CONNECTIONS=('C1','C2')
GAP_SECONDS=15            # Same boundary as the operating TickTiming STREAM_IDLE (>= 15 s).
RECOVERY_SECONDS=30
MIN_SECONDS=60
MAX_SECONDS=3300          # 55 minutes.
STARTUP_MARGIN_SECONDS=5  # Worker budget ends before the supervisor deadline.
GRACE_SECONDS=15          # Same cleanup grace as the PAPER supervisor; then kill => INCOMPLETE.
POLL_SECONDS=0.05
MAX_DRAIN=1000
MAX_MESSAGES=1_000_000
MAX_RECORD_BYTES=65_536
EVENTS='p24-events.jsonl'
RESULT='p24-result.json'
INTERPRETATION='OBSERVATION_ONLY_SIMULTANEOUS_OR_SINGLE_GAPS_DO_NOT_ESTABLISH_CAUSE'
PONG={'status':'UNMEASURED','reason':'NO_BOUNDED_PING_ON_OBSERVED_CONNECTIONS',
      'library_latency_recorded':False,'open_state_is_not_pong':True}
# Mirrors services.market_stream.MarketStream.connect (asserted by tests); applied values are read back.
OPERATING_SETTINGS={'url':'wss://api.upbit.com/websocket/v1','open_timeout':10,'close_timeout':2,
    'ping_interval':20,'ping_timeout':10,'subscription_type':'ticker','isOnlyRealtime':True,
    'operating_recv_timeout_seconds':1}
ROOT=Path(__file__).resolve().parents[1]


def _ms(value):
    if type(value) not in (int,float) or not math.isfinite(value):return None
    return round(max(-1e12,min(1e12,float(value))),3)


def library_version():
    try:
        import websockets
        v=getattr(websockets,'__version__',None)
        return v if isinstance(v,str) and len(v)<=32 else 'UNKNOWN'
    except Exception:return 'UNAVAILABLE'


class Connection:
    def __init__(self,name,stream):
        self.name=name;self.stream=stream;self.state='CONNECTING';self.end_reason=None
        self.connected_at=None;self.last_receive=None;self.last_advance=None;self.last_stamp=None
        self.received=0;self.advanced=0;self.equal=0;self.regressed=0;self.invalid=0
        self.realtime=0;self.snapshot=0;self.other_type=0
        self.max_receive_gap=0.0;self.max_advance_gap=0.0
        self.close_rcvd_code=None;self.close_sent_code=None;self.keepalive_timeout_close=False
        self.first_receive_after=None;self.first_advance_after=None;self.gap_during_window=False
        self.settings=None

    def ref_receive(self):return self.last_receive if self.last_receive is not None else self.connected_at
    def ref_advance(self):return self.last_advance if self.last_advance is not None else self.connected_at

    def record(self,data,now,capture_at):
        self.received+=1;self.last_receive=now
        if capture_at is not None and self.first_receive_after is None:self.first_receive_after=now-capture_at
        if not isinstance(data,dict) or data.get('type')!='ticker' or data.get('code') not in MARKETS:
            self.invalid+=1;return
        kind=data.get('stream_type')
        if kind=='REALTIME':self.realtime+=1
        elif kind=='SNAPSHOT':self.snapshot+=1
        else:self.other_type+=1
        stamp=data.get('timestamp')
        if type(stamp) is not int:self.invalid+=1;return
        if self.last_stamp is None or stamp>self.last_stamp:
            self.advanced+=1;self.last_advance=now;self.last_stamp=stamp
            if capture_at is not None and self.first_advance_after is None:self.first_advance_after=now-capture_at
        elif stamp==self.last_stamp:self.equal+=1
        else:self.regressed+=1  # Not adopted as the new reference.

    def end(self,reason,exc=None):
        self.state='ENDED';self.end_reason=reason
        if exc is not None:
            for side,attr in (('rcvd','close_rcvd_code'),('sent','close_sent_code')):
                code=getattr(getattr(exc,side,None),'code',None)
                setattr(self,attr,code if type(code) is int else None)
            sent=getattr(exc,'sent',None)
            self.keepalive_timeout_close=bool(getattr(sent,'code',None)==1011 and getattr(sent,'reason',None)=='keepalive ping timeout')

    def connection_state(self):
        reader=getattr(self.stream,'connection_state',None)
        try:value=reader() if callable(reader) else 'UNKNOWN'
        except Exception:value='UNKNOWN'
        return value if value in ('OPEN','CLOSING','CLOSED','UNKNOWN') else 'UNKNOWN'

    def gaps(self,now,gap):
        r=self.ref_receive();a=self.ref_advance()
        since_r=now-r if r is not None else None;since_a=now-a if a is not None else None
        no_r=since_r is not None and since_r>=gap;no_a=since_a is not None and since_a>=gap
        kind='NO_RECEIVE' if no_r else 'NO_ADVANCE_WITH_RECEIVE' if no_a else 'NONE'
        return since_r,since_a,kind

    def view(self,now,gap):
        since_r,since_a,kind=self.gaps(now,gap)
        return {'state':self.state,'end_reason':self.end_reason,'connection_state':self.connection_state(),
            'gap_kind':kind if self.state=='RUNNING' else 'NOT_RUNNING',
            'since_last_receive_ms':_ms(since_r*1000) if since_r is not None else None,
            'since_last_advance_ms':_ms(since_a*1000) if since_a is not None else None,
            'received':self.received,'advanced':self.advanced,'equal_timestamp':self.equal,
            'regressed':self.regressed,'invalid':self.invalid,'realtime':self.realtime,'snapshot':self.snapshot,
            'other_stream_type':self.other_type,'max_receive_gap_ms':_ms(self.max_receive_gap*1000),
            'max_advance_gap_ms':_ms(self.max_advance_gap*1000),'close_rcvd_code':self.close_rcvd_code,
            'close_sent_code':self.close_sent_code,'keepalive_timeout_close':self.keepalive_timeout_close}


def applied_settings(stream):
    sock=getattr(stream,'socket',None)
    def value(name):
        v=getattr(sock,name,None)
        return v if type(v) in (int,float) or v is None else 'UNKNOWN'
    return {'ping_interval':value('ping_interval'),'ping_timeout':value('ping_timeout'),'close_timeout':value('close_timeout')}


def observe(budget,*,stream_factory,stopped=lambda:False,monotonic=time.monotonic,wall=time.time,sleep=time.sleep,
            on_capture=lambda record:None,gap=GAP_SECONDS,recovery=RECOVERY_SECONDS,poll=POLL_SECONDS):
    """Bounded observation. Returns one allowlisted record; on_capture gets the first-gap snapshot at once."""
    from websockets.exceptions import ConnectionClosed
    start=monotonic();start_wall=wall();deadline=start+budget
    conns=[];capture=None;capture_at=None;recovery_end=None;reason=None;max_drift=0.0;capture_persisted=None
    def drift(now):return ((wall()-start_wall)-(now-start))*1000
    try:
        for name in CONNECTIONS:
            conn=Connection(name,None);conns.append(conn)
            try:
                conn.stream=stream_factory(list(MARKETS));conn.stream.connect()
            except Exception:
                conn.end('CONNECT_FAILED');reason='CONNECT_FAILED';break
            conn.connected_at=monotonic();conn.state='RUNNING';conn.settings=applied_settings(conn.stream)
        while reason is None:
            now=monotonic()
            if stopped():reason='STOP_REQUESTED';break
            if now>=deadline:
                reason='NOT_REPRODUCED_DEADLINE' if capture is None else 'RECOVERY_WINDOW_TRUNCATED';break
            for conn in conns:
                if conn.state!='RUNNING':continue
                for _ in range(MAX_DRAIN):
                    try:data=conn.stream.get()
                    except TimeoutError:break
                    except ConnectionClosed as exc:conn.end('CONNECTION_CLOSED',exc);break
                    except Exception:conn.end('READ_OR_DECODE_ERROR');break
                    conn.record(data,monotonic(),capture_at)
                    if conn.received>=MAX_MESSAGES:conn.end('MESSAGE_LIMIT');break
            now=monotonic();max_drift=max(max_drift,abs(drift(now)))
            if any(c.state=='ENDED' for c in conns) and (capture is None or all(c.state=='ENDED' for c in conns)):
                reason='CONNECTION_ENDED_BEFORE_GAP' if capture is None else 'ALL_CONNECTIONS_ENDED_DURING_RECOVERY';break
            for c in conns:
                if c.state=='RUNNING':
                    since_r,since_a,kind=c.gaps(now,gap)
                    c.max_receive_gap=max(c.max_receive_gap,since_r or 0.0);c.max_advance_gap=max(c.max_advance_gap,since_a or 0.0)
                    if capture is not None and kind!='NONE' and capture['connections'][c.name]['gap_kind']=='NONE':c.gap_during_window=True
            if capture is None and any(c.state=='RUNNING' and c.gaps(now,gap)[2]!='NONE' for c in conns):
                capture_at=now;recovery_end=min(now+recovery,deadline)
                capture={'schema':SCHEMA,'kind':'GAP_CAPTURED','at_elapsed_ms':_ms((now-start)*1000),
                    'wall_minus_monotonic_ms':_ms(drift(now)),'threshold_ms':int(gap*1000),
                    'connections':{c.name:c.view(now,gap) for c in conns}}
                try:on_capture(json.loads(json.dumps(capture)));capture_persisted=True  # Saved before recovery watch.
                except Exception:capture_persisted=False
            if capture is not None and now>=capture_at+recovery:reason='RECOVERY_WINDOW_COMPLETE';break
            sleep(poll)
    except Exception:
        reason=reason or 'OBSERVER_FAILED'
    finally:
        cleanup_error=None
        for c in conns:
            if c.stream is None:continue
            try:c.stream.close()
            except Exception:cleanup_error='STREAM_CLOSE_UNCONFIRMED'
    end=monotonic()
    complete_ok=reason in ('NOT_REPRODUCED_DEADLINE','RECOVERY_WINDOW_COMPLETE') and cleanup_error is None and \
        all(c.state=='RUNNING' for c in conns) and capture_persisted is not False
    status=('NOT_REPRODUCED' if reason=='NOT_REPRODUCED_DEADLINE' else 'REPRODUCED') if complete_ok else 'INCOMPLETE'
    def recovery_view(c):
        if capture is None:return {'classification':'NO_CAPTURE'}
        gapped=capture['connections'][c.name]['gap_kind'] not in ('NONE','NOT_RUNNING')
        if not gapped:cls='NOT_GAPPED_AT_CAPTURE'
        elif c.state=='ENDED':cls='ENDED_DURING_RECOVERY'
        elif c.first_advance_after is not None:cls='RECEIVE_AND_ADVANCE_RESUMED'
        elif c.first_receive_after is not None:cls='RECEIVE_ONLY_NO_ADVANCE'
        else:cls='NOT_RECOVERED_WITHIN_WINDOW'
        return {'classification':cls,'gap_during_window':c.gap_during_window,
            'first_receive_after_capture_ms':_ms(c.first_receive_after*1000) if c.first_receive_after is not None else None,
            'first_advance_after_capture_ms':_ms(c.first_advance_after*1000) if c.first_advance_after is not None else None}
    record={'schema':SCHEMA,'kind':'FINAL','status':status,'reason':reason,'cleanup_error':cleanup_error,
        'elapsed_ms':_ms((end-start)*1000),'budget_ms':_ms(budget*1000),'threshold_ms':int(gap*1000),
        'recovery_window_ms':int(recovery*1000),
        'recovery_window_observed_ms':_ms((min(end,recovery_end)-capture_at)*1000) if capture_at is not None else None,
        'capture':capture,'capture_persisted':capture_persisted,
        'connections':{c.name:{**c.view(end,gap),'recovery':recovery_view(c),'applied_settings':c.settings} for c in conns},
        'max_abs_wall_minus_monotonic_ms':_ms(max_drift),'pong':dict(PONG),'library':'websockets','library_version':library_version(),
        'operating_settings':dict(OPERATING_SETTINGS),'markets':list(MARKETS),
        'observer_poll':'recv(timeout=0) drain then sleep','poll_ms':int(poll*1000),
        'interpretation':INTERPRETATION,'reconnections':0,'automatic_retry':False,'ledger_reads':0,'ledger_writes':0,
        'accounts_created':0,'paper_orders':0,'real_orders':0,'strategy_calls':0,'rest_requests':0}
    return json.loads(json.dumps(record,allow_nan=False))


def _worker(seconds,stop,pipe):
    """Spawned child. Imports only the public stream adapter; never trading, ledger or strategy code."""
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    import logging;logging.disable(logging.CRITICAL)
    if os.name=='posix':
        signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGHUP,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,lambda *_:stop.set())
    try:
        from services.market_stream import MarketStream
        result=observe(seconds-STARTUP_MARGIN_SECONDS,stream_factory=lambda codes:MarketStream(codes,timeout=0),
                       stopped=stop.is_set,on_capture=lambda c:pipe.send(c))
        pipe.send(result)
    except BaseException:
        try:pipe.send({'schema':SCHEMA,'kind':'FINAL','status':'INCOMPLETE','reason':'WORKER_FAILED'})
        except Exception:pass
    finally:pipe.close()


def supervise(seconds,*,write,worker=_worker,grace=GRACE_SECONDS,clock=time.monotonic):
    """Hard process deadline: seconds + grace, then kill. Every child record is written as it arrives."""
    import multiprocessing
    ctx=multiprocessing.get_context('spawn')
    receiver,sender=ctx.Pipe(duplex=False);stop=ctx.Event()
    process=ctx.Process(target=worker,args=(seconds,stop,sender))
    interrupted=[False];handlers={}
    def request_stop(*_):interrupted[0]=True;stop.set()
    for sig in (signal.SIGINT,signal.SIGTERM)+((signal.SIGHUP,) if os.name=='posix' else ()):
        handlers[sig]=signal.signal(sig,request_stop)
    final=None;capture=None;forced=False;hard=None;write_ok=True;channel_open=True
    def take(message):
        nonlocal final,capture,write_ok
        if not isinstance(message,dict) or message.get('schema')!=SCHEMA:return
        if message.get('kind')=='GAP_CAPTURED':capture=message
        elif message.get('kind')=='FINAL':final=message
        try:write(message)
        except Exception:write_ok=False
    try:
        deadline=clock()+seconds
        process.start();sender.close()
        while process.is_alive():
            now=clock()
            if now>=deadline or interrupted[0]:
                stop.set()
                if hard is None:hard=now+grace
            if hard is not None and now>=hard:process.kill();forced=True;break
            try:
                if channel_open and receiver.poll(.1):take(receiver.recv())
                elif not channel_open:time.sleep(.05)
            except (EOFError,OSError):channel_open=False
        process.join(5)
        while channel_open:
            try:
                if not receiver.poll(0):break
                take(receiver.recv())
            except (EOFError,OSError):break
        if process.is_alive():process.kill();process.join(5);forced=True
    finally:
        if process.pid is not None and process.is_alive():process.kill();process.join(5)
        sender.close();receiver.close()
        for sig,old in handlers.items():signal.signal(sig,old)
    result=dict(final) if isinstance(final,dict) else {'schema':SCHEMA,'kind':'FINAL','status':'INCOMPLETE',
        'reason':'FORCED_TERMINATION' if forced else 'WORKER_RESULT_MISSING','capture':capture,'pong':dict(PONG)}
    if result.get('capture') is None and capture is not None:result['capture']=capture
    if forced:result.update(status='INCOMPLETE',reason='FORCED_TERMINATION')
    elif process.exitcode!=0:result.update(status='INCOMPLETE',reason='WORKER_EXIT_UNCONFIRMED')
    if interrupted[0] and result.get('status')!='INCOMPLETE':result.update(status='INCOMPLETE',reason='STOP_REQUESTED')
    if not write_ok:result.update(status='INCOMPLETE',reason='PRIVATE_OUTPUT_UNVERIFIED')
    result.update(forced_termination=forced,process_exit_code=process.exitcode,user_or_supervisor_stop=interrupted[0],
                  supervisor_grace_seconds=grace,automatic_restart=False)
    return result


def private_directory(value):
    """Existing, empty, private, user-owned directory outside the repository. Never creates or chmods."""
    p=Path(value).expanduser().absolute()
    if any(q.is_symlink() for q in (p,*p.parents)):raise ValueError('OUTPUT_SYMLINK_FORBIDDEN')
    p=p.resolve()
    if p.is_relative_to(ROOT) or ROOT.is_relative_to(p):raise ValueError('OUTPUT_IN_PROJECT_FORBIDDEN')
    info=p.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode&0o077 or (os.name=='posix' and info.st_uid!=os.getuid()):
        raise ValueError('OUTPUT_NOT_PRIVATE_DIRECTORY')
    if any(p.iterdir()):raise ValueError('OUTPUT_NOT_EMPTY')
    return p


class Writer:
    def __init__(self,directory):
        self.directory=directory
        fd=os.open(directory/EVENTS,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_APPEND|getattr(os,'O_NOFOLLOW',0),0o600)
        self.handle=os.fdopen(fd,'w',encoding='utf-8')
    def __call__(self,message):
        raw=json.dumps(message,ensure_ascii=False,allow_nan=False,sort_keys=True)
        if len(raw.encode())>MAX_RECORD_BYTES:raw=json.dumps({'schema':SCHEMA,'kind':message.get('kind'),'status':'RECORD_TOO_LARGE'})
        self.handle.write(raw+'\n');self.handle.flush();os.fsync(self.handle.fileno())
    def final(self,result):
        fd=os.open(self.directory/RESULT,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as h:
            h.write(json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True)+'\n');h.flush();os.fsync(h.fileno())
    def close(self):self.handle.close()


def summarize(result):
    """At most 8 lines; enums and numbers only."""
    def g(d,*keys):
        for k in keys:
            d=d.get(k) if isinstance(d,dict) else None
        if d is None or type(d) in (bool,int,float):return d
        return ''.join(ch for ch in str(d) if ch.isalnum() or ch in '_.-')[:48]
    cap=result.get('capture') if isinstance(result.get('capture'),dict) else {}
    lines=[f"1 P24 status={g(result,'status')} reason={g(result,'reason')} elapsed_ms={g(result,'elapsed_ms')} forced={g(result,'forced_termination')} cleanup={g(result,'cleanup_error')}",
           f"2 capture at_ms={g(cap,'at_elapsed_ms')} drift_ms={g(cap,'wall_minus_monotonic_ms')} window_ms={g(result,'recovery_window_observed_ms')}/{g(result,'recovery_window_ms')} persisted={g(result,'capture_persisted')}"]
    for i,name in enumerate(CONNECTIONS):
        c=cap['connections'].get(name,{}) if isinstance(cap.get('connections'),dict) else {}
        f=(result.get('connections') or {}).get(name,{}) if isinstance(result.get('connections'),dict) else {}
        lines.append(f"{3+i} {name} gap={g(c,'gap_kind')} recv_ms={g(c,'since_last_receive_ms')} adv_ms={g(c,'since_last_advance_ms')} "
                     f"conn={g(c,'connection_state')}->{g(f,'connection_state')} recovery={g(f,'recovery','classification')} "
                     f"first_recv/adv_ms={g(f,'recovery','first_receive_after_capture_ms')}/{g(f,'recovery','first_advance_after_capture_ms')}")
    def counts(name):
        f=(result.get('connections') or {}).get(name,{}) if isinstance(result.get('connections'),dict) else {}
        return f"{g(f,'received')}/{g(f,'advanced')}/{g(f,'equal_timestamp')}/{g(f,'regressed')}/{g(f,'invalid')} end={g(f,'end_reason')} close={g(f,'close_rcvd_code')}/{g(f,'close_sent_code')} ka_timeout={g(f,'keepalive_timeout_close')}"
    lines.append(f"5 recv/adv/equal/regress/invalid C1={counts('C1')}")
    lines.append(f"6 recv/adv/equal/regress/invalid C2={counts('C2')}")
    s=(result.get('connections') or {}).get('C1',{}).get('applied_settings') if isinstance(result.get('connections'),dict) else None
    lines.append(f"7 websockets={g(result,'library_version')} ping={g(s or {},'ping_interval')}/{g(s or {},'ping_timeout')} close={g(s or {},'close_timeout')} pong={g(result,'pong','status')} max_drift_ms={g(result,'max_abs_wall_minus_monotonic_ms')}")
    lines.append("8 observation only: cause unconfirmed; no account/trading/reconnect; details in private dir")
    return lines[:8]


class _Parser(argparse.ArgumentParser):
    def error(self,message):self.exit(2,'Invalid P24 observer arguments; no action performed.\n')


def main(argv=None):
    parser=_Parser(description=__doc__)
    parser.add_argument('--connect-public',action='store_true')
    parser.add_argument('--seconds',type=int)
    parser.add_argument('--output-dir')
    args=parser.parse_args(argv)
    if not args.connect_public:
        if args.seconds is not None or args.output_dir is not None:parser.error('explicit connection required')
        print(json.dumps({'status':'DISABLED','schema':SCHEMA,'network_calls':0,'ledger_reads':0,'accounts_created':0}))
        return 2
    if args.seconds is None or not MIN_SECONDS<=args.seconds<=MAX_SECONDS or args.output_dir is None:
        parser.error('bounded seconds and private output directory required')
    try:directory=private_directory(args.output_dir)
    except (OSError,ValueError):
        print('1 P24 status=BLOCKED reason=OUTPUT_DIRECTORY_UNVERIFIED network_calls=0');return 2
    writer=Writer(directory)
    try:
        result=supervise(args.seconds,write=writer)
        try:writer.final(result)
        except Exception:result.update(status='INCOMPLETE',reason='PRIVATE_OUTPUT_UNVERIFIED')
    finally:writer.close()
    for line in summarize(result):print(line)
    return 0 if result.get('status') in ('REPRODUCED','NOT_REPRODUCED') else 2


if __name__=='__main__':raise SystemExit(main())
