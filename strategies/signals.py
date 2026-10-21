"""Research hypotheses and a compatibility strategy for the existing bot.

Strategies see only one completed observation; costs, sizing, cooldown and
execution belong to their callers. New research strategies are NOT live defaults.
"""
from dataclasses import dataclass
import math
from typing import Protocol


@dataclass(frozen=True)
class Snapshot:
    close: float
    rsi: float
    rsi_delta: float
    macd: float
    macd_signal: float
    macd_delta: float
    bb_lower: float
    bb_middle: float
    bb_upper: float

    def __post_init__(self):
        if not all(math.isfinite(v) for v in vars(self).values()) or self.close <= 0:
            raise ValueError('A snapshot must contain finite indicators and a positive close')


@dataclass(frozen=True)
class Signal:
    action: str = 'HOLD'
    reason: str | None = None
    target_price: float | None = None
    votes: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if self.action not in ('BUY', 'SELL', 'HOLD'):
            raise ValueError('Invalid signal action')


class Strategy(Protocol):
    name: str

    def decide(self, snapshot: Snapshot) -> Signal:
        ...


@dataclass(frozen=True)
class RSIStrategy:
    name: str = 'rsi'

    def decide(self, s):
        if s.rsi < 30 and s.rsi_delta > 0:
            return Signal('BUY', f'RSI 과매도 반등({s.rsi:.1f})', s.close * 1.05)
        if s.rsi > 70 and s.rsi_delta < 0:
            return Signal('SELL', f'RSI 과매수 하락({s.rsi:.1f})')
        return Signal()


@dataclass(frozen=True)
class MACDStrategy:
    name: str = 'macd'

    def decide(self, s):
        # Preserve the existing level-and-trend rule; this is not a new crossover definition.
        difference = s.macd - s.macd_signal
        if s.macd > s.macd_signal and s.macd < 0 and s.macd_delta > 0:
            return Signal('BUY', f'MACD 골든크로스 상승({difference:.1f})', s.close * 1.03)
        if s.macd < s.macd_signal and s.macd > 0 and s.macd_delta < 0:
            return Signal('SELL', f'MACD 데드크로스 하락({difference:.1f})')
        return Signal()


@dataclass(frozen=True)
class BollingerStrategy:
    name: str = 'bollinger'

    def decide(self, s):
        # Research-only standalone band rule: no RSI/MACD confirmation dependency.
        if s.close < s.bb_lower:
            return Signal('BUY', 'BB 하단 밴드 아래', s.bb_middle)
        if s.close > s.bb_upper:
            return Signal('SELL', 'BB 상단 밴드 위')
        return Signal()


@dataclass(frozen=True)
class ConsensusStrategy:
    quorum: int = 2

    def __post_init__(self):
        if self.quorum not in (2, 3):
            raise ValueError('Consensus quorum must be 2 or 3')

    @property
    def name(self):
        return f'consensus_{self.quorum}of3'

    def decide(self, s):
        votes = tuple((strategy.name, strategy.decide(s).action)
                      for strategy in (RSIStrategy(), MACDStrategy(), BollingerStrategy()))
        buys = sum(action == 'BUY' for _, action in votes)
        sells = sum(action == 'SELL' for _, action in votes)
        if buys >= self.quorum:
            return Signal('BUY', f'{buys}/3 indicator consensus', votes=votes)
        if sells >= self.quorum:
            return Signal('SELL', f'{sells}/3 indicator consensus', votes=votes)
        return Signal(votes=votes)


@dataclass(frozen=True)
class LegacyCompositeStrategy:
    """Exact existing sequential priority, BB confirmation and targets for the bot."""
    name: str = 'legacy_composite'

    def decide(self, s):
        rsi = RSIStrategy().decide(s)
        macd = MACDStrategy().decide(s)
        position = (s.close - s.bb_middle) / s.bb_middle * 100
        bb = Signal()
        if s.close < s.bb_lower and (s.rsi_delta > 0 or s.macd_delta > 0):
            bb = Signal('BUY', f'BB 하단 반등({position:.1f}%)', s.bb_middle)
        elif s.close > s.bb_upper:
            bb = Signal('SELL', f'BB 상단 돌파({position:.1f}%)')
        action, target = 'HOLD', None
        reasons = []
        for signal in (rsi, macd, bb):
            if signal.action != 'HOLD':
                action = signal.action
                reasons.append(signal.reason)
                if signal.action == 'BUY':
                    target = signal.target_price
        buys = sum(signal.action == 'BUY' for signal in (rsi, macd, bb))
        sells = sum(signal.action == 'SELL' for signal in (rsi, macd, bb))
        if buys >= 2:
            action, target = 'BUY', s.close * 1.05
            reasons.append(f'복합 매수 신호({buys}개)')
        elif sells >= 2:
            action = 'SELL'
            reasons.append(f'복합 매도 신호({sells}개)')
        return Signal(action, ' & '.join(reasons) if reasons else None, target)


def research_strategies():
    """A new set of stateless candidates for each experiment, never live selection."""
    return [RSIStrategy(), MACDStrategy(), BollingerStrategy(),
            ConsensusStrategy(2), ConsensusStrategy(3)]
