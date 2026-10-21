from importlib import import_module

__all__ = ['MessageQueue', 'retry_on_failure']


def __getattr__(name):
    if name not in __all__:
        raise AttributeError(name)
    module = '.message_queue' if name == 'MessageQueue' else '.decorators'
    return getattr(import_module(module, __name__), name)
