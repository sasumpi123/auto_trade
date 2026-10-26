"""Offline legacy analyzer -> AutoTrade -> PAPER engine diagnostic, never live."""
import argparse
import hashlib
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from .data import dataset_hash,validate_candles
from .development_data import load_development
from .engine import CostModel,decimal,serializable
from .historical import load_protocol

# Do not load credentials from .env when this dedicated CLI imports legacy modules.
os.environ['PYTHON_DOTENV_DISABLED']='1'
from data_analyzer.analyzer import DataAnalyzer
from strategies.signals import Snapshot,LegacyCompositeStrategy
from trading.auto_trade import AutoTrade
from trading.execution import ExecutionEngine,number
from services.notification_metrics import replay

WINDOW=200
ARCHIVE_MANIFEST_SHA256='508df7f440378a11ef06530e3f8c91988b40469e87e94f001164b27da90442a1'
RULES={
 'rsi_buy':('rsi_lt_30','rsi_rising'), 'rsi_sell':('rsi_gt_70','rsi_falling'),
 'macd_buy':('macd_gt_signal','macd_negative','macd_rising'),
 'macd_sell':('macd_lt_signal','macd_positive','macd_falling'),
 'bb_buy':('below_lower','rsi_or_macd_rising'), 'bb_sell':('above_upper',),
}


def predicates(s):
    """Observation only. Actual action ALWAYS comes from the unmodified strategy."""
    return dict(rsi_lt_30=s.rsi<30,rsi_rising=s.rsi_delta>0,rsi_gt_70=s.rsi>70,rsi_falling=s.rsi_delta<0,
        macd_gt_signal=s.macd>s.macd_signal,macd_negative=s.macd<0,macd_rising=s.macd_delta>0,
        macd_lt_signal=s.macd<s.macd_signal,macd_positive=s.macd>0,macd_falling=s.macd_delta<0,
        below_lower=s.close<s.bb_lower,rsi_or_macd_rising=s.rsi_delta>0 or s.macd_delta>0,above_upper=s.close>s.bb_upper)


def prepare(frame,bar_seconds):
    """Recompute EXACT legacy rolling-200 indicators using prefixes only.

    Legacy bfill sees only historical rows; never evaluate an early filled row.
    The final 20 rows suffice for unchanged analyze's volume average/latest deltas.
    """
    frame=validate_candles(frame,bar_seconds);windows={}
    analyzer=DataAnalyzer('KRW-BTC')
    for i in range(WINDOW-1,len(frame)-1):
        analyzer.df=frame.iloc[i-WINDOW+1:i+1].copy()
        DataAnalyzer.calculate_indicators.__wrapped__(analyzer)
        windows[i]=analyzer.df.iloc[-20:].copy()
    return frame,windows


def snapshot(df):
    a,b=df.iloc[-2],df.iloc[-1]
    return Snapshot(float(b.close),float(b.rsi),float(b.rsi-a.rsi),float(b.macd),float(b.macd_signal),
        float(b.macd-a.macd),float(b.bb_lower),float(b.bb_middle),float(b.bb_upper))


class CostPaperEngine(ExecutionEngine):
    """Disposable research receipt adapter; real broker is never supported.

    Existing _execute persists intent and invokes existing _apply_fill accounting.
    Fees are all-in within the original requested BUY budget; no real PAPER default changes.
    """
    def __init__(self,path,cash,costs,clock):
        super().__init__(path,cash,real_trading=False);self.costs=costs;self.clock=clock
        self.slippage=Decimal(0);self.spread=Decimal(0);self.trace=[];self.opening=None
    def _execute(self,side,ticker,price,value,**kwargs):
        self.opening=number(price,positive=True)
        execution=self.costs.price(self.opening,'BUY' if side=='bid' else 'SELL')
        return super()._execute(side,ticker,execution,value,**kwargs)
    def _apply_fill(self,order,receipt):
        receipt=deepcopy(receipt)
        if order['side']=='bid':
            funds=number(order['value'])/(1+self.costs.fee_rate)
            q=funds/number(order['price_hint'])
            receipt.update(executed_volume=str(q),trades=[{'volume':str(q),'funds':str(funds)}])
        funds=sum(number(t['funds']) for t in receipt['trades']);q=number(receipt['executed_volume'])
        receipt['paid_fee']=str(funds*self.costs.fee_rate)
        with patch('trading.execution.time',SimpleNamespace(time=self.clock)):
            filled=super()._apply_fill(order,receipt)
        if filled:
            self.slippage+=q*self.opening*self.costs.slippage_bps/10000
            self.spread+=q*self.opening*self.costs.spread_bps/20000
            self.trace.append({'side':order['side'],'executed_at':self.clock(),
                'price':order['average_fill_price'],'quantity':str(q),'fee':order['paid_fee']})
        return filled


class ProbeAnalyzer(DataAnalyzer):
    def __init__(self,ticker,counts,clock):
        super().__init__(ticker);self.counts=counts;self.clock=clock;self.action=None
    def analyze(self,index=-1):
        self.counts['analyzer_calls']+=1
        low=self.df.volume.iloc[-1]<self.df.volume.mean()*.5
        cooldown=bool(self.last_signal and self.last_signal_time and self.clock()-self.last_signal_time<self.signal_cooldown)
        if low:self.counts['volume_blocked']+=1
        elif cooldown:
            self.counts['cooldown_blocked']+=1
            if LegacyCompositeStrategy().decide(snapshot(self.df)).action!='HOLD':self.counts['cooldown_blocked_actionable']+=1
        with patch('data_analyzer.analyzer.time',SimpleNamespace(time=self.clock)):
            result=DataAnalyzer.analyze.__wrapped__(self,index)
        self.action=result['action'];self.counts['effective_'+self.action]+=1
        return result


class ProbeBot(AutoTrade):
    def buy_coin(self,ticker,current_price,reason=None,target_price=None,amount=None):
        c=self.diagnostic_counts;c['buy_attempts']+=1
        from config import MIN_TRADING_AMOUNT,MAX_COINS_AT_ONCE
        if amount is None and self.get_significant_holdings_count()>=MAX_COINS_AT_ONCE:c['reject_position_limit']+=1
        value=number(amount) if amount is not None else min(self.max_per_coin,self.engine.cash)
        if value<MIN_TRADING_AMOUNT:c['reject_cash_below_minimum' if self.engine.cash<MIN_TRADING_AMOUNT else 'reject_minimum_order']+=1
        result=super().buy_coin(ticker,current_price,reason,target_price,amount)
        if not result:c['buy_rejected']+=1
        return result
    def sell_coin(self,ticker,current_price,stop_loss_triggered=False,reason=None):
        self.diagnostic_counts['sell_attempts']+=1
        result=super().sell_coin(ticker,current_price,stop_loss_triggered,reason)
        if not result:self.diagnostic_counts['sell_rejected']+=1
        return result


def simulate(frame,windows,*,market,bar_seconds,costs,evaluation_start,initial_cash='1000000',repeat_evaluations=1):
    """One asset, close_i features -> open_(i+1) execution, no terminal liquidation.

    Actual risk rules observe next open before analyzer, exactly AutoTrade priority.
    repeats are diagnostic attempts, suppressed before bot execution, never repeated orders.
    """
    cash=decimal(initial_cash)
    if cash<=0 or type(repeat_evaluations) is not int or not 1<=repeat_evaluations<=10:raise ValueError('Invalid simulation limits')
    counts=Counter({k:0 for k in ('evaluations','analyzer_calls','raw_BUY','raw_SELL','raw_HOLD','effective_BUY','effective_SELL','effective_HOLD',
        'volume_blocked','cooldown_blocked','cooldown_blocked_actionable','nonfinite_skipped','duplicate_evaluations_suppressed','repeat_signal_candidates',
        'buy_attempts','sell_attempts','buy_rejected','sell_rejected','reject_position_limit','reject_cash_below_minimum',
        'reject_minimum_order','buy_while_held','sell_without_position','risk_bypassed_analyzer')})
    conditions={};first_failed={};curve=[cash];now=[0.0];seen=set();first_open=None
    with TemporaryDirectory(prefix='legacy-offline-') as temp:
        bot=ProbeBot(start_cash=cash,tickers=[market],state_path=Path(temp)/'paper.json',real_trading=False,
            notification=None,analyzer_factory=lambda t:ProbeAnalyzer(t,counts,lambda:now[0]))
        bot.diagnostic_counts=counts;bot.engine=CostPaperEngine(Path(temp)/'paper.json',cash,costs,lambda:now[0])
        bot.initialize();bot.max_per_coin=cash*Decimal('.4')
        try:
            for i,df in windows.items():
                if frame.index[i]<pd.Timestamp(evaluation_start):continue
                execution_time=frame.index[i+1];now[0]=execution_time.timestamp()
                try:s=snapshot(df)
                except ValueError:
                    if number(bot.engine.position(market)['quantity'])>0:raise ValueError('NONFINITE_FEATURES_WHILE_HOLDING') from None
                    counts['nonfinite_skipped']+=1;continue
                flags=predicates(s);raw=LegacyCompositeStrategy().decide(s)
                for name,value in flags.items():
                    row=conditions.setdefault(name,{'met':0,'not_met':0});row['met' if value else 'not_met']+=1
                for rule,keys in RULES.items():
                    failed=next((k for k in keys if not flags[k]),'ALL_MET')
                    first_failed.setdefault(rule,Counter())[failed]+=1
                counts['raw_'+raw.action]+=1
                if repeat_evaluations>1 and raw.action!='HOLD':counts['repeat_signal_candidates']+=repeat_evaluations-1
                for _ in range(repeat_evaluations):
                    if i in seen:counts['duplicate_evaluations_suppressed']+=1;continue
                    seen.add(i);counts['evaluations']+=1
                    bot.analyzers[market].df=df;bot.analyzers[market].action=None
                    held=number(bot.engine.position(market)['quantity'])>0
                    if first_open is None:first_open=number(frame.open.iloc[i+1])
                    with patch('trading.auto_trade.time',SimpleNamespace(time=lambda:now[0],monotonic=lambda:now[0])):
                        bot.process_market_data(market,{'trade_price':str(frame.open.iloc[i+1])})
                    action=bot.analyzers[market].action
                    if action is None:counts['risk_bypassed_analyzer']+=1
                    elif action=='BUY' and held:counts['buy_while_held']+=1
                    elif action=='SELL' and not held:counts['sell_without_position']+=1
                mark=number(frame.close.iloc[i+1]);curve.append(bot.engine.cash+number(bot.engine.position(market)['quantity'])*mark)
            state=deepcopy(bot.engine.state)
            if bot.engine.store.load()!=state or bot.engine.halted:raise ValueError('RESEARCH_LEDGER_MISMATCH')
            end=frame.index[-1]+pd.Timedelta(seconds=bar_seconds)
            accounting=replay(state,cash,as_of=end.to_pydatetime(),calendar='UTC')
            closed=[r['net_realized'] for r in accounting['rows'] if r['side']=='sell']
            trades=accounting['buy_count']+accounting['sell_count'];fees=accounting['fees'];final=curve[-1]
            peak=cash;dd=Decimal(0)
            for equity in curve:peak=max(peak,equity);dd=max(dd,(peak-equity)/peak*100)
            metrics={k:None for k in ('gross_fee_addback_return_pct','net_return_pct','mdd_pct','win_rate_pct','average_net_sell_pnl',
                'expectancy_per_sell','net_realized_pnl','unrealized_pnl','buy_hold_100_net_pct','buy_hold_40_net_pct')}
            if trades:
                metrics.update(gross_fee_addback_return_pct=(final+fees-cash)/cash*100,net_return_pct=(final-cash)/cash*100,
                    mdd_pct=dd,win_rate_pct=Decimal(accounting['wins'])/len(closed)*100 if closed else None,
                    average_net_sell_pnl=sum(closed)/len(closed) if closed else None,
                    expectancy_per_sell=sum(closed)/len(closed) if closed else None,net_realized_pnl=accounting['net_profit'],
                    unrealized_pnl=final-cash-accounting['net_profit'])
                for fraction in (Decimal(1),Decimal('.4')):
                    budget=cash*fraction;qty=budget/(1+costs.fee_rate)/costs.price(first_open,'BUY')
                    metrics['buy_hold_'+('100' if fraction==1 else '40')+'_net_pct']=((cash-budget+qty*number(frame.close.iloc[-1]))/cash-1)*100
            return serializable({'source':'SYNTHETIC_OR_CALLER_DATA_UNVERIFIED','status':'EVALUATED','market':market,'bar_seconds':bar_seconds,'input_candles':len(frame),
                'warmup_window':WINDOW,'terminal_candles_without_next_open':1,
                'dominant_failures_by_rule':{rule:max(((k,n) for k,n in items.items() if k!='ALL_MET'),key=lambda pair:pair[1],default=None) for rule,items in first_failed.items()},'evaluation_first_open':frame.index[min(seen)].isoformat() if seen else None,
                'execution_last_open':frame.index[-1].isoformat(),'counts':dict(counts),'conditions':conditions,
                'first_failed_by_rule':first_failed,'fills':{'buys':accounting['buy_count'],'sells':accounting['sell_count']},
                'metrics':metrics,'na_reason':'NO_CONFIRMED_TRADES' if not trades else 'NO_CLOSED_SELLS' if not closed else None,
                'fees':fees,'slippage_cost':bot.engine.slippage,'half_spread_cost':bot.engine.spread,
                'costs':vars(costs),'initial_cash':cash,'final_equity':final,'accounting_verified':True,
                'trace':bot.engine.trace,'data_sha256':dataset_hash(frame)})
        finally:bot.stop()


def run(data,market,unit,split):
    protocol,ph=load_protocol();data=Path(data)
    if market not in protocol['markets'] or unit not in protocol['units_minutes'] or split not in ('train','validation'):
        raise ValueError('Unsupported dataset or sealed split')
    raw=(data/'manifest.json').read_text(encoding='utf-8').encode()
    if hashlib.sha256(raw).hexdigest()!=ARCHIVE_MANIFEST_SHA256:raise ValueError('Archive manifest not recognized')
    manifest=json.loads(raw);meta=manifest['datasets'][f'{market}-{unit}']
    if manifest['protocol']!=protocol or manifest['protocol_sha256']!=ph or not manifest.get('completed_at'):raise ValueError('Unverified protocol')
    if meta['snapshot']!=f'{market}-{unit}.csv.gz':raise ValueError('Unverified snapshot')
    frame=load_development(data/meta['snapshot'],protocol,unit,meta)
    end=protocol['train_end'] if split=='train' else protocol['validation_end']
    start=protocol['start'] if split=='train' else protocol['train_end']
    context=frame.loc[frame.index<pd.Timestamp(end)]
    context,windows=prepare(context,unit*60)
    results=[simulate(context,windows,market=market,bar_seconds=unit*60,costs=CostModel(fee,slip,protocol['full_spread_bps']),evaluation_start=start)
             for fee in protocol['fee_bps_per_side'] for slip in protocol['slippage_bps_per_side']]
    for result in results:result['source']='VERIFIED_ARCHIVED_REAL_DEVELOPMENT_PREFIX'
    return {'schema_version':1,'source':'VERIFIED_ARCHIVED_REAL_DATA','research_only':True,'split':split,
        'holdout_evaluated':False,'holdout_rows_parsed':0,'independent_validation':False,
        'production_minute1_status':'INSUFFICIENT_REAL_DATA','multi_asset_portfolio':False,'protocol_sha256':ph,
        'period_start':start,'period_end_exclusive':end,'results':results}


def markdown(report):
    lines=['# Legacy strategy offline diagnostic',
        f"Status: {report.get('status','EVALUATED')}; minute1: {report.get('production_minute1_status','INSUFFICIENT_REAL_DATA')}",
        f"Source: {report.get('source')}; split: {report.get('split')}",
        f"UTC period: [{report.get('period_start','N/A')}, {report.get('period_end_exclusive','N/A')})",
        'Exploratory; holdout untouched. 15/60-minute results do not validate minute1 live activity.']
    results=report.get('results',[])
    if not results:return '\n'.join(lines)+'\n'
    labels=[f"fee{r['costs']['fee_bps']}/slip{r['costs']['slippage_bps']}" for r in results]
    def table(title,rows):
        lines.extend(['',title,'','|Metric|'+'|'.join(labels)+'|','|---|'+'|'.join(['---:']*len(results))+'|'])
        for name,values in rows:
            def display(v):
                if v is None:return 'N/A'
                if isinstance(v,(int,float)):return str(v)
                try:return f'{Decimal(v):.6f}'
                except Exception:return str(v)
            lines.append('|'+name+'|'+'|'.join(display(v) for v in values)+'|')
    lines.extend(['',f"Market: {results[0]['market']}; minutes: {results[0]['bar_seconds']//60}; spread: {results[0]['costs']['spread_bps']}bps"])
    table('Performance (fee-addback is NOT a no-cost counterfactual)',
        [(name,[r['metrics'][name] for r in results]) for name in results[0]['metrics']]
        +[(name,[r[name] for r in results]) for name in ('fees','slippage_cost','half_spread_cost','na_reason')])
    table('Decision and confirmed fill counts',
        [(name,[r['counts'][name] for r in results]) for name in results[0]['counts']]
        +[(name,[r['fills'][name] for r in results]) for name in ('buys','sells')])
    # Predicates are observation-only and identical across the fixed cost grid.
    if any(r['conditions']!=results[0]['conditions'] for r in results):raise ValueError('Incomparable predicate diagnostics')
    lines.extend(['','Atomic predicates (overlap; do not sum as HOLD count)','','|Predicate|Met|Not met|','|---|---:|---:|'])
    for name,v in results[0]['conditions'].items():lines.append(f"|{name}|{v['met']}|{v['not_met']}|")
    lines.extend(['','First failed condition per rule (full counts retained in JSON)','','|Rule|Most frequent first failure|Count|','|---|---|---:|'])
    for name,v in results[0]['dominant_failures_by_rule'].items():lines.append(f"|{name}|{v[0] if v else 'N/A'}|{v[1] if v else 'N/A'}|")
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--market',required=True)
    parser.add_argument('--unit',type=int,required=True);parser.add_argument('--split',choices=('train','validation'),required=True)
    parser.add_argument('--format',choices=('json','markdown'),required=True)
    args=parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:report=run(args.data,args.market,args.unit,args.split)
    except (ValueError,KeyError,OSError,TypeError,EOFError):
        report={'status':'INSUFFICIENT_REAL_DATA','source':'UNVERIFIED_OR_MISSING_INPUT','network_calls':0,'results':[]}
    except Exception:
        report={'status':'HALTED_OFFLINE_VALIDATION_FAILED','source':'NOT_A_VALID_RESULT','network_calls':0,'results':[]}
    print(json.dumps(report,ensure_ascii=False,allow_nan=False,indent=2) if args.format=='json' else markdown(report))
    return 2 if report.get('status') in ('INSUFFICIENT_REAL_DATA','HALTED_OFFLINE_VALIDATION_FAILED') else 0


if __name__=='__main__':raise SystemExit(main())
