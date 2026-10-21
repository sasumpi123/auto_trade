from dataclasses import replace
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from backtesting.data import demo_candles, indicators, load_csv, snapshot_at, validate_candles, dataset_hash
from backtesting.engine import BacktestConfig, BacktestEngine, CostModel, compare
from backtesting.__main__ import main, write_report
from strategies.signals import Signal, RSIStrategy, research_strategies


def candles(count=45):
    return pd.DataFrame({'open': [100.] * count, 'high': [101.] * count, 'low': [99.] * count,
                         'close': [100.] * count, 'volume': [100.] * count},
                        index=pd.date_range('2025-01-01', periods=count, freq='min', tz='UTC'))


class ScriptedStrategy:
    name = 'scripted'

    def __init__(self, actions):
        self.actions = iter(actions)
        self.seen = []

    def decide(self, snapshot):
        self.seen.append(snapshot)
        return Signal(next(self.actions, 'HOLD'))


class BacktestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No network in research tests'))
        cls.network.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()

    def setUp(self):
        self.config = BacktestConfig(CostModel(0, 0), initial_cash=1000, position_fraction=1,
                                     min_order=0, cooldown_seconds=0)

    def run_strategy(self, actions, data=None, config=None):
        return BacktestEngine(config or self.config).run(candles() if data is None else data, ScriptedStrategy(actions))

    def test_next_open_execution_not_signal_close_or_future_close(self):
        data = candles()
        data.iloc[35, data.columns.get_loc('open')] = 130
        data.iloc[35, data.columns.get_loc('high')] = 131
        data.iloc[35, data.columns.get_loc('low')] = 79
        data.iloc[35, data.columns.get_loc('close')] = 80
        result = self.run_strategy(['BUY'], data)
        fill = result.fills[0]
        self.assertEqual(result.warmup_bars, 34)
        self.assertEqual(fill.signal_bar, data.index[34])
        self.assertEqual(fill.executed_at, data.index[35])
        self.assertEqual(fill.decided_at, fill.executed_at)
        self.assertEqual(fill.price, 130)
        self.assertNotEqual(fill.price, data.close.iloc[34])

    def test_round_trip_zero_cost_conserves_cash(self):
        result = self.run_strategy(['BUY', 'SELL'])
        self.assertEqual([fill.side for fill in result.fills], ['BUY', 'SELL'])
        self.assertEqual(result.metrics['final_equity'], 1000)
        self.assertEqual(result.metrics['net_pnl'], 0)
        self.assertEqual(result.metrics['total_fees'], 0)
        self.assertEqual(result.metrics['ending_quantity'], 0)

    def test_fee_spread_slippage_and_entry_exit_cost_accounting(self):
        config = replace(self.config, costs=CostModel(100, 100, 200))
        result = self.run_strategy(['BUY', 'SELL'], config=config)
        buy, sell = result.fills
        quantity = (Decimal('1000') / Decimal('103.02')).quantize(Decimal('.00000001'), rounding=ROUND_DOWN)
        expected_cash = Decimal('1000') - quantity * Decimal('103.02') + quantity * Decimal('97.02')
        self.assertEqual(buy.price, 102)
        self.assertEqual(sell.price, 98)
        self.assertEqual(buy.quantity, quantity)
        self.assertEqual(sell.quantity, quantity)
        self.assertEqual(result.metrics['ending_cash'], expected_cash)
        self.assertEqual(result.metrics['realized_pnl'], expected_cash - 1000)
        self.assertEqual(result.metrics['total_fees'], quantity * Decimal('2'))
        self.assertGreater(result.metrics['max_drawdown_pct'], 0)
        self.assertEqual(result.metrics['win_rate_pct'], 0)

    def test_higher_costs_reduce_identical_flat_round_trip_result(self):
        free = self.run_strategy(['BUY', 'SELL'])
        costed = self.run_strategy(['BUY', 'SELL'], config=replace(self.config, costs=CostModel(5, 3, 2)))
        self.assertLess(costed.metrics['net_pnl'], free.metrics['net_pnl'])

    def test_fixed_initial_capital_fraction_and_no_leverage(self):
        result = self.run_strategy(['BUY'], config=replace(self.config, position_fraction=Decimal('.4'), costs=CostModel(5, 3)))
        fill = result.fills[0]
        self.assertLessEqual(fill.notional + fill.fee, 400)
        self.assertGreaterEqual(fill.cash_after, 600)
        self.assertGreaterEqual(result.metrics['ending_cash'], 0)

    def test_minimum_notional_and_quantity_rounding(self):
        result = self.run_strategy(['BUY'], config=replace(self.config, min_order=2000))
        self.assertEqual(result.fills, ())
        config = replace(self.config, initial_cash=Decimal('.000000001'))
        self.assertEqual(self.run_strategy(['BUY'], config=config).fills, ())

    def test_duplicate_entries_and_flat_exits_do_not_create_orders(self):
        result = self.run_strategy(['SELL', 'BUY', 'BUY', 'SELL', 'SELL'])
        self.assertEqual([f.side for f in result.fills], ['BUY', 'SELL'])
        self.assertEqual(result.metrics['closed_trades'], 1)

    def test_common_cooldown_uses_bar_time_not_wall_clock(self):
        config = replace(self.config, cooldown_seconds=300)
        result = self.run_strategy(['BUY', 'SELL'], config=config)
        self.assertEqual(len(result.fills), 2)
        self.assertEqual((result.fills[1].executed_at - result.fills[0].executed_at).total_seconds(), 300)

    def test_common_completed_bar_volume_filter(self):
        data = candles()
        data.iloc[34, data.columns.get_loc('volume')] = 0
        result = self.run_strategy(['BUY'], data)
        self.assertEqual(result.fills[0].signal_bar, data.index[35])
        zero = candles()
        zero['volume'] = 0
        self.assertEqual(self.run_strategy(['BUY'], zero).fills, ())

    def test_last_signal_is_not_filled_without_a_next_bar(self):
        data = candles(40)
        result = self.run_strategy(['HOLD'] * 5 + ['BUY'], data)
        self.assertEqual(result.fills, ())
        self.assertEqual(result.unfilled_final_signal['signal_bar'], data.index[-1])
        self.assertEqual(result.metrics['ending_quantity'], 0)

    def test_open_position_marked_to_close_without_forced_exit(self):
        data = candles()
        data.iloc[-1, data.columns.get_loc('close')] = 110
        data.iloc[-1, data.columns.get_loc('high')] = 111
        result = self.run_strategy(['BUY'], data)
        self.assertEqual(result.metrics['ending_quantity'], 10)
        self.assertEqual(result.metrics['final_equity'], 1100)
        self.assertEqual(result.metrics['closed_trades'], 0)
        self.assertEqual(result.metrics['unrealized_pnl'], 100)
        self.assertIsNone(result.metrics['win_rate_pct'])

    def test_realized_plus_unrealized_matches_total_net_pnl(self):
        data = demo_candles(60)
        result = self.run_strategy(['BUY', 'SELL', 'BUY'], data, replace(self.config, costs=CostModel(5, 3, 2)))
        combined = result.metrics['realized_pnl'] + result.metrics['unrealized_pnl']
        self.assertLess(abs(combined - result.metrics['net_pnl']), Decimal('1e-20'))
        self.assertEqual(result.metrics['total_fees'], sum((f.fee for f in result.fills), Decimal('0')))

    def test_drawdown_includes_loss_after_first_entry(self):
        data = candles()
        data.iloc[35:, data.columns.get_loc('close')] = 50
        data.iloc[35:, data.columns.get_loc('low')] = 49
        result = self.run_strategy(['BUY'], data)
        self.assertEqual(result.metrics['max_drawdown_pct'], 50)

    def test_same_data_costs_warmup_and_config_for_all_candidates(self):
        data = demo_candles(60)
        original = data.copy(deep=True)
        report = compare(data, self.config)
        self.assertEqual(len(report['results']), 5)
        for name, result in report['results'].items():
            self.assertEqual(result['data_hash'], report['data_hash'])
            self.assertEqual(result['config'], report['config'])
            self.assertEqual(result['warmup_bars'], 34)
            self.assertEqual(len(result['equity']), len(data))
        pd.testing.assert_frame_equal(data, original)
        self.assertTrue(report['research_only'])
        self.assertFalse(report['assumptions']['final_liquidation'])

    def test_comparison_is_deterministic_and_json_has_no_nan(self):
        data = candles()
        first = compare(data, self.config)
        self.assertEqual(first, compare(data, self.config))
        json.dumps(first, allow_nan=False)

    def test_insufficient_warmup_and_duplicate_or_empty_strategies_fail(self):
        with self.assertRaises(ValueError):
            self.run_strategy(['BUY'], candles(35))
        with self.assertRaises(ValueError):
            compare(candles(), self.config, [])
        with self.assertRaises(ValueError):
            compare(candles(), self.config, [RSIStrategy(), RSIStrategy()])

    def test_unsafe_strategy_report_identifier_is_rejected(self):
        strategy = ScriptedStrategy(['BUY'])
        strategy.name = '../outside'
        with self.assertRaises(ValueError):
            BacktestEngine(self.config).run(candles(), strategy)

    def test_data_validation_rejects_bad_timestamps_and_prices(self):
        invalid = []
        data = candles()
        data.index = data.index.tz_localize(None)
        invalid.append(data)
        invalid.extend([candles().iloc[::-1], pd.concat([candles().iloc[:2], candles().iloc[1:]])])
        data = candles().drop(candles().index[5])
        invalid.append(data)
        for column, value in [('open', 0), ('volume', -1), ('close', float('inf')), ('high', 90), ('low', 110)]:
            data = candles()
            data.iloc[0, data.columns.get_loc(column)] = value
            invalid.append(data)
        invalid.append(candles().drop(columns='volume'))
        for data in invalid:
            with self.subTest(index_type=type(data.index).__name__):
                with self.assertRaises(ValueError):
                    validate_candles(data, 60)

    def test_timezone_normalization_and_full_precision_hash(self):
        data = validate_candles(candles(), 60)
        renamed = data.copy()
        renamed.index.name = 'another_name'
        self.assertEqual(dataset_hash(data), dataset_hash(renamed))
        shifted = data.copy()
        shifted.index += pd.Timedelta(microseconds=1)
        self.assertNotEqual(dataset_hash(data), dataset_hash(shifted))
        korea = data.copy()
        korea.index = korea.index.tz_convert('Asia/Seoul')
        self.assertEqual(dataset_hash(data), dataset_hash(validate_candles(korea, 60)))

    def test_causal_indicators_no_bfill_and_prefix_invariance(self):
        data = demo_candles(100)
        full = indicators(data)
        prefix = indicators(data.iloc[:55])
        pd.testing.assert_frame_equal(full.iloc[:55], prefix)
        changed = data.copy()
        changed.iloc[55:, :4] *= 2
        pd.testing.assert_frame_equal(full.iloc[:55], indicators(changed).iloc[:55])
        self.assertTrue(full.rsi.iloc[:13].isna().all())
        self.assertTrue(full.bb_middle.iloc[:19].isna().all())
        self.assertTrue(full.macd_signal.iloc[:33].isna().all())
        self.assertIsNone(snapshot_at(full, 33))
        self.assertIsNotNone(snapshot_at(full, 34))

    def test_future_bars_cannot_change_earlier_fills_or_signals(self):
        data = demo_candles(80)
        prefix = BacktestEngine(self.config).run(data.iloc[:55], RSIStrategy())
        full = BacktestEngine(self.config).run(data, RSIStrategy())
        self.assertEqual(prefix.fills, tuple(f for f in full.fills if f.executed_at <= data.index[54]))
        self.assertEqual(prefix.signals, tuple(s for s in full.signals if s['signal_bar'] <= data.index[54]))

    def test_flat_rsi_has_documented_neutral_value_after_warmup(self):
        feature = indicators(candles())
        self.assertEqual(feature.rsi.iloc[-1], 50)
        self.assertTrue(np.isfinite(feature.iloc[-1][['rsi', 'macd', 'bb_middle']]).all())

    def test_config_and_cost_validation(self):
        for values in [(-1, 0, 0), (float('nan'), 0, 0), (0, 10000, 0), (0, 0, 20000), (True, 0, 0)]:
            with self.assertRaises(ValueError):
                CostModel(*values)
        for override in [{'position_fraction': 0}, {'position_fraction': 2}, {'bar_seconds': 0},
                         {'cooldown_seconds': -1}, {'initial_cash': 0}, {'min_order': -1}]:
            with self.assertRaises(ValueError):
                replace(self.config, **override)
        with self.assertRaises(ValueError):
            CostModel(0, 0).price(100, 'SHORT')

    def test_csv_roundtrip_and_naive_csv_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'candles.csv'
            candles().rename_axis('timestamp').to_csv(path)
            loaded = validate_candles(load_csv(path), 60)
            self.assertEqual(dataset_hash(loaded), dataset_hash(validate_candles(candles(), 60)))
            candles().tz_localize(None).rename_axis('timestamp').to_csv(path)
            with self.assertRaises(ValueError):
                load_csv(path)

    def test_report_artifacts_and_overwrite_protection(self):
        report = compare(candles(), self.config)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            write_report(report, output)
            self.assertEqual(json.loads((output / 'report.json').read_text(encoding='utf-8')), report)
            self.assertTrue((output / 'metrics.csv').is_file())
            self.assertTrue((output / 'rsi-equity.csv').is_file())
            with self.assertRaises(FileExistsError):
                write_report(report, output)

    def test_cli_demo_is_explicit_and_costs_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'demo'
            self.assertEqual(main(['--demo', '--fee-bps', '5', '--slippage-bps', '3', '--spread-bps', '2',
                                   '--strategies', 'rsi', 'consensus_2of3', '--output', str(output)]), 0)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['source'], 'SYNTHETIC_DEMO')
            self.assertEqual(report['config']['costs']['fee_bps'], '5')
            self.assertEqual(set(report['results']), {'rsi', 'consensus_2of3'})
            with patch('sys.stderr'):
                with self.assertRaises(SystemExit):
                    main(['--demo', '--output', str(output)])

    def test_research_imports_cannot_construct_a_live_broker(self):
        code = "import socket,sys; socket.socket.connect=lambda *a: (_ for _ in ()).throw(AssertionError('network')); import backtesting,strategies; assert 'pyupbit' not in sys.modules; assert 'config' not in sys.modules; assert 'trading.execution' not in sys.modules"
        child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == '__main__':
    unittest.main()
