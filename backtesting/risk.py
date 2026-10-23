"""Fixed research-only risk hypotheses; never imported by trading/ or main.py."""
from dataclasses import dataclass
from decimal import Decimal

OVERLAYS = ('signal_only', 'take_profit_5', 'stop_5', 'take_profit_stop_5',
            'take_profit_average_once_5')


@dataclass(frozen=True)
class RiskOverlay:
    name: str = 'signal_only'

    def __post_init__(self):
        if self.name not in OVERLAYS:
            raise ValueError('Unknown fixed research overlay')

    def decide(self, portfolio, close, config):
        if not portfolio.quantity or self.name == 'signal_only':
            return None
        # Match operating threshold definition (fees excluded), not accounting cost.
        average = portfolio.entry_notional / portfolio.quantity
        if self.name != 'stop_5' and close >= average * Decimal('1.05'):
            return {'action': 'SELL', 'reason': 'take_profit_5'}
        if self.name == 'take_profit_5' or close > average * Decimal('0.95'):
            return None
        if self.name == 'take_profit_average_once_5' and not portfolio.averaging_used:
            budget = min(portfolio.cash, portfolio.quantity * close * Decimal('0.5'))
            if budget > 0 and budget / (1 + config.costs.fee_rate) >= config.min_order:
                return {'action': 'BUY', 'reason': 'average_once', 'averaging': True, 'budget': budget}
        return {'action': 'SELL', 'reason': 'stop_5'}
