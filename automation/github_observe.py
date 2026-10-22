"""Untrusted event routing + separate local observation journal. No execution bridge."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import UUID

from .events import canonical
from .github_read import VerificationPending, positive
from .github_verify import Snapshot
from .observer_diagnostics import diagnostic

ACTIONS = {
    'issues': {'opened', 'edited', 'labeled', 'unlabeled', 'reopened', 'closed'},
    'issue_comment': {'created', 'edited', 'deleted'},
    'pull_request': {'opened', 'synchronize', 'edited', 'reopened', 'closed'},
    'pull_request_target': {'opened', 'synchronize', 'edited', 'reopened', 'closed'},
    'pull_request_review': {'submitted', 'dismissed', 'edited'},
    'workflow_run': {'completed'},
}


@dataclass(frozen=True)
class Hint:
    kind: str
    action: str
    resource: int
    target: str

    def __post_init__(self):
        if self.kind not in ACTIONS or self.action not in ACTIONS[self.kind] or self.target not in ('issue', 'pr', 'run'):
            raise VerificationPending('unsupported_event')
        positive(self.resource)


def route(kind, event, repository):
    """Actions provides transport context; payload is only a resource lookup hint."""
    try:
        if event['repository']['full_name'] != repository or event['repository']['id'] != 888367411:
            raise VerificationPending('event_repository_mismatch')
        action = event['action']
        if kind == 'workflow_run':
            return Hint(kind, action, event['workflow_run']['id'], 'run')
        if kind in ('pull_request', 'pull_request_target', 'pull_request_review'):
            return Hint(kind, action, event['pull_request']['number'], 'pr')
        issue = event['issue']
        return Hint(kind, action, issue['number'], 'pr' if 'pull_request' in issue else 'issue')
    except VerificationPending:
        raise
    except Exception:
        raise VerificationPending('invalid_event_hint') from None


SCHEMA = (
    'CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    '''CREATE TABLE observations(seq INTEGER PRIMARY KEY, delivery TEXT UNIQUE NOT NULL,
       target TEXT NOT NULL, resource INTEGER NOT NULL, kind TEXT NOT NULL, action TEXT NOT NULL,
       status TEXT NOT NULL, metadata TEXT, sha TEXT)''',
    '''CREATE TABLE current(target TEXT NOT NULL, resource INTEGER NOT NULL, seq INTEGER NOT NULL
       REFERENCES observations(seq), version INTEGER NOT NULL, PRIMARY KEY(target,resource))''',
)


def schema_hash(db):
    return hashlib.sha256(canonical(db.execute("SELECT type,name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()).encode()).hexdigest()


class ObservationJournal:
    """Companion SQLite, not P1 schema; real data cannot become Fake-only Events."""
    def __init__(self, path):
        self.path = Path(path).absolute()
        if self.path.is_symlink() or not self.path.is_file():
            raise VerificationPending('observation_database_required')
        with self._transaction() as db:
            self._validate(db)

    @classmethod
    def create(cls, path):
        path = Path(path)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(path) as db:
                db.execute('PRAGMA application_id=1096041551')
                db.execute('PRAGMA user_version=1')
                for command in SCHEMA:
                    db.execute(command)
                db.execute('INSERT INTO meta VALUES (?,?)', ('schema', schema_hash(db)))
                db.commit()
            db.close()
            return cls(path)
        except Exception:
            raise VerificationPending('observation_create_failed') from None

    @contextmanager
    def _transaction(self):
        db = None
        try:
            db = sqlite3.connect(self.path.as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=5)
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN IMMEDIATE')
            self._validate(db)
            yield db
            self._validate(db)
            db.commit()
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('observation_database_unverified') from None
        finally:
            if db:
                db.rollback()
                db.close()

    def _validate(self, db):
        if (db.execute('PRAGMA application_id').fetchone()[0] != 1096041551
                or db.execute('PRAGMA user_version').fetchone()[0] != 1
                or db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]
                or db.execute('PRAGMA foreign_key_check').fetchone()
                or db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] != schema_hash(db)):
            raise VerificationPending('observation_integrity')
        for seq, metadata, sha in db.execute('SELECT seq,metadata,sha FROM observations WHERE metadata IS NOT NULL'):
            if hashlib.sha256(metadata.encode()).hexdigest() != sha:
                raise VerificationPending('observation_integrity')
            Snapshot(**json.loads(metadata))
        for status, metadata in db.execute('SELECT status,metadata FROM observations'):
            if status not in ('PENDING', 'UNVERIFIED', 'VERIFIED') or (status == 'VERIFIED') != (metadata is not None):
                raise VerificationPending('observation_integrity')
        for target, resource, seq, version in db.execute('SELECT target,resource,seq,version FROM current'):
            row = db.execute('SELECT target,resource,status FROM observations WHERE seq=?', (seq,)).fetchone()
            run_alias = (row is not None and row[0] == 'run' and row[2] == 'VERIFIED' and target == 'pr'
                         and json.loads(db.execute('SELECT metadata FROM observations WHERE seq=?', (seq,)).fetchone()[0])['pr'] == resource)
            if (row != (target, resource, 'VERIFIED') and not run_alias) or type(version) is not int or version < 1:
                raise VerificationPending('observation_integrity')

    def begin(self, delivery, hint):
        # Delivery IDs are opaque canonical UUIDs, or trusted Actions run/attempt IDs.
        try:
            valid = bool(re.fullmatch(r'actions:[1-9][0-9]*:[1-9][0-9]*', delivery)
                         or (len(delivery) == 36 and str(UUID(delivery)) == delivery))
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise VerificationPending('invalid_delivery_id')
        with self._transaction() as db:
            old = db.execute('SELECT seq,target,resource,kind,action FROM observations WHERE delivery=?', (delivery,)).fetchone()
            if old:
                if old[1:] != (hint.target, hint.resource, hint.kind, hint.action):
                    raise VerificationPending('delivery_reused')
                return None
            # Insert sequence BEFORE API calls. A late earlier request cannot replace a newer observation.
            cursor = db.execute('INSERT INTO observations(delivery,target,resource,kind,action,status) VALUES (?,?,?,?,?,?)',
                                (delivery, hint.target, hint.resource, hint.kind, hint.action, 'PENDING'))
            return cursor.lastrowid

    def complete(self, seq, snapshot=None):
        if snapshot is not None and type(snapshot) is not Snapshot:
            raise VerificationPending('snapshot_required')
        with self._transaction() as db:
            row = db.execute('SELECT target,resource,status FROM observations WHERE seq=?', (seq,)).fetchone()
            if not row or row[2] != 'PENDING':
                raise VerificationPending('observation_not_pending')
            target, resource, _ = row
            if snapshot is None:
                db.execute("UPDATE observations SET status='UNVERIFIED' WHERE seq=?", (seq,))
                return 'UNVERIFIED'
            if ((target == 'issue' and snapshot.issue != resource) or (target == 'pr' and snapshot.pr != resource)):
                raise VerificationPending('snapshot_target_mismatch')
            metadata = canonical(snapshot.metadata())
            db.execute("UPDATE observations SET status='VERIFIED',metadata=?,sha=? WHERE seq=?",
                       (metadata, hashlib.sha256(metadata.encode()).hexdigest(), seq))
            # Run event routing records current PR separately; source run remains an audit key.
            targets = [(target, resource)]
            if target == 'run':
                targets.append(('pr', snapshot.pr))
            applied = False
            for current_target, current_resource in targets:
                # A newer in-flight/failed request also fences an older completion.
                later = db.execute('SELECT 1 FROM observations WHERE seq>? AND target=? AND resource=? LIMIT 1',
                                   (seq, current_target, current_resource)).fetchone()
                if later:
                    continue
                newer = db.execute('SELECT seq,version FROM current WHERE target=? AND resource=?', (current_target, current_resource)).fetchone()
                if newer and newer[0] >= seq:
                    continue
                # A verified decrease of server update time is treated as unstable, never rollback.
                if newer:
                    previous = json.loads(db.execute('SELECT metadata FROM observations WHERE seq=?', (newer[0],)).fetchone()[0])
                    if snapshot.issue_updated < previous['issue_updated'] or (snapshot.pr and snapshot.pr_updated < previous['pr_updated']):
                        continue
                db.execute('INSERT INTO current VALUES (?,?,?,?) ON CONFLICT(target,resource) DO UPDATE SET seq=excluded.seq,version=excluded.version',
                           (current_target, current_resource, seq, newer[1] + 1 if newer else 1))
                applied = True
            return snapshot.decision if applied else 'PENDING'

    def current(self, target, resource):
        with self._transaction() as db:
            row = db.execute('SELECT c.seq,c.version,o.metadata FROM current c JOIN observations o ON o.seq=c.seq WHERE c.target=? AND c.resource=?',
                             (target, resource)).fetchone()
            # Any newer pending/failed verification invalidates cached candidate status.
            # Scope uncertainty to this resource and API-verified workflow-run aliases.
            # A new unmapped run is not proof that any arbitrary PR changed.
            related = [(target, resource)]
            if target == 'pr':
                related.extend(('run', run) for run, metadata in db.execute(
                    "SELECT resource,metadata FROM observations WHERE target='run' AND status='VERIFIED'")
                    if json.loads(metadata)['pr'] == resource)
            latest = max(db.execute('SELECT COALESCE(MAX(seq),0) FROM observations WHERE target=? AND resource=?',
                                    (kind, number)).fetchone()[0] for kind, number in related)
            uncertain = any(db.execute(
                "SELECT 1 FROM observations WHERE seq>? AND target=? AND resource=? AND status!='VERIFIED' LIMIT 1",
                (row[0] if row else 0, kind, number)).fetchone() for kind, number in related)
            if not row or uncertain or (latest and latest > row[0]):
                return None
            return {'version': row[1], 'snapshot': json.loads(row[2]), 'execution_enabled': False,
                    'freshness': 'historical_only_requery_required'}


def observe(verifier, journal, delivery, hint):
    seq = journal.begin(delivery, hint)
    if seq is None:
        # Never return a cached candidate from a duplicate webhook.
        return {'decision': 'PENDING', 'reason': 'duplicate_requires_fresh_poll', 'execution_enabled': False}
    try:
        if hint.target == 'run':
            run = verifier.api.get(f'/actions/runs/{hint.resource}')
            if run['id'] != hint.resource or run['repository']['full_name'] != verifier.policy['repository']:
                raise VerificationPending('run_mismatch')
            prs = run['pull_requests']
            if len(prs) != 1:
                raise VerificationPending('run_pr_ambiguous')
            snapshot = verifier.verify(pr=positive(prs[0]['number']))
            if snapshot.head != run['head_sha']:
                raise VerificationPending('stale_run_event')
        else:
            snapshot = verifier.verify(**{hint.target: hint.resource})
        outcome = journal.complete(seq, snapshot)
        return {'decision': outcome, 'reason': snapshot.reason if outcome != 'PENDING' else 'superseded_observation', 'execution_enabled': False}
    except Exception as error:
        journal.complete(seq)
        return {'decision': 'PENDING', 'reason': 'github_state_unverified', 'diagnostic': diagnostic(error), 'execution_enabled': False}


def p1_reference(snapshot, ledger):
    """Optional read-only comparison, no submit/Event conversion/dispatch/reservation."""
    from .store import SQLiteLedger
    if type(snapshot) is not Snapshot or type(ledger) is not SQLiteLedger:
        raise VerificationPending('snapshot_required')
    item = ledger.get(snapshot.issue)
    return {'matched': bool(item and item['spec_sha256'] == snapshot.spec_sha256),
            'execution_enabled': False, 'source': 'github_observation_not_fake_event'}
