"""P3 end-to-end simulation. Exact fake capabilities only; no live switch."""
import copy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import threading
from uuid import uuid4

from .policy import Ledger, reserve, spec_hash
from .patch_guard import PatchGuard, TreeFile, ValidatedPatch
from .worker_contracts import (Feedback, FakeWorker, WorkInput, WorkerBlocked, fingerprint,
                               number, require, uid)
from .worker_store import ACTIVE, FakeSharedStore, SQLiteTaskStore


@dataclass(frozen=True)
class FakeApproval:
    issue: int
    specification: str = field(repr=False)
    base: str
    version: int = 1
    approved: bool = True

    @property
    def digest(self):
        return spec_hash(self.specification)


class FakeAuthority:
    def __init__(self):
        self.lock = threading.RLock()
        self.issues = {}
        self.reviews = {}

    def approve(self, issue, specification, base):
        number(issue)
        with self.lock:
            previous = self.issues.get(issue)
            self.issues[issue] = FakeApproval(issue, specification, base, previous.version + 1 if previous else 1)

    def cancel(self, issue):
        with self.lock:
            self.issues[issue] = replace(self.issues[issue], approved=False, version=self.issues[issue].version + 1)

    def review(self, feedback):
        require(type(feedback) is Feedback, 'fake_feedback_required')
        feedback.validate()
        with self.lock:
            self.reviews[feedback.issue] = feedback


class Coordinator:
    def __init__(self, store, authority, policy, *, clock):
        require(type(store) in (SQLiteTaskStore, FakeSharedStore) and type(authority) is FakeAuthority, 'fake_coordination_required')
        require(policy.get('enabled') is False, 'disabled_policy_required')
        self.store, self.authority, self.policy, self.clock = store, authority, copy.deepcopy(policy), clock

    def state(self, issue):
        return self.store.read()['issues'].get(str(issue))

    def _change(self, kind, metadata, apply, event=None):
        now = self.clock()
        require(isinstance(now, datetime) and now.tzinfo is not None and now.utcoffset() is not None, 'utc_clock_required')
        seconds = int(now.astimezone(timezone.utc).timestamp())
        def run(state):
            require(seconds >= state['clock'], 'clock_reversed')
            state['clock'] = seconds
            return apply(state, seconds)
        with self.authority.lock:
            return self.store.change(event or str(uuid4()), kind, metadata, run)

    def _fresh(self, item, approval):
        return bool(approval and approval.approved and item['spec'] == approval.digest
                    and item['spec_version'] == approval.version and item['base'] == approval.base)

    def sync(self, issue):
        approval = self.authority.issues.get(issue)
        def apply(state, now):
            item = state['issues'].get(str(issue))
            latest = self.authority.issues.get(issue)
            if item and (latest != approval or not self._fresh(item, approval)):
                self._halt(state, item)
            return {'approved': bool(latest == approval and approval and approval.approved and (not item or self._fresh(item, approval)))}
        result = self._change('APPROVAL_CHECK', [issue, approval.digest if approval else '', approval.version if approval else 0], apply)
        require(result['approved'], 'approval_revoked_or_changed')
        return approval

    def _halt(self, state, item):
        item['status'] = 'HALTED'; item['version'] += 1
        for task in state['tasks'].values():
            if task['issue'] == item['issue'] and task['status'] in ACTIVE:
                task['status'] = 'UNKNOWN'

    def claim(self, issue, owner, *, expected_version=0, cents=500, lease=300, event=None):
        uid(owner); number(lease)
        require(lease <= 300, 'lease_limit')
        approval = self.sync(issue)
        def apply(state, now):
            require(self.authority.issues.get(issue) == approval, 'approval_changed')
            item = state['issues'].setdefault(str(issue), dict(issue=issue, version=0, spec=approval.digest,
                spec_version=approval.version, base=approval.base, attempts=0, failures=0, reserved=0,
                status='READY', pr=0, head='', head_version=0, review_id=0, review_state='NONE', review_hash='',
                ci_id=0, ci_pass=False, current_task=''))
            require(self._fresh(item, approval), 'approval_changed')
            require(item['version'] == expected_version and item['status'] in ('READY', 'CHANGES_REQUESTED', 'FAILED'), 'not_claimable_or_stale_cas')
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            try:
                updated = reserve(Ledger(attempts=item['attempts'], failures=item['failures'], reserved_cents=item['reserved'],
                    day_reserved_cents=state['budgets'].get(day, 0)), self.policy, event_key=str(uuid4()), cents=cents)
            except ValueError:
                self._halt(state, item)
                return {'blocked': True}
            if item['status'] == 'CHANGES_REQUESTED':
                feedback = self.authority.reviews.get(issue)
                require(feedback and fingerprint([feedback.review_id, feedback.state, feedback.text, feedback.head]) == item['review_hash'], 'feedback_changed')
            task_id = str(uuid4())
            task = dict(id=task_id, issue=issue, attempt=updated.attempts, owner=owner, spec=item['spec'], spec_version=item['spec_version'],
                base=item['base'], parent=item['head'] or item['base'], started=now, expires=now+lease, day=day, cents=cents,
                status='RUNNING', dispatched=False, failed=False, patch='', head='', receipt=None, feedback_hash=item['review_hash'])
            state['tasks'][task_id] = task
            state['budgets'][day] = updated.day_reserved_cents
            item.update(version=item['version']+1, attempts=updated.attempts, reserved=updated.reserved_cents,
                        current_task=task_id, status='RUNNING', review_state='NONE', ci_pass=False)
            return copy.deepcopy(task)
        result = self._change('CLAIM', [issue, owner, expected_version, cents, lease], apply, event)
        require(not result.get('blocked'), 'reservation_limit')
        return result

    def _leased(self, state, task_id, owner, now, statuses):
        task = state['tasks'][task_id]; item = state['issues'][str(task['issue'])]
        require(task['owner'] == owner and task['status'] in statuses and item['current_task'] == task_id
                and item['status'] != 'HALTED' and now < task['expires'], 'lease_or_state_invalid')
        require(self._fresh(item, self.authority.issues.get(task['issue'])), 'approval_changed')
        if task['feedback_hash']:
            feedback = self.authority.reviews.get(task['issue'])
            require(feedback and fingerprint([feedback.review_id, feedback.state, feedback.text, feedback.head]) == task['feedback_hash'], 'feedback_changed')
        return task, item

    def dispatch(self, task_id, owner):
        def apply(state, now):
            task, item = self._leased(state, task_id, owner, now, ('RUNNING',))
            require(not task['dispatched'], 'already_dispatched')
            task['dispatched'] = True; item['version'] += 1
            return copy.deepcopy(task)
        task = self._change('DISPATCH', [task_id, owner], apply)
        approval = self.sync(task['issue'])
        feedback = self.authority.reviews.get(task['issue'])
        text = feedback.text if feedback and task['feedback_hash'] else ''
        if text:
            require(fingerprint([feedback.review_id, feedback.state, feedback.text, feedback.head]) == task['feedback_hash'], 'feedback_changed')
        return WorkInput(task_id, task['issue'], task['attempt'], task['spec_version'], task['spec'], task['base'], task['parent'], approval.specification, text)

    def renew(self, task_id, owner, *, expected_version, seconds=300):
        number(seconds); require(seconds <= 300, 'lease_limit')
        def apply(state, now):
            task, item = self._leased(state, task_id, owner, now, ACTIVE)
            require(item['version'] == expected_version, 'stale_cas')
            task['expires'] = min(now+seconds, task['started']+1200)
            require(task['expires'] > now, 'absolute_runtime_limit')
            item['version'] += 1
            return {'version': item['version']}
        return self._change('RENEW', [task_id, owner, expected_version, seconds], apply)

    def stage(self, task_id, owner, validated):
        require(type(validated) is ValidatedPatch, 'validated_patch_required')
        def apply(state, now):
            task, item = self._leased(state, task_id, owner, now, ('RUNNING',))
            require(task['dispatched'] and validated.patch.task == task_id, 'dispatch_required')
            task.update(status='PATCH_READY', patch=validated.digest, head=validated.patch.head)
            item['version'] += 1
            return {'task': task_id}
        return self._change('OUTPUT', [task_id, owner, validated.digest], apply)

    def prepare(self, task_id, owner, validated):
        def apply(state, now):
            task, item = self._leased(state, task_id, owner, now, ('PATCH_READY',))
            require(task['patch'] == validated.digest and task['head'] == validated.patch.head, 'patch_changed')
            task['status'] = 'PUBLISHING'; item['version'] += 1
            return copy.deepcopy(task)
        return self._change('PREPARE', [task_id, owner, validated.digest], apply)

    def publish_guard(self, task_id, owner):
        # Re-check immediately before Fake side effect. Production needs server-side fencing.
        state = self.store.read()
        now = int(self.clock().timestamp())
        task, _ = self._leased(state, task_id, owner, now, ('PUBLISHING',))
        return task

    def published(self, task_id, owner, receipt):
        def apply(state, now):
            task, item = self._leased(state, task_id, owner, now, ('PUBLISHING',))
            require(receipt == {'task': task_id, 'issue': task['issue'], 'patch': task['patch'], 'parent': task['parent'],
                                'head': task['head'], 'pr': receipt['pr']} and type(receipt['pr']) is int and receipt['pr'] > 0, 'receipt_mismatch')
            require(not item['pr'] or receipt['pr'] == item['pr'], 'pr_changed')
            task.update(status='PUBLISHED', receipt=receipt)
            item.update(version=item['version']+1, pr=receipt['pr'], head=receipt['head'], head_version=item['head_version']+1,
                        status='WAIT_REVIEW', review_id=0, review_state='NONE', review_hash='', ci_id=0, ci_pass=False)
            return {'head': item['head'], 'pr': item['pr'], 'execution_enabled': False}
        return self._change('PUBLISHED', [task_id, owner, receipt], apply)

    def fail(self, task_id, *, unknown=False):
        def apply(state, now):
            require(task_id in state['tasks'], 'unknown_task')
            task = state['tasks'][task_id]; item = state['issues'][str(task['issue'])]
            if not task['failed']:
                item['failures'] += 1; task['failed'] = True
            uncertain = unknown or task['status'] == 'UNKNOWN' or item['status'] == 'HALTED'
            task['status'] = 'UNKNOWN' if uncertain else 'FAILED'
            item['status'] = 'HALTED' if uncertain or item['failures'] >= 2 else 'FAILED'
            item['version'] += 1
            return {'status': item['status']}
        return self._change('HALT' if unknown else 'FAIL', [task_id, unknown], apply)

    def reconcile(self):
        def apply(state, now):
            count = 0
            for task in state['tasks'].values():
                if task['status'] in ACTIVE and now >= task['expires']:
                    self._halt(state, state['issues'][str(task['issue'])]); count += 1
            return {'halted': count}
        return self._change('RECONCILE', [], apply)

    def review(self, issue, *, event=None):
        self.sync(issue)
        feedback = self.authority.reviews.get(issue)
        require(type(feedback) is Feedback, 'independent_review_unavailable')
        feedback.validate()
        require(feedback.reviewer == 2002 and feedback.reviewer != 1001, 'independent_reviewer_required')
        data = [feedback.review_id, feedback.state, feedback.text, feedback.head]
        def apply(state, now):
            item = state['issues'][str(issue)]
            require(self.authority.reviews.get(issue) == feedback, 'feedback_changed')
            require(item['status'] in ('WAIT_REVIEW', 'CHANGES_REQUESTED', 'MERGE_CANDIDATE'), 'review_invalid_state')
            require((feedback.pr, feedback.head, feedback.head_version) == (item['pr'], item['head'], item['head_version']), 'stale_review')
            if feedback.review_id < item['review_id']:
                return {'status': 'STALE'}
            if feedback.state == 'DISMISSED' or (feedback.review_id == item['review_id'] and item['review_hash'] != fingerprint(data)):
                self._halt(state, item)
                return {'status': 'HALTED'}
            if item['review_state'] == 'CHANGES_REQUESTED' and feedback.state == 'APPROVED':
                self._halt(state, item)
                return {'status': 'HALTED'}
            if item['review_hash'] == fingerprint(data):
                return {'status': 'DUPLICATE'}
            item.update(review_id=feedback.review_id, review_state=feedback.state, review_hash=fingerprint(data), version=item['version']+1)
            item['status'] = 'CHANGES_REQUESTED' if feedback.state == 'CHANGES_REQUESTED' else 'WAIT_REVIEW'
            self._candidate(item)
            return {'status': item['status'], 'execution_enabled': False}
        return self._change('REVIEW', [issue, fingerprint(data)], apply, event)

    def ci(self, issue, *, head, head_version, run_id, checks):
        self.sync(issue); number(run_id)
        # Closed metadata only. Malformed new evidence revokes a candidate instead of throwing past it.
        fields = ('name', 'head', 'status', 'conclusion', 'app_id', 'workflow')
        safe_checks = []
        if type(checks) in (list, tuple) and len(checks) <= 20:
            for row in checks:
                safe_checks.append({key: (row.get(key) if type(row) is dict and type(row.get(key)) in (str, int) else None)
                                    for key in fields})
        checks = safe_checks
        def apply(state, now):
            item = state['issues'][str(issue)]
            require(item['status'] in ('WAIT_REVIEW', 'CHANGES_REQUESTED', 'MERGE_CANDIDATE'), 'ci_invalid_state')
            require((head, head_version) == (item['head'], item['head_version']), 'stale_ci')
            require(run_id > item['ci_id'], 'ci_duplicate_or_old')
            names = [x.get('name') if type(x) is dict else None for x in checks]
            passed = (all(type(n) is str for n in names) and len(names) == len(self.policy['required_checks']) and set(names) == set(self.policy['required_checks'])
                      and all(type(x) is dict and x.get('head') == head and x.get('status') == 'completed' and x.get('conclusion') == 'success'
                              and x.get('app_id') == 15368 and x.get('workflow') == self.policy['trusted_ci_workflow'] for x in checks))
            item.update(ci_id=run_id, ci_pass=passed, version=item['version']+1)
            if not passed:
                task = state['tasks'][item['current_task']]
                if not task['failed']:
                    task['failed'] = True; item['failures'] += 1
                item['status'] = 'HALTED' if item['failures'] >= 2 else 'FAILED'
            self._candidate(item)
            return {'status': item['status'], 'execution_enabled': False}
        return self._change('CI', [issue, head, head_version, run_id, fingerprint(checks)], apply)

    def _candidate(self, item):
        if item['status'] == 'WAIT_REVIEW' and item['review_state'] == 'APPROVED' and item['ci_pass']:
            item['status'] = 'MERGE_CANDIDATE'; item['failures'] = 0


class PublicationUncertain(WorkerBlocked):
    pass


class FakePublisher:
    """Independent simulated broker identity 3003; no credentials or GitHub client."""
    def __init__(self, base, tree):
        self.base = base
        self.trees = {base: copy.deepcopy(tree)}
        self.refs, self.prs, self.receipts = {}, {}, {}
        self.lock = threading.RLock()
        self.pushes = self.creations = 0
        self.lose_response = False

    def publish(self, task, validated):
        require(type(validated) is ValidatedPatch, 'broker_validation_required')
        with self.lock:
            old = self.receipts.get(task['id'])
            if old:
                require(old['patch'] == validated.digest, 'publication_id_reused')
                return copy.deepcopy(old)
            patch = validated.patch
            require(self.base == task['base'] and self.refs.get(task['issue'], self.base) == task['parent'], 'remote_base_or_head_changed')
            require(task['issue'] in self.refs or task['parent'] == self.base, 'missing_revision_branch')
            result = copy.deepcopy(self.trees[patch.parent])
            for change in patch.changes: result[change.path] = TreeFile(change.content)
            self.trees[patch.head] = result
            self.refs[task['issue']] = patch.head; self.pushes += 1
            if task['issue'] not in self.prs:
                self.prs[task['issue']] = 10000 + task['issue']; self.creations += 1
            receipt = dict(task=task['id'], issue=task['issue'], patch=validated.digest, parent=patch.parent, head=patch.head, pr=self.prs[task['issue']])
            self.receipts[task['id']] = receipt
            if self.lose_response:
                raise PublicationUncertain('publication_outcome_unknown')
            return copy.deepcopy(receipt)

    def lookup(self, task_id):
        return copy.deepcopy(self.receipts.get(task_id))


class FakePipeline:
    def __init__(self, coordinator, publisher):
        require(type(coordinator) is Coordinator and type(publisher) is FakePublisher, 'exact_fake_pipeline_required')
        self.coordinator, self.publisher = coordinator, publisher
        self.guard = PatchGuard(coordinator.policy)

    def run(self, task, worker):
        require(type(worker) is FakeWorker, 'fake_worker_required')
        persisted = self.coordinator.store.read()['tasks'].get(task['id'])
        require(persisted and persisted['status'] == 'RUNNING' and not persisted['dispatched'], 'task_not_dispatchable')
        try:
            self.coordinator.sync(task['issue'])
            request = self.coordinator.dispatch(task['id'], task['owner'])
            require(self.publisher.base == request.approved_base
                    and self.publisher.refs.get(task['issue'], self.publisher.base) == request.checkout, 'checkout_changed')
            patch = worker.run(request)
            validated = self.guard.validate(request, patch, copy.deepcopy(self.publisher.trees[request.checkout]))
            self.coordinator.stage(task['id'], task['owner'], validated)
            return validated
        except Exception:
            current = self.coordinator.store.read()['tasks'].get(task['id'])
            self.coordinator.fail(task['id'], unknown=bool(current and int(self.coordinator.clock().timestamp()) >= current['expires']))
            raise WorkerBlocked('worker_or_patch_rejected') from None

    def publish(self, validated, owner):
        require(type(validated) is ValidatedPatch, 'validated_patch_required')
        task_id = validated.patch.task
        existing = self.coordinator.store.read()['tasks'].get(task_id)
        require(existing is not None, 'unknown_task')
        if existing['status'] == 'PUBLISHED':
            require(existing['patch'] == validated.digest and existing['owner'] == owner, 'duplicate_publication_mismatch')
            return {'head': existing['head'], 'pr': existing['receipt']['pr'], 'execution_enabled': False, 'duplicate': True}
        try:
            self.coordinator.sync(validated.patch.issue)
            task = self.coordinator.store.read()['tasks'][task_id]
            approval = self.coordinator.authority.issues[task['issue']]
            request = WorkInput(task_id, task['issue'], task['attempt'], task['spec_version'], task['spec'], task['base'], task['parent'], approval.specification)
            # Publisher revalidates bytes independently; never trust a worker validation flag.
            checked = self.guard.validate(request, validated.patch, copy.deepcopy(self.publisher.trees[task['parent']]))
            require(checked == validated, 'validation_receipt_changed')
            task = self.coordinator.prepare(task_id, owner, checked)
            # Local fake lock serializes cancellation and effect; NOT a distributed transaction guarantee.
            with self.coordinator.authority.lock:
                task = self.coordinator.publish_guard(task_id, owner)
                receipt = self.publisher.publish(task, checked)
                return self.coordinator.published(task_id, owner, receipt)
        except Exception:
            self.coordinator.fail(task_id, unknown=True)
            raise WorkerBlocked('publication_halted_reconcile_required') from None

    def reconcile_receipt(self, task_id):
        # Read only. Never resends a push or creates a missing PR.
        task = self.coordinator.store.read()['tasks'][task_id]
        receipt = self.publisher.lookup(task_id)
        matched = bool(receipt and receipt['task'] == task_id and receipt['patch'] == task['patch']
                       and receipt['head'] == task['head'] and receipt['parent'] == task['parent']
                       and self.publisher.refs.get(task['issue']) == task['head'])
        def apply(state, now):
            state['tasks'][task_id]['receipt'] = receipt if matched else None
            return {'receipt_confirmed': matched, 'status': state['issues'][str(task['issue'])]['status'], 'execution_enabled': False}
        return self.coordinator._change('RECEIPT', [task_id, matched], apply)

    def review(self, issue):
        item = self.coordinator.state(issue)
        require(self.publisher.base == item['base'] and self.publisher.refs.get(issue) == item['head'], 'remote_head_changed')
        return self.coordinator.review(issue)

    def ci(self, issue, *, success=True, run_id=1):
        item = self.coordinator.state(issue)
        require(self.publisher.base == item['base'] and self.publisher.refs.get(issue) == item['head'], 'remote_head_changed')
        checks = [dict(name=n, head=item['head'], status='completed', conclusion='success' if success else 'failure',
                       app_id=15368, workflow=self.coordinator.policy['trusted_ci_workflow']) for n in self.coordinator.policy['required_checks']]
        return self.coordinator.ci(issue, head=item['head'], head_version=item['head_version'], run_id=run_id, checks=checks)
