"""Closed, metadata-only event contract. No issue bodies, prompts or credentials."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re
from uuid import UUID

STATES = ('ISSUE_APPROVED', 'IMPLEMENTATION_PENDING', 'IMPLEMENTATION_RUNNING', 'PR_CREATED',
          'REVIEW_PENDING', 'CHANGES_REQUESTED', 'REVISION_RUNNING', 'CI_PENDING',
          'MERGE_ELIGIBLE', 'MERGED', 'FAILED', 'HALTED')
KINDS = ('APPROVE', 'INVALIDATE', 'QUEUE', 'CLAIM', 'BUILT', 'REQUEST_REVIEW', 'REVIEW',
         'CI', 'GATE', 'HEAD_UPDATED', 'FAIL', 'RETRY', 'HALT', 'RENEW', 'DISPATCH', 'SIMULATE_MERGE')


class LedgerError(RuntimeError):
    """Sanitized failure; caller must not execute work after this exception."""


def integer(value, *, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid integer metadata')
    return value


def digest(value, size):
    if not isinstance(value, str) or re.fullmatch(f'[0-9a-f]{{{size}}}', value) is None:
        raise ValueError('Invalid hash metadata')
    return value


def identifier(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError('Canonical UUID required')
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class Check:
    name: str
    head_sha: str
    status: str = 'completed'
    conclusion: str = 'success'
    app_slug: str = 'github-actions'
    workflow_path: str = '.github/workflows/tests.yml'


@dataclass(frozen=True)
class Event:
    event_id: str
    issue: int
    kind: str
    spec_sha256: str
    spec_version: int = 1
    actor_id: int = 0
    expected_version: int = 0
    head_sha: str = ''
    head_version: int = 0
    pr_number: int = 0
    review_id: int = 0
    review_status: str = 'NONE'
    ci_run_id: int = 0
    ci_status: str = 'NONE'
    worker: str = ''
    attempt: int = 0
    cents: int = 0
    lease_seconds: int = 300
    checks: tuple[Check, ...] = ()
    changed_paths: tuple[str, ...] = ()
    unresolved_threads: bool | None = None
    branch_protection_verified: bool | None = None
    base_up_to_date: bool | None = None

    def __post_init__(self):
        identifier(self.event_id)
        integer(self.issue)
        integer(self.spec_version)
        digest(self.spec_sha256, 64)
        if self.kind not in KINDS:
            raise ValueError('Unknown event kind')
        for name in ('actor_id', 'expected_version', 'head_version', 'pr_number', 'review_id',
                     'ci_run_id', 'attempt', 'cents'):
            integer(getattr(self, name), minimum=0)
        if self.head_sha:
            digest(self.head_sha, 40)
        if self.worker:
            identifier(self.worker)
        if type(self.lease_seconds) is not int or not 1 <= self.lease_seconds <= 300:
            raise ValueError('Lease must be 1..300 seconds')
        if self.review_status not in ('NONE', 'APPROVED', 'CHANGES_REQUESTED'):
            raise ValueError('Unknown review status')
        if self.ci_status not in ('NONE', 'PENDING', 'PASSED', 'FAILED'):
            raise ValueError('Unknown CI status')
        for flag in (self.unresolved_threads, self.branch_protection_verified, self.base_up_to_date):
            if flag is not None and type(flag) is not bool:
                raise ValueError('Boolean verification metadata required')
        object.__setattr__(self, 'checks', tuple(self.checks))
        object.__setattr__(self, 'changed_paths', tuple(self.changed_paths))
        # Restrict free text before serialization; only fixed check names/paths are accepted by the store.
        if len(self.checks) > 4 or len(self.changed_paths) > 20:
            raise ValueError('Oversized metadata')
        if any(not isinstance(check, Check) for check in self.checks):
            raise ValueError('Typed check metadata required')

    def fingerprint(self):
        fields = asdict(self)
        del fields['event_id']
        return hashlib.sha256(canonical(fields).encode()).hexdigest()


@dataclass(frozen=True)
class Receipt:
    outcome: str
    reason: str
    issue: int
    state: str | None
    version: int
    attempt: int = 0
