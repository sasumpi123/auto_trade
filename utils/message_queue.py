"""Bounded durable notification outbox; never an order ledger.

Explicit initialization only. In-flight requests are UNKNOWN after restart, never retried.
"""
from datetime import datetime, timezone
import hashlib
import math
import sqlite3
import threading
import time


class MessageQueue:
    def __init__(self, path=None, *, initialize=False, clock=time.time,
                 daily_limit=900, min_interval=2):
        if (type(daily_limit) is not int or not 1 <= daily_limit <= 900
            or type(min_interval) not in (int, float) or not math.isfinite(min_interval) or min_interval < 2):
            raise ValueError('Notification limits cannot exceed the safe defaults')
        self.clock, self.daily_limit, self.min_interval = clock, daily_limit, min_interval
        self.lock = threading.Lock()
        self.path = path
        if path is None:
            self.db = sqlite3.connect(':memory:', check_same_thread=False)
        else:
            from pathlib import Path
            p = Path(path)
            if p.is_symlink() or not p.parent.is_dir():
                raise ValueError('Unsafe outbox path')
            if initialize:
                import os
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            elif not p.is_file():
                raise ValueError('Outbox requires explicit initialization')
            import os
            if os.name == 'posix' and (p.stat().st_uid != os.getuid() or p.stat().st_mode & 0o077 or p.parent.stat().st_mode & 0o077):
                raise ValueError('Outbox and parent must be owner-private')
            self.db = sqlite3.connect(p, check_same_thread=False)
        try:
            if path is None or initialize:
                self.db.executescript('''
                    CREATE TABLE messages(id TEXT PRIMARY KEY, channel TEXT, text TEXT,
                        priority INTEGER, state TEXT, attempts INTEGER DEFAULT 0, due REAL);
                    CREATE TABLE sends(channel TEXT, at REAL, day TEXT);
                    CREATE TABLE duplicates(id TEXT PRIMARY KEY, repeats INTEGER);
                    PRAGMA user_version=1;
                ''')
            if self.db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Outbox damaged')
            if self.db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('Unknown outbox schema')
            self.db.execute("UPDATE messages SET state='UNKNOWN' WHERE state='SENDING'")
            self.db.commit()
        except Exception:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def enqueue(self, event_id, channel, text, priority=0):
        with self.lock, self.db:
            # Event identity includes its channel; a repeated event is never re-sent.
            key = hashlib.sha256((channel + ':' + event_id).encode()).hexdigest()
            if self.db.execute('SELECT 1 FROM messages WHERE id=?', (key,)).fetchone():
                self.db.execute('INSERT INTO duplicates VALUES(?,1) ON CONFLICT(id) DO UPDATE SET repeats=repeats+1', (key,))
                return False
            if self.db.execute('SELECT count(*) FROM messages').fetchone()[0] >= 50000:
                return False  # explicit archival/reconciliation required; do not reset IDs
            return self.db.execute('INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,0,?)',
                (key, channel, text, priority, 'QUEUED', self.clock())).rowcount == 1

    def reserve(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                now = self.clock()
                day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
                latest = self.db.execute('SELECT max(at) FROM sends').fetchone()[0]
                if latest is not None and now < latest:
                    self.db.commit()
                    return None
                count = self.db.execute('SELECT count(*) FROM sends WHERE day=?', (day,)).fetchone()[0]
                if count >= self.daily_limit:
                    self.db.commit()
                    return None
                for row in self.db.execute("SELECT id,channel,text FROM messages WHERE state='QUEUED' AND due<=? ORDER BY priority DESC,rowid", (now,)).fetchall():
                    last = self.db.execute('SELECT max(at) FROM sends WHERE channel=?', (row[1],)).fetchone()[0]
                    if last is not None and now - last < self.min_interval:
                        continue
                    self.db.execute("UPDATE messages SET state='SENDING',attempts=attempts+1 WHERE id=?", (row[0],))
                    self.db.execute('INSERT INTO sends VALUES(?,?,?)', (row[1], now, day))
                    self.db.commit()  # Durable intent BEFORE external request.
                    return row
                self.db.commit()
                return None
            except Exception:
                self.db.rollback()
                raise

    def finish(self, key, state, retry_after=None):
        with self.lock, self.db:
            attempts = self.db.execute('SELECT attempts FROM messages WHERE id=?', (key,)).fetchone()[0]
            if state == 'RATE_LIMITED' and attempts < 2 and retry_after is not None:
                self.db.execute("UPDATE messages SET state='QUEUED',due=? WHERE id=?",
                                (self.clock() + max(2, min(3600, retry_after)), key))
            else:
                self.db.execute('UPDATE messages SET state=? WHERE id=?', (state, key))

    # Legacy rate limiter API (uses the same durable quota records).
    def can_send_message(self):
        now = self.clock()
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        count, last = self.db.execute('SELECT count(*),max(at) FROM sends WHERE day=?', (day,)).fetchone()
        return count < self.daily_limit and (last is None or now-last >= self.min_interval)

    def log_message_sent(self):
        now = self.clock()
        with self.db:
            self.db.execute('INSERT INTO sends VALUES(?,?,?)', ('legacy', now, datetime.fromtimestamp(now, timezone.utc).date().isoformat()))
