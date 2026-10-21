"""Reproducible public-data collection and sealed-holdout research CLI."""
import argparse
from dataclasses import dataclass, replace
import gzip
import hashlib
import json
from pathlib import Path

import pandas as pd

from strategies.signals import LegacyCompositeStrategy, Signal, research_strategies
from .collection import MARKETS, UNITS, PublicCandles, quality_report, utc, write_json
from .data import dataset_hash, load_csv
from .engine import BacktestConfig, BacktestEngine, CostModel, serializable

DEFAULT_PROTOCOL = Path(__file__).with_name('protocol.json')


def load_protocol(path=DEFAULT_PROTOCOL):
    raw = Path(path).read_text(encoding='utf-8').encode('utf-8')  # Normalize checkout CRLF to LF.
    protocol = json.loads(raw)
    boundaries = [utc(protocol[key]) for key in ('start', 'train_end', 'validation_end', 'end')]
    if any(a >= b for a, b in zip(boundaries, boundaries[1:])):
        raise ValueError('Strict chronological split boundaries required')
    if protocol['markets'] != list(MARKETS) or protocol['units_minutes'] != list(UNITS):
        raise ValueError('Protocol market/interval mismatch')
    if protocol['holdout_policy'] != 'sealed_not_backtested; no parameter or strategy selection':
        raise ValueError('Holdout must remain sealed')
    for key in ('fee_bps_per_side', 'slippage_bps_per_side'):
        values = protocol[key]
        if not values or len(set(values)) != len(values) or any(type(x) is not int or x < 0 for x in values):
            raise ValueError('Cost grid must contain distinct nonnegative integer bps')
    if protocol['primary_fee_bps'] not in protocol['fee_bps_per_side'] or protocol['primary_slippage_bps'] not in protocol['slippage_bps_per_side']:
        raise ValueError('Primary cost point must be in the declared grid')
    return protocol, hashlib.sha256(raw).hexdigest()


def split_frames(frame, protocol):
    start, train_end, validation_end, end = [utc(protocol[key]) for key in
                                            ('start', 'train_end', 'validation_end', 'end')]
    return {'train': frame.loc[(frame.index >= start) & (frame.index < train_end)],
            'validation': frame.loc[(frame.index >= train_end) & (frame.index < validation_end)],
            'test': frame.loc[(frame.index >= validation_end) & (frame.index < end)]}


@dataclass(frozen=True)
class BuyHold:
    name: str = 'buy_hold_40'

    def decide(self, snapshot):
        return Signal('BUY', 'Passive entry; never sell; same execution/cost rules')


def acquire(directory, protocol_path=DEFAULT_PROTOCOL, client=None):
    protocol, protocol_hash = load_protocol(protocol_path)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    # Write the exact protocol BEFORE looking at any outcome; never choose dates from performance.
    write_json(directory / 'protocol.json', protocol)
    client = client or PublicCandles()
    manifest = {'protocol_sha256': protocol_hash, 'protocol': protocol,
                'started_at': pd.Timestamp.now(tz='UTC').isoformat(), 'datasets': {},
                'endpoint': 'https://api.upbit.com/v1/candles/minutes/{unit}',
                'auth': 'none; public GET only', 'availability_scope': 'existence probes, not full-history gap audit'}
    write_json(directory / 'manifest.json', manifest)
    for market in protocol['markets']:
        for unit in protocol['units_minutes']:
            key = f'{market}-{unit}'
            print(f'Collecting {key}', flush=True)
            audit_begin = len(client.audit)
            availability = client.availability(market, unit, protocol['end'])
            frame, quality = client.collect(market, unit, protocol['start'], protocol['end'])
            path = directory / f'{key}.csv.gz'
            text = frame.to_csv(index_label='timestamp', float_format='%.17g', lineterminator='\n')
            # mtime=0 makes the public-data snapshot byte-reproducible.
            path.write_bytes(gzip.compress(text.encode(), mtime=0))
            splits = split_frames(frame, protocol)
            manifest['datasets'][key] = {'availability': availability, 'quality': quality,
                'snapshot': path.name, 'snapshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'splits': {name: {'rows': len(part), 'sha256': dataset_hash(part),
                                 'first_open': part.index[0].isoformat(), 'last_open': part.index[-1].isoformat(),
                                 'planned_for_evaluation': name != 'test'} for name, part in splits.items()},
                'requests': client.audit[audit_begin:]}
            write_json(directory / 'manifest.json', manifest)
    manifest['completed_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    write_json(directory / 'manifest.json', manifest)
    return manifest


def evaluate(frame, protocol, unit):
    """Test rows never enter indicators, portfolio accounting or strategy evaluation."""
    splits = split_frames(frame, protocol)
    results = []
    base = BacktestConfig(CostModel(5, 5, protocol['full_spread_bps']),
                          initial_cash=protocol['initial_cash'], position_fraction=protocol['position_fraction'],
                          bar_seconds=unit * 60, cooldown_seconds=protocol['cooldown_seconds'],
                          min_volume_ratio=protocol['min_volume_ratio'])
    # Fixed history before each split is indicator context only; portfolio is reset per split.
    for split in ('train', 'validation'):
        part = splits[split]
        context = frame.loc[frame.index <= part.index[-1]]
        prepared = BacktestEngine(base).prepare(context)
        # All candidates including passive baseline share this completed observation/entry opportunity.
        eligible = max(prepared.first, context.index.get_loc(part.index[0]))
        candidates = [*research_strategies(), LegacyCompositeStrategy(), BuyHold(), BuyHold('buy_hold_100')]
        if [candidate.name for candidate in candidates] != protocol['strategies']:
            raise ValueError('Frozen strategy registry changed')
        for fee in protocol['fee_bps_per_side']:
            for slippage in protocol['slippage_bps_per_side']:
                config = replace(base, costs=CostModel(fee, slippage, protocol['full_spread_bps']))
                for candidate in candidates:
                    selected = replace(config, position_fraction='1') if candidate.name == 'buy_hold_100' else config
                    if candidate.name.startswith('buy_hold_'):
                        selected = replace(selected, min_volume_ratio='0')
                    # Passive benchmarks buy once at the common first eligible next-open, without a signal volume veto.
                    # The benchmark gets no signal/cooldown benefit; it merely stays invested after that entry.
                    engine = BacktestEngine(selected)
                    outcome = engine.run(prepared, candidate, evaluation_start=part.index[0])
                    results.append({'split': split, 'strategy': candidate.name, 'fee_bps': fee,
                        'slippage_bps': slippage, 'spread_bps': protocol['full_spread_bps'],
                        'exposure_fraction': str(selected.position_fraction),
                        'primary': fee == protocol['primary_fee_bps'] and slippage == protocol['primary_slippage_bps'],
                        'segment_sha256': dataset_hash(part), 'context_sha256': prepared.data_hash,
                        'first_eligible_signal_bar': context.index[eligible].isoformat(),
                        'metrics': serializable(outcome.metrics)})
    return results


def run(directory, output, protocol_path=DEFAULT_PROTOCOL):
    protocol, protocol_hash = load_protocol(protocol_path)
    directory, output = Path(directory), Path(output)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['protocol_sha256'] != protocol_hash or manifest['protocol'] != protocol:
        raise ValueError('Collected snapshot does not match frozen protocol')
    expected_keys = {f'{market}-{unit}' for market in protocol['markets'] for unit in protocol['units_minutes']}
    if set(manifest['datasets']) != expected_keys or not manifest.get('completed_at'):
        raise ValueError('Acquisition is incomplete; evaluation must wait for all snapshots')
    output.mkdir(parents=True, exist_ok=False)
    report = {'complete': False, 'protocol_sha256': protocol_hash, 'protocol': protocol, 'datasets': {},
              'holdout_evaluated': False, 'selection_performed': False,
              'runtime': {'python': __import__('platform').python_version(), 'pandas': pd.__version__},
              'marking': 'final close; no forced liquidation or hypothetical exit cost',
              'win_rate': 'profitable closed round trips / closed round trips; null if none',
              'trade_counts': 'fill_count is orders; closed_trades is closed round trips'}
    for market in protocol['markets']:
        for unit in protocol['units_minutes']:
            key = f'{market}-{unit}'
            meta = manifest['datasets'][key]
            if meta['snapshot'] != f'{key}.csv.gz':
                raise ValueError('Snapshot filename must match market/interval')
            path = directory / meta['snapshot']
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta['snapshot_sha256']:
                raise ValueError('Snapshot byte hash mismatch')
            frame = load_csv(path)
            quality = quality_report(frame, unit, protocol['start'], protocol['end'], strict=False)
            if quality['dataset_sha256'] != meta['quality']['dataset_sha256']:
                raise ValueError('Normalized dataset hash mismatch')
            # A defective sealed test window cannot contaminate otherwise complete development data.
            development = frame.loc[frame.index < utc(protocol['validation_end'])]
            development_quality = quality_report(development, unit, protocol['start'], protocol['validation_end'])
            print(f'Evaluating {key}; holdout sealed', flush=True)
            report['datasets'][key] = {'quality': quality, 'development_quality': development_quality,
                                       'availability': meta['availability'],
                                       'results': evaluate(frame, protocol, unit)}
            write_json(output / 'results.json', report)
    rows = []
    for key, dataset in report['datasets'].items():
        for result in dataset['results']:
            rows.append({'dataset': key, **{k: v for k, v in result.items() if k != 'metrics'}, **result['metrics']})
    pd.DataFrame(rows).to_csv(output / 'metrics.csv', index=False)
    report['complete'] = True
    write_json(output / 'results.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='Public historical research only; no account/order APIs')
    parser.add_argument('action', choices=('collect', 'evaluate'))
    parser.add_argument('--data', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--protocol', default=DEFAULT_PROTOCOL, type=Path)
    args = parser.parse_args(argv)
    if args.action == 'collect':
        acquire(args.data, args.protocol)
    elif args.output is None:
        parser.error('--output is required for evaluate')
    else:
        run(args.data, args.output, args.protocol)


if __name__ == '__main__':
    main()
