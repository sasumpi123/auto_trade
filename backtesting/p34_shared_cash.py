"""P34 offline check: two markets in ONE PAPER account falling together (shared cash, averaging, stop).

Run from the repository root:
    python -m backtesting.p34_shared_cash --output NEW_PRIVATE_DIRECTORY

Real code paths only: trading.auto_trade.AutoTrade (risk priority, one 50% averaging at -5%, stop,
MAX_COINS_AT_ONCE=2, 40% per market) on the disposable research P17 engine (5/5/2 bps, fee-inclusive
budget, minimum order) and the P9 replay (services.notification_metrics.replay). Only the FIRST entry is a
scripted BUY signal; every later decision comes from the unchanged bot. Fixed synthetic prices, a new
temporary account per run, no network, no existing/real account, no P19/P22/P33 data. Descriptive only:
no new limit or policy is applied.
"""
import argparse
import csv
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from . import p19_baseline as base
from services.notification_metrics import close,replay
from trading.auto_trade import AutoTrade
from trading.costs import CostModel
from trading.execution import number

SCHEMA='P34_SHARED_CASH_V1'
INITIAL=Decimal('1000000')
BTC,XRP='KRW-BTC','KRW-XRP'
ENTRY={BTC:Decimal('100000000'),XRP:Decimal('1000')}
# Fixed synthetic paths (multipliers of the entry price), applied to both markets at the same step.
SCENARIOS={
    'drop_then_further_drop':[Decimal('0.945'),Decimal('0.888')],   # -5.5% then a further fall below the averaged stop line.
    'gap_down_then_recover':[Decimal('0.85'),Decimal('0.90')],        # -15% gap (past the -5% line), then a partial rebound.
    'drop_then_diverge':[Decimal('0.945'),{BTC:Decimal('1.00'),XRP:Decimal('0.90')}],  # BTC recovers, XRP keeps falling.
}
ORDERS={'btc_first':(BTC,XRP),'xrp_first':(XRP,BTC)}
EPOCH=1_800_000_000


class ScriptedEntry:
    """Analyzer stand-in: one BUY on the first strategy call, HOLD afterwards. Risk exits never reach it."""
    def __init__(self,ticker):
        self.ticker=ticker;self.calls=0;self.last_signal=None;self.last_signal_time=None
    def analyze(self):
        self.calls+=1
        if self.calls==1:
            self.last_signal='BUY';self.last_signal_time=EPOCH
            return {'action':'BUY','reason':'P34 scripted first entry','target_price':None}
        return {'action':'HOLD','reason':None,'target_price':None}


def snapshot(bot,marks):
    cash=bot.engine.cash;rows={};held=Decimal(0)
    for m in (BTC,XRP):
        p=bot.engine.position(m);q=number(p['quantity']);value=q*marks[m] if m in marks else Decimal(0)
        held+=value;rows[m]={'quantity':str(q),'avg_price_fee_exclusive':p['avg_price'],
            'averaging_down_used':p['averaging_down_used'],'mark':str(marks.get(m,'0')),'marked_value':str(value)}
    equity=cash+held
    return {'cash':str(cash),'positions':rows,'marked_equity':str(equity),'total_exposure_value':str(held),
        'total_exposure_ratio':str(held/equity) if equity else None}


def run(order,scenario,folder):
    """One fixed path in a brand-new disposable account; returns the step table and the P9 reconciliation."""
    folder=Path(folder);folder.mkdir(mode=0o700)
    clock=[EPOCH]
    engine=base.P17ResearchEngine(folder/'unused.json',1000000,CostModel(5,5,2),lambda:clock[0])
    bot=AutoTrade(start_cash=1000000,tickers=list(order),state_path=folder/'paper.json',real_trading=False,
        notification=None,analyzer_factory=ScriptedEntry)
    bot.engine=engine;bot.initialize();bot.max_per_coin=INITIAL*Decimal('0.4')
    steps=[];marks={}
    try:
        plan=[(f'entry {m}',m,ENTRY[m]) for m in order]
        for i,mult in enumerate(SCENARIOS[scenario],1):
            plan+=[(f'step{i} x{mult[m] if isinstance(mult,dict) else mult} {m}',m,ENTRY[m]*(mult[m] if isinstance(mult,dict) else mult)) for m in order]
        seen=set()
        for label,market,price in plan:
            clock[0]+=60;marks[market]=price;before=snapshot(bot,marks)
            with patch('trading.auto_trade.time',type('T',(),{'time':staticmethod(lambda:clock[0]),'monotonic':staticmethod(time.monotonic)})):
                result=bot.process_market_data(market,{'trade_price':str(price)})
            new=[o for k,o in bot.engine.state['orders'].items() if k not in seen];seen|=set(bot.engine.state['orders'])
            events=[{'side':'BUY' if o['side']=='bid' else 'SELL','market':o['ticker'],'reason':o.get('reason'),
                'averaging':o.get('averaging'),'status':o.get('status'),'funds':o.get('funds'),'fee':o.get('paid_fee'),
                'executed_volume':o.get('executed_volume'),'fill_price':o.get('average_fill_price')} for o in new]
            steps.append({'step':label,'market':market,'price':str(price),'result':result if isinstance(result,bool) else None,
                'before':before,'after':snapshot(bot,marks),'orders':events})
        state=json.loads(json.dumps(bot.engine.state))
        ledger=replay(state,INITIAL,as_of=__import__('datetime').datetime.fromtimestamp(clock[0]+60,tz=__import__('datetime').timezone.utc),calendar='UTC')
    finally:bot.stop()
    rec=reconcile(state,ledger,marks)
    low=min(number(s['after']['cash']) for s in steps)  # Peak deployment: every KRW not in cash is at risk.
    rec['theoretical_max_account_loss_at_peak_exposure']=str(INITIAL-low);rec['min_cash_during_run']=str(low)
    rec['realized_and_marked_synthetic_loss']=str(-Decimal(rec['total_pnl']))
    return steps,rec


def reconcile(state,ledger,marks):
    """P9 replay vs engine state; marked identity without double-deducting fees already in cash."""
    cash=number(state['cash']);engine_pos={m:number(p['quantity']) for m,p in state['positions'].items()}
    fills=[o for o in state['orders'].values() if o.get('status')=='settled']
    fees=sum((number(o['paid_fee']) for o in fills),Decimal(0))
    base.require(close(Decimal(ledger['cash']),cash),'P34_P9_CASH_MISMATCH')
    base.require(close(Decimal(ledger['fees']),fees),'P34_P9_FEE_MISMATCH')
    remaining={m:Decimal(0) for m in (BTC,XRP)};cost=Decimal(0)
    for m,p in ledger['positions'].items():
        remaining[m]=Decimal(p['quantity']);cost+=Decimal(p['cost'])
        base.require(close(remaining[m],engine_pos.get(m,Decimal(0))),'P34_P9_POSITION_MISMATCH')
    marked=sum((remaining[m]*marks[m] for m in remaining),Decimal(0));unrealized=marked-cost
    net=Decimal(ledger['net_profit'])
    base.require(close(cash+marked-INITIAL,net+unrealized),'P34_P9_EQUITY_IDENTITY')
    return {'p9_verified':True,'cash':str(cash),'fees_total':str(fees),'net_realized_pnl':str(net),'remaining_cost_fee_inclusive':str(cost),
        'unrealized_pnl':str(unrealized),'marked_equity':str(cash+marked),'total_pnl':str(cash+marked-INITIAL),
        'buys':ledger['buy_count'],'sells':ledger['sell_count'],'remaining_quantity':{m:str(q) for m,q in remaining.items()},
        'end_loss_if_remaining_marks_go_to_zero':str(INITIAL-cash)}


def analyze(output):
    out=base.fresh(output);report={'schema':SCHEMA,'status':'INCOMPLETE','network_calls':0,'initial_cash':str(INITIAL),
        'costs_bps':{'fee':'5','slippage':'5','full_spread':'2'},'entry_prices':{m:str(p) for m,p in ENTRY.items()},
        'scenarios':{k:[({m:str(x) for m,x in v.items()} if isinstance(v,dict) else str(v)) for v in vals] for k,vals in SCENARIOS.items()},'runs':[],
        'interpretation':'Synthetic fixed paths only; describes the CURRENT policy; applies no new limit; not a forecast.'}
    base.write_json(out/'results.json',report)
    with tempfile.TemporaryDirectory(prefix='p34-') as temp:
        temp=Path(temp).resolve()
        for scenario in SCENARIOS:
            for name,order in ORDERS.items():
                steps,rec=run(order,scenario,temp/f'{scenario}-{name}')
                report['runs'].append({'scenario':scenario,'order':name,'processing_order':list(order),'steps':steps,'p9':rec})
                base.write_json(out/'results.json',report)
    report['status']='VERIFIED';base.write_json(out/'results.json',report);write_table(out,report);(out/'INCOMPLETE').unlink()
    return out,report


def write_table(out,report):
    fields=['scenario','order','step','price','orders','cash_before','cash_after','btc_qty_after','xrp_qty_after','equity_after','exposure_after']
    with (out/'steps.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
        for r in report['runs']:
            for s in r['steps']:
                w.writerow({'scenario':r['scenario'],'order':r['order'],'step':s['step'],'price':s['price'],
                    'orders':'; '.join(f"{e['side']} {e['market']} {e['reason']} fee={e['fee']}" for e in s['orders']) or '-',
                    'cash_before':s['before']['cash'],'cash_after':s['after']['cash'],
                    'btc_qty_after':s['after']['positions'][BTC]['quantity'],'xrp_qty_after':s['after']['positions'][XRP]['quantity'],
                    'equity_after':s['after']['marked_equity'],'exposure_after':s['after']['total_exposure_ratio']})


def outcome(r):
    def who(reason):return sorted({e['market'] for s in r['steps'] for e in s['orders'] if e['reason']==reason})
    return {'averaged':who('물타기 매수'),'stopped':who('손절'),'took_profit':who('이익 실현 (5%)')}


def summary(report):
    lines=[f"P34 {report['status']} runs={len(report['runs'])} network=0"]
    for r in report['runs']:
        o=outcome(r);p=r['p9']
        lines.append(f"{r['scenario']} {r['order']}: avg={','.join(o['averaged']) or '-'} stop={','.join(o['stopped']) or '-'} "
            f"cash={Decimal(p['cash']):.0f} equity={Decimal(p['marked_equity']):.0f} pnl={Decimal(p['total_pnl']):.0f} fees={Decimal(p['fees_total']):.0f}")
    lines.append('Synthetic paths; current policy only; no new limit applied; details in results.json/steps.csv.')
    return lines[:8]


def main(argv=None):
    parser=argparse.ArgumentParser(description='P34 two-market shared-cash check (offline, synthetic)')
    parser.add_argument('--output',required=True);a=parser.parse_args(argv)
    try:_,report=analyze(a.output)
    except Exception as exc:
        reason=str(exc) if __import__('re').fullmatch(r'[A-Z][A-Z0-9_]{0,79}',str(exc)) else 'P34_UNVERIFIED'
        print(f'P34 INCOMPLETE exit=2 reason={reason}');return 2
    for line in summary(report):print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
