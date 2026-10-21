"""Persistent dry-run coordinator. Exact fake adapters; there is no live switch."""
from uuid import uuid4
from .events import Event, LedgerError
from .fakes import FakeCodex, FakeGitHub, FakeReviewer
from .policy import approved, spec_hash
from .store import SQLiteLedger


class DryRunOrchestrator:
    def __init__(self, ledger, github, codex, reviewer):
        if type(ledger) is not SQLiteLedger or type(github) is not FakeGitHub or type(codex) is not FakeCodex or type(reviewer) is not FakeReviewer:
            raise LedgerError('Only exact local fake adapters are supported')
        if ledger.policy['enabled'] is not False:
            raise LedgerError('Automation must remain disabled')
        self.ledger, self.github, self.codex, self.reviewer = ledger, github, codex, reviewer
        self.worker = str(uuid4())
        # Recovery never calls an adapter or silently steals another process's lease.
        self.ledger.reconcile()

    def _event(self, issue, kind, *, event_id=None, **fields):
        current = self.ledger.get(issue)
        if current:
            base = {'spec_sha256': current['spec_sha256'], 'spec_version': current['spec_version'],
                    'head_sha': current['head_sha'], 'head_version': current['head_version']}
            if kind == 'RENEW':
                base['expected_version'] = current['version']
        else:
            base = {'spec_sha256': spec_hash(self.github.issues[issue]['body'])}
        return Event(event_id or str(uuid4()), issue, kind, **(base | fields))

    def _approval_current(self, issue):
        source = self.github.issues.get(issue)
        current = self.ledger.get(issue)
        valid = source is not None and approved(source, self.ledger.policy)
        if current and (not valid or spec_hash(source['body']) != current['spec_sha256']):
            self.ledger.submit(self._event(issue, 'INVALIDATE'))
            return False
        return valid

    def approve(self, issue, *, event_id=None, spec_version=1):
        source = self.github.issues[issue]
        valid = approved(source, self.ledger.policy)
        current = self.ledger.get(issue)
        if current and (not valid or spec_hash(source['body']) != current['spec_sha256']):
            self.ledger.submit(self._event(issue, 'INVALIDATE'))
        return self.ledger.submit(Event(event_id or str(uuid4()), issue, 'APPROVE', spec_hash(source['body']),
                                        spec_version=spec_version, actor_id=45135977 if valid else 0))

    def queue(self, issue, *, event_id=None):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval is absent or changed')
        return self.ledger.submit(self._event(issue, 'QUEUE', event_id=event_id))

    def claim(self, issue, *, cents=500, lease_seconds=300, event_id=None):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval is absent or changed')
        current = self.ledger.get(issue)
        if current is None:
            raise LedgerError('Approval not recorded')
        return self.ledger.submit(self._event(issue, 'CLAIM', event_id=event_id, worker=self.worker,
                                             expected_version=current['version'], cents=cents, lease_seconds=lease_seconds))

    def build(self, issue):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        current = self.ledger.get(issue)
        if current is None or current['state'] not in ('IMPLEMENTATION_RUNNING', 'REVISION_RUNNING'):
            raise LedgerError('No reserved running fake work')
        # Validate lease/owner before even a fake side effect; the completion also checks it transactionally.
        heartbeat = self.ledger.submit(self._event(issue, 'RENEW', worker=self.worker, attempt=current['active_attempt']))
        if heartbeat.outcome != 'APPLIED' or heartbeat.state not in ('IMPLEMENTATION_RUNNING', 'REVISION_RUNNING'):
            raise LedgerError('Lease invalid; no fake work permitted')
        dispatch = self.ledger.submit(self._event(issue, 'DISPATCH', worker=self.worker, attempt=current['active_attempt']))
        if dispatch.outcome != 'APPLIED':
            raise LedgerError('Attempt already dispatched; reconciliation required')
        try:
            head = self.codex.work(issue, current['spec_sha256'], current['active_attempt'])
            pr = self.github.publish(issue, current['head_version'] + 1, current['pr_number'])
            result = self.ledger.submit(self._event(issue, 'BUILT', actor_id=1001, worker=self.worker,
                                                   attempt=current['active_attempt'], pr_number=pr,
                                                   head_sha=head, head_version=current['head_version'] + 1))
        except Exception:
            # Never claim that an exception means no side effect happened after dispatch.
            try:
                self.ledger.submit(self._event(issue, 'HALT'))
            except LedgerError:
                pass  # Persisted RUNNING/dispatch token remains fenced until lease reconciliation.
            raise LedgerError('Fake task outcome uncertain; reconciliation required') from None
        # Crash between fake work and recording is deliberately ambiguous: no automatic replay.
        return result

    def request_review(self, issue):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        return self.ledger.submit(self._event(issue, 'REQUEST_REVIEW'))

    def gate(self, issue, **fields):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        defaults = {'changed_paths': ('tests/test_fake_generated.py',), 'unresolved_threads': False,
                    'branch_protection_verified': True, 'base_up_to_date': True}
        return self.ledger.submit(self._event(issue, 'GATE', **(defaults | fields)))

    def review(self, issue, *, changes=False, review_id=1, event_id=None):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        current = self.ledger.get(issue)
        state = self.reviewer.review(current['head_sha'], changes=changes)
        return self.ledger.submit(self._event(issue, 'REVIEW', event_id=event_id, actor_id=2002,
                                             review_id=review_id, review_status=state))

    def ci(self, issue, *, success=True, run_id=1, event_id=None):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        current = self.ledger.get(issue)
        checks = self.github.checks(current['head_sha'], self.ledger.policy, success=success)
        return self.ledger.submit(self._event(issue, 'CI', event_id=event_id, ci_run_id=run_id,
                                             ci_status='PASSED' if success else 'FAILED', checks=checks))

    def retry(self, issue):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        return self.ledger.submit(self._event(issue, 'RETRY'))

    def simulate_merge(self, issue):
        if not self._approval_current(issue):
            raise LedgerError('Issue approval changed')
        # LOCAL STATE ONLY. No adapter method, HTTP, Push, PR creation or merge API.
        return self.ledger.submit(self._event(issue, 'SIMULATE_MERGE'))

    def approve_next(self):
        for number in sorted(self.github.issues):
            current = self.ledger.get(number)
            if current and current['state'] not in ('MERGED', 'HALTED'):
                return None  # Never automatically claim a second issue while a workflow is active.
        for number in sorted(self.github.issues):
            if self.ledger.get(number) is None and self._approval_current(number):
                return self.approve(number)
        return None
