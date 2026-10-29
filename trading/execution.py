"""Fail-closed execution. No HTTP imports; paper execution never constructs a broker."""
from decimal import Decimal, InvalidOperation
from copy import deepcopy
import logging
import threading
import time
import uuid

from .state_store import StateStore
from .costs import from_policy


class TradingHalted(RuntimeError):
    pass


def number(value, *, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('Invalid numeric value') from exc
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise ValueError('Expected a finite non-negative number')
    return result


class ExecutionEngine:
    def __init__(self, state_path, start_cash, *, broker=None, real_trading=False,
                 poll_attempts=10, poll_delay=0.5, sleep=time.sleep):
        if real_trading and broker is None:
            raise ValueError('A broker is required for real execution')
        if not real_trading and broker is not None:
            raise ValueError('Paper mode must not receive a real broker')
        self._paper_cost_policy=None
        self._paper_costs=None
        self.real_trading = real_trading
        self.broker = broker
        self.store = StateStore(state_path)
        self.poll_attempts = poll_attempts
        self.poll_delay = poll_delay
        self.sleep = sleep
        self.ready = False
        self.halted = False
        self._mutex = threading.RLock()
        self._journal_writable = False
        self.state = {
            'version': 1, 'mode': 'real' if real_trading else 'paper',
            'account': broker.identity if real_trading else 'paper',
            'cash': str(number(start_cash, positive=True)), 'positions': {},
            'orders': {}, 'signals': {}, 'halt_reason': None,
        }

    @property
    def cash(self):
        return number(self.state['cash'])

    def position(self, ticker):
        return self.state['positions'].get(ticker, {
            'quantity': '0', 'avg_price': '0', 'averaging_down_used': False,
        })

    def _validate(self, saved):
        for key in ('version', 'mode', 'account'):
            if saved[key] != self.state[key]:
                raise ValueError('State version, mode or account mismatch')
        number(saved['cash'])
        for ticker, pos in saved['positions'].items():
            if not ticker.startswith('KRW-'):
                raise ValueError('Unsupported saved market')
            quantity = number(pos['quantity'])
            number(pos['avg_price'], positive=quantity > 0)
            if type(pos['averaging_down_used']) is not bool:
                raise ValueError('Invalid averaging state')
        if not isinstance(saved['orders'], dict) or not isinstance(saved['signals'], dict):
            raise ValueError('Invalid journal')
        for identifier, order in saved['orders'].items():
            if order['identifier'] != identifier or order['side'] not in ('bid', 'ask'):
                raise ValueError('Invalid order journal')
            if not order['ticker'].startswith('KRW-') or type(order['averaging']) is not bool:
                raise ValueError('Invalid saved order market or purpose')
            if order['uuid'] is not None and not isinstance(order['uuid'], str):
                raise ValueError('Invalid saved order UUID')
            if order['status'] not in ('intent', 'submitted', 'settled'):
                raise ValueError('Invalid order status')
            number(order['value'], positive=True)
        for signal in saved['signals'].values():
            if signal['action'] not in ('BUY', 'SELL'):
                raise ValueError('Invalid saved signal')
            number(signal['timestamp'], positive=True)
        if sum(o['status'] != 'settled' for o in saved['orders'].values()) > 1:
            raise ValueError('Multiple unresolved orders require manual reconciliation')

    def open(self):
        with self._mutex:
            if self.ready or self.halted:
                raise TradingHalted('Engine cannot be opened twice; create a new instance')
            self.store.acquire()
            recovery_checkpoint = None
            try:
                saved = self.store.load()
                if saved is not None:
                    self._validate(saved)
                    if 'manual_recovery' in saved:
                        # Audit completion is not permission to restart. Reject BEFORE
                        # assigning/writing state, including the legacy main.py path.
                        raise TradingHalted('Manual PAPER recovery requires separate restart approval')
                    if 'paper_experiment' in saved:
                        from ops.paper_experiment import validate_start
                        token=getattr(self,'_experiment_session',None)
                        validate_start(saved,self.store.path,token)
                        costs=saved['paper_experiment'].get('cost_policy')
                        if costs is not None:
                            if self.real_trading:raise ValueError('PAPER_COSTS_ONLY')
                            self._paper_costs=from_policy(costs);self._paper_cost_policy=deepcopy(costs)
                        saved['paper_experiment']['last_session_id']=token
                    self.state = saved
                self._journal_writable = True
                pending = [o for o in self.state['orders'].values() if o['status'] != 'settled']
                if pending:
                    recovery_checkpoint = deepcopy(self.state)
                if self.real_trading:
                    # Recover by UUID/identifier only. Never submit from recovery.
                    for order in pending:
                        receipt = self._wait_for_terminal(order)
                        self._apply_fill(order, receipt)
                    expected = {o['ticker']: number(self.position(o['ticker'])['quantity']) for o in pending}
                    self._restore_accounts()
                    if any(abs(number(self.position(t)['quantity']) - qty) > Decimal('0.00000001')
                           for t, qty in expected.items()):
                        raise TradingHalted('Recovered fills and account balances disagree; retry reads later')
                    open_orders = self.broker.open_orders()
                    if not isinstance(open_orders, list):
                        raise ValueError('Invalid open orders response')
                    self.state['external_open_orders'] = open_orders
                    if open_orders:
                        raise TradingHalted('Unresolved account orders; inspect/cancel manually')
                    if self.state.get('locked_assets'):
                        raise TradingHalted('Locked assets without visible open orders; reconciliation incomplete')
                elif pending:
                    raise TradingHalted('Unresolved paper journal; inspect before restarting')
                self.state['halt_reason'] = None
                self.store.save(self.state)
                self.ready = True
            except Exception as exc:
                if recovery_checkpoint is not None:
                    # Never settle a recovered order against a stale/failed account snapshot.
                    self.state = recovery_checkpoint
                self.halt(exc)
                self.store.close()
                raise TradingHalted(str(exc)) from exc
        return self

    def _restore_accounts(self):
        balances = self.broker.balances()
        if not isinstance(balances, list):
            raise ValueError('Invalid account response')
        positions = {}
        seen = set()
        cash = None
        locked_assets = {}
        for item in balances:
            currency = item['currency']
            if currency in seen:
                raise ValueError('Duplicate balance row')
            seen.add(currency)
            available = number(item['balance'])
            locked = number(item['locked'])
            if locked > 0:
                locked_assets[currency] = str(locked)
            quantity = available + locked
            if currency == 'KRW':
                cash = available
                continue
            if quantity == 0:
                continue
            if item['unit_currency'] != 'KRW':
                raise TradingHalted('Non-KRW asset requires manual reconciliation')
            ticker = 'KRW-' + currency
            old = self.position(ticker)
            positions[ticker] = {
                'quantity': str(quantity),
                'avg_price': str(number(item['avg_buy_price'], positive=True)),
                # Unknown history must not grant another averaging-down allowance.
                'averaging_down_used': old['averaging_down_used'] if number(old['quantity']) > 0 else True,
            }
        if cash is None:
            raise ValueError('KRW balance missing')
        self.state['cash'] = str(cash)
        self.state['positions'] = positions
        self.state['locked_assets'] = locked_assets

    def _ensure_ready(self):
        if not self.ready or self.halted:
            raise TradingHalted(self.state.get('halt_reason') or 'Engine is not ready')
        if self.state.get('paper_experiment',{}).get('cost_policy')!=self._paper_cost_policy:
            raise TradingHalted('PAPER_COST_POLICY_MISMATCH')
        if any(o['status'] != 'settled' for o in self.state['orders'].values()):
            raise TradingHalted('An unresolved order blocks all new orders')

    def halt(self, reason):
        self.halted = True
        self.ready = False
        self.state['halt_reason'] = str(reason)
        logging.error('Trading halted; raw reason withheld. Inspect the private journal before restart.')
        try:
            if self.store._lock is not None and self._journal_writable:
                self.store.save(self.state)
        except Exception:
            logging.error('Could not persist halt; last durable journal must be reconciled; raw error withheld.')

    def close(self):
        with self._mutex:
            self.ready = False
            self.store.close()

    def save_signal(self, ticker, action, timestamp):
        with self._mutex:
            try:
                self._ensure_ready()
                if action not in ('BUY', 'SELL'):
                    raise ValueError('Invalid signal')
                number(timestamp, positive=True)
                self.state['signals'][ticker] = {'action': action, 'timestamp': timestamp}
                self.store.save(self.state)
            except Exception as exc:
                self.halt(exc)
                raise TradingHalted(str(exc)) from exc

    def buy(self, ticker, price, amount, *, averaging=False, reason=None):
        return self._execute('bid', ticker, price, amount, averaging=averaging, reason=reason)

    def sell(self, ticker, price, *, reason=None):
        return self._execute('ask', ticker, price, None, reason=reason)

    def _execute(self, side, ticker, price, value, *, averaging=False, reason=None):
        with self._mutex:
            self._ensure_ready()
            try:
                if not ticker.startswith('KRW-'):
                    raise ValueError('Only KRW spot markets are supported')
                price = number(price, positive=True)
                pos = self.position(ticker)
                quantity = number(pos['quantity'])
                if side == 'bid':
                    if averaging and (quantity == 0 or pos['averaging_down_used']):
                        return False
                    if quantity > 0 and not averaging:
                        return False
                    value = number(value, positive=True)
                    if value > self.cash:
                        return False
                    if not self.real_trading and self._paper_cost_policy is not None:
                        planned_funds=value/(1+self._paper_costs.fee_rate)
                        planned_fee=planned_funds*self._paper_costs.fee_rate
                        # Reserve fees and apply the existing Decimal cap correction
                        # before minimum-notional validation and durable intent.
                        if planned_funds+planned_fee>value or self.cash-planned_funds-planned_fee<0:
                            planned_funds=planned_funds.next_minus()
                            planned_fee=planned_funds*self._paper_costs.fee_rate
                        if planned_funds+planned_fee>value or self.cash-planned_funds-planned_fee<0:
                            raise ValueError('PAPER_COST_BUDGET_UNVERIFIED')
                        from config import MIN_TRADING_AMOUNT
                        if planned_funds<number(MIN_TRADING_AMOUNT,positive=True):
                            return False  # Normal rejection: no intent, fill or HALT.
                else:
                    if quantity == 0:
                        return False
                    value = quantity
                identifier = 'autotrade-' + uuid.uuid4().hex
                order = {
                    'identifier': identifier, 'uuid': None, 'ticker': ticker, 'side': side,
                    'value': str(value), 'price_hint': str(price), 'averaging': averaging,
                    'reason': reason, 'status': 'intent',
                }
                self.state['orders'][identifier] = order
                # The intent is durable BEFORE the only submission attempt.
                self.store.save(self.state)
                if self.real_trading:
                    response = self.broker.submit(side, ticker, value, identifier)
                    if not isinstance(response, dict) or not response.get('uuid'):
                        raise TradingHalted('Submission result is unknown; no automatic retry')
                    order['uuid'] = response['uuid']
                    order['status'] = 'submitted'
                    self.store.save(self.state)
                    receipt = self._wait_for_terminal(order)
                else:
                    costs=self._paper_costs
                    if costs is None or not (costs.fee_bps or costs.slippage_bps or costs.spread_bps):
                        executed = value / price if side == 'bid' else value
                        receipt = {'uuid': identifier, 'market': ticker, 'side': side,
                                   'state': 'done', 'executed_volume': str(executed),
                                   'remaining_volume': '0', 'paid_fee': '0',
                                   'trades': [{'volume': str(executed), 'funds': str(executed * price)}]}
                    else:
                        execution=costs.price(price,'BUY' if side=='bid' else 'SELL')
                        if side=='bid':
                            funds,fee=planned_funds,planned_fee
                            executed=funds/execution
                        else:
                            executed=value;funds=executed*execution;fee=funds*costs.fee_rate
                        receipt={'uuid':identifier,'market':ticker,'side':side,'state':'done',
                                 'executed_volume':str(executed),'remaining_volume':'0','paid_fee':str(fee),
                                 'trades':[{'volume':str(executed),'funds':str(funds)}]}
                filled = self._apply_fill(order, receipt)
                self.store.save(self.state)
                return filled
            except Exception as exc:
                self.halt(exc)
                raise TradingHalted(str(exc)) from exc

    def _wait_for_terminal(self, order):
        for attempt in range(self.poll_attempts):
            receipt = self.broker.order(uuid=order['uuid'], identifier=order['identifier'])
            if not isinstance(receipt, dict) or 'error' in receipt:
                raise TradingHalted('Cannot confirm order result')
            if receipt.get('market') != order['ticker'] or receipt.get('side') != order['side']:
                raise ValueError('Order identity mismatch')
            if order['uuid'] and receipt.get('uuid') != order['uuid']:
                raise ValueError('Order UUID mismatch')
            if not order['uuid']:
                if receipt.get('identifier') != order['identifier'] or not receipt.get('uuid'):
                    raise ValueError('Order identifier mismatch')
                order['uuid'] = receipt['uuid']
                order['status'] = 'submitted'
                self.store.save(self.state)
            if receipt.get('state') in ('done', 'cancel'):
                return receipt
            if receipt.get('state') not in ('wait', 'watch'):
                raise ValueError('Unknown exchange order state')
            if attempt + 1 < self.poll_attempts:
                self.sleep(self.poll_delay)
        raise TradingHalted('Order is still pending; stopped without resubmission')

    def _apply_fill(self, order, receipt):
        if order['status'] == 'settled':
            return False
        if receipt['state'] not in ('done', 'cancel'):
            raise ValueError('Only terminal orders can be applied')
        executed = number(receipt['executed_volume'])
        fee = number(receipt['paid_fee'])
        trades = receipt['trades']
        quantity = sum((number(t['volume'], positive=True) for t in trades), Decimal(0))
        funds = sum((number(t['funds'], positive=True) for t in trades), Decimal(0))
        if abs(quantity - executed) > Decimal('0.00000001'):
            raise ValueError('Executed volume differs from trade fills')
        if executed == 0 and (funds != 0 or fee != 0):
            raise ValueError('Fees/funds without a fill')
        if order['side'] == 'bid' and funds > number(order['value']):
            raise ValueError('Buy fills exceed the requested amount')
        if self._paper_cost_policy is not None and order['side']=='bid' and funds+fee>number(order['value']):
            raise ValueError('Paper buy fills and fee exceed total budget')
        if order['side'] == 'ask' and executed > number(order['value']):
            raise ValueError('Sell fills exceed the requested quantity')
        if executed > 0 and (funds == 0 or fee > funds):
            raise ValueError('Invalid fill value or fee')
        ticker = order['ticker']
        old = self.position(ticker)
        old_qty = number(old['quantity'])
        old_avg = number(old['avg_price'])
        if executed > 0:
            if order['side'] == 'bid':
                total_qty = old_qty + executed
                cash = self.cash - funds - fee
                if cash < 0:
                    raise ValueError('Fill exceeds recorded cash')
                self.state['cash'] = str(cash)
                self.state['positions'][ticker] = {
                    'quantity': str(total_qty),
                    'avg_price': str((old_qty * old_avg + funds) / total_qty),
                    'averaging_down_used': old['averaging_down_used'] or order['averaging'],
                }
            else:
                remaining = old_qty - executed
                if remaining < -Decimal('0.00000001'):
                    raise ValueError('Sell fill exceeds recorded position')
                self.state['cash'] = str(self.cash + funds - fee)
                if remaining <= 0:
                    self.state['positions'].pop(ticker, None)
                else:
                    self.state['positions'][ticker] = dict(old, quantity=str(remaining))
        order.update(status='settled', exchange_state=receipt['state'],
                     executed_volume=str(executed), funds=str(funds), paid_fee=str(fee),
                     average_fill_price=str(funds / executed) if executed else None,
                     settled_at=time.time())
        if order['side'] == 'ask' and executed:
            order['profit_amount'] = str(funds - fee - executed * old_avg)
            order['profit_rate'] = str((funds / executed - old_avg) / old_avg * 100)
        return executed > 0
