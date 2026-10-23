"""Cooperative kernel locks; never unlink lock files, including after a crash."""
import os
import stat


def lock_handle(handle, *, initialize=True):
    """The same nonblocking lifetime lock used by PAPER state and the outbox."""
    if os.name == 'nt':
        import msvcrt
        handle.seek(0, 2)
        if handle.tell() == 0:
            if not initialize:
                raise RuntimeError('Empty Windows lock requires owner initialization')
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    elif os.name == 'posix':
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        raise RuntimeError('Unsupported process locking platform')


class OutboxOwnership:
    """Private, canonical local-filesystem path; advisory locking, not a witness."""
    def __init__(self, path):
        self.path = path
        self.handle = None
        self.pid = os.getpid()

    def acquire(self):
        flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(str(self.path) + '.lock', flags, 0o600)
        handle = os.fdopen(fd, 'r+b')
        try:
            info = os.fstat(fd)
            actual = os.stat(str(self.path) + '.lock', follow_symlinks=False)
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeError('Unsafe ownership alias')
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (os.name == 'posix' and (info.st_uid != os.getuid() or info.st_mode & 0o077))):
                raise RuntimeError('Unsafe outbox ownership file')
            lock_handle(handle)
        except Exception:
            handle.close()
            raise RuntimeError('Outbox ownership unavailable') from None
        self.handle = handle

    def check(self):
        if self.handle is None or self.pid != os.getpid():
            raise RuntimeError('Outbox ownership is not held by this process')
        actual = os.stat(str(self.path) + '.lock', follow_symlinks=False)
        held = os.fstat(self.handle.fileno())
        if (actual.st_dev, actual.st_ino, actual.st_nlink) != (held.st_dev, held.st_ino, 1):
            raise RuntimeError('Outbox ownership file changed')

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
