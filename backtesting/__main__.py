"""Run an offline, explicitly costed experiment: python -m backtesting --help."""
import argparse
import csv
from decimal import Decimal
import json
from pathlib import Path

from strategies.signals import LegacyCompositeStrategy, research_strategies
from .data import demo_candles, load_csv
from .engine import BacktestConfig, CostModel, compare, decimal


def write_report(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    metrics = [{'strategy': name, **result['metrics']} for name, result in report['results'].items()]
    with (directory / 'metrics.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    for name, result in report['results'].items():
        for key in ('fills', 'equity', 'signals'):
            rows = result[key]
            if rows:
                with (directory / f'{name}-{key}.csv').open('w', newline='', encoding='utf-8') as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                    writer.writeheader()
                    writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline indicator comparison; never connects to an exchange')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--csv', type=Path, help='Timezone-aware candle OPEN timestamps and OHLCV')
    source.add_argument('--demo', action='store_true', help='SYNTHETIC data, not market performance')
    parser.add_argument('--fee-bps', required=True, type=decimal, help='Research assumption per side, not a quoted exchange fee')
    parser.add_argument('--slippage-bps', required=True, type=decimal)
    parser.add_argument('--spread-bps', default=Decimal('0'), type=decimal, help='Full spread; half is applied per side')
    parser.add_argument('--bar-seconds', default=60, type=int)
    parser.add_argument('--cash', default=Decimal('1000000'), type=decimal)
    parser.add_argument('--position-fraction', default=Decimal('0.4'), type=decimal)
    parser.add_argument('--cooldown-seconds', default=300, type=int)
    parser.add_argument('--output', required=True, type=Path, help='A NEW directory; existing outputs are not overwritten')
    names = ['rsi', 'macd', 'bollinger', 'consensus_2of3', 'consensus_3of3', 'legacy_composite']
    parser.add_argument('--strategies', nargs='+', choices=names, default=names[:-1])
    args = parser.parse_args(argv)
    try:
        if args.demo and args.bar_seconds != 60:
            raise ValueError('Demo data uses 60-second bars')
        data = demo_candles() if args.demo else load_csv(args.csv)
        registry = {strategy.name: strategy for strategy in [*research_strategies(), LegacyCompositeStrategy()]}
        config = BacktestConfig(CostModel(args.fee_bps, args.slippage_bps, args.spread_bps),
                                initial_cash=args.cash, position_fraction=args.position_fraction,
                                bar_seconds=args.bar_seconds, cooldown_seconds=args.cooldown_seconds)
        report = compare(data, config, [registry[name] for name in args.strategies],
                         source='SYNTHETIC_DEMO' if args.demo else 'USER_SUPPLIED_CSV')
        write_report(report, args.output)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f'Research-only comparison saved to {args.output}; no exchange orders executed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
