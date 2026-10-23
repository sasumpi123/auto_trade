"""Read-only PAPER ledger reconciliation; never updates orders or positions."""
from decimal import Decimal, InvalidOperation
import re


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


def report_metrics(state, start_cash, marks, *, now, max_age=300, verified=False):
    """Net performance only for complete reconciled zero-fee PAPER history.

    marks: ticker -> (Decimal price, monotonic observed_at); never price_cache floats.
    Nonzero fees are displayed exactly, but allocated-cost P/L is deliberately N/A.
    """
    result = {'집계 범위':'누적 확정 원장 (일간 아님)', '검증 상태':'미검증',
              '전달 상태':'로컬 보고서 생성; enqueue는 Slack 전달 성공 아님'}
    try:
        if verified is not True or state['mode']!='paper' or state.get('halt_reason'):
            raise ValueError('ledger_unverified')
        start, cash = decimal(start_cash), decimal(state['cash'])
        if start<=0:
            raise ValueError('start_unverified')
        orders=state['orders']; positions=state['positions']
        qty={}; buys=Decimal(0); sells=Decimal(0); fees=Decimal(0); profits=[]; count=0
        for identifier, order in orders.items():
            if (order['identifier']!=identifier or order['status']!='settled'
                or order['side'] not in ('bid','ask') or not order['ticker'].startswith('KRW-')):
                raise ValueError('unresolved_order')
            q=decimal(order['executed_volume']); f=decimal(order['funds']); fee=decimal(order['paid_fee'])
            if q==0:
                if f or fee or order['exchange_state'] not in ('done','cancel'):
                    raise ValueError('zero_fill_inconsistent')
                continue  # settled cancellation is NOT a trade
            if not confirmed(order):
                raise ValueError('fill_unverified')
            ticker=order['ticker'];qty[ticker]=qty.get(ticker,Decimal(0))+(q if order['side']=='bid' else -q)
            count+=1; fees+=fee
            if order['side']=='bid':buys+=f
            else:
                sells+=f
                profits.append(decimal(order['profit_amount'],signed=True))
        if not close(start-buys+sells-fees,cash):
            raise ValueError('cash_mismatch')
        basis=Decimal(0)
        for ticker in set(qty)|set(positions):
            if not re.fullmatch(r'KRW-[A-Z0-9]{1,20}',ticker):raise ValueError('market_unverified')
            pos=positions.get(ticker,{'quantity':'0','avg_price':'0'})
            quantity=decimal(pos['quantity']);avg=decimal(pos['avg_price'])
            if not close(qty.get(ticker,Decimal(0)),quantity) or quantity and avg<=0:
                raise ValueError('position_mismatch')
            basis+=quantity*avg
        # Current engine basis excludes buy fees; validate that exact definition first.
        realized=sum(profits,Decimal(0))
        sellfees=sum((decimal(o['paid_fee']) for o in orders.values() if o['side']=='ask'),Decimal(0))
        if not close(buys-sells+realized+sellfees,basis):
            raise ValueError('basis_mismatch')
        result.update({'시작 자산':start,'현금':cash,'거래 횟수':count,'수수료 합계':fees,
                       '검증 상태':'원장 일치','승률 기준':'양수 손익 매도 체결 주문 / 전체 매도 체결 주문 (포지션/개별 fill 횟수 아님)'})
        # Valid positions are numeric metadata only; no raw account/strategy data.
        result['보유 포지션']='; '.join(f'{t}: {p["quantity"]} 코인, 평균가 {p["avg_price"]} KRW/코인' for t,p in positions.items()) or '보유 없음'
        if fees==0:
            result['실현 손익']=realized
            result['승률']=Decimal(sum(p>0 for p in profits))*100/len(profits) if profits else None
        else:
            result['손익 제한']='N/A: 매수 수수료 원가배분 미검증'
        equity=cash
        for ticker,pos in positions.items():
            q=decimal(pos['quantity'])
            if not q:continue
            mark,stamp=marks[ticker]
            age=now-stamp
            if not 0<=age<=max_age:raise LookupError('price_stale')
            price=decimal(mark)
            if price<=0:raise LookupError('price_invalid')
            equity+=q*price
        result['종료 평가자산']=equity
        result['누적 수익률']=(equity/start-1)*100
        if fees==0:
            result['평가 손익']=equity-cash-basis
        return result
    except (LookupError,TypeError,ValueError,InvalidOperation,ArithmeticError):
        if result['검증 상태']=='원장 일치':
            result['평가 제한']='N/A: 최신 가격 근거 없음'
            return result
        return {'집계 범위':'누적 확정 원장 (일간 아님)', '검증 상태':'N/A: 원장/시작 자산/복구 상태 미검증',
                '전달 상태':'로컬 보고서 생성; enqueue는 Slack 전달 성공 아님'}
