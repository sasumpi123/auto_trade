"""Explicit bounded public WebSocket timing ONLY. No ledger, accounts or strategy."""
import json
import logging
import os
import signal
import time

from ops.paper_recovery_diagnostic import SafeParser
from services.public_market import MARKET, positive, PublicDataError, PUBLIC_ERROR_CODES
from services.tick_timing import TickTiming, TickTimingError

MAX_MESSAGES=100_000
MAX_OUTPUT_BYTES=131_072


class DiagnosticLimit(ValueError):pass


def monitor(tickers,seconds,*,stream_factory,stopped,wall=time.time,monotonic=time.monotonic):
    start=monotonic();start_wall=wall();deadline=start+seconds;wall_deadline=start_wall+seconds
    stream=None;guard=None;cleanup_error=None;validated=0;status='HALTED';reason='PUBLIC_DIAGNOSTIC_FAILED'
    def stop():return stopped() or monotonic()>=deadline or wall()>=wall_deadline
    try:
        stream=stream_factory(tickers);stream.connect()
        guard=TickTiming(tickers,wall(),monotonic(),clock_origin=(start_wall,start))
        while not stop():
            try:data=stream.get()
            except TimeoutError:
                if stop():break
                guard.idle(wall(),monotonic());continue
            if stop():break
            guard.observe(data,wall(),monotonic())
            positive(data.get('trade_price'))  # No price is logged or passed to a strategy.
            validated+=1
            if guard.received>=MAX_MESSAGES:raise DiagnosticLimit()
        status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
    except DiagnosticLimit:reason='DIAGNOSTIC_MESSAGE_LIMIT'
    except TickTimingError as exc:reason=str(exc)  # Fixed internal codes only.
    except PublicDataError as exc:
        reason=str(exc) if str(exc) in PUBLIC_ERROR_CODES else 'PUBLIC_DATA_UNVERIFIED'
    except Exception:reason='PUBLIC_STREAM_OR_DIAGNOSTIC_UNVERIFIED'
    finally:
        if stream is not None:
            try:stream.close()
            except Exception:
                cleanup_error='PUBLIC_STREAM_CLOSE_UNCONFIRMED'
                if status!='HALTED':status='HALTED';reason=cleanup_error
    return {'status':status,'reason':reason,'cleanup_error':cleanup_error,'mode':'PUBLIC_WEBSOCKET_DIAGNOSTIC_NO_TRADING',
            'tick_timing':guard.report() if guard else None,'diagnostic_has_valid_ticks':validated>0,'valid_public_ticks':validated,
            'ledger_reads':0,'ledger_writes':0,'accounts_created':0,'paper_orders':0,'real_orders':0,
            'strategy_calls':0,'rest_requests':0,'reconnections':0,'automatic_restart':False,
            'execution_authorized':False,'retained_log_files':0,'max_messages':MAX_MESSAGES}


def _diagnostic_worker(_state,seconds,_top,tickers,stop,pipe):
    logging.disable(logging.CRITICAL)
    if os.name=='posix':
        signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGHUP,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,lambda *_:stop.set())
    try:
        from services.market_stream import MarketStream
        result=monitor(tickers,seconds,stream_factory=lambda codes:MarketStream(codes,timeout=1),stopped=stop.is_set)
        pipe.send(result)
    except BaseException:
        try:pipe.send({'status':'HALTED','reason':'PUBLIC_DIAGNOSTIC_WORKER_FAILED','ledger_reads':0,'ledger_writes':0})
        except Exception:pass
    finally:pipe.close()


def main(argv=None):
    parser=SafeParser(description=__doc__)
    parser.add_argument('--connect-public',action='store_true')
    parser.add_argument('--seconds',type=int)
    parser.add_argument('--tickers',nargs='+')
    args=parser.parse_args(argv)
    if not args.connect_public:
        if args.seconds is not None or args.tickers is not None:parser.error('explicit connection required')
        result={'status':'DISABLED','mode':'PUBLIC_WEBSOCKET_DIAGNOSTIC_NO_TRADING','network_calls':0,
                'ledger_reads':0,'ledger_writes':0,'accounts_created':0,'execution_authorized':False}
    else:
        if args.seconds is None or not 1<=args.seconds<=600:parser.error('bounded duration required')
        if (not args.tickers or len(args.tickers)>30 or len(set(args.tickers))!=len(args.tickers)
                or any(not MARKET.fullmatch(t) for t in args.tickers)):parser.error('explicit valid markets required')
        from ops.paper_watch import supervise
        try:
            result=supervise(None,args.seconds,0,args.tickers,emit=lambda _:None,worker=_diagnostic_worker)
            result.pop('last_snapshot',None)  # One bounded final record, no redundant nested payload.
        except Exception:
            result={'status':'HALTED','reason':'PUBLIC_DIAGNOSTIC_SUPERVISOR_FAILED','execution_authorized':False}
    result.update(max_output_bytes=MAX_OUTPUT_BYTES,retained_log_files=0)
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,sort_keys=True)
    if len(raw.encode('utf-8'))>MAX_OUTPUT_BYTES:
        result={'status':'HALTED','reason':'DIAGNOSTIC_OUTPUT_LIMIT','retained_log_files':0}
        raw=json.dumps(result)
    print(raw)
    return 0 if result['status']=='STOPPED' else 2


if __name__=='__main__':raise SystemExit(main())
