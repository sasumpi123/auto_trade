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
from services.tick_timing import TickTiming,DISCARD_SESSION_SECONDS
from services.paper_decisions import DecisionCounters
from services.realtime_activity import Observation,ActivityError,ACTIVITY_ERRORS


def validate_state(saved, path, initial_cash):
    engine=ExecutionEngine(path,initial_cash,real_trading=False)
    engine._validate(saved)
    markets=set(saved['positions']) | set(saved['signals']) | {o['ticker'] for o in saved['orders'].values()}
    if any(not MARKET.fullmatch(t) for t in markets):
        raise ValueError('PAPER_INVALID_MARKET_IN_LEDGER')
    if 'paper_experiment' in saved:
        from ops.paper_experiment import validate_identity
        validate_identity(saved,path)
    if 'manual_recovery' in saved:
        raise ValueError('PAPER_MANUAL_RECOVERY_RESTART_NOT_APPROVED')
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
    def __init__(self,path,initial_cash,*,prepare=None,before_first_save=None):
        # Check original alias before StateStore.resolve can hide a symlink.
        _private(path,{'.json'})
        super().__init__(path);self.initial_cash=initial_cash;self.prepare=prepare;self.preparation_snapshot=None
        self.before_first_save=before_first_save;self._first_save_done=False

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
            saved=self.validate_existing(p)
            self._lock=handle  # Preparation may atomically checkpoint under this same owner.
            if self.prepare is not None:
                self.preparation_snapshot=state_summary(saved)
                self.prepare(saved)  # Lock held; engine has not loaded/saved/opened yet.
        except Exception:
            self._lock=None;handle.close();raise
        self._lock=handle

    def validate_existing(self,path):
        return validate_state(_json(path.read_text(encoding='utf-8')),path,self.initial_cash)

    def save(self,saved):
        if not self._first_save_done and self.before_first_save is not None:self.before_first_save()
        super().save(saved);self._first_save_done=True

    def load(self):return _json(self.path.read_text(encoding='utf-8'))


def state_summary(saved, counts=None, last=None, *, marks=None, now=None,decisions=None,timing=None):
    counts={} if counts is None else counts
    last={} if last is None else last
    positions={}
    for ticker,pos in saved['positions'].items():
        positions[ticker]={'quantity':pos['quantity'],'average_price':pos['avg_price'],
                          'averaging_used':pos['averaging_down_used']}
    markets=set(counts)|set(positions)
    result={'mode':'PAPER','cash':saved['cash'],'positions':positions,
        'orders':len(saved['orders']),
        'pending_orders':sum(o['status']!='settled' for o in saved['orders'].values()),
        'halted':bool(saved.get('halt_reason')),
        'session_ticks':sum(counts.values()) if counts else 0,
        'historical_ticks':'NOT_RECORDED',
        'markets':{t:{'session_ticks':counts.get(t,0),'last_price':last.get(t),
                      'state':'RECEIVED' if counts.get(t) else 'WAIT_TICK'} for t in sorted(markets)},
        'real_orders':0,'slack_messages':0,'launchd_changed':False}
    if 'paper_experiment' in saved:
        from trading.costs import summary as cost_summary
        result.update(cost_summary(saved))
        metrics=report_metrics(saved,1_000_000,marks or {},now=time.monotonic() if now is None else now,verified=True)
        def number(key):
            v=metrics.get(key)
            return str(v) if isinstance(v,Decimal) else v if type(v) is int else 'N/A'
        result.update(account_scope='NEW_INDEPENDENT_PAPER_EXPERIMENT_NOT_RECOVERY',initial_cash='1000000',
                      cumulative_buys=number('매수 횟수'),cumulative_sells=number('매도 횟수'),
                      net_return_percent=number('누적 수익률'),equity=number('종료 평가자산'),
                      valuation_reason=metrics.get('평가 제한',metrics.get('정산 사유','CONFIRMED_LEDGER_AND_FRESH_MARKS_ONLY')),
                      net_realized_pnl=number('실현 손익'),unrealized_pnl=number('평가 손익'),fees=number('수수료 합계'))
        for t in result['markets']:
            rows=[r for r in metrics.get('accounting_rows',[]) if r['ticker']==t]
            result['markets'][t].update(
                cumulative_buys=sum(r['side']=='buy' for r in rows) if type(metrics.get('거래 횟수')) is int else 'N/A',
                cumulative_sells=sum(r['side']=='sell' for r in rows) if type(metrics.get('거래 횟수')) is int else 'N/A')
    if decisions is not None:
        result['decision_trace']=decisions.snapshot(timing)
        result['price_age_seconds']={t:round(now-marks[t][1],3) if marks and t in marks and now is not None and now>=marks[t][1] else None for t in sorted(markets)}
    return result


def offline_report(state, *, outbox=None):
    report=inspect(state=state,outbox=outbox)
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
    if report.get('halted') is True:
        from ops.paper_recovery_diagnostic import diagnose
        result['halted_reconciliation']=diagnose(state)
    return result



def validate_candle_report(report):
    """Small typed observation, separate from P13's existing trace schema."""
    import math
    import pandas as pd
    from copy import deepcopy
    keys={'last_completed_open_utc','last_evaluated_open_utc','skipped_same_candle','refreshes',
          'volume','mean_volume_20_completed','threshold_fraction','mean_includes_evaluated_candle'}
    if type(report) is not dict or len(report)>30:raise ValueError('CANDLE_REPORT_INVALID')
    for ticker,r in report.items():
        if not isinstance(ticker,str) or not MARKET.fullmatch(ticker) or type(r) is not dict or set(r)!=keys:raise ValueError('CANDLE_REPORT_INVALID')
        for k in ('last_completed_open_utc','last_evaluated_open_utc'):
            if r[k] is not None:
                t=pd.Timestamp(r[k])
                if t.tzinfo is None or t.utcoffset().total_seconds()!=0 or t!=t.floor('min'):raise ValueError('CANDLE_REPORT_TIME')
        for k in ('skipped_same_candle','refreshes'):
            if type(r[k]) is not int or not 0<=r[k]<=2_147_483_647:raise ValueError('CANDLE_REPORT_COUNT')
        for k in ('volume','mean_volume_20_completed'):
            if r[k] is not None and (type(r[k]) not in (int,float) or not math.isfinite(r[k]) or r[k]<0):raise ValueError('CANDLE_REPORT_VOLUME')
        if type(r['threshold_fraction']) is not float or r['threshold_fraction']!=.5 or r['mean_includes_evaluated_candle'] is not True:raise ValueError('CANDLE_REPORT_FILTER')
    return deepcopy(report)


def public_analyzer_factory(client,*,wall=time.time,cancelled=lambda:False):
    from data_analyzer.analyzer import DataAnalyzer
    class PublicAnalyzer(DataAnalyzer):
        def __init__(self,ticker):
            super().__init__(ticker);self._evaluated=None;self._requested=None;self.skipped_same_candle=0;self.refreshes=0
        def candle_open(self):
            # PublicMarketClient explicitly preserves pyupbit's KST-naive contract.
            import pandas as pd
            t=self.df.index[-1]
            if not isinstance(t,pd.Timestamp) or t.tzinfo is not None:raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME')
            return t.tz_localize('Asia/Seoul').tz_convert('UTC')
        def ready_for_strategy(self):
            import pandas as pd
            cutoff=pd.Timestamp(wall(),unit='s',tz='UTC').floor('min')
            latest=self.candle_open()
            if latest>=cutoff or (self._evaluated is not None and latest<self._evaluated):
                raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME')
            if self._requested is not None and cutoff<self._requested:
                raise PublicDataError('PUBLIC_RESPONSE_CLOCK_UNVERIFIED')
            if latest!=cutoff-pd.Timedelta(minutes=1):
                if self._requested==cutoff:raise PublicDataError('PUBLIC_STALE_CANDLE')
                self._requested=cutoff
                try:self.update_data()
                except PublicDataError as exc:
                    if str(exc) in ('PUBLIC_STOP_REQUESTED','PUBLIC_DEADLINE') and cancelled():return False
                    raise
                latest=self.candle_open()
                if latest!=cutoff-pd.Timedelta(minutes=1):raise PublicDataError('PUBLIC_CANDLE_GAP')
            if self._evaluated is not None and latest<self._evaluated:raise PublicDataError('PUBLIC_CANDLE_ORDER')
            if latest==self._evaluated:
                self.skipped_same_candle=min(2_147_483_647,self.skipped_same_candle+1);return False
            self._evaluated=latest;self.last_volume_check=None
            return True
        def candle_report(self):
            import math
            values=getattr(self,'last_volume_check',None)
            valid=values is not None and all(math.isfinite(v) and v>=0 for v in values)
            try:latest=self.candle_open().isoformat()
            except Exception:latest=None
            return {'last_completed_open_utc':latest,
                'last_evaluated_open_utc':self._evaluated.isoformat() if self._evaluated is not None else None,
                'skipped_same_candle':self.skipped_same_candle,'refreshes':self.refreshes,
                'volume':values[0] if valid else None,'mean_volume_20_completed':values[1] if valid else None,
                'threshold_fraction':.5,'mean_includes_evaluated_candle':True}
        # Reuse exact indicator, signal, filter and cooldown bodies. Bypass only raw-error
        # decorators (which contain legacy Slack/log paths); no strategy change.
        def fetch_data(self,interval='minute1',count=200):
            if interval!='minute1' or count!=200:raise ValueError('PAPER_CANDLE_CONTRACT')
            self.df=client.candles(self.ticker)
            self.refreshes=min(2_147_483_647,self.refreshes+1)
        def update_data(self):
            self.fetch_data()
            DataAnalyzer.calculate_indicators.__wrapped__(self)
        def analyze(self,index=-1):
            return DataAnalyzer.analyze.__wrapped__(self,index)
    return PublicAnalyzer


def run_session(state, *, seconds, stopped, emit, client, stream_factory,
                tickers=None, top=10, analyzer_factory=None, monotonic=time.monotonic, wall=time.time, experiment=None):
    """Called only by the supervised explicit-network branch (or offline injected tests)."""
    from config import REAL_TRADING, SLACK_NOTIFICATIONS_ENABLED, START_CASH, DATA_UPDATE_INTERVAL
    from trading.auto_trade import AutoTrade
    if REAL_TRADING is not False or SLACK_NOTIFICATIONS_ENABLED is not False:
        raise ValueError('PAPER_POLICY_BLOCKED')
    discard_enabled=bool(experiment and experiment.get('discard_small_timestamp_regressions') is True)
    if experiment and type(experiment.get('discard_small_timestamp_regressions',False)) is not bool:
        raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
    if discard_enabled and (experiment.get('observe_realtime_seconds') is None
                            or type(seconds) is not int or not 1<=seconds<=DISCARD_SESSION_SECONDS):
        raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
    observation_discard=bool(experiment and experiment.get('discard_small_timestamp_regressions_during_observation') is True)
    if experiment and type(experiment.get('discard_small_timestamp_regressions_during_observation',False)) is not bool:
        raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
    if observation_discard and (not discard_enabled or not tickers or experiment.get('eligible_top') is not None
            or experiment.get('select_realtime_active') or experiment.get('candidate_limit') is not None):
        raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
    decisions=DecisionCounters(wall=wall)
    begun=monotonic();wall_begun=wall()
    deadline=begun+seconds;wall_deadline=wall_begun+seconds
    p=_private(state,{'.json'})
    bot=None;stream=None;timing=None;activity=None;ignored_activity_messages=0;cleanup_error=None;counts={};last={};marks={};status='HALTED';reason='SESSION_FAILED'
    phase='PREPARATION'
    def regression_evidence():
        from services.realtime_activity import timing_evidence,validate_regression_evidence
        observed=timing_evidence(activity.observation_timing or activity.guard.report())
        combined=timing_evidence(timing.report()) if timing is not None else observed
        # Legacy runtime-only mode has separate timing counters, but no observation discards.
        return validate_regression_evidence({'observation':observed,'combined':combined,
            'runtime_enabled':discard_enabled,'runtime_started':timing is not None,
            'runtime_discarded':combined['discarded']-observed['discarded']})
    def publish(report):
        if activity is not None:report['regression_evidence']=regression_evidence()
        if bot is not None and bot._initialized:
            report['candle_evaluation']={t:a.candle_report() for t,a in bot.analyzers.items() if callable(getattr(a,'candle_report',None)) and not a.df.empty}
        emit(report)
    try:
        # Take the original state lock and validate recovery before ANY network discovery.
        # Build the bot with a seed ticker so its constructor never discovers implicitly.
        def stop_requested():return stopped() or monotonic()>=deadline or wall()>=wall_deadline
        base_factory=analyzer_factory or public_analyzer_factory(client,wall=wall,cancelled=stop_requested)
        def factory(t):
            analyzer=base_factory(t);analyzer.decision_observer=decisions;return analyzer
        prepared={}
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=p,real_trading=False,broker=None,
                      notification=None,analyzer_factory=factory)
        bot.decision_observer=decisions
        selected=[]
        def check_activity(pool,held,wanted,replace,report):
            nonlocal stream,activity
            try:
                activity=Observation(pool,wall=wall,monotonic=monotonic,discard_small_regressions=observation_discard,
                                     clock_origin=(wall_begun,begun) if observation_discard else None)
                try:
                    stream=stream_factory(pool);stream.connect()
                except Exception:
                    activity.end=monotonic();activity.connection_end='SUBSCRIPTION_OR_CONNECT_FAILED_CAUSE_UNKNOWN'
                    raise ActivityError('ACTIVITY_CONNECTION_FAILED') from None
                activity=Observation(pool,wall=wall,monotonic=monotonic,discard_small_regressions=observation_discard,
                                     clock_origin=(wall_begun,begun) if observation_discard else None)
                activity.observe(stream,experiment['observe_realtime_seconds'],stopped=stopped,deadline=deadline)
                active=[t for t in pool if activity.active(t)]
                report['realtime_activity']=activity.report();report['wire_subscription']=pool.copy()
                for t in pool:
                    if t not in active:report['excluded'].append({'market':t,'stage':'REALTIME_ACTIVITY',
                        'reason':activity.exclusion(t),'cause':'UNKNOWN_NO_TRADE_OR_API_FAILURE_INFERENCE',
                        'role':'HELD_CANNOT_BE_DROPPED' if t in held else 'CANDIDATE'})
                if any(t not in active for t in held):raise ActivityError('HELD_REALTIME_ACTIVITY_UNAVAILABLE')
                if replace:
                    chosen=[t for t in report['candle_eligible_candidates'] if t in active][:wanted]
                    if len(chosen)<wanted:raise ActivityError('INSUFFICIENT_REALTIME_ACTIVE_CANDIDATES')
                    chosen+=sorted(t for t in held if t not in chosen)
                    report['ranking_deviation_declared']=True
                else:
                    if len(active)!=len(pool):raise ActivityError('REALTIME_ACTIVITY_REQUIRED')
                    chosen=pool.copy()
                activity.validate_start(chosen)
                return chosen
            finally:
                if activity is not None:report['realtime_activity']=activity.report()
        def before_first_save():
            if activity is not None:
                activity.validate_start(selected)
                if stopped() or monotonic()>=deadline:raise ActivityError('ACTIVITY_OBSERVATION_INTERRUPTED')
        def prepare(saved):
            if 'paper_experiment' in saved:
                from ops.paper_experiment import validate_start, prepare_markets
                validate_start(saved,p,experiment.get('session_id') if experiment else None)
                # No per-hour recharge via another session on the same account.
                # P15 reserves observation use durably BEFORE network/engine startup.
                if discard_enabled and saved['paper_experiment']['last_session_id'] is not None:
                    raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
                from ops.paper_experiment import first_use_path
                used=_json(_private(first_use_path(p.parent),{'.json'}).read_text(encoding='utf-8'))
                reservation=used.get('observation_discard_session')
                if (observation_discard and reservation!=experiment['session_id']) or (discard_enabled
                        and reservation is not None and reservation!=experiment['session_id']):
                    raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
                if observation_discard:
                    from ops.paper_experiment import marker,save_marker
                    m=marker(p.parent)
                    if m.get('observation_discard_started') is not None:
                        raise ValueError('PAPER_REGRESSION_EXPERIMENT_BLOCKED')
                    m['observation_discard_started']=experiment['session_id']
                    save_marker(bot.engine.store,m)  # Durable before even candle preparation/network.
                chosen,analyzers=prepare_markets(saved,client,top,tickers,experiment,emit,analyzer_factory,
                    activity_check=check_activity if experiment.get('observe_realtime_seconds') is not None else None,cancelled=stop_requested)
                selected.extend(chosen);prepared.update(analyzers)
                return
            if experiment is not None:raise ValueError('PAPER_EXPERIMENT_IDENTITY_REQUIRED')
            candidates=list(tickers) if tickers is not None else client.top_tickers(top)
            if (not candidates or len(candidates)>100
                    or any(not isinstance(t,str) or not MARKET.fullmatch(t) for t in candidates)
                    or len(set(candidates))!=len(candidates)):
                raise ValueError('PAPER_INVALID_MARKETS')
            candidates+=sorted(t for t,pos in saved['positions'].items()
                               if Decimal(pos['quantity'])>0 and t not in candidates)
            if len(candidates)>100:raise ValueError('PAPER_TOO_MANY_HELD_MARKETS')
            selected.extend(candidates)
        bot.engine.store=ExistingPaperStore(p,START_CASH,prepare=prepare,before_first_save=before_first_save)
        bot.engine._experiment_session=experiment.get('session_id') if experiment else None
        bot.initialize()
        publish({'status':'STARTING',**state_summary(bot.engine.state,decisions=decisions)})
        bot.tickers=selected;bot.analyzers=prepared or {t:factory(t) for t in selected}
        for analyzer in bot.analyzers.values():analyzer.decision_observer=decisions
        bot.performance_analyzer.tickers=selected
        for t,saved in bot.engine.state['signals'].items():
            if t in bot.analyzers:
                bot.analyzers[t].last_signal=saved['action'];bot.analyzers[t].last_signal_time=saved['timestamp']
        counts={t:0 for t in selected};bot._sync_views()
        for analyzer in bot.analyzers.values():
            if stop_requested():break
            if not prepared:analyzer.update_data()
        if stop_requested():
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
        else:
            if activity is not None:
                activity.validate_start(selected)
                timing=activity.runtime_guard(selected,(wall_begun,begun),discard_small_regressions=discard_enabled)
            else:
                stream=stream_factory(selected);stream.connect()
                timing=TickTiming(selected,wall(),monotonic(),clock_origin=(wall_begun,begun))
            last_update=last_report=monotonic()
            while not stop_requested():
                phase='STREAM'
                try:data=stream.get()
                except TimeoutError:
                    if stop_requested():break
                    timing.idle(wall(),monotonic())
                    continue
                phase='MARKET_VALIDATION'
                observed_market=data.get('code') if isinstance(data,dict) and data.get('code') in selected else None
                decisions.record('stream_messages_received',observed_market)
                if stop_requested():break
                if activity is not None:
                    if (not isinstance(data,dict) or data.get('type')!='ticker'
                            or not isinstance(data.get('code'),str) or data['code'] not in activity.stats):
                        raise ActivityError('ACTIVITY_INVALID_MESSAGE')
                    if data.get('stream_type') not in ('REALTIME','SNAPSHOT'):raise ActivityError('ACTIVITY_STREAM_TYPE_UNKNOWN')
                    if data['code'] not in selected or data['stream_type']=='SNAPSHOT':
                        ignored_activity_messages=min(2_147_483_647,ignored_activity_messages+1)
                        timing.idle(wall(),monotonic());continue
                t=timing.observe(data,wall(),monotonic())
                if t is None:continue  # No strategy, fill, mark, count, or timer refresh.
                decisions.record('timing_accepted',t)
                price=positive(data.get('trade_price'))
                counts[t]+=1;last[t]=str(price);marks[t]=(price,monotonic())
                phase='STRATEGY_AND_ORDER'
                before_orders=len(bot.engine.state['orders'])
                bot.process_market_data(t,data)
                now=monotonic()
                # Preserve injected legacy analyzers' refresh contract. Public adapters
                # refresh inside ready_for_strategy, after risk, once per new minute.
                if now-last_update>=DATA_UPDATE_INTERVAL:
                    phase='CANDLE_REFRESH'
                    for analyzer in bot.analyzers.values():
                        if stop_requested():break
                        if not callable(getattr(analyzer,'ready_for_strategy',None)):analyzer.update_data()
                    last_update=monotonic()
                if now-last_report>=5 or len(bot.engine.state['orders'])!=before_orders:
                    publish({'status':'RUNNING',**state_summary(bot.engine.state,counts,last,marks=marks,now=monotonic(),decisions=decisions,timing=timing)})
                    last_report=now
            if timing.awaiting_followup is not None:
                timing.fail('TIMESTAMP_REGRESSION',timing.awaiting_followup,followup_newer_received=False)
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
    except Exception as exc:
        if phase=='MARKET_VALIDATION' or str(exc) in {'PAPER_STREAM_TIMEOUT','PAPER_STALE_TICK','PAPER_MARKET_STALE','PAPER_CLOCK_DISCONTINUITY','PAPER_INVALID_TICK'}:
            decisions.record('data_error',reason='MARKET_VALIDATION')
        elif isinstance(exc,PublicDataError):decisions.record('data_error',reason='CANDLE_PREPARATION_OR_REFRESH')
        allowed={'PAPER_STREAM_TIMEOUT','PAPER_STALE_TICK','PAPER_MARKET_STALE','PAPER_CLOCK_DISCONTINUITY',
                 'PAPER_INVALID_TICK','PAPER_INVALID_MARKETS','PAPER_TOO_MANY_HELD_MARKETS',
                 'INSUFFICIENT_CONTIGUOUS_CANDIDATES','HELD_CANDLE_GAP_RISK_MANAGEMENT_UNAVAILABLE',
                 'PAPER_REGRESSION_EXPERIMENT_BLOCKED','HELD_MARKET_LIMIT','SELECTION_LIMIT_INVALID','CANDIDATE_LIMIT_INVALID','CANDIDATES_INVALID'} | ACTIVITY_ERRORS
        if str(exc) in allowed or (isinstance(exc,ActivityError) and str(exc) in ACTIVITY_ERRORS) or (isinstance(exc,PublicDataError) and str(exc) in PUBLIC_ERROR_CODES):
            reason=str(exc)
        cancel_only=(experiment is None or (isinstance(exc,PublicDataError)
                     and str(exc) in ('PUBLIC_STOP_REQUESTED','PUBLIC_DEADLINE')))
        if cancel_only and (stopped() or monotonic()>=deadline) and bot is not None and bot.engine.ready:
            status='STOPPED';reason='TIME_LIMIT_OR_USER_STOP'
        # Fixed private reason; no exception, URL/body/token in terminal output.
        if status!='STOPPED' and bot is not None and bot.engine.ready:
            bot.engine.halt('PAPER_SESSION_FAILURE')
    finally:
        if stream is not None:
            try:stream.close()
            except Exception:
                cleanup_error='STREAM_CLOSE_UNCONFIRMED'
                if status!='HALTED':status='HALTED';reason=cleanup_error
        result={'status':status,'reason':reason,'post_validation':'UNVERIFIED',
                'network_enabled':True,'execution_authorized':False,
                'engine_started':bool(bot is not None and bot._initialized),'decision_trace':decisions.snapshot(timing)}
        if activity is not None:
            result['realtime_activity']=activity.report()
            result['wire_subscription']=list(activity.stats)
            result['fixed_strategy_markets']=selected.copy()
            result['ignored_non_strategy_or_snapshot_messages']=ignored_activity_messages
        if cleanup_error is not None:result['cleanup_error']=cleanup_error
        if timing is not None:result['tick_timing']=timing.report()
        if experiment is not None:
            calls=getattr(client,'calls',None)
            result['public_rest_requests']=calls if type(calls) is int else 'FAKE_NOT_RECORDED'
            result['public_rest_budget']=64
        if bot is not None and bot.engine.ready:
            try:
                saved=_json(p.read_text(encoding='utf-8'))
                if saved!=bot.engine.state:raise ValueError('PAPER_DISK_MISMATCH')
                validate_state(saved,p,START_CASH)
                result['post_validation']='VERIFIED_UNDER_OWNER_LOCK'
            except Exception:
                bot.engine.halt('PAPER_POST_VALIDATION_FAILURE');result['status']='HALTED'
                if status!='HALTED':result['reason']='PAPER_POST_VALIDATION_FAILURE'
        if bot is not None and bot._initialized:
            if bot.engine.halted:result['status']='HALTED'
            result.update(state_summary(bot.engine.state,counts,last,marks=marks,now=monotonic(),decisions=decisions,timing=timing))
            if result['status']=='HALTED' and experiment and experiment.get('record_decisions') is True:
                try:
                    from ops.paper_experiment import checkpoint_decisions
                    checkpoint_decisions(bot.engine.store,experiment['session_id'],bot.engine.state,result['decision_trace'])
                    result['decision_checkpoint']='WORKER_OBSERVATION_ONLY_NOT_FINALIZATION'
                except Exception:result['decision_checkpoint']='UNVERIFIED_NOT_SAVED'
            if activity is not None and experiment is not None:
                try:
                    from ops.paper_experiment import checkpoint_regression
                    checkpoint_regression(bot.engine.store,experiment['session_id'],regression_evidence(),result['reason'])
                except Exception:
                    bot.engine.halt('PAPER_SESSION_FAILURE');result['status']='HALTED';result['halted']=True
                    result['reason']='REGRESSION_EVIDENCE_UNVERIFIED'
            bot.stop()
        elif bot is not None:
            snapshot=getattr(bot.engine.store,'preparation_snapshot',None)
            if snapshot is not None:
                result.update(snapshot)
                result['ledger_snapshot']='VERIFIED_BEFORE_DISCOVERY_ONLY'
            bot.stop()
        try:client.close()
        except Exception:
            result['cleanup_error']='PUBLIC_CLIENT_CLOSE_UNCONFIRMED'
            if result['status']!='HALTED':result['reason']='PUBLIC_CLIENT_CLOSE_UNCONFIRMED'
            result['status']='HALTED'
        publish(result)
    return result
