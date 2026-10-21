import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from backtesting.collection import PublicCandles, quality_report, utc
from backtesting.data import demo_candles, dataset_hash, load_csv
from backtesting.engine import BacktestConfig, BacktestEngine, CostModel
from backtesting.historical import BuyHold, evaluate, load_protocol, split_frames, run
from strategies.signals import MACDStrategy


def candle(timestamp, market='KRW-BTC', unit=15):
    return {'market': market, 'unit': unit, 'candle_date_time_utc': timestamp,
            'opening_price': 100, 'high_price': 101, 'low_price': 99,
            'trade_price': 100, 'candle_acc_trade_volume': 10}


def response(rows, status=200, headers=None):
    value = Mock(status_code=status, headers=headers or {})
    value.json.return_value = rows
    value.raise_for_status.side_effect = requests.HTTPError('status')
    return value


def research_fixture():
    frame = demo_candles(220)
    frame.index = pd.date_range('2025-01-01', periods=len(frame), freq='15min', tz='UTC')
    protocol, _ = load_protocol()
    protocol = dict(protocol, start=frame.index[0].isoformat(), train_end=frame.index[120].isoformat(),
                    validation_end=frame.index[180].isoformat(),
                    end=(frame.index[-1] + pd.Timedelta(minutes=15)).isoformat(),
                    fee_bps_per_side=[5], slippage_bps_per_side=[5])
    return frame, protocol


class CollectorTests(unittest.TestCase):
    def test_get_only_public_url_and_audit_no_auth(self):
        session = Mock()
        session.get.return_value = response([candle('2025-01-01T00:00:00')], headers={'Date': 'audit date'})
        client = PublicCandles(session, sleep=Mock())
        client.page('KRW-BTC', 15, '2025-01-01T00:15Z')
        args, kwargs = session.get.call_args
        self.assertEqual(args, ('https://api.upbit.com/v1/candles/minutes/15',))
        self.assertNotIn('headers', kwargs)
        self.assertFalse(kwargs['allow_redirects'])
        self.assertFalse(session.trust_env)
        self.assertEqual(client.audit[0]['server_date'], 'audit date')
        self.assertEqual(len(client.audit[0]['response_sha256']), 64)
        session.post.assert_not_called()

    def test_pagination_cursor_exclusive_and_window_clipping(self):
        client = PublicCandles(Mock(), sleep=Mock())
        with patch.object(client, 'page', side_effect=[
                [candle('2025-01-01T00:45:00'), candle('2025-01-01T00:30:00')],
                [candle('2025-01-01T00:15:00'), candle('2025-01-01T00:00:00'),
                 candle('2024-12-31T23:45:00')]]) as page:
            frame, quality = client.collect('KRW-BTC', 15, '2025-01-01T00:00Z', '2025-01-01T01:00Z',
                                            now='2025-01-02T00:00Z')
        self.assertEqual(len(frame), 4)
        self.assertTrue(quality['passed'])
        self.assertEqual(page.call_args_list[1].args[2], utc('2025-01-01T00:30Z'))

    def test_response_validation_and_no_progress(self):
        for rows in ([candle('2025-01-01T00:00:00', market='KRW-ETH')],
                     [candle('2025-01-01T00:01:00')],
                     [candle('2025-01-01T00:15:00')],
                     [candle('2025-01-01T00:00:00')] * 2, {'error': 'bad'}):
            with self.subTest(rows=rows):
                session = Mock()
                session.get.return_value = response(rows)
                with self.assertRaises(ValueError):
                    PublicCandles(session, Mock()).page('KRW-BTC', 15, '2025-01-01T00:15Z')

    def test_rate_limit_transient_retry_and_hard_fail(self):
        session = Mock()
        session.get.side_effect = [response([], 429), response([], 503), response([])]
        client = PublicCandles(session, Mock())
        self.assertEqual(client.page('KRW-BTC', 15, '2025-01-01T00:00Z'), [])
        self.assertEqual(session.get.call_count, 3)
        session.get.side_effect = [response([], 418)]
        with self.assertRaisesRegex(ValueError, '418'):
            client.page('KRW-BTC', 15, '2025-01-01T00:00Z')
        session.get.side_effect = [response([], 429)] * 5
        with self.assertRaises(requests.HTTPError):
            client.page('KRW-BTC', 15, '2025-01-01T00:00Z')

    def test_timeout_is_bounded(self):
        session = Mock()
        session.get.side_effect = requests.Timeout('offline')
        with self.assertRaises(requests.Timeout):
            PublicCandles(session, Mock()).page('KRW-BTC', 60, '2025-01-01T00:00Z')
        self.assertEqual(session.get.call_count, 5)

    def test_inputs_and_incomplete_current_candles_rejected(self):
        client = PublicCandles(Mock(), Mock())
        for market, unit in [('KRW-DOGE', 15), ('KRW-BTC', 1)]:
            with self.assertRaises(ValueError):
                client.page(market, unit, '2025-01-01T00:00Z')
        for start, end in [('2025-01-01T00:01Z', '2025-01-01T01:00Z'),
                           ('2025-01-01T01:00Z', '2025-01-01T00:00Z'),
                           ('2025-01-01T00:00Z', '2025-01-01T01:00Z')]:
            with self.assertRaises(ValueError):
                client.collect('KRW-BTC', 15, start, end, now='2025-01-01T00:50Z')
        with self.assertRaises(ValueError):
            utc('2025-01-01')

    def test_availability_exact_first_candle_not_claiming_continuity(self):
        first = utc('2020-05-04T13:15Z')
        client = PublicCandles(Mock(), Mock())
        def page(market, unit, before, count=200):
            if before <= first:
                return []
            return [candle(first.tz_localize(None).isoformat())]
        with patch.object(client, 'page', side_effect=page):
            result = client.availability('KRW-BTC', 15, '2025-01-01T00:00Z')
        self.assertEqual(utc(result['earliest_observed']), first)
        self.assertFalse(result['full_history_continuity_verified'])


class HistoricalResearchTests(unittest.TestCase):
    def test_archived_public_snapshots_integrity_development_quality_and_sealed_test(self):
        directory = Path(__file__).resolve().parents[1] / 'research/2026-10-21'
        manifest = json.loads((directory / 'data/manifest.json').read_text(encoding='utf-8'))
        report = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        protocol, digest = load_protocol()
        self.assertEqual(manifest['protocol_sha256'], digest)
        self.assertTrue(report['complete'])
        self.assertFalse(report['holdout_evaluated'])
        self.assertFalse(report['selection_performed'])
        self.assertEqual(len(report['datasets']), 8)
        for key, meta in manifest['datasets'].items():
            path = directory / 'data' / meta['snapshot']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), meta['snapshot_sha256'])
            frame = load_csv(path)
            self.assertEqual(dataset_hash(frame), meta['quality']['dataset_sha256'])
            development = frame.loc[frame.index < utc(protocol['validation_end'])]
            quality = quality_report(development, int(key.rsplit('-', 1)[1]), protocol['start'], protocol['validation_end'])
            self.assertTrue(quality['passed'])
            self.assertEqual(quality['zero_volume_bars'], 0)
            results = report['datasets'][key]['results']
            self.assertEqual(len(results), 96)
            self.assertEqual({r['split'] for r in results}, {'train', 'validation'})

    def test_archived_btc_hour_primary_validation_metrics_reproduce_without_network(self):
        directory = Path(__file__).resolve().parents[1] / 'research/2026-10-21'
        frame = load_csv(directory / 'data/KRW-BTC-60.csv.gz')
        protocol, _ = load_protocol()
        development = frame.loc[frame.index < utc(protocol['validation_end'])]
        engine = BacktestEngine(BacktestConfig(CostModel(5, 5, 2), bar_seconds=3600))
        with patch('socket.socket.connect', side_effect=AssertionError('No network in CI')):
            result = engine.run(development, MACDStrategy(), evaluation_start=protocol['train_end'])
        report = json.loads((directory / 'results.json').read_text(encoding='utf-8'))
        expected = next(row['metrics'] for row in report['datasets']['KRW-BTC-60']['results']
                        if row['primary'] and row['split'] == 'validation' and row['strategy'] == 'macd')
        for metric in ('net_return_pct', 'max_drawdown_pct', 'win_rate_pct'):
            self.assertAlmostEqual(float(result.metrics[metric]), float(expected[metric]), places=8)
        self.assertEqual(result.metrics['fill_count'], expected['fill_count'])
        self.assertEqual(result.metrics['closed_trades'], expected['closed_trades'])

    def test_quality_rejects_gaps_duplicates_missing_edges_nonfinite_and_bounds(self):
        frame, protocol = research_fixture()
        quality = quality_report(frame, 15, protocol['start'], protocol['end'])
        self.assertEqual(quality['rows'], 220)
        broken = [frame.iloc[1:], frame.iloc[:-1], frame.drop(frame.index[70]),
                  pd.concat([frame.iloc[:1], frame])]
        invalid = frame.copy()
        invalid.iloc[1, invalid.columns.get_loc('high')] = 1
        broken.append(invalid)
        nonfinite = frame.copy()
        nonfinite.iloc[1, 0] = float('nan')
        broken.append(nonfinite)
        for data in broken:
            with self.assertRaises(ValueError):
                quality_report(data, 15, protocol['start'], protocol['end'])

    def test_split_disjoint_complete_time_order_and_protocol_validation(self):
        frame, protocol = research_fixture()
        splits = split_frames(frame, protocol)
        self.assertEqual([len(splits[k]) for k in ('train', 'validation', 'test')], [120, 60, 40])
        self.assertLess(splits['train'].index[-1], splits['validation'].index[0])
        self.assertLess(splits['validation'].index[-1], splits['test'].index[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'protocol.json'
            protocol['train_end'] = protocol['start']
            path.write_text(json.dumps(protocol), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_protocol(path)

    def test_prepared_cache_matches_original_engine_and_rejects_interval_mismatch(self):
        frame, _ = research_fixture()
        engine = BacktestEngine(BacktestConfig(CostModel(5, 5), bar_seconds=900))
        prepared = engine.prepare(frame)
        self.assertEqual(engine.run(frame, MACDStrategy()).to_dict(),
                         engine.run(prepared, MACDStrategy()).to_dict())
        other = BacktestEngine(BacktestConfig(CostModel(5, 5), bar_seconds=3600))
        with self.assertRaises(ValueError):
            other.run(prepared, MACDStrategy())

    def test_validation_context_has_no_trades_equity_or_signal_before_split(self):
        frame, _ = research_fixture()
        engine = BacktestEngine(BacktestConfig(CostModel(5, 5), bar_seconds=900))
        start = frame.index[120]
        result = engine.run(frame, BuyHold(), evaluation_start=start)
        self.assertEqual(result.fills[0].signal_bar, start)
        self.assertEqual(result.fills[0].executed_at, frame.index[121])
        self.assertEqual(result.equity[0]['cash'], engine.config.initial_cash)
        self.assertEqual(len(result.equity), 100)
        self.assertEqual(len(result.fills), 1)

    def test_holdout_never_enters_features_or_results_and_no_test_metrics(self):
        frame, protocol = research_fixture()
        original = evaluate(frame, protocol, 15)
        poisoned = frame.copy()
        poisoned.loc[poisoned.index >= utc(protocol['validation_end']), :] = float('nan')
        self.assertEqual(original, evaluate(poisoned, protocol, 15))
        self.assertEqual({row['split'] for row in original}, {'train', 'validation'})
        self.assertEqual(len(original), 16)

    def test_sensitivity_grid_complete_no_selection_and_baseline_exposure(self):
        frame, protocol = research_fixture()
        protocol['fee_bps_per_side'] = [5, 10]
        protocol['slippage_bps_per_side'] = [0, 5, 10]
        results = evaluate(frame, protocol, 15)
        self.assertEqual(len(results), 2 * 8 * 2 * 3)
        for split in ('train', 'validation'):
            baselines = [row for row in results if row['split'] == split and row['strategy'].startswith('buy_hold_')]
            self.assertEqual({row['exposure_fraction'] for row in baselines}, {'0.4', '1'})
            for row in baselines:
                self.assertEqual(row['metrics']['fill_count'], 1)
                self.assertEqual(row['metrics']['closed_trades'], 0)
                self.assertIsNone(row['metrics']['win_rate_pct'])

    def test_snapshot_gzip_round_trip_keeps_normalized_hash(self):
        frame, _ = research_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'candles.csv.gz'
            path.write_bytes(gzip.compress(frame.to_csv(index_label='timestamp', float_format='%.17g').encode(), mtime=0))
            self.assertEqual(dataset_hash(frame), dataset_hash(load_csv(path)))

    def test_snapshot_tamper_fails_before_evaluation(self):
        protocol, digest = load_protocol()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / 'KRW-BTC-15.csv.gz'
            path.write_bytes(b'changed')
            manifest = {'protocol': protocol, 'protocol_sha256': digest, 'completed_at': 'offline',
                        'datasets': {f'{market}-{unit}': {'snapshot': f'{market}-{unit}.csv.gz', 'snapshot_sha256': '0' * 64}
                                     for market in protocol['markets'] for unit in protocol['units_minutes']}}
            (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'byte hash'):
                run(directory, directory / 'report')

    def test_incomplete_acquisition_rejected_before_creating_results(self):
        protocol, digest = load_protocol()
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            manifest = {'protocol': protocol, 'protocol_sha256': digest, 'datasets': {}}
            (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                run(directory, directory / 'report')
            self.assertFalse((directory / 'report').exists())

    def test_quarantined_holdout_gap_does_not_imply_development_gap(self):
        frame, protocol = research_fixture()
        gapped = frame.drop(frame.index[-5])
        quality = quality_report(gapped, 15, protocol['start'], protocol['end'], strict=False)
        self.assertFalse(quality['passed'])
        self.assertEqual(quality['missing'], 1)
        development = gapped.loc[gapped.index < utc(protocol['validation_end'])]
        self.assertTrue(quality_report(development, 15, protocol['start'], protocol['validation_end'])['passed'])
        self.assertEqual(evaluate(frame, protocol, 15), evaluate(gapped, protocol, 15))


if __name__ == '__main__':
    unittest.main()
