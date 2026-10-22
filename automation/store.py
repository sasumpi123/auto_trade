"""SQLite transaction journal for FAKE-ONLY development; never invokes services.

Explicit create, fail-closed open, serialized read/check/write and no automatic
refund/re-execution of an ambiguous attempt. Requires a local filesystem.
"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .events import Event, LedgerError, Receipt, STATES, canonical, identifier
from .policy import Ledger, allowed_paths, finish, plan, reserve

APP_ID = 0x41544452
SCHEMA_VERSION = 1
MAX_ATTEMPT_SECONDS = 1200
RUNNING = ('IMPLEMENTATION_RUNNING', 'REVISION_RUNNING')
TERMINAL = ('HALTED', 'MERGED')
GRAPH = {
    'ISSUE_APPROVED': {'IMPLEMENTATION_PENDING'},
    'IMPLEMENTATION_PENDING': {'IMPLEMENTATION_RUNNING'},
    'IMPLEMENTATION_RUNNING': {'PR_CREATED', 'FAILED'},
    'PR_CREATED': {'REVIEW_PENDING', 'CI_PENDING', 'CHANGES_REQUESTED', 'FAILED', 'MERGE_ELIGIBLE'},
    'REVIEW_PENDING': {'CHANGES_REQUESTED', 'CI_PENDING', 'MERGE_ELIGIBLE', 'FAILED'},
    'CHANGES_REQUESTED': {'REVISION_RUNNING', 'CI_PENDING'},
    'REVISION_RUNNING': {'CI_PENDING', 'FAILED'},
    'CI_PENDING': {'REVIEW_PENDING', 'CHANGES_REQUESTED', 'MERGE_ELIGIBLE', 'FAILED'},
    'MERGE_ELIGIBLE': {'MERGED', 'CI_PENDING', 'REVIEW_PENDING', 'CHANGES_REQUESTED', 'FAILED'},
    'FAILED': {'IMPLEMENTATION_PENDING', 'CHANGES_REQUESTED'},
    'HALTED': set(), 'MERGED': set(),
}
SCHEMA = (
    'CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE issues (issue INTEGER PRIMARY KEY CHECK(issue>0), data TEXT NOT NULL)',
    '''CREATE TABLE approvals (issue INTEGER NOT NULL REFERENCES issues(issue), version INTEGER NOT NULL CHECK(version>0),
       spec TEXT NOT NULL CHECK(length(spec)=64), actor INTEGER NOT NULL, PRIMARY KEY(issue,version))''',
    '''CREATE TABLE attempts (issue INTEGER NOT NULL REFERENCES issues(issue), number INTEGER NOT NULL CHECK(number>0),
       owner TEXT NOT NULL, started INTEGER NOT NULL, expires INTEGER NOT NULL, cents INTEGER NOT NULL CHECK(cents>0),
       day TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('RUNNING','BUILT','CHANGES','PASSED','FAILED','UNKNOWN')),
       spec_version INTEGER NOT NULL, dispatched INTEGER NOT NULL CHECK(dispatched IN (0,1)), PRIMARY KEY(issue,number),
       FOREIGN KEY(issue,spec_version) REFERENCES approvals(issue,version))''',
    "CREATE UNIQUE INDEX one_running_attempt ON attempts(issue) WHERE status='RUNNING'",
    'CREATE TABLE budgets (day TEXT PRIMARY KEY, cents INTEGER NOT NULL CHECK(cents>=0))',
    '''CREATE TABLE events (id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, issue INTEGER NOT NULL,
       kind TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL, at INTEGER NOT NULL)''',
    '''CREATE TABLE transitions (id INTEGER PRIMARY KEY, event TEXT NOT NULL REFERENCES events(id),
       issue INTEGER NOT NULL REFERENCES issues(issue), old_state TEXT, new_state TEXT NOT NULL,
       version INTEGER NOT NULL, at INTEGER NOT NULL, projection_sha TEXT NOT NULL, UNIQUE(issue,version))''',
)


def utc_seconds(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LedgerError('UTC-aware clock required')
    result = int(value.astimezone(timezone.utc).timestamp())
    if result < 0:
        raise LedgerError('Invalid clock')
    return result


def now_utc():
    return datetime.now(timezone.utc)


def _schema_hash(connection):
    rows = connection.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(canonical([tuple(row) for row in rows]).encode()).hexdigest()


def validate_policy(policy):
    caps = {'max_attempts_per_issue': 3, 'max_consecutive_failures': 2,
            'max_reserved_cents_per_attempt': 500, 'max_reserved_cents_per_issue': 1500,
            'max_reserved_cents_per_day': 5000}
    if policy.get('enabled') is not False or any(type(policy.get(k)) is not int or policy[k] != v for k, v in caps.items()):
        raise LedgerError('Only disabled approved P1 limits are supported')


class SQLiteLedger:
    def __init__(self, path, policy, *, clock=now_utc):
        self.path = Path(path).absolute()
        if self.path.is_symlink() or not self.path.is_file():
            raise LedgerError('Existing regular ledger required; never implicitly recreate')
        self.policy = json.loads(canonical(policy))
        validate_policy(self.policy)
        # A policy change requires an explicit reviewed migration, never a silent budget reset.
        self.policy_hash = hashlib.sha256(canonical(self.policy).encode()).hexdigest()
        self.clock = clock
        with self._connection() as connection:
            # Keep schema, projection, attempts and budgets in one read snapshot.
            # Another WAL writer may commit while an opener validates the ledger.
            connection.execute('BEGIN')
            self._validate(connection, full=True)

    @classmethod
    def create(cls, path, policy, *, clock=now_utc):
        validate_policy(policy)
        path = Path(path).absolute()
        if not path.parent.is_dir() or path.is_symlink():
            raise LedgerError('Existing local parent directory required')
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('PRAGMA synchronous=FULL')
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('BEGIN IMMEDIATE')
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute(f'PRAGMA application_id={APP_ID}')
                connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                rows = {'mode': 'fake-only', 'policy_hash': hashlib.sha256(canonical(policy).encode()).hexdigest(),
                        'last_clock': str(utc_seconds(clock())), 'recovery_required': '0',
                        'schema_hash': _schema_hash(connection)}
                connection.executemany('INSERT INTO meta VALUES (?,?)', rows.items())
                connection.commit()
            finally:
                connection.close()
        except (OSError, sqlite3.Error):
            # Keep any partially initialized file for diagnosis; never fall back to empty state.
            raise LedgerError('Ledger creation failed; no execution permitted') from None
        return cls(path, policy, clock=clock)

    @contextmanager
    def _connection(self):
        connection = None
        try:
            if self.path.is_symlink() or not self.path.is_file():
                raise LedgerError('Ledger file missing or unsafe')
            connection = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True,
                                         isolation_level=None, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA synchronous=FULL')
            connection.execute('PRAGMA trusted_schema=OFF')
            yield connection
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
            raise LedgerError('Ledger unavailable or invalid; no execution permitted') from None
        finally:
            if connection is not None:
                connection.close()

    def _validate(self, connection, *, full=False):
        if connection.execute('PRAGMA application_id').fetchone()[0] != APP_ID or connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise LedgerError('Unknown ledger schema; no automatic migration')
        meta = dict(connection.execute('SELECT key,value FROM meta').fetchall())
        if meta.get('mode') != 'fake-only' or meta.get('policy_hash') != self.policy_hash or meta.get('schema_hash') != _schema_hash(connection):
            raise LedgerError('Ledger policy/schema mismatch')
        if meta.get('recovery_required') not in ('0', '1') or int(meta['last_clock']) < 0:
            raise LedgerError('Invalid recovery/clock metadata')
        if full and ([row[0] for row in connection.execute('PRAGMA integrity_check')] != ['ok'] or connection.execute('PRAGMA foreign_key_check').fetchall()):
            raise LedgerError('Database integrity check failed')
        if connection.execute('PRAGMA foreign_key_check').fetchall() or connection.execute("SELECT 1 FROM events WHERE outcome NOT IN ('APPLIED','REJECTED','STALE') LIMIT 1").fetchone():
            raise LedgerError('Incomplete or orphaned journal')
        for row in connection.execute('SELECT issue,data FROM issues'):
            item = json.loads(row['data'])
            attempts = connection.execute('SELECT * FROM attempts WHERE issue=? ORDER BY number', (row['issue'],)).fetchall()
            if item['state'] not in STATES or type(item['approved']) is not bool:
                raise LedgerError('Invalid state projection')
            if item['attempts'] != len(attempts) or item['reserved_cents'] != sum(a['cents'] for a in attempts):
                raise LedgerError('Reservation projection mismatch')
            if any(a['number'] != i + 1 for i, a in enumerate(attempts)):
                raise LedgerError('Attempt sequence mismatch')
            for attempt in attempts:
                identifier(attempt['owner'])
                if (attempt['cents'] > self.policy['max_reserved_cents_per_attempt'] or attempt['started'] < 0 or
                        not attempt['started'] < attempt['expires'] <= attempt['started'] + MAX_ATTEMPT_SECONDS or
                        attempt['day'] != datetime.fromtimestamp(attempt['started'], timezone.utc).date().isoformat()):
                    raise LedgerError('Attempt time/reservation mismatch')
            for key in ('version', 'spec_version', 'attempts', 'failures', 'reserved_cents', 'head_version'):
                if type(item[key]) is not int or item[key] < 0:
                    raise LedgerError('Invalid projection counters')
            if item['attempts'] > self.policy['max_attempts_per_issue'] or item['reserved_cents'] > self.policy['max_reserved_cents_per_issue']:
                raise LedgerError('Issue limit invariant failed')
            approval = connection.execute('SELECT * FROM approvals WHERE issue=? AND version=?', (row['issue'], item['spec_version'])).fetchone()
            if not approval or approval['spec'] != item['spec_sha256']:
                raise LedgerError('Approval projection mismatch')
            chain = connection.execute('SELECT * FROM transitions WHERE issue=? ORDER BY version', (row['issue'],)).fetchall()
            previous = None
            for i, transition in enumerate(chain):
                if transition['version'] != i + 1 or transition['old_state'] != previous:
                    raise LedgerError('Broken transition chain')
                previous = transition['new_state']
            if not chain or len(chain) != item['version'] or previous != item['state']:
                raise LedgerError('State journal mismatch')
            if chain[-1]['projection_sha'] != hashlib.sha256(canonical(item).encode()).hexdigest():
                raise LedgerError('Projection checksum mismatch')
            active = [a for a in attempts if a['status'] == 'RUNNING']
            if bool(active) != (item['state'] in RUNNING) or (active and item['active_attempt'] != active[0]['number']):
                raise LedgerError('Lease projection mismatch')
            if not active and item['active_attempt'] is not None:
                raise LedgerError('Unexpected active attempt')
        actual = dict(connection.execute('SELECT day,SUM(cents) FROM attempts GROUP BY day').fetchall())
        recorded = dict(connection.execute('SELECT day,cents FROM budgets').fetchall())
        if actual != recorded or any(cents > self.policy['max_reserved_cents_per_day'] for cents in actual.values()):
            raise LedgerError('Daily reservation invariant failed')
        return meta

    @contextmanager
    def _transaction(self, *, allow_recovery=False):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                meta = self._validate(connection)
                now = utc_seconds(self.clock())
                if now < int(meta['last_clock']):
                    raise LedgerError('Clock moved backwards; operator reconciliation required')
                if meta['recovery_required'] == '1' and not allow_recovery:
                    raise LedgerError('Restored backup is quarantined; execution forbidden')
                connection.execute("UPDATE meta SET value=? WHERE key='last_clock'", (str(now),))
                yield connection, now
                self._validate(connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _load(connection, issue):
        row = connection.execute('SELECT data FROM issues WHERE issue=?', (issue,)).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def _move(connection, issue, item, state, event_id, now):
        previous = item['state'] if item['version'] else None
        if previous and previous != state and state != 'HALTED' and state not in GRAPH[previous]:
            raise LedgerError('Illegal internal state transition')
        if previous == 'MERGED' and state != 'MERGED':
            raise LedgerError('Merged simulation is terminal')
        item['state'] = state
        item['version'] += 1
        connection.execute('UPDATE issues SET data=? WHERE issue=?', (canonical(item), issue))
        connection.execute('INSERT INTO transitions(event,issue,old_state,new_state,version,at,projection_sha) VALUES (?,?,?,?,?,?,?)',
                           (event_id, issue, previous, state, item['version'], now,
                            hashlib.sha256(canonical(item).encode()).hexdigest()))

    def get(self, issue):
        with self._transaction(allow_recovery=True) as (connection, _):
            return self._load(connection, issue)

    def history(self, issue):
        with self._transaction(allow_recovery=True) as (connection, _):
            return [dict(row) for row in connection.execute('SELECT * FROM transitions WHERE issue=? ORDER BY version', (issue,))]

    def audit(self):
        with self._transaction(allow_recovery=True) as (connection, _):
            return [dict(row) for row in connection.execute('SELECT * FROM events ORDER BY rowid')]

    def summary(self):
        with self._transaction(allow_recovery=True) as (connection, _):
            return [{'issue': row['issue'], **{key: value for key, value in json.loads(row['data']).items()
                                             if key in ('state', 'version', 'attempts', 'failures', 'reserved_cents', 'halt_reason')}}
                    for row in connection.execute('SELECT issue,data FROM issues ORDER BY issue')]

    def day_reserved(self, day):
        with self._transaction(allow_recovery=True) as (connection, _):
            row = connection.execute('SELECT cents FROM budgets WHERE day=?', (day,)).fetchone()
            return row[0] if row else 0

    def integrity(self):
        with self._connection() as connection:
            connection.execute('BEGIN')
            self._validate(connection, full=True)
            connection.rollback()
        return True

    def _stop_attempt(self, connection, issue, item, status):
        if item['attempts']:
            row = connection.execute('SELECT status FROM attempts WHERE issue=? AND number=?', (issue, item['attempts'])).fetchone()
            if row[0] not in ('FAILED', 'UNKNOWN'):
                connection.execute('UPDATE attempts SET status=? WHERE issue=? AND number=?', (status, issue, item['attempts']))
                if status in ('FAILED', 'UNKNOWN'):
                    item['failures'] = finish(Ledger(failures=item['failures'], in_flight=True), self.policy, success=False).failures
        item['active_attempt'] = None

    def _eligible(self, issue, item):
        if item['review_status'] != 'APPROVED' or item['ci_status'] != 'PASSED':
            return False
        simulated = dict(self.policy, enabled=True, builder_ids=[1001], independent_reviewer_ids=[2002])
        snapshot = {'issue': {'number': issue, 'state': 'open', 'repository': self.policy['repository'],
                             'body': '', 'labels': [self.policy['approved_label']],
                             'approval_comments': [{'author_id': self.policy['maintainer_ids'][0],
                                                    'body': '/autodev approve ' + hashlib.sha256(b'').hexdigest()}]},
                    'ledger': asdict(Ledger(attempts=item['attempts'], failures=item['failures'])),
                    'pr': {'state': 'open', 'draft': False, 'repository': self.policy['repository'],
                           'head_repository': self.policy['repository'], 'base': self.policy['base_branch'],
                           'branch': f'autodev/issue-{issue}', 'author_id': 1001, 'head_sha': item['head_sha'],
                           'spec_sha256': hashlib.sha256(b'').hexdigest(), 'changed_paths': item['gate']['changed_paths']},
                    'reviews': [{'id': item['review_id'], 'author_id': item['review_actor'],
                                 'commit_id': item['head_sha'], 'state': item['review_status']}],
                    'checks': item['checks'], **{key: value for key, value in item['gate'].items() if key != 'changed_paths'}}
        # This fixed synthetic empty specification avoids storing issue bodies. It cannot authorize live work.
        return item['approved'] and plan(snapshot, simulated)['action'] == 'external_merge_gate_candidate'

    def submit(self, event):
        if type(event) is not Event:
            raise ValueError('Typed metadata-only Event required')
        fingerprint = event.fingerprint()
        with self._transaction() as (connection, now):
            existing = connection.execute('SELECT * FROM events WHERE id=? OR fingerprint=?', (event.event_id, fingerprint)).fetchall()
            item = self._load(connection, event.issue)
            if existing:
                if any(row['id'] == event.event_id and row['fingerprint'] != fingerprint for row in existing):
                    raise LedgerError('Event ID reused for different metadata')
                return Receipt('DUPLICATE', 'already_recorded', event.issue,
                               item['state'] if item else None, item['version'] if item else 0,
                               item['attempts'] if item else 0)
            connection.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                               (event.event_id, fingerprint, event.issue, event.kind, 'PROCESSING', '', now))
            outcome, reason = self._apply(connection, now, event, item)
            connection.execute('UPDATE events SET outcome=?,reason=? WHERE id=?', (outcome, reason, event.event_id))
            final = self._load(connection, event.issue)
            return Receipt(outcome, reason, event.issue, final['state'] if final else None,
                           final['version'] if final else 0, final['attempts'] if final else 0)

    def _apply(self, connection, now, event, item):
        issue = event.issue
        def reject(reason):
            return 'REJECTED', reason
        def move(state):
            if state == 'HALTED' and not item['halt_reason']:
                item['halt_reason'] = 'execution_or_failure_limit'
            self._move(connection, issue, item, state, event.event_id, now)
            return 'APPLIED', 'recorded'
        if event.kind == 'APPROVE':
            if event.actor_id not in self.policy['maintainer_ids']:
                return reject('unapproved_issue')
            if item is None:
                if event.spec_version != 1:
                    return reject('approval_version_gap')
                item = {'state': 'ISSUE_APPROVED', 'version': 0, 'spec_sha256': event.spec_sha256,
                        'spec_version': 1, 'approved': True, 'attempts': 0, 'failures': 0, 'reserved_cents': 0,
                        'active_attempt': None, 'pr_number': 0, 'head_sha': '', 'head_version': 0,
                        'review_id': 0, 'review_actor': 0, 'review_status': 'NONE', 'ci_run_id': 0,
                        'ci_status': 'NONE', 'checks': [], 'gate': {'changed_paths': [], 'unresolved_threads': None,
                        'branch_protection_verified': None, 'base_up_to_date': None}, 'halt_reason': ''}
                connection.execute('INSERT INTO issues VALUES (?,?)', (issue, canonical(item)))
            else:
                if item['state'] == 'MERGED':
                    return reject('terminal_issue')
                if event.spec_version <= item['spec_version']:
                    return ('STALE', 'old_approval') if event.spec_sha256 == item['spec_sha256'] else reject('approval_version_reuse')
                if event.spec_version != item['spec_version'] + 1:
                    return reject('approval_version_gap')
                self._stop_attempt(connection, issue, item, 'UNKNOWN')
                item.update(spec_sha256=event.spec_sha256, spec_version=event.spec_version, approved=True,
                            halt_reason='approval_changed_requires_reconciliation')
            connection.execute('INSERT INTO approvals VALUES (?,?,?,?)', (issue, event.spec_version, event.spec_sha256, event.actor_id))
            return move('ISSUE_APPROVED' if item['version'] == 0 else 'HALTED')
        if item is None:
            return reject('unapproved_issue')
        if event.kind in ('HALT', 'INVALIDATE'):
            if item['state'] == 'MERGED':
                return reject('terminal_issue')
            self._stop_attempt(connection, issue, item, 'UNKNOWN')
            item.update(approved=False if event.kind == 'INVALIDATE' else item['approved'], halt_reason=event.kind.lower())
            return move('HALTED')
        if event.spec_sha256 != item['spec_sha256'] or event.spec_version != item['spec_version']:
            return 'STALE', 'stale_specification'
        if item['state'] in TERMINAL or not item['approved']:
            return reject('terminal_or_unapproved')
        if event.kind == 'QUEUE':
            return move('IMPLEMENTATION_PENDING') if item['state'] == 'ISSUE_APPROVED' else reject('invalid_transition')
        if event.kind == 'CLAIM':
            if item['state'] not in ('IMPLEMENTATION_PENDING', 'CHANGES_REQUESTED') or event.expected_version != item['version'] or not event.worker:
                return reject('not_claimable_or_stale_version')
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            row = connection.execute('SELECT cents FROM budgets WHERE day=?', (day,)).fetchone()
            budget = row[0] if row else 0
            try:
                updated = reserve(Ledger(attempts=item['attempts'], failures=item['failures'], reserved_cents=item['reserved_cents'],
                                         day_reserved_cents=budget), self.policy, event_key=event.event_id, cents=event.cents)
            except ValueError:
                item['halt_reason'] = 'execution_or_budget_limit'
                return move('HALTED')
            number = updated.attempts
            connection.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)',
                               (issue, number, event.worker, now, now + event.lease_seconds, event.cents, day, 'RUNNING', item['spec_version'], 0))
            connection.execute('INSERT INTO budgets VALUES (?,?) ON CONFLICT(day) DO UPDATE SET cents=excluded.cents',
                               (day, updated.day_reserved_cents))
            item.update(attempts=number, reserved_cents=updated.reserved_cents, active_attempt=number,
                        review_status='NONE', ci_status='NONE', checks=[])
            return move('IMPLEMENTATION_RUNNING' if item['state'] == 'IMPLEMENTATION_PENDING' else 'REVISION_RUNNING')
        if event.kind in ('BUILT', 'FAIL', 'RENEW', 'DISPATCH'):
            if item['state'] not in RUNNING or item['active_attempt'] != event.attempt:
                return reject('no_matching_running_attempt')
            attempt = connection.execute('SELECT * FROM attempts WHERE issue=? AND number=?', (issue, event.attempt)).fetchone()
            if attempt['owner'] != event.worker:
                return reject('wrong_lease_owner')
            if now >= attempt['expires']:
                self._stop_attempt(connection, issue, item, 'UNKNOWN')
                item['halt_reason'] = 'lease_expired_ambiguous'
                return move('HALTED')
            if event.kind == 'RENEW':
                if event.expected_version != item['version']:
                    return reject('stale_heartbeat_version')
                expiry = min(now + event.lease_seconds, attempt['started'] + MAX_ATTEMPT_SECONDS)
                if expiry <= now:
                    return reject('attempt_runtime_limit')
                connection.execute('UPDATE attempts SET expires=? WHERE issue=? AND number=?', (expiry, issue, event.attempt))
                return move(item['state'])
            if event.kind == 'DISPATCH':
                if attempt['dispatched']:
                    return reject('already_dispatched')
                connection.execute('UPDATE attempts SET dispatched=1 WHERE issue=? AND number=?', (issue, event.attempt))
                return move(item['state'])
            if event.kind == 'FAIL':
                self._stop_attempt(connection, issue, item, 'FAILED')
                return move('HALTED' if item['failures'] >= self.policy['max_consecutive_failures'] else 'FAILED')
            if not attempt['dispatched'] or event.actor_id != 1001 or event.pr_number < 1 or not event.head_sha or event.head_version != item['head_version'] + 1 or event.head_sha == item['head_sha']:
                return reject('invalid_fake_build_result')
            if item['pr_number'] and item['pr_number'] != event.pr_number:
                return reject('pr_identity_changed')
            connection.execute("UPDATE attempts SET status='BUILT' WHERE issue=? AND number=?", (issue, event.attempt))
            item.update(active_attempt=None, pr_number=event.pr_number, head_sha=event.head_sha,
                        head_version=event.head_version, review_id=0, review_actor=0, review_status='NONE',
                        ci_run_id=0, ci_status='PENDING', checks=[], gate={'changed_paths': [], 'unresolved_threads': None,
                        'branch_protection_verified': None, 'base_up_to_date': None})
            return move('PR_CREATED' if item['state'] == 'IMPLEMENTATION_RUNNING' else 'CI_PENDING')
        if event.kind == 'RETRY':
            if item['state'] != 'FAILED':
                return reject('retry_requires_failed_state')
            if item['failures'] >= self.policy['max_consecutive_failures'] or item['attempts'] >= self.policy['max_attempts_per_issue']:
                return move('HALTED')
            return move('CHANGES_REQUESTED' if item['pr_number'] else 'IMPLEMENTATION_PENDING')
        if not item['pr_number'] or item['state'] in RUNNING or item['state'] in ('ISSUE_APPROVED', 'IMPLEMENTATION_PENDING'):
            return reject('event_out_of_order')
        if event.kind == 'HEAD_UPDATED':
            if item['state'] == 'FAILED':
                return reject('explicit_retry_required')
            if event.head_version <= item['head_version']:
                return 'STALE', 'old_head_version'
            if event.head_version != item['head_version'] + 1 or not event.head_sha or event.head_sha == item['head_sha']:
                return reject('invalid_head_version')
            item.update(head_sha=event.head_sha, head_version=event.head_version, review_id=0, review_actor=0,
                        review_status='NONE', ci_run_id=0, ci_status='PENDING', checks=[])
            item['gate'] = dict(changed_paths=[], unresolved_threads=None, branch_protection_verified=None, base_up_to_date=None)
            return move('CI_PENDING')
        if event.head_sha != item['head_sha'] or event.head_version != item['head_version']:
            return 'STALE', 'old_head'
        if event.kind == 'REQUEST_REVIEW':
            return move('REVIEW_PENDING') if item['state'] in ('PR_CREATED', 'CI_PENDING') else reject('invalid_transition')
        if event.kind == 'REVIEW':
            if event.actor_id != 2002 or not event.review_id or event.review_status == 'NONE':
                return reject('independent_fake_review_required')
            if event.review_id <= item['review_id']:
                return 'STALE', 'old_review'
            if item['state'] == 'CHANGES_REQUESTED' and event.review_status == 'APPROVED':
                return reject('revision_required')
            item.update(review_id=event.review_id, review_actor=event.actor_id, review_status=event.review_status)
            if item['state'] == 'FAILED':
                return move('FAILED')
            if event.review_status == 'CHANGES_REQUESTED':
                self._stop_attempt(connection, issue, item, 'CHANGES')
                return move('HALTED' if item['attempts'] >= self.policy['max_attempts_per_issue'] else 'CHANGES_REQUESTED')
        elif event.kind == 'CI':
            if not event.ci_run_id or event.ci_status == 'NONE':
                return reject('invalid_ci_metadata')
            if event.ci_run_id < item['ci_run_id']:
                return 'STALE', 'old_ci_run'
            if event.ci_run_id == item['ci_run_id'] and item['ci_status'] in ('PASSED', 'FAILED'):
                return reject('terminal_ci_result_conflict')
            if event.ci_status == 'PASSED':
                expected = self.policy['required_checks']
                checks = [asdict(check) for check in event.checks]
                if sorted(check['name'] for check in checks) != sorted(expected) or any(
                        c['head_sha'] != item['head_sha'] or c['status'] != 'completed' or c['conclusion'] != 'success' or
                        c['app_slug'] != 'github-actions' or c['workflow_path'] != self.policy['trusted_ci_workflow'] for c in checks):
                    return reject('unverified_or_unsuccessful_checks')
            else:
                checks = []
            item.update(ci_run_id=event.ci_run_id, ci_status=event.ci_status, checks=checks)
            if event.ci_status == 'FAILED':
                self._stop_attempt(connection, issue, item, 'FAILED')
                return move('HALTED' if item['failures'] >= self.policy['max_consecutive_failures'] else 'FAILED')
            if item['state'] == 'FAILED':
                return move('FAILED')  # A late rerun cannot bypass explicit retry or failure accounting.
            if item['state'] == 'CHANGES_REQUESTED':
                return move('CHANGES_REQUESTED')  # Retry does not mean a revision has actually run.
        elif event.kind == 'GATE':
            if not allowed_paths(event.changed_paths, self.policy):
                return reject('unapproved_paths')
            item['gate'] = {'changed_paths': list(event.changed_paths), 'unresolved_threads': event.unresolved_threads,
                            'branch_protection_verified': event.branch_protection_verified, 'base_up_to_date': event.base_up_to_date}
            if item['state'] in ('FAILED', 'CHANGES_REQUESTED'):
                return move(item['state'])
        elif event.kind == 'SIMULATE_MERGE':
            return move('MERGED') if item['state'] == 'MERGE_ELIGIBLE' and self._eligible(issue, item) else reject('not_merge_eligible')
        else:
            return reject('invalid_transition')
        if self._eligible(issue, item):
            item['failures'] = 0
            connection.execute("UPDATE attempts SET status='PASSED' WHERE issue=? AND number=?", (issue, item['attempts']))
            return move('MERGE_ELIGIBLE')
        if item['review_status'] == 'CHANGES_REQUESTED':
            return move('CHANGES_REQUESTED')
        return move('CI_PENDING' if item['review_status'] == 'APPROVED' else 'REVIEW_PENDING')

    def reconcile(self):
        """Expired work is UNKNOWN + HALTED; never refund, release into retry, or call a service."""
        expired = []
        with self._transaction() as (connection, now):
            rows = connection.execute("SELECT issue FROM attempts WHERE status='RUNNING' AND expires<=?", (now,)).fetchall()
            for row in rows:
                issue = row['issue']
                item = self._load(connection, issue)
                event_id = str(uuid4())
                connection.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                                   (event_id, hashlib.sha256(event_id.encode()).hexdigest(), issue, 'RECONCILE', 'APPLIED', 'lease_expired_ambiguous', now))
                self._stop_attempt(connection, issue, item, 'UNKNOWN')
                item['halt_reason'] = 'lease_expired_ambiguous'
                self._move(connection, issue, item, 'HALTED', event_id, now)
                expired.append(issue)
        return expired

    def backup(self, destination):
        destination = Path(destination).absolute()
        if destination.exists() or destination.is_symlink():
            raise LedgerError('Backup destination must be new')
        self.integrity()
        try:
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with self._connection() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                finally:
                    target.close()
            SQLiteLedger(destination, self.policy, clock=self.clock).integrity()
            checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
            manifest = destination.with_suffix(destination.suffix + '.ledger-backup.json')
            with manifest.open('x', encoding='utf-8') as handle:
                json.dump({'schema_version': SCHEMA_VERSION, 'sha256': checksum, 'mode': 'fake-only'}, handle)
            return checksum
        except (OSError, sqlite3.Error):
            raise LedgerError('Backup failed; do not use partial destination') from None

    @classmethod
    def restore(cls, backup, destination, policy, *, clock=now_utc):
        backup, destination = Path(backup).absolute(), Path(destination).absolute()
        if backup.is_symlink() or destination.exists() or destination.is_symlink():
            raise LedgerError('Restore requires a regular backup and new destination')
        try:
            manifest = json.loads(backup.with_suffix(backup.suffix + '.ledger-backup.json').read_text(encoding='utf-8'))
            if set(manifest) != {'schema_version', 'mode', 'sha256'} or manifest['schema_version'] != SCHEMA_VERSION or manifest['mode'] != 'fake-only' or hashlib.sha256(backup.read_bytes()).hexdigest() != manifest['sha256']:
                raise LedgerError('Backup checksum mismatch')
            original = cls(backup, policy, clock=clock)
            original.integrity()
            fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with original._connection() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target)
                    target.execute("UPDATE meta SET value='1' WHERE key='recovery_required'")
                    target.commit()
                finally:
                    target.close()
            restored = cls(destination, policy, clock=clock)
            # Keep the exact journal/ambiguous leases for audit. Global quarantine blocks ALL submissions.
            return restored
        except (OSError, sqlite3.Error, ValueError, KeyError):
            raise LedgerError('Restore failed; no execution permitted') from None
