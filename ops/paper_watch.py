"""Offline by default; explicit bounded PAPER/public-data session, no Slack or launchd."""
import argparse
import json
import logging
import multiprocessing
import os
from pathlib import Path
import signal
import sys
import time

from ops.local_preflight import ROOT
from ops.paper_session import offline_report


def _worker(state,seconds,top,tickers,stop,pipe):
    # Isolate legacy imports/loggers and never load .env. No raw exception output.
    os.environ['PYTHON_DOTENV_DISABLED']='1'
    logging.disable(logging.CRITICAL)
    if os.name=='posix':
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,lambda *_:stop.set())
    try:
        from services.public_market import PublicMarketClient
        from services.market_stream import MarketStream
        from ops.paper_session import run_session
        client=PublicMarketClient(deadline=time.monotonic()+seconds,stopped=stop.is_set)
        run_session(state,seconds=seconds,top=top,tickers=tickers,stopped=stop.is_set,
                    emit=pipe.send,client=client,stream_factory=lambda codes:MarketStream(codes,timeout=1))
    except BaseException:
        try:pipe.send({'status':'HALTED','reason':'WORKER_FAILED','post_validation':'UNVERIFIED'})
        except Exception:pass
    finally:pipe.close()


def supervise(state,seconds,top,tickers,*,emit,worker=_worker,grace=15):
    """Hard process deadline incl. discovery, candle reads and shutdown. No auto-restart."""
    ctx=multiprocessing.get_context('spawn')
    receiver,sender=ctx.Pipe(duplex=False);stop=ctx.Event()
    process=ctx.Process(target=worker,args=(str(state),seconds,top,tickers,stop,sender))
    handlers={};interrupted=[False]
    def request_stop(*_):interrupted[0]=True;stop.set()
    signals=(signal.SIGINT,signal.SIGTERM)+( (signal.SIGHUP,) if os.name=='posix' else () )
    for sig in signals:
        handlers[sig]=signal.signal(sig,request_stop)
    deadline=time.monotonic()+seconds;wall_deadline=time.time()+seconds
    stop_deadline=None;last=None;forced=False
    try:
        process.start();sender.close()
        while process.is_alive():
            now=time.monotonic()
            if now>=deadline or time.time()>=wall_deadline or interrupted[0]:
                stop.set()
                if stop_deadline is None:stop_deadline=now+grace
            if stop_deadline is not None and now>=stop_deadline:
                process.kill();forced=True;break
            if receiver.poll(.1):
                try:last=receiver.recv();emit(last)
                except EOFError:break
        process.join(5)
        while receiver.poll():
            try:last=receiver.recv();emit(last)
            except EOFError:break
        if process.is_alive():process.kill();process.join(5);forced=True
        if forced or process.exitcode!=0 or last is None or last.get('status')!='STOPPED':
            result={'status':'HALTED','reason':'FORCED_STOP_OR_UNCONFIRMED_SESSION',
                'automatic_restart':False,'recovery_action':'READ_ONLY_RECONCILIATION_REQUIRED',
                'execution_authorized':False,'last_snapshot':last}
        else:result={**last,'automatic_restart':False}
        # After owner release, recheck disk. A clean snapshot never overrides forced-stop uncertainty.
        result['after_close']=offline_report(state)
        if result['after_close'].get('summary') is None:
            result['status']='HALTED'
        emit(result)
        return result
    finally:
        if process.pid is not None and process.is_alive():process.kill();process.join(5)
        sender.close();receiver.close()
        for sig,old in handlers.items():signal.signal(sig,old)


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self,message):self.exit(2,'Invalid PAPER observer arguments; no action performed.\n')
    parser=SafeParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true')
    mode.add_argument('--connect-public',action='store_true')
    parser.add_argument('--state',type=Path,default=ROOT/'runtime/paper-state.json')
    parser.add_argument('--seconds',type=int)
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--top',type=int)
    selection.add_argument('--tickers',nargs='+')
    args=parser.parse_args(argv)
    def emit(report):print(json.dumps(report,ensure_ascii=False,sort_keys=True),flush=True)
    if not args.connect_public:
        if args.seconds is not None or args.top is not None or args.tickers is not None:
            parser.error('network options require explicit connection')
        emit(offline_report(args.state));return 2
    if args.seconds is None or not 1<=args.seconds<=3600:
        parser.error('explicit seconds from 1 to 3600 required')
    if args.top is not None and not 1<=args.top<=100:parser.error('invalid top')
    from services.public_market import MARKET
    if args.tickers is not None and (not args.tickers or len(args.tickers)>100
            or len(set(args.tickers))!=len(args.tickers) or any(not MARKET.fullmatch(t) for t in args.tickers)):
        parser.error('invalid markets')
    # This process never imports config or creates runtime/locks. Refuse uncertain startup.
    report=offline_report(args.state)
    checks=report['preflight']['checks']
    outbox=checks.get('outbox','')
    if (os.name!='posix' or sys.version_info[:2]!=(3,12) or report['summary'] is None
            or checks.get('paper_slack_automation_off')!='READY'
            or outbox not in ('READY_METADATA_ONLY','PENDING_FILE_MISSING_NO_INITIALIZATION')):
        emit({'status':'BLOCKED','reason':'PAPER_STARTUP_UNVERIFIED','diagnostic':report});return 2
    try:result=supervise(args.state,args.seconds,args.top or 10,args.tickers,emit=emit)
    except Exception:
        emit({'status':'HALTED','reason':'SUPERVISOR_FAILED','automatic_restart':False});return 2
    return 0 if result['status']=='STOPPED' and result.get('post_validation')=='VERIFIED_UNDER_OWNER_LOCK' else 2


if __name__=='__main__':raise SystemExit(main())
