"""P3 contracts. NO paid backend, environment access, subprocess or network runner."""
from dataclasses import dataclass, field
import hashlib
import json
import re
from typing import Protocol
from uuid import UUID

from .events import canonical, digest


class WorkerBlocked(RuntimeError):
    """Fixed reason code; never attach prompts, patches, tokens or tool output."""


def require(condition, reason):
    if not condition:
        raise WorkerBlocked(reason)


def number(value, minimum=1):
    require(type(value) is int and value >= minimum, 'invalid_counter')
    return value


def uid(value):
    try:
        require(str(UUID(value)) == value, 'invalid_task_id')
    except (ValueError, TypeError, AttributeError):
        raise WorkerBlocked('invalid_task_id') from None
    return value


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Isolation:
    wall_seconds: int = 120
    max_calls: int = 1
    max_output_bytes: int = 131072
    network: bool = False
    environment: tuple = ()
    writable_scope: str = 'ephemeral-task-only'

    def validate(self):
        require(type(self.wall_seconds) is int and 1 <= self.wall_seconds <= 120, 'invalid_runtime_limit')
        require(self.max_calls == 1 and type(self.max_calls) is int, 'invalid_call_limit')
        require(type(self.max_output_bytes) is int and 1 <= self.max_output_bytes <= 131072, 'invalid_output_limit')
        require(self.network is False and self.environment == () and self.writable_scope == 'ephemeral-task-only', 'unsafe_capability')


@dataclass(frozen=True)
class WorkInput:
    task: str
    issue: int
    attempt: int
    spec_version: int
    spec_sha256: str
    approved_base: str
    checkout: str
    specification: str = field(repr=False)
    feedback: str = field(default='', repr=False)
    isolation: Isolation = Isolation()

    def validate(self):
        uid(self.task)
        for value in (self.issue, self.attempt, self.spec_version): number(value)
        require(self.attempt <= 3, 'attempt_limit')
        digest(self.spec_sha256, 64); digest(self.approved_base, 40); digest(self.checkout, 40)
        require(type(self.specification) is str and 0 < len(self.specification.encode()) <= 32768, 'invalid_specification')
        require(hashlib.sha256(self.specification.encode()).hexdigest() == self.spec_sha256, 'specification_mismatch')
        require(type(self.feedback) is str and len(self.feedback.encode()) <= 8192, 'feedback_limit')
        self.isolation.validate()

    def prompt_data(self):
        self.validate()
        # Structured data only: never interpolate into a shell/template/command.
        return json.dumps({'untrusted_specification': self.specification, 'untrusted_feedback': self.feedback}, ensure_ascii=True)


@dataclass(frozen=True)
class Change:
    path: str
    before: str
    content: bytes = field(repr=False)
    mode: str = '100644'
    operation: str = 'write'


@dataclass(frozen=True)
class Patch:
    task: str
    issue: int
    attempt: int
    spec_version: int
    spec_sha256: str
    approved_base: str
    parent: str
    head: str
    changes: tuple[Change, ...] = field(repr=False)


def patch_head(task, parent, changes):
    # Explicit FAKE commit identity, not a valid Git commit object or production SHA proof.
    return hashlib.sha1(('FAKE-COMMIT:' + canonical([task, parent, [
        [x.path, x.before, hashlib.sha256(x.content).hexdigest(), x.mode, x.operation] for x in changes]])).encode()).hexdigest()


class Worker(Protocol):
    def run(self, request: WorkInput) -> Patch: ...


class Publisher(Protocol):
    """Separate broker principal; worker never receives this capability."""
    def publish(self, task_metadata: dict, validated_patch) -> dict: ...
    def lookup(self, task: str) -> dict | None: ...


class UsageMeter(Protocol):
    """Future included-subscription quota evidence. NOT billing authority or an API backend."""
    def authorize(self, task: str, maximum_cents: int) -> bool: ...
    def reconcile(self, task: str) -> dict: ...


class DisabledPaidWorker:
    def run(self, request):
        # No flags, key arguments, CLI installation or fake hard-cap claim can open this.
        raise WorkerBlocked('paid_execution_unavailable')


class FakeUsageMeter:
    def authorize(self, task, maximum_cents):
        uid(task); number(maximum_cents)
        return False  # Never a billing enforcement capability.

    def reconcile(self, task):
        uid(task)
        return {'actual_model_calls': 0, 'actual_cents': 0, 'hard_cap_verified': False}


class FakeWorker:
    """In-memory capability simulation. NOT an OS sandbox or a model invocation."""
    def __init__(self, changes, *, elapsed=1, calls=1, fail=False):
        self.changes = tuple(changes)
        self.elapsed, self.calls, self.fail = elapsed, calls, fail
        self.invocations = 0

    def run(self, request):
        require(type(request) is WorkInput, 'typed_work_input_required')
        request.validate()
        self.invocations += 1
        # Simulated supervisor observations. A live worker's own timing report is not trusted.
        require(type(self.elapsed) is int and 0 <= self.elapsed < request.isolation.wall_seconds, 'worker_timeout')
        require(type(self.calls) is int and self.calls == request.isolation.max_calls, 'worker_call_limit')
        require(sum(len(c.content) for c in self.changes) <= request.isolation.max_output_bytes, 'worker_output_limit')
        require(not self.fail, 'worker_failed')
        return Patch(request.task, request.issue, request.attempt, request.spec_version, request.spec_sha256,
                     request.approved_base, request.checkout, patch_head(request.task, request.checkout, self.changes), self.changes)


@dataclass(frozen=True)
class Feedback:
    issue: int
    pr: int
    head: str
    head_version: int
    review_id: int
    reviewer: int
    state: str
    text: str = field(default='', repr=False)

    def validate(self):
        for value in (self.issue, self.pr, self.head_version, self.review_id, self.reviewer): number(value)
        digest(self.head, 40)
        require(self.state in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'), 'invalid_review')
        require(type(self.text) is str and len(self.text.encode()) <= 8192, 'feedback_limit')


class FakeIndependentReviewer:
    """No model or GitHub client; identity is a test fixture, never an authentication proof."""
    def review(self, issue, pr, head, head_version, review_id, *, state='APPROVED', text=''):
        result = Feedback(issue, pr, head, head_version, review_id, 2002, state, text)
        result.validate()
        return result


def p2_feedback_candidates(snapshot):
    """Unprivileged metadata contract only; cannot mint Fake feedback/approval authority."""
    from .github_verify import Snapshot
    require(type(snapshot) is Snapshot and snapshot.pr > 0, 'p2_snapshot_required')
    return {'issue': snapshot.issue, 'pr': snapshot.pr, 'head': snapshot.head,
            'reviews': snapshot.reviews, 'execution_enabled': False, 'requires_fresh_broker_authentication': True}
