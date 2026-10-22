"""P3 fake task coordination. Local SQLite only; shared backend is simulation only."""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Protocol
from uuid import uuid4

from .events import canonical
from .store import validate_policy
from .worker_contracts import fingerprint, require, uid, WorkerBlocked

ACTIVE = ('RUNNING', 'PATCH_READY', 'PUBLISHING')


class CoordinationStore(Protocol):
    """Future server-side transaction/CAS contract; no client read-then-write emulation."""
    def read(self) -> dict: ...
    def change(self, event, kind, metadata, apply) -> dict: ...


class FakeWitness:
    """Independent in-memory high-water mark for fault tests, NOT a durable anti-rollback service."""
    def __init__(self):
        self.value = None

    def verify(self, state):
        require(self.value == (state['epoch'], state['revision'], fingerprint(state)), 'checkpoint_mismatch')

    def advance(self, state):
        self.value = (state['epoch'], state['revision'], fingerprint(state))


def initial(policy):
    validate_policy(policy)
    return dict(mode='FAKE_ONLY', epoch=str(uuid4()), revision=0, clock=0, policy=fingerprint(policy),
                issues={}, tasks={}, budgets={}, events={}, audit=[])


def validate(state, policy):
    require(state['mode'] == 'FAKE_ONLY' and state['policy'] == fingerprint(policy), 'store_policy_mismatch')
    uid(state['epoch'])
    totals = {}
    for task_id, task in state['tasks'].items():
        uid(task_id); uid(task['owner'])
        require(task['id'] == task_id and task['status'] in (*ACTIVE, 'PUBLISHED', 'FAILED', 'UNKNOWN'), 'invalid_task_state')
        require(type(task['cents']) is int and 0 < task['cents'] <= 500, 'reservation_integrity')
        require(0 < task['expires'] - task['started'] <= 1200, 'lease_integrity')
        require(task['day'] == datetime.fromtimestamp(task['started'], timezone.utc).date().isoformat(), 'utc_integrity')
        totals[task['day']] = totals.get(task['day'], 0) + task['cents']
    require(totals == state['budgets'] and all(v <= 5000 for v in totals.values()), 'daily_budget_integrity')
    for issue, item in state['issues'].items():
        tasks = [t for t in state['tasks'].values() if t['issue'] == int(issue)]
        require(len(tasks) == item['attempts'] <= 3 and sum(t['cents'] for t in tasks) == item['reserved'] <= 1500, 'issue_budget_integrity')
        require(sum(t['status'] in ACTIVE for t in tasks) <= 1 and item['failures'] >= 0, 'task_integrity')
    require(state['revision'] == len(state['audit']) and [a['revision'] for a in state['audit']] == list(range(1, state['revision'] + 1)), 'audit_integrity')
    for task_id, row in state.get('merge', {}).items():
        require(task_id in state['tasks'] and row['reference']['task'] == task_id
                and row['head'] == state['tasks'][task_id]['head'] and row['base'] == state['tasks'][task_id]['base'], 'merge_task_integrity')
        require(row['status'] in ('MERGE_ELIGIBLE', 'MERGE_INTENT', 'FAKE_MERGED', 'HALTED')
                and type(row['expires']) is int and row['expires'] > 0, 'merge_state_integrity')
        if row['status'] == 'FAKE_MERGED':
            require(row['receipt'] is not None and row['receipt']['task'] == task_id
                    and row['receipt']['head'] == row['head'] and row['receipt']['base'] == row['base'], 'merge_receipt_integrity')


def mutate(state, policy, event, kind, metadata, apply):
    uid(event)
    require(kind in ('CLAIM', 'DISPATCH', 'OUTPUT', 'PREPARE', 'PUBLISHED', 'FAIL', 'HALT', 'RENEW',
                     'REVIEW', 'CI', 'RECONCILE', 'APPROVAL_CHECK', 'RECEIPT', 'MERGE_GATE', 'MERGE_INTENT',
                     'FAKE_MERGED', 'MERGE_HALT', 'MERGE_RECEIPT', 'REVIEW_FEEDBACK'), 'invalid_audit_kind')
    digest = fingerprint([kind, metadata])
    old = state['events'].get(event)
    if old:
        require(old['digest'] == digest, 'event_reused')
        return copy.deepcopy(old['result']), False
    result = apply(state)
    state['revision'] += 1
    state['audit'].append({'revision': state['revision'], 'kind': kind, 'event': event, 'digest': digest})
    state['events'][event] = {'digest': digest, 'result': copy.deepcopy(result)}
    validate(state, policy)
    return result, True


class FakeBackend:
    def __init__(self, policy):
        self.lock = threading.RLock()
        self.state = initial(policy)
        self.witness = FakeWitness()
        self.witness.advance(self.state)


class FakeSharedStore:
    """Different runner clients use ONE simulated service/lock; never shared SQLite."""
    def __init__(self, backend, policy):
        require(type(backend) is FakeBackend, 'fake_backend_required')
        self.backend, self.policy = backend, copy.deepcopy(policy)
        self.read()

    def read(self):
        with self.backend.lock:
            self.backend.witness.verify(self.backend.state)
            validate(self.backend.state, self.policy)
            return copy.deepcopy(self.backend.state)

    def change(self, event, kind, metadata, apply):
        with self.backend.lock:
            state = self.read()
            result, changed = mutate(state, self.policy, event, kind, metadata, apply)
            if changed:
                self.backend.witness.advance(state)
                self.backend.state = state
            return copy.deepcopy(result)


class SQLiteTaskStore:
    """Single Mac local file. Never use on NFS or across GitHub-hosted runners."""
    def __init__(self, path, policy, *, witness):
        self.path = Path(path).absolute()
        require(not self.path.is_symlink() and self.path.is_file(), 'existing_task_store_required')
        require(type(witness) is FakeWitness, 'dry_run_witness_required')
        self.policy, self.witness = copy.deepcopy(policy), witness
        self.read()

    @classmethod
    def create(cls, path, policy, *, witness):
        require(type(witness) is FakeWitness and witness.value is None, 'new_witness_required')
        state = initial(policy)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        db = sqlite3.connect(path)
        try:
            db.execute('PRAGMA application_id=1096041552')
            db.execute('PRAGMA user_version=1')
            db.execute('CREATE TABLE state(id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL, sha TEXT NOT NULL)')
            db.execute('INSERT INTO state VALUES(1,?,?)', (canonical(state), fingerprint(state)))
            db.commit()
            witness.advance(state)
        finally:
            db.close()
        return cls(path, policy, witness=witness)

    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=5)
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN IMMEDIATE')
            require(db.execute('PRAGMA application_id').fetchone()[0] == 1096041552
                    and db.execute('PRAGMA user_version').fetchone()[0] == 1
                    and db.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'task_store_integrity')
            row = db.execute('SELECT data,sha FROM state WHERE id=1').fetchone()
            state = json.loads(row[0])
            require(fingerprint(state) == row[1], 'task_store_integrity')
            self.witness.verify(state)
            validate(state, self.policy)
            yield db, state
            db.commit()
        except WorkerBlocked:
            raise
        except Exception:
            raise WorkerBlocked('task_store_unavailable') from None
        finally:
            if db:
                db.rollback(); db.close()

    def read(self):
        with self._transaction() as (_, state):
            return state

    def change(self, event, kind, metadata, apply):
        with self._transaction() as (db, state):
            result, changed = mutate(state, self.policy, event, kind, metadata, apply)
            if changed:
                db.execute('UPDATE state SET data=?,sha=? WHERE id=1', (canonical(state), fingerprint(state)))
        # DB commit then witness advance. A crash in this gap MUST halt on mismatch.
        if changed:
            self.witness.advance(state)
        return result
