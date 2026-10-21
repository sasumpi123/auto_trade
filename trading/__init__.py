__all__ = ['AutoTrade']


def __getattr__(name):
    if name != 'AutoTrade':
        raise AttributeError(name)
    from .auto_trade import AutoTrade
    return AutoTrade
