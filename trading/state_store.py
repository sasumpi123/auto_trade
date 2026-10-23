"""Atomic journal and an OS-held single-process lock (macOS/Linux/Windows)."""
import json
import os
from pathlib import Path
import tempfile


class StateStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self._lock = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(str(self.path) + '.lock', 'a+b')
        try:
            from utils.process_lock import lock_handle
            lock_handle(handle)
        except OSError as exc:
            handle.close()
            raise RuntimeError('Another bot owns this state file') from exc
        self._lock = handle

    def load(self):
        if not self.path.exists():
            return None
        with self.path.open(encoding='utf-8') as handle:
            return json.load(handle)

    def save(self, state):
        if self._lock is None:
            raise RuntimeError('State lock is not held')
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.state-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(state, handle, ensure_ascii=False, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            if os.name != 'nt':
                fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def close(self):
        if self._lock is not None:
            self._lock.close()  # Kernel releases the lock, including on process death.
            self._lock = None
