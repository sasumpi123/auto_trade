"""Original signal strategy with durable, fail-closed order execution."""
from collections import defaultdict, deque
from decimal import Decimal
import logging
from pathlib import Path
import threading
import time

from config import (
    REAL_TRADING, START_CASH, MIN_TRADING_AMOUNT, CASH_USAGE_RATIO,
    MAX_COINS_AT_ONCE, STOP_LOSS, DATA_UPDATE_INTERVAL, STATUS_INTERVAL,
    REPORT_CHECK_INTERVAL, UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY, get_top_tickers,
)
from data_analyzer.analyzer import DataAnalyzer
from services.performance_service import PerformanceAnalyzer
from .execution import ExecutionEngine, TradingHalted, number


class AutoTrade:
    def __init__(self, start_cash=START_CASH, *, tickers=None, state_path=None,
                 real_trading=REAL_TRADING, broker=None, analyzer_factory=DataAnalyzer,
                 notification=None, stream_factory=None, poll_attempts=10, poll_delay=0.5):
        self.start_cash = start_cash
        self.real_trading = real_trading
        self.tickers = list(tickers if tickers is not None else get_top_tickers(10))
        if not self.tickers:
            raise ValueError('No trading markets available')
        if real_trading and broker is None:
            from services.upbit_broker import UpbitBroker
            broker = UpbitBroker(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY, enabled=True)
        state_path = state_path or Path(__file__).resolve().parents[1] / 'runtime' / (
            'real-state.json' if real_trading else 'paper-state.json')
        self.engine = ExecutionEngine(state_path, start_cash, broker=broker,
                                      real_trading=real_trading, poll_attempts=poll_attempts,
                                      poll_delay=poll_delay)
        self.analyzer_factory = analyzer_factory
        self.analyzers = {t: analyzer_factory(t) for t in self.tickers}
        self.price_cache = defaultdict(lambda: deque(maxlen=1))
        self.notification = notification
        self.performance_analyzer = PerformanceAnalyzer(self.tickers)
        self.max_per_coin = number(start_cash) * number(CASH_USAGE_RATIO)
        self.stop_loss = number(STOP_LOSS)
        self.profit_taking_ratio = Decimal('0.05')
        self.running = False
        self.wm = None
        self.stream_factory = stream_factory
        self._initialized = False
        self._stop_requested = False
        self._mutex = threading.RLock()
        self._sync_views()

    def _sync_views(self):
        markets = set(self.tickers) | set(self.engine.state['positions'])
        self.current_cash = float(self.engine.cash)
        self.coin_balance = {t: float(number(self.engine.position(t)['quantity'])) for t in markets}
        self.coin_avg_price = {t: float(number(self.engine.position(t)['avg_price'])) for t in markets}
        self.buy_price = dict(self.coin_avg_price)
        self.buy_yn = {t: self.coin_balance[t] > 0 for t in markets}
        self.averaging_down_used = {t: self.engine.position(t)['averaging_down_used'] for t in markets}

    def initialize(self):
        if self._initialized:
            return
        try:
            self.engine.open()
            for ticker, pos in self.engine.state['positions'].items():
                if number(pos['quantity']) > 0 and ticker not in self.tickers:
                    self.tickers.append(ticker)
                    self.analyzers[ticker] = self.analyzer_factory(ticker)
            for ticker, saved in self.engine.state['signals'].items():
                if ticker in self.analyzers:
                    self.analyzers[ticker].last_signal = saved['action']
                    self.analyzers[ticker].last_signal_time = saved['timestamp']
            self.performance_analyzer.tickers = self.tickers
            self._sync_views()
            self._initialized = True
        except Exception as exc:
            self.engine.halt(exc)
            self.engine.close()
            raise

    def _halt(self, exc):
        self.running = False
        self.engine.halt(exc)
        if self.notification:
            try:
                self.notification.send_error_alert(str(exc))
            except Exception:
                logging.exception('Error notification failed')

    def request_stop(self):
        # A signal must not release the journal lock during an in-flight order.
        self._stop_requested = True
        self.running = False

    def stop(self):
        self.request_stop()
        with self._mutex:
            try:
                if self.wm is not None:
                    self.wm.close()
            finally:
                self.wm = None
                self.engine.close()

    def start(self):
        try:
            self.initialize()
            if self._stop_requested:
                return
            self.running = True
            for analyzer in self.analyzers.values():
                analyzer.update_data()
            if self.stream_factory is None:
                from services.market_stream import MarketStream
                self.stream_factory = MarketStream
            self.wm = self.stream_factory(self.tickers)
            self.wm.connect()
            last_data = last_status = last_report = time.monotonic()
            while self.running:
                data = self.wm.get()
                if not self.running:
                    break
                if not isinstance(data, dict) or data.get('code') not in self.analyzers:
                    raise ValueError('Invalid market stream message')
                self.process_market_data(data['code'], data)
                now = time.monotonic()
                if now - last_data >= DATA_UPDATE_INTERVAL:
                    for analyzer in self.analyzers.values():
                        analyzer.update_data()
                    last_data = now
                if now - last_status >= STATUS_INTERVAL:
                    self.log_status()
                    last_status = now
                if now - last_report >= REPORT_CHECK_INTERVAL:
                    if self.performance_analyzer.check_daily_report_time():
                        report = self.performance_analyzer.generate_daily_report()
                        if self.notification:
                            self.notification.send_report(report)
                        self.performance_analyzer.clear_old_data()
                    last_report = now
        except Exception as exc:
            self._halt(exc)
            raise TradingHalted(str(exc)) from exc
        finally:
            self.stop()

    def get_balance(self, currency='KRW'):
        return float(self.engine.cash if currency == 'KRW' else number(self.engine.position(currency)['quantity']))

    def get_significant_holdings_count(self):
        count = 0
        for ticker, pos in self.engine.state['positions'].items():
            quantity = number(pos['quantity'])
            if quantity == 0:
                continue
            if not self.price_cache[ticker] or quantity * number(self.price_cache[ticker][-1]) >= MIN_TRADING_AMOUNT:
                count += 1
        return count

    def buy_coin(self, ticker, current_price, reason=None, target_price=None, amount=None):
        with self._mutex:
            try:
                if self._stop_requested:
                    raise TradingHalted('Stop requested')
                averaging = amount is not None
                if not averaging and self.get_significant_holdings_count() >= MAX_COINS_AT_ONCE:
                    return False
                value = number(amount) if averaging else min(self.max_per_coin, self.engine.cash)
                if value < MIN_TRADING_AMOUNT:
                    return False
                result = self.engine.buy(ticker, current_price, value, averaging=averaging, reason=reason)
                self._sync_views()
                if result:
                    self._record_trade(ticker)
                return result
            except Exception as exc:
                self._halt(exc)
                raise

    def sell_coin(self, ticker, current_price, stop_loss_triggered=False, reason=None):
        with self._mutex:
            try:
                if self._stop_requested:
                    raise TradingHalted('Stop requested')
                result = self.engine.sell(ticker, current_price, reason=reason or ('손절' if stop_loss_triggered else '매도 신호'))
                self._sync_views()
                if result:
                    self._record_trade(ticker)
                return result
            except Exception as exc:
                self._halt(exc)
                raise

    def check_stop_loss(self, ticker, current_price):
        pos = self.engine.position(ticker)
        quantity = number(pos['quantity'])
        if quantity == 0:
            return False
        price = number(current_price, positive=True)
        avg = number(pos['avg_price'], positive=True)
        if price > avg * (1 - self.stop_loss):
            return False
        # Preserve the original one-time 50% averaging policy at -5%.
        if not pos['averaging_down_used']:
            amount = quantity * price * Decimal('0.5')
            if self.buy_coin(ticker, price, reason='물타기 매수', amount=amount):
                return False
        return True

    def process_market_data(self, ticker, data):
        with self._mutex:
            try:
                self.engine._ensure_ready()
                if self._stop_requested:
                    raise TradingHalted('Stop requested')
                price = number(data['trade_price'], positive=True)
                self.price_cache[ticker].append(float(price))
                pos = self.engine.position(ticker)
                if number(pos['quantity']) > 0:
                    avg = number(pos['avg_price'], positive=True)
                    if price >= avg * (1 + self.profit_taking_ratio):
                        return self.sell_coin(ticker, price, reason='이익 실현 (5%)')
                    if price <= avg * (1 - self.stop_loss):
                        if self.check_stop_loss(ticker, price):
                            return self.sell_coin(ticker, price, stop_loss_triggered=True)
                        return
                analyzer = self.analyzers[ticker]
                analysis = analyzer.analyze()
                if analysis['action'] not in ('BUY', 'SELL', 'HOLD'):
                    raise ValueError('Invalid analysis action')
                if analysis['action'] != 'HOLD':
                    self.engine.save_signal(ticker, analyzer.last_signal, analyzer.last_signal_time)
                held = number(self.engine.position(ticker)['quantity']) > 0
                if analysis['action'] == 'BUY' and not held:
                    return self.buy_coin(ticker, price, reason=analysis['reason'], target_price=analysis['target_price'])
                if analysis['action'] == 'SELL' and held:
                    return self.sell_coin(ticker, price, reason=analysis['reason'])
                return False
            except Exception as exc:
                self._halt(exc)
                raise

    def log_status(self):
        status = {ticker: analyzer.get_strategy_status() for ticker, analyzer in self.analyzers.items()}
        message = f'현금: {self.engine.cash}, 보유: {self.engine.state["positions"]}, 지표: {status}'
        logging.info(message)
        if self.notification:
            self.notification.send_status_update(message)

    def _record_trade(self, ticker):
        # Execution is already durable before reporting; reporting errors never retry orders.
        order = next(reversed(self.engine.state['orders'].values()))
        trade = {'type': 'buy' if order['side'] == 'bid' else 'sell',
                 'price': float(number(order['average_fill_price'])),
                 'amount': float(number(order['funds'])),
                 'quantity': float(number(order['executed_volume'])),
                 'reason': order['reason'], 'profit': float(order.get('profit_rate', 0)),
                 'profit_amount': float(order.get('profit_amount', 0))}
        self.performance_analyzer.add_trade(ticker, trade)
        logging.info('Confirmed %s fill: %s', 'real' if self.real_trading else 'paper', trade)
        if self.notification:
            self.notification.send_trade_alert(str(trade))
