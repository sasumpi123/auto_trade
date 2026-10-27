"""One explicit PAPER CLI: init, check, run, status, stop, report. Never live trading."""
from datetime import datetime,timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time

from ops import paper_experiment as exp
from ops.local_preflight import _private,_json,cold_lock,ROOT
from ops.paper_recovery_diagnostic import SafeParser,snapshot

CONTROL='control.sock'  # Volatile local IPC, not a journal, receipt or restart authority.
LIMIT=131072


def view(event):
    keys=('status','reason','account_scope','initial_cash','cash','positions','orders','pending_orders','halted',
          'session_ticks','historical_ticks','markets','cumulative_buys','cumulative_sells','fees',
          'net_realized_pnl','unrealized_pnl','net_return_percent','equity','valuation_reason',
          'engine_started','post_validation','session_finalization','supervisor_reason','automatic_restart',
          'forced_termination','public_rest_requests','public_rest_budget','selection','tick_timing')
    return {k:event[k] for k in keys if k in event}


class ControlServer:
    """Same-user private Unix socket; no PID signaling or second ledger reader."""
    def __init__(self,directory):
        self.path=directory/CONTROL;self.socket=None;self.identity=None
        self.latest={'status':'PREPARING','engine_started':False};self.updated=time.monotonic()
    def __enter__(self):
        exp.require(os.name=='posix','LOCAL_CONTROL_PLATFORM_UNVERIFIED')
        exp.require(not os.path.lexists(self.path),'CONTROL_PATH_EXISTS_NO_AUTOMATIC_CLEANUP')
        self.socket=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
        try:
            self.socket.bind(str(self.path));info=self.path.lstat();self.identity=(info.st_dev,info.st_ino)
            self.path.chmod(0o600);self.socket.listen(4);self.socket.setblocking(False)
        except Exception:
            self.__exit__(None,None,None);raise
        return self
    def publish(self,event):self.latest=view(event);self.updated=time.monotonic()
    def poll(self):
        try:conn,_=self.socket.accept()
        except BlockingIOError:return False
        stop=False
        with conn:
            conn.settimeout(.2)
            try:
                request=b'';deadline=time.monotonic()+.2
                while not request.endswith(b'\n') and len(request)<65:
                    remaining=deadline-time.monotonic()
                    if remaining<=0:raise TimeoutError()
                    conn.settimeout(remaining)
                    part=conn.recv(65-len(request))
                    if not part:break
                    request+=part
                if request==b'STOP\n':
                    stop=True;result={'status':'STOP_REQUESTED_NOT_YET_STOPPED','automatic_restart':False}
                elif request==b'STATUS\n':
                    age=max(0,time.monotonic()-self.updated)
                    result={'status':'RUNNING_PROCESS_OBSERVATION','snapshot_age_seconds':round(age,3),
                            'snapshot_fresh':age<=10,'summary':self.latest,
                            'valuation_scope':'LAST_WORKER_OBSERVATION_NOT_NEW_MARKET_QUERY'}
                else:result={'status':'BLOCKED','reason':'INVALID_LOCAL_REQUEST'}
                conn.sendall(json.dumps(result,ensure_ascii=False,allow_nan=False).encode()+b'\n')
            except (OSError,ValueError):pass
        return stop  # An accepted stop remains accepted even if its response is lost.
    def __exit__(self,*_):
        if self.socket is not None:self.socket.close()
        if self.identity is not None:
            info=self.path.lstat()
            exp.require((info.st_dev,info.st_ino)==self.identity and stat.S_ISSOCK(info.st_mode),'CONTROL_OWNER_CHANGED')
            self.path.unlink()  # Only this process's volatile socket, never an account/lock/receipt.


def query(directory,command):
    p=exp.directory(directory);path=p/CONTROL
    info=path.lstat()
    exp.require(stat.S_ISSOCK(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077,'LOCAL_CONTROL_UNVERIFIED')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
        channel.settimeout(1);channel.connect(str(path));channel.sendall((command+'\n').encode())
        raw=b''
        while not raw.endswith(b'\n') and len(raw)<=LIMIT:
            part=channel.recv(min(8192,LIMIT+1-len(raw)))
            if not part:break
            raw+=part
        exp.require(len(raw)<=LIMIT and raw.endswith(b'\n'),'CONTROL_RESPONSE_UNVERIFIED')
        return _json(raw.decode())


def initialize(directory):
    """Explicit init may create one new private parent; never chmod/reset existing paths."""
    if os.name!='posix':return exp.create(directory)  # Existing fail-closed Windows contract.
    exp.require(sys.version_info[:2]==(3,12),'PYTHON312_REQUIRED')
    exp.require(exp.initial_cash()==1_000_000,'PAPER_POLICY_REQUIRED')
    p=Path(directory).expanduser().absolute()
    exp.require(not any(q.is_symlink() for q in (p,*p.parents)),'SYMLINK_PATH_FORBIDDEN')
    p=p.resolve()
    exp.require(not p.is_relative_to(ROOT) and not ROOT.is_relative_to(p),'PROJECT_PATH_FORBIDDEN')
    exp.require(not any(q.name.lower() in ('.ssh','.aws','.azure','.codex','.git','keychains','runtime') for q in (p,*p.parents)),
                'PRIVATE_AUTH_OR_OPERATING_PATH_FORBIDDEN')
    exp.require(not any((q/'.git').is_file() or (q/'.git/HEAD').is_file() for q in p.parents),'GIT_CHECKOUT_PATH_FORBIDDEN')
    exp.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',p.name),'INVALID_EXPERIMENT_NAME')
    if not p.parent.exists():p.parent.mkdir(mode=0o700)  # Immediate existing ancestor required; no recursive setup.
    return exp.create(p)


def read_report(directory,*,limit=20):
    exp.require(type(limit) is int and 1<=limit<=1000,'REPORT_LIMIT_INVALID')
    p=exp.directory(directory);state=p/exp.STATE
    with cold_lock(state) as ownership:
        exp.require(ownership=='READY_IDLE_AT_SNAPSHOT','OWNER_ACTIVE_OR_UNVERIFIED')
        saved,accounting,digest=snapshot(state,1_000_000)
        exp.validate_identity(saved,state)
        if saved.get('halt_reason'):
            phase='HALTED_NO_RESTART'
        else:phase=exp.resumable(saved,state)
        # Pure P9 replay allows HALTED accounting, never engine.open or reset.
        q=sum((v['quantity'] for v in accounting['positions'].values()),Decimal(0))
        flat=q==0
        summary={'mode':'PAPER','cash':str(accounting['cash']),'initial_cash':'1000000',
            'positions':{t:{'quantity':str(v['quantity']),'average_price':str(v['engine_avg']),
                            'fee_inclusive_remaining_cost':str(v['cost'])} for t,v in accounting['positions'].items() if v['quantity']>0},
            'orders':len(saved['orders']),'pending_orders':0,'halted':bool(saved.get('halt_reason')),
            'cumulative_buys':accounting['buy_count'],'cumulative_sells':accounting['sell_count'],
            'fees':str(accounting['fees']),'net_realized_pnl':str(accounting['net_profit']),
            'unrealized_pnl':'0' if flat else 'N/A','equity':str(accounting['cash']) if flat else 'N/A',
            'net_return_percent':str((accounting['cash']/1_000_000-1)*100) if flat else 'N/A',
            'valuation_reason':'NO_OPEN_POSITION' if flat else 'OFFLINE_NO_FRESH_MARKS',
            'fee_policy':'CONFIRMED_RECEIPTS;CURRENT_PAPER_GENERATOR_ZERO_FEE'}
        from services.notification_metrics import reason_group
        orders=[o for o in saved['orders'].values() if o['status']=='settled' and Decimal(o['executed_volume'])>0]
        trades=[{'sequence':r['sequence'],'market':r['ticker'],'side':r['side'],
            'executed_at_utc':datetime.fromtimestamp(o['settled_at'],timezone.utc).isoformat(),
            'quantity':str(r['quantity']),'price':o['average_fill_price'],'funds':str(r['funds']),
            'fee':str(r['fee']),'net_realized_pnl':str(r['net_realized']) if r['net_realized'] is not None else None,
            'reason_category':reason_group(o)} for r,o in zip(accounting['rows'][-limit:],orders[-limit:])]
        result={'status':'READ_ONLY_ACCOUNT_REPORT','session_state':phase,'ledger_sha256':digest,
            'summary':summary,'trades':trades,'trade_history_total':len(accounting['rows']),
            'trade_history_shown':len(trades),'account_scope':exp.SCOPE,'network_calls':0,'state_writes':0,
            'execution_authorized':False,'automatic_restart':False}
        if (p/'session.json').exists():
            m=exp.marker(p)
            # History is not a fresh mark nor restart authority, and must match this ledger.
            if m.get('end_sha256')==digest and m.get('phase')=='STOPPED_VERIFIED' and isinstance(m.get('session_summary'),dict):
                history=m['session_summary'];markets={}
                from services.public_market import MARKET,positive
                exp.require(isinstance(history.get('markets'),dict) and len(history['markets'])<=exp.MAX_CANDIDATES,'HISTORY_UNVERIFIED')
                for ticker,row in history['markets'].items():
                    exp.require(isinstance(ticker,str) and MARKET.fullmatch(ticker) and isinstance(row,dict),'HISTORY_UNVERIFIED')
                    n=row.get('session_ticks');exp.require(type(n) is int and 0<=n<=2_147_483_647,'HISTORY_UNVERIFIED')
                    markets[ticker]={'session_ticks':n,'last_observed_price':str(positive(row['last_price'])) if row.get('last_price') is not None else None}
                total=history.get('session_ticks')
                exp.require(type(total) is int and total==sum(r['session_ticks'] for r in markets.values()),'HISTORY_UNVERIFIED')
                recorded=datetime.fromisoformat(m['summary_recorded_at_utc']);exp.require(recorded.tzinfo is not None,'HISTORY_UNVERIFIED')
                result['last_session']={'session_ticks':total,'markets':markets,'recorded_at_utc':recorded.isoformat(),
                    'scope':'HISTORICAL_SESSION_COUNTS_NOT_CURRENT_PRICES'}
        return result


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if args and args[0]=='run':
        if args[1:] in (['--help'],['-h']):
            print('Usage: python -m ops.paper_bot run --directory DIR --connect-public --seconds 1..3600')
            print('Markets: --tickers KRW-BTC ... | --top N | --eligible-top N --candidate-limit M')
            print('Optional existing experiment flags: --observe-realtime-seconds N --select-realtime-active --discard-small-timestamp-regressions')
            print('Foreground only. Ctrl+C or paper_bot stop requests cooperative shutdown. No implicit init or restart.')
            return 0
        if '--connect-public' not in args[1:]:
            print(json.dumps({'status':'BLOCKED','reason':'EXPLICIT_PUBLIC_RUN_REQUIRED','network_calls':0}));return 2
        # Delegate argument validation, explicit network opt-in, ownership and supervision.
        def emit(event):
            print(json.dumps({'account_scope':exp.SCOPE,'fee_policy':'EXISTING_PAPER_ZERO_FEE',**view(event)},ensure_ascii=False),flush=True)
        return exp.main(args[1:],emit=emit,control_factory=ControlServer,record_summary=True)
    parser=SafeParser(description=__doc__)
    parser.add_argument('command',choices=('init','check','run','status','stop','report'))
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--limit',type=int,default=20)
    options=parser.parse_args(args)
    if options.command=='run':parser.error('run must be the first subcommand; explicit connection required')
    try:
        if options.command=='init':r=initialize(options.directory)
        elif options.command=='check':
            exp.require(not os.path.lexists(exp.directory(options.directory)/CONTROL),'CONTROL_PRESENT_NO_RUN_AUTHORITY')
            r=exp.inspect_experiment(options.directory)
            if r['status']=='READ_ONLY_NO_EXECUTION_AUTHORITY':
                r={'status':'READY_FOR_EXPLICIT_RUN','session_state':r['session_state'],
                    'summary':r['diagnostic']['summary'],'execution_authorized':False,'network_calls':0}
        elif options.command=='stop':r=query(options.directory,'STOP')
        elif options.command=='status' and os.path.lexists(exp.directory(options.directory)/CONTROL):r=query(options.directory,'STATUS')
        else:r=read_report(options.directory,limit=options.limit)
        print(json.dumps(r,ensure_ascii=False,allow_nan=False),flush=True)
        return 0 if r['status'] in ('CREATED_NEW_INDEPENDENT_EXPERIMENT','READY_FOR_EXPLICIT_RUN',
            'RUNNING_PROCESS_OBSERVATION','READ_ONLY_ACCOUNT_REPORT','STOP_REQUESTED_NOT_YET_STOPPED') else 2
    except Exception:
        print(json.dumps({'status':'BLOCKED','reason':'ACCOUNT_OR_CONTROL_UNVERIFIED_NO_RESET',
            'network_calls':0,'automatic_restart':False,'execution_authorized':False}));return 2


if __name__=='__main__':raise SystemExit(main())
