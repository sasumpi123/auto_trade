"""Read-only PAPER ledger reconciliation; never updates orders or positions."""
from decimal import Decimal, InvalidOperation
import re
from datetime import datetime, timedelta, timezone
from decimal import localcontext


def decimal(value, *, signed=False):
    if isinstance(value, bool):
        raise ValueError('invalid_metric')
    n = Decimal(str(value))
    if not n.is_finite() or (not signed and n < 0):
        raise ValueError('invalid_metric')
    return n


def close(a, b):
    return abs(a-b) <= max(Decimal('1e-20'), max(abs(a),abs(b))*Decimal('1e-24'))


def confirmed(order):
    try:
        q, funds, fee, avg = (decimal(order[k]) for k in
            ('executed_volume','funds','paid_fee','average_fill_price'))
        return (order['status']=='settled' and order['exchange_state'] in ('done','cancel')
                and order['side'] in ('bid','ask') and q>0 and funds>0 and fee<=funds
                and avg>0 and close(q*avg,funds) and bool(order['identifier'])
                and isinstance(order['ticker'],str) and bool(re.fullmatch(r'KRW-[A-Z0-9]{1,20}',order['ticker'])))
    except (KeyError,TypeError,ValueError,InvalidOperation):
        return False


class AccountingUnavailable(ValueError):
    pass


def check(condition, reason):
    if not condition:
        raise AccountingUnavailable(reason)


def reason_group(order):
    # Never forward raw reason/strategy strings or infer a strategy from indicators.
    raw=order.get('reason')
    if not isinstance(raw,str):return '기타/미기록'
    return next((tag for tag in ('물타기','손절','이익 실현','매수','매도') if tag in raw),'기타/미기록')


def replay(state, start, *, as_of, calendar):
    """Original mapping order AND strictly increasing terminal timestamps required.

    Fee-inclusive cost is independent of the engine's fee-exclusive moving average.
    No sorting, inferred initial inventory, timestamp repair or persistent writes.
    """
    check(calendar in ('UTC','KST'),'CALENDAR_UNVERIFIED')
    check(isinstance(as_of,datetime) and as_of.tzinfo is not None and as_of.utcoffset() is not None,'AS_OF_UNVERIFIED')
    zone=timezone.utc if calendar=='UTC' else timezone(timedelta(hours=9),name='KST')
    today=as_of.astimezone(zone).date().isoformat()
    wall=decimal(as_of.timestamp())
    cash=start;positions={};rows=[];daily={};groups={};strategies={};last=None
    engine_profit=Decimal(0);net_profit=Decimal(0);fees=Decimal(0)
    buy_count=sell_count=wins=0
    orders=state['orders']
    check(type(orders) is dict and len(orders)<=100000,'ORDERS_UNVERIFIED')
    for index,(identifier,o) in enumerate(orders.items(),1):
        check(o['identifier']==identifier and type(identifier) is str and 0<len(identifier)<=128
              and o['status']=='settled' and o['side'] in ('bid','ask')
              and type(o['averaging']) is bool,'ORDER_UNVERIFIED')
        ticker=o['ticker']
        check(isinstance(ticker,str) and re.fullmatch(r'KRW-[A-Z0-9]{1,20}',ticker),'MARKET_UNVERIFIED')
        stamp=decimal(o['settled_at'])
        check(stamp>0 and stamp<=wall and (last is None or stamp>last),'ORDER_SEQUENCE_UNVERIFIED')
        last=stamp
        # Integer seconds classify days exactly; fractional seconds never round into tomorrow.
        day=datetime.fromtimestamp(int(stamp),timezone.utc).astimezone(zone).date().isoformat()
        q,f,fee=(decimal(o[k]) for k in ('executed_volume','funds','paid_fee'))
        check(o['exchange_state'] in ('done','cancel'),'TERMINAL_UNVERIFIED')
        if q==0:
            check(f==fee==0 and o['average_fill_price'] is None,'ZERO_FILL_INCONSISTENT')
            continue
        check(confirmed(o),'FILL_UNVERIFIED')
        if 'value' in o:
            limit=decimal(o['value'])
            check(limit>0 and (f<=limit if o['side']=='bid' else q<=limit),'REQUEST_LIMIT_INCONSISTENT')
        pos=positions.setdefault(ticker,{'quantity':Decimal(0),'cost':Decimal(0),'engine_avg':Decimal(0),'averaged':False})
        old_q=pos['quantity'];old_cost=pos['cost'];allocated=Decimal(0);net=Decimal(0)
        if o['side']=='bid':
            cash-=f+fee
            check(cash>=0,'CASH_PATH_INCONSISTENT')
            pos['engine_avg']=(old_q*pos['engine_avg']+f)/(old_q+q)
            pos['quantity']+=q;pos['cost']+=f+fee
            pos['averaged']=pos['averaged'] or o['averaging']
            buy_count+=1
        else:
            check(q<=old_q and old_q>0,'SELL_QUANTITY_INCONSISTENT')
            check(o['averaging'] is False,'SELL_PURPOSE_INCONSISTENT')
            legacy=f-fee-q*pos['engine_avg']
            check(close(decimal(o['profit_amount'],signed=True),legacy),'ENGINE_PROFIT_INCONSISTENT')
            engine_profit+=legacy
            allocated=old_cost if q==old_q else old_cost*q/old_q
            net=f-fee-allocated
            pos['quantity']-=q;pos['cost']-=allocated;cash+=f-fee
            if pos['quantity']==0:
                pos.update(cost=Decimal(0),engine_avg=Decimal(0),averaged=False)
            net_profit+=net;sell_count+=1;wins+=int(net>0)
        fees+=fee
        d=daily.setdefault(day,{'buy_count':0,'sell_count':0,'buy_funds':Decimal(0),
            'sell_funds':Decimal(0),'fees':Decimal(0),'net_realized':Decimal(0)})
        side='buy' if o['side']=='bid' else 'sell'
        d[side+'_count']+=1;d[side+'_funds']+=f;d['fees']+=fee;d['net_realized']+=net
        reason=reason_group(o)
        strategy=o.get('strategy')
        strategy=strategy if strategy in ('RSI','MACD','BB','RSI_MACD_BB') else 'N/A (전략 ID 미기록/미검증)'
        for collection,key in ((groups,reason),(strategies,strategy)):
            g=collection.setdefault(key,{'buy_count':0,'sell_count':0,'fees':Decimal(0),'net_realized':Decimal(0)})
            g[side+'_count']+=1;g['fees']+=fee;g['net_realized']+=net
        rows.append({'sequence':index,'date':day,'ticker':ticker,'side':side,'quantity':q,
                     'funds':f,'fee':fee,'allocated_cost':allocated,'net_realized':net if side=='sell' else None,
                     'remaining_quantity':pos['quantity'],'remaining_cost':pos['cost'],
                     'remaining_average_cost':pos['cost']/pos['quantity'] if pos['quantity'] else None,
                     'averaging':o['averaging'],'reason_group':reason,'strategy_group':strategy})
    check(close(cash,decimal(state['cash'])),'CASH_INCONSISTENT')
    saved=state['positions'];check(type(saved) is dict,'POSITIONS_UNVERIFIED')
    for ticker in set(positions)|set(saved):
        check(isinstance(ticker,str) and re.fullmatch(r'KRW-[A-Z0-9]{1,20}',ticker),'MARKET_UNVERIFIED')
        p=positions.get(ticker,{'quantity':Decimal(0),'cost':Decimal(0),'engine_avg':Decimal(0),'averaged':False})
        original=saved.get(ticker,{'quantity':'0','avg_price':'0'})
        check(close(p['quantity'],decimal(original['quantity'])) and close(p['engine_avg'],decimal(original['avg_price'])),
              'POSITION_OR_BASIS_INCONSISTENT')
        if 'averaging_down_used' in original:
            check(type(original['averaging_down_used']) is bool and original['averaging_down_used']==p['averaged'],
                  'AVERAGING_STATE_INCONSISTENT')
    check(close(cash+sum((p['cost'] for p in positions.values()),Decimal(0)),start+net_profit),'NET_COST_IDENTITY_INCONSISTENT')
    for p in positions.values():p['net_average_cost']=p['cost']/p['quantity'] if p['quantity'] else None
    return {'cash':cash,'positions':positions,'rows':rows,'daily':daily,'today':today,'calendar':calendar,
            'net_profit':net_profit,'engine_profit':engine_profit,'fees':fees,'buy_count':buy_count,
            'sell_count':sell_count,'wins':wins,'reasons':groups,'strategies':strategies}


def report_metrics(state, start_cash, marks, *, now, max_age=300, verified=False, as_of=None, calendar='KST'):
    """Read-only fee-inclusive PAPER accounting. No day-start valuation is invented."""
    result={'집계 범위':'누적 확정 원장 (일간 아님)', '검증 상태':'미검증',
            '전달 상태':'로컬 보고서 생성; enqueue는 Slack 전달 성공 아님'}
    try:
        check(verified is True and state['mode']=='paper' and not state.get('halt_reason'),'LEDGER_UNVERIFIED')
        start=decimal(start_cash);check(start>0,'START_UNVERIFIED')
        with localcontext() as context:
            context.prec=28  # Engine arithmetic compatibility; no fiat/coin quantization.
            ledger=replay(state,start,as_of=as_of if as_of is not None else datetime.now(timezone.utc),calendar=calendar)
            result.update({'검증 상태':'원장 일치','시작 자산':start,'현금':ledger['cash'],
                '거래 횟수':ledger['buy_count']+ledger['sell_count'],'매수 횟수':ledger['buy_count'],
                '매도 횟수':ledger['sell_count'],'수수료 합계':ledger['fees'],'실현 손익':ledger['net_profit'],
                '승률':Decimal(ledger['wins'])*100/ledger['sell_count'] if ledger['sell_count'] else None,
                '승률 기준':'수수료 포함 양수 순손익 매도 주문 / 전체 매도 체결 주문',
                '집계 날짜':ledger['today']+' '+calendar,'engine_profit_amount_sum':ledger['engine_profit'],
                'accounting_rows':ledger['rows'],'daily':ledger['daily'],'positions':ledger['positions'],
                'reason_statistics':ledger['reasons'],'strategy_statistics':ledger['strategies']})
            day=ledger['daily'].get(ledger['today'],{'buy_count':0,'sell_count':0,'buy_funds':Decimal(0),
                'sell_funds':Decimal(0),'fees':Decimal(0),'net_realized':Decimal(0)})
            result['일간 순실현 손익']=day['net_realized'];result['일간 집계']=day
            result['일간 평가손익']='N/A (과거 평가 스냅샷 없음)'
            result['일간 총수익률']='N/A (기초 평가자산 없음)'
            holdings=[(t,p) for t,p in ledger['positions'].items() if p['quantity']>0]
            result['보유 포지션']='; '.join(f'{t}: {p["quantity"]} 코인, 순원가 {p["cost"]} KRW' for t,p in holdings) or '보유 없음'
            result['사유별 결과']='; '.join(f'{k}: 매수{v["buy_count"]}/매도{v["sell_count"]}, 매도순손익 {v["net_realized"]} KRW' for k,v in ledger['reasons'].items()) or 'N/A (체결 없음)'
            result['전략별 결과']='; '.join(f'{k}: 매수{v["buy_count"]}/매도{v["sell_count"]}, 매도순손익 {v["net_realized"]} KRW' for k,v in ledger['strategies'].items()) or 'N/A (체결 없음)'
            equity=ledger['cash']
            try:
                check(isinstance(now,(int,float)) and not isinstance(now,bool),'PRICE_CLOCK_UNVERIFIED')
                for ticker,p in holdings:
                    mark,stamp=marks[ticker];age=now-stamp
                    check(0<=age<=max_age,'PRICE_STALE_OR_FUTURE')
                    price=decimal(mark);check(price>0,'PRICE_INVALID')
                    equity+=p['quantity']*price
                remaining=sum((p['cost'] for _,p in holdings),Decimal(0))
                unrealized=equity-ledger['cash']-remaining
                check(close(ledger['net_profit']+unrealized,equity-start),'VALUATION_IDENTITY_UNVERIFIED')
                result.update({'종료 평가자산':equity,'평가 손익':unrealized,'누적 수익률':(equity/start-1)*100})
            except (LookupError,ValueError,TypeError,ArithmeticError):
                result['평가 제한']='N/A: 최신 가격 근거 없음/평가 불일치'
            return result
    except (LookupError,ValueError,TypeError,ArithmeticError,AttributeError) as error:
        reason=str(error) if isinstance(error,AccountingUnavailable) else 'REQUIRED_EVIDENCE_UNVERIFIED'
        return {'집계 범위':'누적 확정 원장 (일간 아님)','검증 상태':'N/A: 원장/시작 자산/복구 상태 미검증',
                '정산 사유':reason,'전달 상태':'로컬 보고서 생성; enqueue는 Slack 전달 성공 아님'}
