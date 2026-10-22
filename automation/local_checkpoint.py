"""Local durable witness for FAKE_ONLY ledgers, never a trusted anti-rollback oracle."""
import json
import os
from pathlib import Path
import stat

from .worker_contracts import fingerprint, require


class LocalCheckpoint:
    """Separate user-owned file. Joint DB/checkpoint restore is NOT detectable.

    Caller must hold a single process writer lock across store operations. A crash
    between SQLite commit and checkpoint replace fails closed; never reconstruct.
    """
    def __init__(self, path):
        self.path = Path(path).absolute()
        self._secure(self.path.parent, directory=True)
        if self.path.exists() or self.path.is_symlink():
            self._secure(self.path)

    @staticmethod
    def _secure(path, directory=False):
        require(path.absolute() == path.resolve(), 'checkpoint_symlink_path')
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), 'checkpoint_symlink')
        require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'checkpoint_type')
        if os.name == 'posix':
            require(info.st_uid == os.getuid() and not info.st_mode & 0o077, 'checkpoint_permissions')

    @property
    def value(self):
        if not self.path.exists():
            return None
        self._secure(self.path)
        require(self.path.stat().st_size <= 4096, 'checkpoint_invalid')
        try:
            value = json.loads(self.path.read_text())
            require(type(value) is list and len(value) == 3, 'checkpoint_invalid')
            return tuple(value)
        except (ValueError, OSError):
            require(False, 'checkpoint_unavailable')

    def verify(self, state):
        require(self.value == (state['epoch'], state['revision'], fingerprint(state)), 'checkpoint_mismatch')

    def advance(self, state):
        temporary = self.path.with_name(self.path.name + '.next')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump([state['epoch'], state['revision'], fingerprint(state)], stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        if os.name == 'posix':
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
