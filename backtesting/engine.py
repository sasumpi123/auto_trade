"""Deterministic next-open simulation, isolated from live/persistent execution."""
from dataclasses import asdict, dataclass, field
from decimal import Decimal, DecimalException, ROUND_DOWN
import platform
import re

import pandas as pd
import numpy as np

from strategies.signals import Strategy, research_strategies
from .risk import RiskOverlay
from .data import dataset_hash, indicators, snapshot_at, validate_candles


def decimal(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a monetary value')
    try:
        result = Decimal(str(value))
    except (DecimalException, ValueError, TypeError) as exc:
        raise ValueError('Invalid decimal') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('Expected a finite non-negative decimal')
    return result


@dataclass(frozen=True)
class CostModel:
    # Research inputs, NOT a claim about current exchange fees.
    fee_bps: Decimal
    slippage_bps: Decimal
    spread_bps: Decimal = Decimal('0')

    def __post_init__(self):
        for name in ('fee_bps', 'slippage_bps', 'spread_bps'):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if self.fee_bps >= 10000 or self.slippage_bps + self.spread_bps / 2 >= 10000:
            raise ValueError('Costs must leave positive execution prices')

    @property
    def fee_rate(self):
        return self.fee_bps / 10000

    def price(self, opening, side):
        if side not in ('BUY', 'SELL'):
            raise ValueError('Invalid execution side')
        impact = (self.slippage_bps + self.spread_bps / 2) / 10000
        return decimal(opening) * (1 + impact if side == 'BUY' else 1 - impact)


@dataclass(frozen=True)
class BacktestConfig:
    costs: CostModel
    initial_cash: Decimal = Decimal('1000000')
    position_fraction: Decimal = Decimal('0.4')
    min_order: Decimal = Decimal('5000')
    bar_seconds: int = 60
    cooldown_seconds: int = 300
    min_volume_ratio: Decimal = Decimal('0.5')

    def __post_init__(self):
        if not isinstance(self.costs, CostModel):
            raise ValueError('Explicit cost model required')
        for name in ('initial_cash', 'position_fraction', 'min_order', 'min_volume_ratio'):
            object.__setattr__(self, name, decimal(getattr(self, name)))
        if self.initial_cash <= 0 or not 0 < self.position_fraction <= 1:
            raise ValueError('Invalid initial cash or position fraction')
        if type(self.bar_seconds) is not int or self.bar_seconds <= 0:
            raise ValueError('bar_seconds must be a positive integer')
        if type(self.cooldown_seconds) is not int or self.cooldown_seconds < 0:
            raise ValueError('cooldown_seconds must be a non-negative integer')


def serializable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [serializable(item) for item in value]
    return value


@dataclass(frozen=True)
class Fill:
    side: str
    signal_bar: pd.Timestamp
    decided_at: pd.Timestamp
    executed_at: pd.Timestamp
    quantity: Decimal
    price: Decimal
    notional: Decimal
    fee: Decimal
    cash_after: Decimal
    realized_pnl: Decimal | None
    reason: str = "signal"


@dataclass
class SimulationPortfolio:
    """In-memory fills only; cannot accept a real broker or persist an order."""
    cash: Decimal
    quantity: Decimal = Decimal('0')
    entry_cost: Decimal = Decimal('0')
    fees: Decimal = Decimal('0')
    entry_notional: Decimal = Decimal('0')
    averaging_used: bool = False
    fills: list[Fill] = field(default_factory=list)

    def execute(self, pending, timestamp, opening, config):
        side = pending['action']
        price = config.costs.price(opening, side)
        if side == 'BUY':
            averaging = pending.get('averaging') is True
            if self.quantity and (not averaging or self.averaging_used):
                return False
            if averaging and not self.quantity:
                raise ValueError('Cannot average an empty position')
            budget = min(pending['budget'], self.cash) if averaging else min(config.initial_cash * config.position_fraction, self.cash)
            quantity = (budget / (price * (1 + config.costs.fee_rate))).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN)
            notional = quantity * price
            if quantity <= 0 or notional < config.min_order:
                return False
            fee = notional * config.costs.fee_rate
            self.cash -= notional + fee
            self.quantity += quantity
            self.entry_cost += notional + fee
            self.entry_notional += notional
            self.averaging_used = averaging
            realized = None
        elif side == 'SELL':
            if not self.quantity:
                return False
            quantity = self.quantity
            notional = quantity * price
            fee = notional * config.costs.fee_rate
            self.cash += notional - fee
            realized = notional - fee - self.entry_cost
            self.quantity = Decimal('0')
            self.entry_cost = Decimal('0')
            self.entry_notional = Decimal('0')
            self.averaging_used = False
        else:
            raise ValueError('Only BUY/SELL may execute')
        if self.cash < 0:
            raise ValueError('Simulation must not borrow cash')
        self.fees += fee
        self.fills.append(Fill(side, pending['signal_bar'], pending['decided_at'], timestamp,
                               quantity, price, notional, fee, self.cash, realized, pending.get('reason', 'signal')))
        return True


@dataclass(frozen=True)
class BacktestResult:
    strategy: str
    data_hash: str
    config: BacktestConfig
    warmup_bars: int
    metrics: dict
    fills: tuple[Fill, ...]
    equity: tuple[dict, ...]
    signals: tuple[dict, ...]
    unfilled_final_signal: dict | None

    def to_dict(self):
        return serializable(asdict(self))


@dataclass(frozen=True)
class PreparedCandles:
    # Internal immutable-by-contract cache; callers must not mutate frame/features.
    frame: pd.DataFrame
    features: pd.DataFrame
    snapshots: tuple
    first: int
    data_hash: str
    bar_seconds: int


class BacktestEngine:
    def __init__(self, config):
        if not isinstance(config, BacktestConfig):
            raise ValueError('BacktestConfig required')
        self.config = config

    def prepare(self, data):
        frame = validate_candles(data, self.config.bar_seconds)
        features = indicators(frame)
        snapshots = tuple(snapshot_at(features, i) for i in range(len(frame)))
        first = next((i for i, snapshot in enumerate(snapshots) if snapshot is not None), None)
        if first is None or first + 1 >= len(frame):
            raise ValueError('Insufficient common indicator warm-up plus a following execution bar')
        return PreparedCandles(frame, features, snapshots, first, dataset_hash(frame), self.config.bar_seconds)

    def run(self, data, strategy: Strategy, *, evaluation_start=None, overlay=None):
        overlay = RiskOverlay() if overlay is None else overlay
        if not isinstance(overlay, RiskOverlay):
            raise ValueError('Explicit research RiskOverlay required')
        config = self.config
        if not isinstance(strategy.name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', strategy.name):
            raise ValueError('Strategy names must be safe, unique report identifiers')
        prepared = data if isinstance(data, PreparedCandles) else self.prepare(data)
        if prepared.bar_seconds != config.bar_seconds:
            raise ValueError('Prepared candle interval mismatch')
        frame, features, snapshots, first = prepared.frame, prepared.features, prepared.snapshots, prepared.first
        if evaluation_start is None:
            evaluation_start = frame.index[0]
        evaluation_start = pd.Timestamp(evaluation_start)
        if evaluation_start.tzinfo is None or evaluation_start not in frame.index:
            raise ValueError('Evaluation start must be a timezone-aware candle open in the dataset')
        if evaluation_start > frame.index[-2]:
            raise ValueError('Evaluation requires at least two bars')
        portfolio = SimulationPortfolio(config.initial_cash)
        pending, last_signal_at = None, None
        curve, signals = [], []
        peak = config.initial_cash
        max_drawdown = Decimal('0')
        for i, row in enumerate(frame.itertuples()):
            timestamp = row.Index
            if timestamp < evaluation_start:
                continue
            # A signal from the previous COMPLETED bar executes at this bar's OPEN.
            if pending is not None:
                executed = portfolio.execute(pending, timestamp, row.open, config)
                if not executed and pending.get('averaging'):
                    # At the execution open, minimum/rounding may make averaging impossible.
                    # Operating policy then stops; no invented successful averaging receipt.
                    portfolio.execute({**pending, 'action': 'SELL', 'reason': 'stop_average_unfilled'},
                                      timestamp, row.open, config)
                pending = None
            equity = portfolio.cash + portfolio.quantity * decimal(row.close)
            peak = max(peak, equity)
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)
            curve.append({'timestamp': timestamp + pd.Timedelta(seconds=config.bar_seconds),
                          'cash': portfolio.cash, 'quantity': portfolio.quantity,
                          'close': decimal(row.close), 'equity': equity, 'drawdown': drawdown})
            risk = overlay.decide(portfolio, decimal(row.close), config)
            if risk is not None:
                # Completed-close observation, before signal-only filters. No intrabar high/low.
                pending = {**risk, 'signal_bar': timestamp,
                           'decided_at': timestamp + pd.Timedelta(seconds=config.bar_seconds),
                           'scheduled': True}
                signals.append(pending)
                continue
            snapshot = snapshots[i]
            if snapshot is None:
                continue
            if row.volume <= 0 or decimal(row.volume) < decimal(features.volume_average.iloc[i]) * config.min_volume_ratio:
                continue
            decided_at = timestamp + pd.Timedelta(seconds=config.bar_seconds)
            if last_signal_at is not None and (decided_at - last_signal_at).total_seconds() < config.cooldown_seconds:
                continue
            signal = strategy.decide(snapshot)
            if signal.action not in ('BUY', 'SELL', 'HOLD'):
                raise ValueError('Strategy returned an invalid action')
            if signal.action == 'HOLD':
                continue
            last_signal_at = decided_at
            executable = (signal.action == 'BUY' and not portfolio.quantity) or (
                signal.action == 'SELL' and portfolio.quantity > 0)
            decision = {'signal_bar': timestamp, 'decided_at': decided_at, 'action': signal.action,
                        'reason': signal.reason, 'votes': signal.votes,
                        'target_price_metadata': signal.target_price, 'scheduled': bool(executable)}
            signals.append(decision)
            if executable:
                pending = decision
        # Open positions are marked to the final close; no invented final sell.
        final_equity = curve[-1]['equity']
        closed = [fill.realized_pnl for fill in portfolio.fills if fill.side == 'SELL']
        realized = sum(closed, Decimal('0'))
        unrealized = portfolio.quantity * decimal(frame.close.iloc[-1]) - portfolio.entry_cost
        metrics = {
            'initial_cash': config.initial_cash, 'final_equity': final_equity,
            'net_pnl': final_equity - config.initial_cash,
            'net_return_pct': (final_equity / config.initial_cash - 1) * 100,
            'max_drawdown_pct': max_drawdown * 100, 'total_fees': portfolio.fees,
            'fill_count': len(portfolio.fills), 'closed_trades': len(closed),
            'win_rate_pct': Decimal(sum(pnl > 0 for pnl in closed)) / len(closed) * 100 if closed else None,
            'realized_pnl': realized, 'unrealized_pnl': unrealized,
            'ending_cash': portfolio.cash, 'ending_quantity': portfolio.quantity,
            'invested_bar_pct': Decimal(sum(row['quantity'] > 0 for row in curve)) / len(curve) * 100,
            'mean_market_exposure_pct': sum((row['quantity'] * row['close'] / row['equity'] * 100
                for row in curve), Decimal('0')) / len(curve),
            'max_market_exposure_pct': max(row['quantity'] * row['close'] / row['equity'] * 100 for row in curve),
            'averaging_fill_count': sum(fill.reason == 'average_once' for fill in portfolio.fills),
            'take_profit_count': sum(fill.reason == 'take_profit_5' for fill in portfolio.fills),
            'stop_count': sum(fill.reason in ('stop_5', 'stop_average_unfilled') for fill in portfolio.fills),
        }
        return BacktestResult(strategy.name, prepared.data_hash, config, first, metrics,
                              tuple(portfolio.fills), tuple(curve), tuple(signals), pending)


def compare(data, config, strategies=None, *, source='provided_dataframe'):
    candidates = list(research_strategies() if strategies is None else strategies)
    names = [strategy.name for strategy in candidates]
    if not names or len(set(names)) != len(names):
        raise ValueError('A non-empty set of uniquely named strategies is required')
    results = [BacktestEngine(config).run(data, strategy) for strategy in candidates]
    if len({result.data_hash for result in results}) != 1:
        raise ValueError('Comparison inputs differ')
    return {'schema_version': 1, 'research_only': True, 'source': source,
            'runtime': {'python': platform.python_version(), 'pandas': pd.__version__, 'numpy': np.__version__},
            'data_hash': results[0].data_hash, 'bars': len(data),
            'assumptions': {'single_asset': True, 'long_only': True,
                            'execution': 'next_bar_open', 'final_liquidation': False,
                            'risk_overlay': 'signal_only_no_stop_take_profit_or_averaging',
                            'warmup': 'common_all_indicators_no_bfill',
                            'quantity_step': '0.00000001', 'cost_bps_are_research_inputs': True},
            'config': serializable(asdict(config)),
            'results': {result.strategy: result.to_dict() for result in results}}
