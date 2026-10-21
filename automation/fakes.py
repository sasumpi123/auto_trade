"""Exact fake adapters only. No HTTP clients, credentials, file edits or subprocesses."""
from dataclasses import dataclass, field
import hashlib
from .events import Check
from .policy import spec_hash


@dataclass
class FakeGitHub:
    repository: str = 'sasumpi123/auto_trade'
    issues: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)

    def add_issue(self, number, *, body='Fake offline development specification', approved=True):
        self.issues[number] = {'number': number, 'state': 'open', 'repository': self.repository,
                               'body': body, 'labels': ['autodev:approved'] if approved else [],
                               'approval_comments': [{'author_id': 45135977, 'body': '/autodev approve ' + spec_hash(body)}] if approved else []}

    def publish(self, issue, version, previous_pr=0):
        self.calls.append(('fake_publish', issue, version))
        return previous_pr or 10000 + issue

    def checks(self, head, policy, *, success=True):
        self.calls.append(('fake_ci', head, success))
        return tuple(Check(name, head, conclusion='success' if success else 'failure') for name in policy['required_checks'])


@dataclass
class FakeCodex:
    calls: list = field(default_factory=list)

    def work(self, issue, specification_hash, attempt):
        self.calls.append((issue, specification_hash, attempt))
        return hashlib.sha1(f'fake-only:{issue}:{specification_hash}:{attempt}'.encode()).hexdigest()


@dataclass
class FakeReviewer:
    calls: list = field(default_factory=list)

    def review(self, head, *, changes=False):
        self.calls.append((head, changes))
        return 'CHANGES_REQUESTED' if changes else 'APPROVED'
