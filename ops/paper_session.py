"""PAPER-only session. Existing engine/strategy/storage, public injected I/O only."""
from decimal import Decimal
import os
from pathlib import Path
import time

from ops.local_preflight import _private, _json, cold_lock, inspect
from trading.execution import ExecutionEngine
from trading.state_store import StateStore
from services.notification_metrics import report_metrics
from services.public_market import MARKET, positive, PublicDataError, PUBLIC_ERROR_CODES
from utils.process_lock import lock_handle


def validate_state(saved, path, initial_cash):
    engine=ExecutionEngine(path,initial_cash,real_trading=False)
    engine._validate(saved)
    markets=set(saved['positions']) | set(saved['signals']) | {o['ticker'] for o in saved['orders'].values()}
    if any(not MARKET.fullmatch(t) for t in markets):
        raise ValueError('PAPER_INVALID_MARKET_IN_LEDGER')
    if saved.get('halt_reason') or any(o['status']!='settled' for o in saved['orders'].values()):
        raise ValueError('PAPER_RECOVERY_REQUIRED')
    metrics=report_metrics(saved,initial_cash,{},now=time.monotonic(),verified=True)
    if type(metrics.get('거래 횟수')) is not int:
        raise ValueError('PAPER_LEDGER_MISMATCH')
    return saved


class ExistingPaperStore(StateStore):
    """Same .lock and atomic save format; validates BEFORE engine.open can clear halt.

    Never creates a database, state file or lock, even for a missing original path.
    """
    def __init__(self,path,initial_cash):
        # Check original alias before StateStore.resolve can hide a symlink.
        _private(path,{'.json'})
        super().__init__(path);self.initial_cash=initial_cash

    def acquire(self):
        if self._lock is not None:raise ValueError('PAPER_ALREADY_OPEN')
        p=_private(self.path,{'.json'})
        lock=Path(str(p)+'.lock')
        if lock.is_symlink() or not lock.is_file():raise ValueError('PAPER_LOCK_UNVERIFIED')
        fd=os.open(lock,os.O_RDWR|getattr(os,'O_NOFOLLOW',0))
        handle=os.fdopen(fd,'r+b')
        try:
            import stat
            info=os.fstat(fd);actual=lock.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or (info.st_dev,info.st_ino)!=(actual.st_dev,actual.st_ino):
                raise ValueError('PAPER_LOCK_UNVERIFIED')
            if os.name=='posix' and info.st_uid!=os.getuid():raise ValueError('PAPER_LOCK_UNVERIFIED')
            lock_handle(handle,initialize=False)
            validate_state(_json(p.read_text(encoding='utf-8')),p,self.initial_cash)
        except Exception:
            handle.close();raise
        self._lock=handle

    def load(self):return _json(self.path.read_text(encoding='utf-8'))


def state_summary(saved, counts=None, last=None):
    counts={} if counts is None else counts
    last={} if last is None else last
    positions={}
    for ticker,pos in saved['positions'].items():
        positions[ticker]={'quantity':pos['quantity'],'average_price':pos['avg_price'],
                          'averaging_used':pos['averaging_down_used']}
    markets=set(counts)|set(positions)
    return {'mode':'PAPER','cash':saved['cash'],'positions':positions,
        'orders':len(saved['orders']),
        'pending_orders':sum(o['status']!='settled' for o in saved['orders'].values()),
        'halted':bool(saved.get('halt_reason')),
        'session_ticks':sum(counts.values()) if counts else 0,
        'historical_ticks':'NOT_RECORDED',
        'markets':{t:{'session_ticks':counts.get(t,0),'last_price':last.get(t),
                      'state':'RECEIVED' if counts.get(t) else 'WAIT_TICK'} for t in sorted(markets)},
        'real_orders':0,'slack_messages':0,'launchd_changed':False}


def offline_report(state):
    report=inspect(state=state)
    result={'status':'PENDING','network_enabled':False,'preflight':report,
            'execution_authorized':False,'summary':None}
    if report['checks'].get('paper_ledger')=='READY_LOCAL_RECONCILIATION_ONLY':
        # Reacquire a stable read snapshot; do not trust a previously released lock.
        try:
            p=_private(state,{'.json'})
            with cold_lock(p) as owner:
                if owner!='READY_IDLE_AT_SNAPSHOT':raise ValueError()
                from ops.local_preflight import _literal
                import ast
                from ops.local_preflight import ROOT
                initial=_literal(ast.parse((ROOT/'config.py').read_text(encoding='utf-8')),'START_CASH')
                saved=validate_state(_json(p.read_text(encoding='utf-8')),p,initial)
                result['summary']=state_summary(saved)
        except Exception:result['status']='BLOCKED'
    return result


def public_analyzer_factory(client):
    from data_analyzer.analyzer import DataAnalyzer
    class PublicAnalyzer(DataAnalyzer):
        # Reuse exact indicator, signal, filter and cooldown bodies. Bypass only raw-error
        # decorators (which contain legacy Slack/log paths); no strategy change.
        def fetch_data(self,interval='minute1',count=200):
            if interval!='minute1' or count!=200:raise ValueError('PAPER_CANDLE_CONTRACT')
            self.df=client.candles(self.ticker)
        def update_data(self):
            self.fetch_data()
            DataAnalyzer.calculate_indicators.__wrapped__(self)
        def analyze(self,index=-1):
            return DataAnalyzer.analyze.__wrapped__(self,index)
    return PublicAnalyzer


def run_session(state, *, seconds, stopped, emit, client, stream_factory,
                tickers=None, top=10, analyzer_factory=None, monotonic=time.monotonic, wall=time.time):
    """Called only by the supervised explicit-network branch (or offline injected tests)."""
    from config import REAL_TRADING, SLACK_NOTIFICATIONS_ENABLED, START_CASH, DATA_UPDATE_INTERVAL
    from trading.auto_trade import AutoTrade
    if REAL_TRADING is not False or SLACK_NOTIFICATIONS_ENABLED is not False:
        raise ValueError('PAPER_POLICY_BLOCKED')
    begun=monotonic();wall_begun=wall()
    deadline=begun+seconds;wall_deadline=wall_begun+seconds
    p=_private(state,{'.json'})
    bot=None;stream=None;counts={};last={};previous={};status='HALTED';reason='SESSION_FAILED'
    try:
        # Take the original state lock and validate recovery before ANY network discovery.
        # Build the bot with a seed ticker so its constructor never discovers implicitly.
        factory=analyzer_factory or public_analyzer_factory(client)
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=p,real_trading=False,broker=None,
                      notification=None,analyzer_factory=factory)
        bot.engine.store=ExistingPaperStore(p,START_CASH)
        bot.initialize()
        emit({'status':'STARTING',**state_summary(bot.engine.state)})
        selected=list(tickers) if tickers is not None else client.top_tickers(top)
        if (not selected or len(selected)>100 or len(set(selected))!=len(selected)
                or any(not isinstance(t,str) or not MARKET.fullmatch(t) for t in selected)):
            raise ValueError('PAPER_INVALID_MARKETS')
        selected+=sorted(t for t,pos in bot.engine.state['positions'].items()
                         if Decimal(pos['quantity'])>0 and t not in selected)
        if len(selected)>100:raise ValueError('PAPER_TOO_MANY_HELD_MARKETS')
        bot.tickers=selected;bot.analyzers={t:factory(t) for t in selected}
        bot.performance_analyzer.tickers=selected
        for t,saved in bot.engine.state['signals'].items():
            if t in bot.analyzers:
                bot.analyzers[t].last_signal=saved['action'];bot.analyzers[t].last_signal_time=saved['timestamp']
        counts={t:0 for t in selected};bot._sync_views()
        def stop_requested():return stopped() or monotonic()>=deadline or wall()>=wall_deadline
        for analyzer in bot.analyzers.values():
            if stop_requested():break
            analyzer.update_data()
        if stop_requested():
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
        else:
            stream=stream_factory(selected);stream.connect()
            last_update=last_report=last_received=monotonic()
            market_received={t:last_received for t in selected}
            while not stop_requested():
                try:data=stream.get()
                except TimeoutError:
                    if stop_requested():break
                    if monotonic()-last_received>=15 or any(monotonic()-at>=60 for at in market_received.values()):
                        raise ValueError('PAPER_STREAM_TIMEOUT') from None
                    continue
                if stop_requested():break
                if not isinstance(data,dict) or data.get('type')!='ticker' or data.get('code') not in counts:
                    raise ValueError('PAPER_INVALID_TICK')
                if abs((wall()-wall_begun)-(monotonic()-begun))>5:
                    raise ValueError('PAPER_CLOCK_DISCONTINUITY')
                t=data['code'];price=positive(data.get('trade_price'))
                last_received=monotonic();market_received[t]=last_received
                if any(last_received-at>=60 for at in market_received.values()):
                    raise ValueError('PAPER_MARKET_STALE')
                stamp=data.get('timestamp')
                if type(stamp) is not int or not wall()*1000-60_000<=stamp<=wall()*1000+5000 or stamp<previous.get(t,0):
                    raise ValueError('PAPER_STALE_TICK')
                previous[t]=stamp;counts[t]+=1;last[t]=str(price)
                bot.process_market_data(t,data)
                now=monotonic()
                if now-last_update>=DATA_UPDATE_INTERVAL:
                    for analyzer in bot.analyzers.values():
                        if stop_requested():break
                        analyzer.update_data()
                    last_update=monotonic()
                if now-last_report>=5:
                    emit({'status':'RUNNING',**state_summary(bot.engine.state,counts,last)})
                    last_report=now
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
    except Exception as exc:
        allowed={'PAPER_STREAM_TIMEOUT','PAPER_STALE_TICK','PAPER_MARKET_STALE','PAPER_CLOCK_DISCONTINUITY',
                 'PAPER_INVALID_TICK','PAPER_INVALID_MARKETS','PAPER_TOO_MANY_HELD_MARKETS'}
        if str(exc) in allowed or (isinstance(exc,PublicDataError) and str(exc) in PUBLIC_ERROR_CODES):
            reason=str(exc)
        if (stopped() or monotonic()>=deadline) and bot is not None and bot.engine.ready:
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
        # Fixed private reason; no exception, URL/body/token in terminal output.
        if status!='STOPPED' and bot is not None and bot.engine.ready:
            bot.engine.halt('PAPER_SESSION_FAILURE')
    finally:
        if stream is not None:
            try:stream.close()
            except Exception:status='HALTED';reason='STREAM_CLOSE_UNCONFIRMED'
        result={'status':status,'reason':reason,'post_validation':'UNVERIFIED',
                'network_enabled':True,'execution_authorized':False}
        if bot is not None and bot.engine.ready:
            try:
                saved=_json(p.read_text(encoding='utf-8'))
                if saved!=bot.engine.state:raise ValueError('PAPER_DISK_MISMATCH')
                validate_state(saved,p,START_CASH)
                result['post_validation']='VERIFIED_UNDER_OWNER_LOCK'
            except Exception:
                bot.engine.halt('PAPER_POST_VALIDATION_FAILURE');result['status']='HALTED'
        if bot is not None and bot._initialized:
            if bot.engine.halted:result['status']='HALTED'
            result.update(state_summary(bot.engine.state,counts,last))
            bot.stop()
        elif bot is not None:
            bot.stop()
        try:client.close()
        except Exception:result['status']='HALTED'
        emit(result)
    return result
