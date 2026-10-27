"""Read-only attribution of disposable research fills; P9 remains accounting authority."""
from decimal import Decimal

import pandas as pd

from .engine import serializable
from services.notification_metrics import replay,close,check,decimal

ZERO=Decimal(0)


def reason(order):
    # Allowlisted categories only; raw strategy reasons/ledger IDs never leave this layer.
    if order['side']=='bid':
        return 'AVERAGING_DOWN' if order['averaging'] else 'STRATEGY_BUY'
    raw=order.get('reason')
    if raw=='손절':return 'STOP_LOSS'
    if raw=='이익 실현 (5%)':return 'TAKE_PROFIT'
    return 'STRATEGY_SELL' if isinstance(raw,str) and raw else 'UNKNOWN'


def components(order):
    raw=order.get('reason')
    if not isinstance(raw,str):return []
    return [tag for tag in ('RSI','MACD','BB','consensus','복합') if tag in raw]


def fill_detail(order,reference,costs,signal_at):
    q=decimal(order['executed_volume']);reference=decimal(reference)
    code=reason(order)
    signal_at=signal_at if code in ('STRATEGY_BUY','STRATEGY_SELL') else None
    return {'side':order['side'],'executed_at':order['settled_at'],'signal_at':signal_at,
        'reference_price':reference,'price':decimal(order['average_fill_price']),
        'quantity':q,'funds':decimal(order['funds']),'fee':decimal(order['paid_fee']),
        'slippage_cost':q*reference*costs.slippage_bps/10000,
        'half_spread_cost':q*reference*costs.spread_bps/20000,'reason':reason(order),'signal_components':components(order)}


def stamp(value):return pd.Timestamp(float(value),unit='s',tz='UTC').isoformat()
def mean(values):return sum(values,ZERO)/len(values) if values else None


def attribute(state,fills,initial_cash,*,as_of,mark,exposure):
    """Moving-average cost allocation including partial sells and averaging.

    Entry lots are proportional accounting allocations, not FIFO or causal P&L claims.
    Only a fresh, single-asset simulation state is accepted, never an operational path.
    """
    ledger=replay(state,initial_cash,as_of=as_of.to_pydatetime(),calendar='UTC')
    rows=ledger['rows'];check(len(rows)==len(fills),'ATTRIBUTION_FILL_COUNT')
    check(len(ledger['positions'])<=1,'ATTRIBUTION_SINGLE_ASSET_REQUIRED')
    lots=[];events=[];sales=[];groups={};streak=longest=0
    for seq,(r,f) in enumerate(zip(rows,fills),1):
        q=decimal(f['quantity']);price=decimal(f['price']);ref=decimal(f['reference_price'])
        fee=decimal(f['fee']);slip=decimal(f['slippage_cost']);spread=decimal(f['half_spread_cost'])
        check(q>0 and price>0 and ref>0 and close(q,r['quantity']) and close(fee,r['fee'])
            and close(decimal(f['funds']),r['funds']) and close(price*q,r['funds'])
            and f['side']==('bid' if r['side']=='buy' else 'ask'),'ATTRIBUTION_FILL_MISMATCH')
        check(f['reason'] in ('STRATEGY_BUY','AVERAGING_DOWN','STOP_LOSS','TAKE_PROFIT','STRATEGY_SELL','UNKNOWN'),'ATTRIBUTION_REASON')
        signed=r['funds']-q*ref if f['side']=='bid' else q*ref-r['funds']
        check(close(signed,slip+spread),'ATTRIBUTION_PRICE_COST_MISMATCH')
        # Verify time against the authoritative insertion-ordered terminal receipt, not a supplied trace alone.
        order=list(state['orders'].values())[r['sequence']-1]
        check(f['executed_at']==order['settled_at'] and f['reason']==reason(order) and f['signal_components']==components(order),'ATTRIBUTION_TIME_OR_REASON')
        if f['signal_at'] is not None:check(f['signal_at']<f['executed_at'],'ATTRIBUTION_LOOKAHEAD')
        event={'sequence':seq,'side':r['side'],'executed_at_utc':stamp(f['executed_at']),
            'signal_candle_open_utc':stamp(f['signal_at']) if f['signal_at'] is not None else None,
            'reason':f['reason'],'signal_components':f['signal_components'],'quantity':q,'price':price,'reference_price':ref,'funds':r['funds'],
            'fee':fee,'slippage_cost':slip,'half_spread_cost':spread,'net_realized_pnl':r['net_realized']}
        events.append(event)
        if f['side']=='bid':
            lots.append({'quantity':q,'funds':r['funds'],'reference_funds':q*ref,'fee':fee,
                'slip':slip,'spread':spread,'time':decimal(f['executed_at']),'reason':f['reason'],'components':f['signal_components']})
            continue
        old_q=sum((lot['quantity'] for lot in lots),ZERO)
        check(old_q>=q and old_q>0,'ATTRIBUTION_INVENTORY')
        ratio=q/old_q
        allocated={k:sum((lot[k]*ratio for lot in lots),ZERO) for k in ('funds','reference_funds','fee','slip','spread')}
        allocated_cost=allocated['funds']+allocated['fee']
        check(close(allocated_cost,r['allocated_cost']),'ATTRIBUTION_COST_ALLOCATION')
        first=min(lot['time'] for lot in lots);last=max(lot['time'] for lot in lots)
        weighted_time=sum((lot['time']*lot['quantity']/old_q for lot in lots),ZERO)
        reason_qty={tag:sum((lot['quantity']*ratio for lot in lots if lot['reason']==tag),ZERO)
            for tag in sorted({lot['reason'] for lot in lots})}
        gross=q*ref-allocated['reference_funds']
        net=r['net_realized']
        check(close(gross-allocated['fee']-fee-allocated['slip']-slip-allocated['spread']-spread,net),'ATTRIBUTION_NET_IDENTITY')
        sale={'sequence':seq,'entry_first_at_utc':stamp(first),'entry_last_at_utc':stamp(last),
            'exit_at_utc':stamp(f['executed_at']),'entry_reasons':reason_qty,'exit_reason':f['reason'],
            'entry_signals':[{'at_utc':stamp(lot['time']),'reason':lot['reason'],'signal_components':lot['components'],
                'allocated_quantity':lot['quantity']*ratio} for lot in lots],
            'entry_average_price':allocated['funds']/q,'entry_fee_inclusive_cost':allocated_cost,
            'exit_price':price,'exit_reference_price':ref,'quantity':q,
            'holding_seconds_since_first_entry':decimal(f['executed_at'])-first,
            'quantity_weighted_holding_seconds':decimal(f['executed_at'])-weighted_time,
            'gross_reference_price_pnl':gross,'allocated_buy_fee':allocated['fee'],'sell_fee':fee,
            'allocated_buy_slippage':allocated['slip'],'sell_slippage':slip,
            'allocated_buy_half_spread':allocated['spread'],'sell_half_spread':spread,'net_realized_pnl':net,
            'had_averaging_down':reason_qty.get('AVERAGING_DOWN',ZERO)>0}
        sales.append(sale)
        g=groups.setdefault(f['reason'],{'sells':0,'wins':0,'net_realized_pnl':ZERO,'holding_seconds_sum':ZERO})
        g['sells']+=1;g['wins']+=int(net>0);g['net_realized_pnl']+=net
        g['holding_seconds_sum']+=sale['quantity_weighted_holding_seconds']
        streak=streak+1 if net<0 else 0;longest=max(longest,streak)
        for lot in lots:
            for k in ('quantity','funds','reference_funds','fee','slip','spread'):lot[k]*=1-ratio
        if q==old_q:lots=[]
    net=sum((s['net_realized_pnl'] for s in sales),ZERO)
    check(close(net,ledger['net_profit']),'ATTRIBUTION_REALIZED_IDENTITY')
    mark=decimal(mark);check(mark>0,'ATTRIBUTION_MARK')
    remaining_q=sum((l['quantity'] for l in lots),ZERO)
    remaining_cost=sum((l['funds']+l['fee'] for l in lots),ZERO)
    check(close(remaining_q,sum((p['quantity'] for p in ledger['positions'].values()),ZERO))
        and close(remaining_cost,sum((p['cost'] for p in ledger['positions'].values()),ZERO)),'ATTRIBUTION_REMAINING_IDENTITY')
    unrealized=remaining_q*mark-remaining_cost
    check(close(ledger['cash']+remaining_q*mark-initial_cash,net+unrealized),'ATTRIBUTION_EQUITY_IDENTITY')
    held=duration=notional_seconds=ZERO;previous=None
    for index,e in enumerate(exposure):
        t=decimal(e['timestamp']);until=decimal(exposure[index+1]['timestamp']) if index+1<len(exposure) else decimal(as_of.timestamp())
        check((previous is None or t>previous) and until>t,'ATTRIBUTION_EXPOSURE_TIME')
        span=until-t;quantity=decimal(e['quantity']);reference=decimal(e['reference_price'])
        duration+=span;held+=span if quantity>0 else ZERO
        notional_seconds+=quantity*reference*span;previous=t
    wins=[s for s in sales if s['net_realized_pnl']>0];losses=[s for s in sales if s['net_realized_pnl']<0]
    def subset(items):return {'count':len(items),'mean_net_pnl':mean([s['net_realized_pnl'] for s in items]),
        'mean_holding_seconds':mean([s['quantity_weighted_holding_seconds'] for s in items])}
    return serializable({'verified':True,'allocation':'P9_FEE_INCLUSIVE_MOVING_AVERAGE_PROPORTIONAL_ENTRY_LOTS',
        'events':events,'closed_sales':sales,'exit_reasons':groups,
        'averaging':{'buys':sum(f['reason']=='AVERAGING_DOWN' for f in fills),
            'sells_after_averaging':sum(s['had_averaging_down'] for s in sales),
            'net_realized_after_averaging':sum((s['net_realized_pnl'] for s in sales if s['had_averaging_down']),ZERO),
            'causal_effect':'UNKNOWN_NO_POLICY_COUNTERFACTUAL'},
        'wins':subset(wins),'losses':subset(losses),'breakeven_sells':sum(s['net_realized_pnl']==0 for s in sales),
        'expectancy_per_sell':net/len(sales) if sales else None,'max_consecutive_losing_sells':longest if sales else None,
        'net_realized_pnl':net,'unrealized_pnl':unrealized,'open_position':{'quantity':remaining_q,'remaining_cost':remaining_cost,
            'mark_price':mark,'mark_at_utc':as_of.isoformat(),'valuation':'LAST_CANDLE_CLOSE_NO_HYPOTHETICAL_EXIT_COST',
            'entry_first_at_utc':stamp(lots[0]['time']) if lots else None},
        'cost_totals':{'buy_fees':sum((decimal(f['fee']) for f in fills if f['side']=='bid'),ZERO),
            'sell_fees':sum((decimal(f['fee']) for f in fills if f['side']=='ask'),ZERO),
            'slippage':sum((decimal(f['slippage_cost']) for f in fills),ZERO),
            'half_spread':sum((decimal(f['half_spread_cost']) for f in fills),ZERO)},
        'exposure':{'evaluated_seconds':duration,'position_held_seconds':held,
            'position_held_time_pct':held/duration*100 if duration else None,
            'mean_marked_notional_over_initial_pct':notional_seconds/duration/initial_cash*100 if duration else None,
            'buy_hold_40_time_pct':100 if duration else None,'buy_hold_initial_allocation_pct':40,
            'scope':'NEXT_OPEN_TO_FINAL_CANDLE_CLOSE; NOT_INTRABAR_OR_RISK_ADJUSTED'}})
