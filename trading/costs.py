"""Pure explicit cost assumptions; no data, broker or network dependencies."""
from dataclasses import dataclass
from decimal import Decimal,DecimalException


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



def policy(model):
    return {'schema_version':1,'fee_bps':str(model.fee_bps),'slippage_bps':str(model.slippage_bps),
            'full_spread_bps':str(model.spread_bps)}


def from_policy(value):
    if (type(value) is not dict or set(value)!={'schema_version','fee_bps','slippage_bps','full_spread_bps'}
            or type(value['schema_version']) is not int or value['schema_version']!=1):
        raise ValueError('PAPER_COST_POLICY_UNVERIFIED')
    model=CostModel(value['fee_bps'],value['slippage_bps'],value['full_spread_bps'])
    if value!=policy(model):raise ValueError('PAPER_COST_POLICY_NONCANONICAL')
    return model


def summary(saved):
    meta=saved.get('paper_experiment',{})
    if meta.get('schema_version')==2:
        value=meta.get('cost_policy');from_policy(value)
        return {'cost_policy':dict(value),'fee_policy':'EXPLICIT_PAPER_EXECUTION_COSTS_V1'}
    if 'cost_policy' in meta or meta.get('schema_version',1)!=1:
        raise ValueError('PAPER_COST_POLICY_UNVERIFIED')
    return {'cost_policy':None,'fee_policy':'EXISTING_PAPER_ZERO_FEE'}
