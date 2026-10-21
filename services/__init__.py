"""Service exports loaded on demand to avoid circular imports."""
from importlib import import_module

_EXPORTS = {'verify_api_keys': 'api_service', 'NotificationService': 'notification_service',
            'PerformanceMonitor': 'performance_service', 'PerformanceAnalyzer': 'performance_service'}
__all__ = list(_EXPORTS)


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    return getattr(import_module('.' + _EXPORTS[name], __name__), name)
