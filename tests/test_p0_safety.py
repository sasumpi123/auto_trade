"""All network connections are forbidden; real-mode paths use an in-memory broker."""
from copy import deepcopy
from decimal import Decimal
import json
import logging
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from trading.execution import ExecutionEngine, TradingHalted, number
from trading.auto_trade import AutoTrade
from data_analyzer.analyzer import DataAnalyzer
from services.upbit_broker import UpbitBroker

TICKER = 'KRW-BTC'
OTHER = 'KRW-ETH'


def account(qty='0', cash='1000000', avg='10000', locked='0', ticker=TICKER):
    rows = [{'currency': 'KRW', 'balance': cash, 'locked': '0'}]
    if number(qty) + number(locked) > 0:
        rows.append({'currency': ticker[4:], 'balance': qty, 'locked': locked,
                     'avg_buy_price': avg, 'unit_currency': 'KRW'})
    return rows


def receipt(qty='1', funds='10000', *, state='done', fee='0', remaining='0'):
    return {'state': state, 'executed_volume': qty, 'paid_fee': fee, 'price': None,
            'remaining_volume': remaining,
            'trades': [{'volume': qty, 'funds': funds}] if number(qty) > 0 else []}


class FakeBroker:
    identity = 'fake-account'

    def __init__(self, balances=None):
        self.accounts = balances if balances is not None else account()
        self.outstanding = []
        self.next_receipts = [receipt()]
        self.by_uuid = {}
        self.by_identifier = {}
        self.submissions = []
        self.reads = []
        self.submit_error = None
        self.balance_error = None
        self.order_error = None

    def balances(self):
        if self.balance_error:
            raise self.balance_error
        return deepcopy(self.accounts)

    def open_orders(self):
        return deepcopy(self.outstanding)

    def submit(self, side, ticker, value, identifier):
        uid = 'fake-' + str(len(self.submissions) + 1)
        self.submissions.append((side, ticker, value, identifier))
        self.by_identifier[identifier] = uid
        self.by_uuid[uid] = [dict(deepcopy(r), uuid=uid, market=ticker, side=side,
                                 identifier=identifier) for r in self.next_receipts]
        if self.submit_error:
            raise self.submit_error
        return {'uuid': uid}

    def order(self, *, uuid=None, identifier=None):
        self.reads.append((uuid, identifier))
        if self.order_error:
            raise self.order_error
        uid = uuid or self.by_identifier[identifier]
        items = self.by_uuid[uid]
        return deepcopy(items.pop(0) if len(items) > 1 else items[0])


class HoldAnalyzer:
    def __init__(self, ticker):
        self.last_signal = None
        self.last_signal_time = None
        self.calls = 0

    def update_data(self):
        pass

    def analyze(self):
        self.calls += 1
        return {'action': 'HOLD', 'reason': None, 'target_price': None}

    def get_strategy_status(self):
        return {'RSI': 'neutral'}


class SafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        logging.disable(logging.CRITICAL)
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        cls.network.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'state.json'
        self.engines = []
        self.bots = []

    def tearDown(self):
        for bot in self.bots:
            bot.stop()
        for engine in self.engines:
            engine.close()
        self.temp.cleanup()

    def engine(self, broker=None, *, path=None, open_now=True, attempts=3):
        engine = ExecutionEngine(path or self.path, 1000000, broker=broker,
                                 real_trading=broker is not None, poll_attempts=attempts,
                                 poll_delay=0, sleep=lambda _: None)
        self.engines.append(engine)
        if open_now:
            engine.open()
        return engine

    def bot(self, **kwargs):
        bot = AutoTrade(tickers=[TICKER], state_path=self.path,
                        analyzer_factory=HoldAnalyzer, **kwargs)
        self.bots.append(bot)
        bot.initialize()
        return bot

    def seed(self, bot, *, used=False, amount=200000):
        self.assertTrue(bot.engine.buy(TICKER, 10000, amount))
        bot.engine.state['positions'][TICKER]['averaging_down_used'] = used
        bot.engine.store.save(bot.engine.state)
        bot._sync_views()

    def test_paper_default_and_live_broker_guard(self):
        import config
        self.assertIs(config.REAL_TRADING, False)
        with self.assertRaises(RuntimeError):
            UpbitBroker('not-a-key', 'not-a-secret')
        with self.assertRaises(ValueError):
            self.engine(FakeBroker(), open_now=False).__class__(self.path, 100, broker=FakeBroker())

    def test_take_profit_at_exact_five_percent_bypasses_hold(self):
        bot = self.bot()
        self.seed(bot)
        self.assertTrue(bot.process_market_data(TICKER, {'trade_price': 10500}))
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 0)
        self.assertEqual(bot.analyzers[TICKER].calls, 0)
        self.assertEqual(bot.engine.cash, Decimal('1010000'))

    def test_stop_loss_after_averaging_at_exact_boundary(self):
        bot = self.bot()
        self.seed(bot, used=True)
        self.assertTrue(bot.process_market_data(TICKER, {'trade_price': 9500}))
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 0)
        self.assertEqual(bot.analyzers[TICKER].calls, 0)

    def test_first_stop_boundary_preserves_averaging_policy_and_cost(self):
        bot = self.bot()
        self.seed(bot)
        bot.process_market_data(TICKER, {'trade_price': 9500})
        pos = bot.engine.position(TICKER)
        self.assertEqual(number(pos['quantity']), 30)
        self.assertEqual(number(pos['avg_price']), Decimal('295000') / 30)
        self.assertTrue(pos['averaging_down_used'])
        self.assertEqual(len(bot.engine.state['orders']), 2)
        bot.process_market_data(TICKER, {'trade_price': 9000})
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 0)

    def test_insufficient_cash_for_averaging_falls_back_to_stop(self):
        bot = self.bot()
        self.seed(bot, amount=1000000)
        bot.process_market_data(TICKER, {'trade_price': 9500})
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 0)

    def test_hold_inside_risk_boundaries(self):
        bot = self.bot()
        self.seed(bot)
        bot.process_market_data(TICKER, {'trade_price': 10001})
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 20)
        self.assertEqual(bot.analyzers[TICKER].calls, 1)

    def test_main_loop_reaches_risk_exit_and_closes_stream(self):
        bot = self.bot()
        self.seed(bot, used=True)
        stream = Mock()
        def get():
            if stream.get.call_count == 1:
                return {'code': TICKER, 'trade_price': 9400}
            bot.request_stop()
            return {}
        stream.get.side_effect = get
        bot.stream_factory = lambda _: stream
        bot.start()
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 0)
        stream.close.assert_called_once()

    def test_duplicate_buy_and_sell_are_noops(self):
        engine = self.engine()
        self.assertTrue(engine.buy(TICKER, 10000, 10000))
        self.assertFalse(engine.buy(TICKER, 10000, 10000))
        self.assertTrue(engine.sell(TICKER, 10000))
        self.assertFalse(engine.sell(TICKER, 10000))
        self.assertEqual(len(engine.state['orders']), 2)

    def test_concurrent_buys_are_serialized(self):
        engine = self.engine()
        barrier = threading.Barrier(3)
        results = []
        def buy():
            barrier.wait()
            results.append(engine.buy(TICKER, 10000, 10000))
        threads = [threading.Thread(target=buy) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(engine.state['orders']), 1)

    def test_second_process_state_owner_is_rejected(self):
        first = self.engine()
        second = self.engine(open_now=False)
        with self.assertRaises(RuntimeError):
            second.open()
        self.assertTrue(first.ready)

    def test_os_lock_blocks_another_python_process(self):
        self.engine()
        code = 'from trading.state_store import StateStore; import sys; StateStore(sys.argv[1]).acquire()'
        child = subprocess.run([sys.executable, '-c', code, str(self.path)],
                               capture_output=True, text=True, timeout=10)
        self.assertNotEqual(child.returncode, 0)
        self.assertIn('Another bot owns this state file', child.stderr)

    def test_delayed_fill_is_polled_without_duplicate_submission(self):
        broker = FakeBroker()
        broker.next_receipts = [receipt('0', '0', state='wait'), receipt('2', '19980', fee='9.99')]
        engine = self.engine(broker)
        self.assertTrue(engine.buy(TICKER, 12000, 20000))
        self.assertEqual(len(broker.submissions), 1)
        self.assertEqual(len(broker.reads), 2)
        self.assertEqual(number(engine.position(TICKER)['quantity']), 2)
        self.assertEqual(number(engine.position(TICKER)['avg_price']), 9990)
        self.assertEqual(engine.cash, Decimal('980010.01'))

    def test_pending_order_halts_and_prevents_further_orders(self):
        broker = FakeBroker()
        broker.next_receipts = [receipt('0', '0', state='wait')]
        engine = self.engine(broker)
        with self.assertRaises(TradingHalted):
            engine.buy(TICKER, 10000, 10000)
        with self.assertRaises(TradingHalted):
            engine.buy(OTHER, 10000, 10000)
        self.assertEqual(len(broker.submissions), 1)
        self.assertEqual(len(broker.reads), 3)

    def test_market_sell_uses_trade_funds_when_price_is_null(self):
        broker = FakeBroker(account(qty='2', cash='1000'))
        broker.next_receipts = [receipt('2', '22000', fee='11')]
        engine = self.engine(broker)
        self.assertTrue(engine.sell(TICKER, 9000))
        self.assertEqual(engine.cash, Decimal('22989'))
        self.assertEqual(number(engine.position(TICKER)['quantity']), 0)

    def test_partial_cancelled_buy_applies_only_actual_fill(self):
        broker = FakeBroker()
        broker.next_receipts = [receipt('0.5', '5000', state='cancel', fee='2.5')]
        engine = self.engine(broker)
        self.assertTrue(engine.buy(TICKER, 99999, 10000))
        self.assertEqual(number(engine.position(TICKER)['quantity']), Decimal('.5'))
        self.assertEqual(engine.cash, Decimal('994997.5'))

    def test_partial_sell_preserves_remaining_position(self):
        broker = FakeBroker(account(qty='2'))
        broker.next_receipts = [receipt('0.5', '5500', state='cancel', fee='2.75', remaining='1.5')]
        engine = self.engine(broker)
        engine.sell(TICKER, 11000)
        self.assertEqual(number(engine.position(TICKER)['quantity']), Decimal('1.5'))
        self.assertTrue(engine.position(TICKER)['averaging_down_used'])

    def test_cancel_without_fill_changes_no_position(self):
        broker = FakeBroker()
        broker.next_receipts = [receipt('0', '0', state='cancel')]
        engine = self.engine(broker)
        self.assertFalse(engine.buy(TICKER, 10000, 10000))
        self.assertEqual(engine.cash, 1000000)

    def test_lost_ack_recovers_by_identifier_without_resubmission(self):
        broker = FakeBroker()
        broker.submit_error = TimeoutError('lost acknowledgement')
        engine = self.engine(broker)
        with self.assertRaises(TradingHalted):
            engine.buy(TICKER, 10000, 10000)
        self.assertIsNone(next(iter(json.loads(self.path.read_text())['orders'].values()))['uuid'])
        engine.close()
        broker.accounts = account(qty='1', cash='990000')
        recovered = self.engine(broker)
        self.assertEqual(number(recovered.position(TICKER)['quantity']), 1)
        self.assertEqual(len(broker.submissions), 1)
        self.assertIsNone(broker.reads[-1][0])
        recovered.close()
        again = self.engine(broker)
        self.assertEqual(number(again.position(TICKER)['quantity']), 1)
        self.assertEqual(len(broker.submissions), 1)

    def test_pending_uuid_recovery_restores_terminal_fill_once(self):
        broker = FakeBroker()
        broker.next_receipts = [receipt('0', '0', state='wait')]
        first = self.engine(broker)
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 10000, 10000)
        first.close()
        item = broker.by_uuid['fake-1'][0]
        broker.by_uuid['fake-1'] = [dict(receipt(), uuid=item['uuid'], identifier=item['identifier'], market=TICKER, side='bid')]
        broker.accounts = account(qty='1', cash='990000')
        restored = self.engine(broker)
        self.assertEqual(number(restored.position(TICKER)['quantity']), 1)
        self.assertTrue(all(o['status'] == 'settled' for o in restored.state['orders'].values()))
        self.assertEqual(len(broker.submissions), 1)

    def test_crash_after_fill_before_final_save_recovers_once(self):
        class PowerLoss(BaseException):
            pass
        broker = FakeBroker()
        first = self.engine(broker)
        save = first.store.save
        def crash_on_settlement(state):
            if any(o['status'] == 'settled' for o in state['orders'].values()):
                raise PowerLoss()
            save(state)
        with patch.object(first.store, 'save', side_effect=crash_on_settlement):
            with self.assertRaises(PowerLoss):
                first.buy(TICKER, 10000, 10000)
        first.close()
        broker.accounts = account(qty='1', cash='990000')
        recovered = self.engine(broker)
        self.assertEqual(number(recovered.position(TICKER)['quantity']), 1)
        self.assertEqual(recovered.cash, 990000)
        self.assertEqual(len(broker.submissions), 1)

    def test_partial_averaging_preserves_used_flag_on_restart(self):
        broker = FakeBroker(account(qty='2', cash='900000'))
        first = self.engine(broker)
        # Model a known bot position with its original averaging allowance.
        first.state['positions'][TICKER]['averaging_down_used'] = False
        first.store.save(first.state)
        broker.next_receipts = [receipt('.5', '4500', state='cancel')]
        first.buy(TICKER, 9000, 9000, averaging=True)
        first.close()
        broker.accounts = account(qty='2.5', cash='895500', avg='9800')
        restored = self.engine(broker)
        self.assertTrue(restored.position(TICKER)['averaging_down_used'])
        self.assertFalse(restored.buy(TICKER, 8000, 8000, averaging=True))
        self.assertEqual(len(broker.submissions), 1)

    def test_unknown_identifier_keeps_recovery_halted(self):
        broker = FakeBroker()
        broker.submit_error = TimeoutError('unknown')
        first = self.engine(broker)
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 10000, 10000)
        first.close()
        broker.order_error = LookupError('not found')
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        self.assertEqual(len(broker.submissions), 1)

    def test_external_open_orders_are_preserved_and_block_startup(self):
        broker = FakeBroker(account(qty='1', locked='2'))
        broker.outstanding = [{'uuid': 'external', 'market': TICKER, 'side': 'ask', 'state': 'wait'}]
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        saved = json.loads(self.path.read_text())
        self.assertEqual(number(saved['positions'][TICKER]['quantity']), 3)
        self.assertEqual(saved['external_open_orders'][0]['uuid'], 'external')
        self.assertEqual(broker.submissions, [])

    def test_stale_account_snapshot_does_not_settle_recovered_order(self):
        broker = FakeBroker()
        broker.submit_error = TimeoutError('lost ack')
        first = self.engine(broker)
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 10000, 10000)
        first.close()
        # The order is done, but balances still show the pre-fill snapshot.
        for _ in range(2):
            with self.assertRaises(TradingHalted):
                self.engine(broker)
            saved = json.loads(self.path.read_text())
            self.assertNotEqual(next(iter(saved['orders'].values()))['status'], 'settled')
        broker.accounts = account(qty='1', cash='990000')
        restored = self.engine(broker)
        self.assertEqual(number(restored.position(TICKER)['quantity']), 1)
        self.assertEqual(len(broker.submissions), 1)

    def test_locked_assets_without_visible_orders_block_recovery(self):
        broker = FakeBroker(account(qty='1', locked='1'))
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        self.assertEqual(broker.submissions, [])

    def test_account_read_error_during_recovery_keeps_order_pending(self):
        broker = FakeBroker()
        broker.submit_error = TimeoutError('lost ack')
        first = self.engine(broker)
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 10000, 10000)
        first.close()
        broker.balance_error = TimeoutError('account temporarily unavailable')
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        saved = json.loads(self.path.read_text())
        self.assertNotEqual(next(iter(saved['orders'].values()))['status'], 'settled')
        self.assertEqual(len(broker.submissions), 1)

    def test_partial_averaging_unknown_order_survives_disk_restart(self):
        broker = FakeBroker(account(qty='2', cash='900000'))
        first = self.engine(broker)
        first.state['positions'][TICKER]['averaging_down_used'] = False
        first.store.save(first.state)
        broker.next_receipts = [receipt('.5', '4500', state='cancel')]
        broker.submit_error = TimeoutError('lost ack')
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 9000, 9000, averaging=True)
        first.close()
        broker.accounts = account(qty='2.5', cash='895500', avg='9800')
        restored = self.engine(broker)
        self.assertTrue(restored.position(TICKER)['averaging_down_used'])
        self.assertEqual(number(restored.position(TICKER)['quantity']), Decimal('2.5'))
        self.assertEqual(len(broker.submissions), 1)

    def test_external_holding_outside_top_list_is_monitored(self):
        broker = FakeBroker(account(qty='2', ticker=OTHER))
        bot = self.bot(broker=broker, real_trading=True)
        self.assertIn(OTHER, bot.tickers)
        self.assertIn(OTHER, bot.analyzers)
        self.assertTrue(bot.engine.position(OTHER)['averaging_down_used'])
        self.assertEqual(bot.get_significant_holdings_count(), 1)

    def test_paper_restart_restores_cash_holdings_averaging_and_cooldown(self):
        first = self.bot()
        self.seed(first, used=True)
        first.engine.save_signal(TICKER, 'BUY', time.time())
        cash = first.engine.cash
        first.stop()
        restored = self.bot()
        self.assertEqual(restored.engine.cash, cash)
        self.assertEqual(restored.coin_balance[TICKER], 20)
        self.assertTrue(restored.averaging_down_used[TICKER])
        self.assertEqual(restored.analyzers[TICKER].last_signal, 'BUY')

    def test_corrupt_journal_is_not_overwritten(self):
        self.path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(TradingHalted):
            self.engine()
        self.assertEqual(self.path.read_text(), '{broken')

    def test_mode_mismatch_is_not_overwritten(self):
        first = self.engine()
        first.close()
        original = self.path.read_bytes()
        with self.assertRaises(TradingHalted):
            self.engine(FakeBroker())
        self.assertEqual(self.path.read_bytes(), original)

    def test_account_mismatch_is_not_overwritten(self):
        broker = FakeBroker()
        first = self.engine(broker)
        first.close()
        broker.identity = 'another-account'
        original = self.path.read_bytes()
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        self.assertEqual(self.path.read_bytes(), original)

    def test_invalid_account_response_halts(self):
        broker = FakeBroker()
        broker.accounts = {'error': 'unauthorized'}
        with self.assertRaises(TradingHalted):
            self.engine(broker)
        self.assertEqual(broker.submissions, [])

    def test_disk_failure_before_submission_never_submits(self):
        broker = FakeBroker()
        engine = self.engine(broker)
        with patch.object(engine.store, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(TradingHalted):
                engine.buy(TICKER, 10000, 10000)
        self.assertTrue(engine.halted)
        self.assertEqual(broker.submissions, [])

    def test_malformed_fill_halts_without_creating_position(self):
        broker = FakeBroker()
        bad = receipt('1', '10000')
        bad['trades'][0]['volume'] = '2'
        broker.next_receipts = [bad]
        engine = self.engine(broker)
        with self.assertRaises(TradingHalted):
            engine.buy(TICKER, 10000, 10000)
        self.assertEqual(number(engine.position(TICKER)['quantity']), 0)

    def test_order_identity_mismatch_halts(self):
        broker = FakeBroker()
        engine = self.engine(broker)
        original_order = broker.order
        def wrong_order(**kwargs):
            result = original_order(**kwargs)
            result['market'] = OTHER
            return result
        broker.order = wrong_order
        with self.assertRaises(TradingHalted):
            engine.buy(TICKER, 10000, 10000)

    def test_stream_timeout_stops_without_reconnect_or_orders(self):
        stream = Mock()
        stream.get.side_effect = TimeoutError('no ticks')
        bot = self.bot(stream_factory=lambda _: stream)
        with self.assertRaises(TradingHalted):
            bot.start()
        self.assertTrue(bot.engine.halted)
        self.assertFalse(bot.running)
        stream.connect.assert_called_once()
        stream.close.assert_called_once()
        self.assertEqual(bot.engine.state['orders'], {})

    def test_analysis_exception_halts_and_blocks_orders(self):
        bot = self.bot()
        bot.analyzers[TICKER].analyze = Mock(side_effect=ValueError('bad candles'))
        with self.assertRaises(ValueError):
            bot.process_market_data(TICKER, {'trade_price': 10000})
        self.assertTrue(bot.engine.halted)
        with self.assertRaises(TradingHalted):
            bot.buy_coin(TICKER, 10000)

    def test_invalid_tick_halts(self):
        bot = self.bot()
        with self.assertRaises(ValueError):
            bot.process_market_data(TICKER, {'trade_price': float('nan')})
        self.assertTrue(bot.engine.halted)

    def test_stop_is_idempotent_and_no_further_order_allowed(self):
        bot = self.bot()
        bot.stop()
        bot.stop()
        with self.assertRaises(TradingHalted):
            bot.buy_coin(TICKER, 10000)

    def test_status_report_does_not_consume_signal(self):
        bot = self.bot()
        bot.analyzers[TICKER].analyze = Mock(side_effect=AssertionError('must not analyze'))
        bot.log_status()
        bot.analyzers[TICKER].analyze.assert_not_called()
        self.assertIsNone(bot.analyzers[TICKER].last_signal_time)

    def test_reporting_exception_halts_after_durable_fill_without_repeat(self):
        bot = self.bot(notification=Mock())
        bot.notification.send_trade_alert.side_effect = RuntimeError('report failed')
        with self.assertRaises(RuntimeError):
            bot.buy_coin(TICKER, 10000)
        self.assertTrue(bot.engine.halted)
        self.assertEqual(number(bot.engine.position(TICKER)['quantity']), 40)
        saved = json.loads(self.path.read_text())
        self.assertEqual(len(saved['orders']), 1)
        self.assertEqual(next(iter(saved['orders'].values()))['status'], 'settled')

    def test_order_read_exception_keeps_pending_and_halts(self):
        broker = FakeBroker()
        first = self.engine(broker)
        broker.order_error = TimeoutError('order lookup unavailable')
        with self.assertRaises(TradingHalted):
            first.buy(TICKER, 10000, 10000)
        self.assertTrue(first.halted)
        self.assertEqual(len(broker.submissions), 1)
        self.assertEqual(next(iter(first.state['orders'].values()))['status'], 'submitted')

    def test_invalid_saved_signal_blocks_recovery_without_overwrite(self):
        first = self.engine()
        first.state['signals'][TICKER] = {'action': 'BUY', 'timestamp': None}
        first.store.save(first.state)
        first.close()
        original = self.path.read_bytes()
        with self.assertRaises(TradingHalted):
            self.engine()
        self.assertEqual(self.path.read_bytes(), original)

    def test_analyzer_fetch_failure_raises_instead_of_using_stale_data(self):
        analyzer = DataAnalyzer(TICKER)
        analyzer.df = pd.DataFrame({'close': [1]})
        with patch('data_analyzer.analyzer.pyupbit.get_ohlcv', return_value=None), \
             patch('utils.decorators.notification_service'):
            with self.assertRaises(ValueError):
                analyzer.fetch_data()

    def test_analyzer_failure_raises_instead_of_silent_hold(self):
        analyzer = DataAnalyzer(TICKER)
        with patch('utils.decorators.notification_service'):
            with self.assertRaises(ValueError):
                analyzer.analyze()

    def test_ticker_discovery_failure_stops_startup(self):
        import config
        with patch('services.public_market.PublicMarketClient.top_tickers', side_effect=ValueError('public discovery failed')):
            with self.assertRaises(ValueError):
                config.get_top_tickers()

    def test_main_returns_nonzero_on_failure_and_cleans_up(self):
        import main
        with patch.object(main, 'get_top_tickers', return_value=[TICKER]), \
             patch.object(main, 'AutoTrade') as factory, \
             patch.object(main.signal, 'signal'):
            factory.return_value.start.side_effect = TradingHalted('test failure')
            self.assertEqual(main.TradingBot().run(), 1)
            factory.return_value.stop.assert_called_once()

    def test_indicator_calculation_and_existing_rsi_signal(self):
        analyzer = DataAnalyzer(TICKER)
        x = np.arange(200)
        close = 10000 + x * 2 + 100 * np.sin(x / 4)
        analyzer.df = pd.DataFrame({'close': close, 'volume': np.ones(200) * 100})
        analyzer.calculate_indicators()
        self.assertTrue(np.isfinite(analyzer.df[['rsi', 'macd', 'bb_middle']].iloc[-1]).all())
        analyzer.df.loc[:, 'rsi'] = 21.
        analyzer.df.loc[analyzer.df.index[-1], 'rsi'] = 22.
        analyzer.df.loc[:, 'macd'] = -1.
        analyzer.df.loc[:, 'macd_signal'] = -1.
        analyzer.df.loc[:, 'bb_lower'] = 1.
        analyzer.df.loc[:, 'bb_upper'] = 999999.
        self.assertEqual(analyzer.analyze()['action'], 'BUY')
        self.assertEqual(analyzer.analyze()['action'], 'HOLD')

    def test_original_indicator_rules_and_conflict_priority(self):
        def analyze(rsi=(50, 50), macd=(-1, -1), signal=(-1, -1), low=5000, high=15000, volume=100):
            analyzer = DataAnalyzer(TICKER)
            analyzer.df = pd.DataFrame({'close': [10000.] * 20, 'volume': [100.] * 20,
                'rsi': [rsi[0]] * 19 + [rsi[1]], 'macd': [macd[0]] * 19 + [macd[1]],
                'macd_signal': [signal[0]] * 19 + [signal[1]],
                'bb_lower': [float(low)] * 20, 'bb_upper': [float(high)] * 20,
                'bb_middle': [10000.] * 20})
            analyzer.df.loc[19, 'volume'] = volume
            return analyzer.analyze()
        cases = [
            ({'rsi': (21, 22)}, 'BUY', 10500),
            ({'rsi': (79, 78)}, 'SELL', None),
            ({'macd': (-2, -1), 'signal': (-3, -3)}, 'BUY', 10300),
            ({'macd': (2, 1), 'signal': (3, 3)}, 'SELL', None),
            ({'rsi': (50, 51), 'low': 11000}, 'BUY', 10000),
            ({'high': 9000}, 'SELL', None),
            ({'rsi': (21, 22), 'macd': (-2, -1), 'signal': (-3, -3)}, 'BUY', 10500),
            # Retain existing sequential conflict resolution rather than invent a new strategy.
            ({'rsi': (21, 22), 'high': 9000}, 'SELL', 10500),
            ({'rsi': (21, 22), 'volume': 1}, 'HOLD', None),
        ]
        for kwargs, action, target in cases:
            with self.subTest(kwargs=kwargs):
                actual = analyze(**kwargs)
                self.assertEqual(actual['action'], action)
                self.assertEqual(actual['target_price'], target)

    def test_private_adapter_timeout_is_not_retried(self):
        session = Mock()
        session.request.side_effect = TimeoutError('response lost')
        broker = UpbitBroker('fake', 'x' * 64, enabled=True, session=session)
        with self.assertRaises(TimeoutError):
            broker.submit('bid', TICKER, 10000, 'intent-1')
        session.request.assert_called_once()

    def test_stream_recv_timeout_and_subscription_without_network(self):
        from services.market_stream import MarketStream
        connection = Mock()
        connection.recv.return_value = b'{"code":"KRW-BTC","trade_price":10000}'
        with patch('websockets.sync.client.connect', return_value=connection):
            stream = MarketStream([TICKER], timeout=7)
            stream.connect()
            self.assertEqual(json.loads(connection.send.call_args.args[0])[1]['codes'], [TICKER])
            self.assertEqual(stream.get()['trade_price'], 10000)
            connection.recv.assert_called_once_with(timeout=7)
            stream.close()
            stream.close()
            connection.close.assert_called_once()

    def test_private_adapter_uses_identifier_and_no_sell_price(self):
        session = Mock()
        session.request.return_value.json.return_value = {'uuid': 'fake'}
        broker = UpbitBroker('fake-key', 'fake-secret-' * 8, enabled=True, session=session)
        broker.submit('ask', TICKER, Decimal('2'), 'unique-intent')
        kwargs = session.request.call_args.kwargs
        self.assertEqual(kwargs['json']['identifier'], 'unique-intent')
        self.assertEqual(kwargs['json']['volume'], '2')
        self.assertNotIn('price', kwargs['json'])
        self.assertEqual(kwargs['timeout'], (3.05, 10))
        self.assertFalse(kwargs['allow_redirects'])

    def test_private_adapter_open_orders_paginates(self):
        session = Mock()
        session.request.return_value.json.side_effect = [[{'uuid': str(i)} for i in range(100)], []]
        broker = UpbitBroker('fake', 'x' * 64, enabled=True, session=session)
        self.assertEqual(len(broker.open_orders()), 100)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(parse_qs(session.request.call_args.kwargs['params'])['states[]'], ['wait', 'watch'])


if __name__ == '__main__':
    unittest.main()
