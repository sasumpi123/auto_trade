from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from automation.__main__ import demo
from automation.events import Check, Event, LedgerError, STATES
from automation.fakes import FakeCodex, FakeGitHub, FakeReviewer
from automation.orchestrator import DryRunOrchestrator
from automation.policy import spec_hash
from automation.store import SQLiteLedger

ROOT = Path(__file__).resolve().parents[1]


def policy():
    return json.loads((ROOT / '.github/automation/policy.json').read_text(encoding='utf-8'))


class Clock:
    def __init__(self):
        self.value = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


def process_claim(path, config, fields, output):
    """Spawn-safe child: its own SQLite connection; no fake or real service invocation."""
    try:
        ledger = SQLiteLedger(path, config)
        result = ledger.submit(Event(**fields))
        output.put(result.outcome)
    except LedgerError:
        output.put('BLOCKED')


def process_crash(path, config, fields):
    ledger = SQLiteLedger(path, config)
    original = ledger._move
    def crash(*args, **kwargs):
        original(*args, **kwargs)
        os._exit(17)  # Only this disposable child process; transaction never commits.
    ledger._move = crash
    ledger.submit(Event(**fields))


class P1LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'journal.sqlite3'
        self.clock = Clock()
        self.ledger = SQLiteLedger.create(self.path, policy(), clock=self.clock)
        self.github, self.codex, self.reviewer = FakeGitHub(), FakeCodex(), FakeReviewer()
        self.github.add_issue(1)
        self.runner = DryRunOrchestrator(self.ledger, self.github, self.codex, self.reviewer)

    def event(self, kind, **fields):
        item = self.ledger.get(1)
        values = {'spec_sha256': spec_hash(self.github.issues[1]['body'])}
        if item:
            values |= {'spec_sha256': item['spec_sha256'], 'spec_version': item['spec_version'],
                       'head_sha': item['head_sha'], 'head_version': item['head_version']}
            if kind == 'RENEW':
                values['expected_version'] = item['version']
        return Event(str(uuid4()), 1, kind, **(values | fields))

    def prepare(self):
        self.runner.approve(1)
        self.runner.queue(1)

    def built(self):
        self.prepare()
        self.runner.claim(1)
        self.runner.build(1)
        self.runner.request_review(1)
        self.runner.gate(1)

    def eligible(self):
        self.built()
        self.runner.review(1)
        self.runner.ci(1)

    def test_normal_e2e_stops_at_eligible_with_all_network_blocked(self):
        with patch('socket.socket.connect', side_effect=AssertionError('External service forbidden')):
            self.eligible()
        row = self.ledger.get(1)
        self.assertEqual(row['state'], 'MERGE_ELIGIBLE')
        self.assertEqual((row['attempts'], row['reserved_cents']), (1, 500))
        self.assertEqual(len(self.codex.calls), 1)
        self.assertEqual(row['pr_number'], 10001)
        self.assertTrue(self.ledger.integrity())

    def test_revision_e2e_new_head_requires_both_ci_and_review(self):
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            self.built()
            old_head = self.ledger.get(1)['head_sha']
            self.runner.review(1, changes=True)
            self.runner.claim(1)
            self.runner.build(1)
            self.assertNotEqual(self.ledger.get(1)['head_sha'], old_head)
            self.runner.gate(1)
            self.runner.ci(1)
            self.assertEqual(self.ledger.get(1)['state'], 'REVIEW_PENDING')
            self.runner.review(1)
        self.assertEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')
        self.assertEqual(self.ledger.get(1)['attempts'], 2)
        self.assertIn('REVISION_RUNNING', [row['new_state'] for row in self.ledger.history(1)])

    def test_simulated_merge_is_local_and_next_approved_issue_can_progress(self):
        self.github.add_issue(2, approved=False)
        self.github.add_issue(3)
        self.eligible()
        self.assertIsNone(self.runner.approve_next())
        before = list(self.github.calls)
        self.assertEqual(self.runner.simulate_merge(1).state, 'MERGED')
        self.assertEqual(self.github.calls, before)
        self.assertEqual(self.runner.approve_next().issue, 3)

    def test_duplicate_delivery_and_different_id_same_semantic_event(self):
        event = self.event('APPROVE', actor_id=45135977)
        self.assertEqual(self.ledger.submit(event).outcome, 'APPLIED')
        version = self.ledger.get(1)['version']
        self.assertEqual(self.ledger.submit(event).outcome, 'DUPLICATE')
        self.assertEqual(self.ledger.submit(replace(event, event_id=str(uuid4()))).outcome, 'DUPLICATE')
        self.assertEqual(self.ledger.get(1)['version'], version)
        self.assertEqual(len(self.ledger.audit()), 1)

    def test_reused_delivery_id_with_changed_metadata_blocks(self):
        event = self.event('APPROVE', actor_id=45135977)
        self.ledger.submit(event)
        with self.assertRaises(LedgerError):
            self.ledger.submit(replace(event, spec_sha256='b' * 64))
        self.assertEqual(self.ledger.get(1)['version'], 1)

    def test_unapproved_issue_is_rejected_without_reservation(self):
        self.github.add_issue(1, approved=False)
        self.assertEqual(self.runner.approve(1).outcome, 'REJECTED')
        with self.assertRaises(LedgerError):
            self.runner.queue(1)
        self.assertIsNone(self.ledger.get(1))
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 0)

    def test_out_of_order_event_journaled_without_advancing_state(self):
        self.prepare()
        before = self.ledger.get(1)
        result = self.ledger.submit(self.event('REVIEW', actor_id=2002, review_id=1, review_status='APPROVED'))
        self.assertEqual(result.outcome, 'REJECTED')
        self.assertEqual(self.ledger.get(1), before)
        self.assertEqual(self.ledger.audit()[-1]['outcome'], 'REJECTED')

    def test_approval_edit_invalidates_running_task_before_fake_work(self):
        self.prepare()
        self.runner.claim(1)
        self.github.issues[1]['body'] += ' changed'
        with self.assertRaises(LedgerError):
            self.runner.build(1)
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.codex.calls, [])
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 500)

    def test_new_approval_version_retains_spend_and_halts_for_reconciliation(self):
        self.built()
        old = self.ledger.get(1)
        result = self.ledger.submit(self.event('APPROVE', actor_id=45135977,
                                             spec_sha256='c' * 64, spec_version=2))
        self.assertEqual(result.state, 'HALTED')
        current = self.ledger.get(1)
        self.assertEqual(current['spec_version'], 2)
        self.assertEqual(current['reserved_cents'], old['reserved_cents'])
        self.assertEqual(current['attempts'], old['attempts'])
        self.assertEqual(self.ledger.submit(self.event('QUEUE')).outcome, 'REJECTED')

    def test_reapproved_edited_body_records_next_version_but_never_auto_resumes(self):
        self.built()
        self.github.add_issue(1, body='new fake approved specification')
        self.runner.approve(1, spec_version=2)
        row = self.ledger.get(1)
        self.assertEqual(row['state'], 'HALTED')
        self.assertEqual(row['spec_version'], 2)
        self.assertEqual(row['spec_sha256'], spec_hash('new fake approved specification'))
        self.assertEqual(row['reserved_cents'], 500)

    def test_approval_change_after_review_blocks_ci_and_eligibility(self):
        self.built()
        self.runner.review(1)
        before = list(self.github.calls)
        self.github.issues[1]['labels'] = []
        with self.assertRaises(LedgerError):
            self.runner.ci(1)
        self.assertEqual(self.github.calls, before)
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')

    def test_stale_specification_event_never_claims_work(self):
        self.prepare()
        result = self.ledger.submit(self.event('CLAIM', worker=str(uuid4()), cents=500,
                                             expected_version=2, spec_sha256='d' * 64))
        self.assertEqual(result.outcome, 'STALE')
        self.assertEqual(self.ledger.get(1)['attempts'], 0)

    def test_two_ci_failures_across_revisions_halt_without_build_reset(self):
        self.built()
        self.runner.ci(1, success=False)
        self.assertEqual(self.ledger.get(1)['failures'], 1)
        self.runner.retry(1)
        self.runner.claim(1)
        self.runner.build(1)
        self.assertEqual(self.ledger.get(1)['failures'], 1)
        self.runner.ci(1, success=False)
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.ledger.get(1)['failures'], 2)

    def test_failed_ci_cannot_be_bypassed_by_late_pass_or_review(self):
        self.built()
        self.runner.ci(1, success=False)
        self.runner.review(1)
        self.runner.ci(1, success=True, run_id=2)
        self.assertEqual(self.ledger.get(1)['state'], 'FAILED')
        self.assertEqual(self.runner.simulate_merge(1).outcome, 'REJECTED')

    def test_retry_intent_alone_cannot_reuse_old_head_ci_to_skip_revision(self):
        self.built()
        self.runner.review(1)
        self.runner.ci(1, success=False)
        self.runner.retry(1)
        self.runner.ci(1, success=True, run_id=2)
        self.assertEqual(self.ledger.get(1)['state'], 'CHANGES_REQUESTED')
        self.assertEqual(self.runner.simulate_merge(1).outcome, 'REJECTED')

    def test_total_three_attempt_limit_and_no_automatic_refund(self):
        self.built()
        for _ in range(2):
            self.runner.review(1, changes=True)
            self.runner.claim(1)
            self.runner.build(1)
        self.runner.review(1, changes=True)
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.ledger.get(1)['attempts'], 3)
        self.assertEqual(self.ledger.get(1)['reserved_cents'], 1500)
        self.assertEqual(self.runner.claim(1).outcome, 'REJECTED')

    def test_third_attempt_success_is_still_merge_eligible(self):
        self.built()
        for _ in range(2):
            self.runner.review(1, changes=True)
            self.runner.claim(1)
            self.runner.build(1)
        self.runner.gate(1)
        self.runner.review(1)
        self.runner.ci(1)
        self.assertEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')

    def test_per_attempt_budget_limit_blocks_before_work(self):
        self.prepare()
        self.runner.claim(1, cents=501)
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.ledger.get(1)['reserved_cents'], 0)
        self.assertEqual(self.codex.calls, [])

    def test_daily_global_budget_serializes_across_issues_and_rolls_utc(self):
        for issue in range(1, 12):
            self.github.add_issue(issue)
            self.runner.approve(issue)
            self.runner.queue(issue)
            result = self.runner.claim(issue)
            self.assertEqual(result.state, 'IMPLEMENTATION_RUNNING' if issue <= 10 else 'HALTED')
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 5000)
        self.clock.advance(86400)
        self.github.add_issue(12)
        self.runner.approve(12)
        self.runner.queue(12)
        self.assertEqual(self.runner.claim(12).state, 'IMPLEMENTATION_RUNNING')
        self.assertEqual(self.ledger.day_reserved('2026-01-02'), 500)

    def test_non_utc_input_is_normalized_for_day_reservation(self):
        self.clock.value = datetime(2026, 1, 2, 1, tzinfo=timezone(timedelta(hours=9)))
        self.prepare()
        self.runner.claim(1)
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 500)

    def test_clock_regression_blocks_instead_of_reopening_old_daily_budget(self):
        self.prepare()
        self.clock.advance(-1)
        with self.assertRaises(LedgerError):
            self.runner.claim(1)

    def test_stale_review_after_new_commit_does_not_promote(self):
        self.built()
        old = self.event('REVIEW', actor_id=2002, review_id=1, review_status='APPROVED')
        self.ledger.submit(self.event('HEAD_UPDATED', head_sha='b' * 40, head_version=2))
        self.assertEqual(self.ledger.submit(old).outcome, 'STALE')
        current = self.ledger.get(1)
        self.assertEqual(current['review_status'], 'NONE')
        self.assertEqual(current['gate']['changed_paths'], [])

    def test_old_ci_and_out_of_order_head_updates_do_not_promote(self):
        self.built()
        old = self.event('CI', ci_status='PASSED', ci_run_id=1,
                         checks=self.github.checks(self.ledger.get(1)['head_sha'], policy()))
        self.ledger.submit(self.event('HEAD_UPDATED', head_sha='b' * 40, head_version=2))
        self.assertEqual(self.ledger.submit(old).outcome, 'STALE')
        stale = self.ledger.submit(self.event('HEAD_UPDATED', head_sha='a' * 40, head_version=1))
        self.assertEqual(stale.outcome, 'STALE')

    def test_new_push_revokes_eligibility_until_fresh_ci_review_and_gate(self):
        self.eligible()
        self.ledger.submit(self.event('HEAD_UPDATED', head_sha='b' * 40, head_version=2))
        self.assertEqual(self.ledger.get(1)['state'], 'CI_PENDING')
        self.runner.review(1)
        self.runner.ci(1)
        self.assertNotEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')
        self.runner.gate(1)
        self.assertEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')

    def test_spoofed_or_skipped_ci_checks_and_self_review_rejected(self):
        self.built()
        row = self.ledger.get(1)
        for change in ({'conclusion': 'skipped'}, {'app_slug': 'untrusted'}, {'head_sha': 'b' * 40},
                       {'workflow_path': 'fake.yml'}):
            checks = list(self.github.checks(row['head_sha'], policy()))
            checks[0] = replace(checks[0], **change)
            self.assertEqual(self.ledger.submit(self.event('CI', ci_run_id=1, ci_status='PASSED', checks=checks)).outcome, 'REJECTED')
        self.assertEqual(self.ledger.submit(self.event('REVIEW', actor_id=1001, review_id=1, review_status='APPROVED')).outcome, 'REJECTED')

    def test_gate_failures_and_protected_paths_prevent_eligibility(self):
        self.built()
        self.runner.gate(1, branch_protection_verified=False)
        self.runner.review(1)
        self.runner.ci(1)
        self.assertNotEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')
        self.assertEqual(self.runner.gate(1, changed_paths=('config.py',)).outcome, 'REJECTED')
        self.runner.gate(1, unresolved_threads=True)
        self.assertNotEqual(self.ledger.get(1)['state'], 'MERGE_ELIGIBLE')

    def test_expired_lease_is_ambiguous_halted_and_late_result_fenced(self):
        self.prepare()
        self.runner.claim(1, lease_seconds=10)
        old = self.event('BUILT', worker=self.runner.worker, attempt=1, actor_id=1001,
                         pr_number=10001, head_sha='b' * 40, head_version=1)
        self.clock.advance(10)
        self.assertEqual(self.ledger.reconcile(), [1])
        self.assertEqual(self.ledger.reconcile(), [])
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.ledger.submit(old).outcome, 'REJECTED')
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 500)

    def test_restart_retains_review_and_ci_without_reserving_again(self):
        self.built()
        self.runner.review(1)
        reopened = SQLiteLedger(self.path, policy(), clock=self.clock)
        runner = DryRunOrchestrator(reopened, self.github, FakeCodex(), FakeReviewer())
        runner.ci(1)
        self.assertEqual(reopened.get(1)['state'], 'MERGE_ELIGIBLE')
        self.assertEqual(reopened.get(1)['attempts'], 1)
        self.assertEqual(runner.codex.calls, [])

    def test_restart_cannot_steal_unexpired_lease(self):
        self.prepare()
        self.runner.claim(1)
        other = DryRunOrchestrator(SQLiteLedger(self.path, policy(), clock=self.clock), self.github, FakeCodex(), FakeReviewer())
        self.assertEqual(other.claim(1).outcome, 'REJECTED')
        with self.assertRaises(LedgerError):
            other.build(1)
        self.assertEqual(other.codex.calls, [])

    def test_restart_reconciles_expiry_without_calling_any_adapter(self):
        self.prepare()
        self.runner.claim(1, lease_seconds=1)
        self.clock.advance(1)
        other = DryRunOrchestrator(SQLiteLedger(self.path, policy(), clock=self.clock), self.github, FakeCodex(), FakeReviewer())
        self.assertEqual(other.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(other.codex.calls, [])

    def test_dispatch_is_persistent_at_most_once_even_for_same_owner(self):
        self.prepare()
        self.runner.claim(1)
        event = self.event('DISPATCH', worker=self.runner.worker, attempt=1)
        self.assertEqual(self.ledger.submit(event).outcome, 'APPLIED')
        self.assertEqual(self.ledger.submit(replace(event, event_id=str(uuid4()))).outcome, 'DUPLICATE')
        with self.assertRaises(LedgerError):
            self.runner.build(1)
        self.assertEqual(self.codex.calls, [])

    def test_exception_after_dispatch_quarantines_without_leaking_exception_body(self):
        self.prepare()
        self.runner.claim(1)
        with patch.object(FakeCodex, 'work', side_effect=RuntimeError('fake-private-exception-content')):
            with self.assertRaises(LedgerError) as caught:
                self.runner.build(1)
        self.assertNotIn('fake-private', str(caught.exception))
        self.assertEqual(self.ledger.get(1)['state'], 'HALTED')
        self.assertEqual(self.ledger.get(1)['reserved_cents'], 500)

    def test_heartbeat_does_not_change_attempt_or_cost_and_has_runtime_ceiling(self):
        self.prepare()
        self.runner.claim(1)
        for _ in range(4):
            self.clock.advance(250)
            self.ledger.submit(self.event('RENEW', worker=self.runner.worker, attempt=1))
        self.assertEqual(self.ledger.get(1)['attempts'], 1)
        self.assertEqual(self.ledger.get(1)['reserved_cents'], 500)
        self.clock.advance(200)
        result = self.ledger.submit(self.event('RENEW', worker=self.runner.worker, attempt=1))
        self.assertEqual(result.state, 'HALTED')

    def test_concurrent_threads_only_one_claim_and_one_reservation(self):
        self.prepare()
        version = self.ledger.get(1)['version']
        def claim(_):
            ledger = SQLiteLedger(self.path, policy(), clock=self.clock)
            return ledger.submit(self.event('CLAIM', worker=str(uuid4()), expected_version=version, cents=500)).outcome
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(claim, range(8)))
        self.assertEqual(outcomes.count('APPLIED'), 1)
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 500)

    def test_concurrent_processes_only_one_claim_and_one_reservation(self):
        # Use real UTC here so child processes and persisted clock share a non-regressing source.
        self.prepare()
        context = multiprocessing.get_context('spawn')
        output = context.Queue()
        fields = [self.event('CLAIM', worker=str(uuid4()), expected_version=2, cents=500).__dict__ for _ in range(3)]
        processes = [context.Process(target=process_claim, args=(str(self.path), policy(), f, output)) for f in fields]
        for process in processes:
            process.start()
        try:
            for process in processes:
                process.join(20)
                self.assertFalse(process.is_alive())
                self.assertEqual(process.exitcode, 0)
            outcomes = [output.get(timeout=3) for _ in processes]
            self.assertEqual(outcomes.count('APPLIED'), 1)
            # Children advanced UTC watermark to real now; reopen with the default clock for inspection.
            self.assertEqual(SQLiteLedger(self.path, policy()).get(1)['reserved_cents'], 500)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            output.close()
            output.join_thread()

    def test_daily_limit_atomic_across_different_issues(self):
        for issue in range(1, 12):
            self.github.add_issue(issue)
            self.runner.approve(issue)
            self.runner.queue(issue)
            if issue <= 9:
                self.runner.claim(issue)
        def claim(issue):
            ledger = SQLiteLedger(self.path, policy(), clock=self.clock)
            return ledger.submit(Event(str(uuid4()), issue, 'CLAIM', spec_hash(self.github.issues[issue]['body']),
                                       expected_version=2, worker=str(uuid4()), cents=500)).state
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(claim, [10, 11]))
        self.assertEqual(states.count('IMPLEMENTATION_RUNNING'), 1)
        self.assertEqual(states.count('HALTED'), 1)
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 5000)

    def test_real_child_exit_before_commit_recovers_entire_transaction(self):
        self.prepare()
        fields = self.event('CLAIM', worker=str(uuid4()), expected_version=2, cents=500).__dict__
        process = multiprocessing.get_context('spawn').Process(target=process_crash,
                        args=(str(self.path), policy(), fields))
        process.start()
        try:
            process.join(20)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 17)
            reopened = SQLiteLedger(self.path, policy(), clock=self.clock)
            self.assertEqual(reopened.get(1)['state'], 'IMPLEMENTATION_PENDING')
            self.assertEqual(reopened.get(1)['attempts'], 0)
            self.assertEqual(reopened.day_reserved('2026-01-01'), 0)
            self.assertEqual(len(reopened.audit()), 2)
            self.assertTrue(reopened.integrity())
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)

    def test_transaction_failure_rolls_back_event_attempt_budget_and_projection(self):
        self.prepare()
        before = self.ledger.get(1)
        original = self.ledger._move
        def fail_after_updates(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('simulated process failure before commit')
        with patch.object(self.ledger, '_move', side_effect=fail_after_updates):
            with self.assertRaises(RuntimeError):
                self.runner.claim(1)
        self.assertEqual(self.ledger.get(1), before)
        self.assertEqual(self.ledger.day_reserved('2026-01-01'), 0)
        self.assertEqual(len(self.ledger.audit()), 2)

    def test_missing_corrupt_unknown_schema_and_changed_policy_block(self):
        missing = Path(self.temp.name) / 'missing.sqlite3'
        with self.assertRaises(LedgerError):
            SQLiteLedger(missing, policy())
        self.assertFalse(missing.exists())
        corrupt = Path(self.temp.name) / 'corrupt.sqlite3'
        corrupt.write_bytes(b'not a database')
        with self.assertRaises(LedgerError):
            SQLiteLedger(corrupt, policy())
        with self.assertRaises(LedgerError):
            SQLiteLedger(self.path, policy() | {'max_attempts_per_issue': 4})
        with sqlite3.connect(self.path) as connection:
            connection.execute('PRAGMA user_version=999')
        connection.close()
        with self.assertRaises(LedgerError):
            SQLiteLedger(self.path, policy())

    def test_projection_and_daily_budget_corruption_are_detected(self):
        self.built()
        with sqlite3.connect(self.path) as connection:
            item = json.loads(connection.execute('SELECT data FROM issues WHERE issue=1').fetchone()[0])
            item['ci_status'] = 'PASSED'
            connection.execute('UPDATE issues SET data=? WHERE issue=1', (json.dumps(item),))
        connection.close()
        with self.assertRaises(LedgerError):
            self.ledger.get(1)

    def test_daily_budget_counter_corruption_is_detected(self):
        self.built()
        with sqlite3.connect(self.path) as connection:
            connection.execute('UPDATE budgets SET cents=0')
        connection.close()
        with self.assertRaises(LedgerError):
            self.ledger.get(1)

    def test_busy_database_never_calls_fake_codex(self):
        self.prepare()
        connection = sqlite3.connect(self.path, isolation_level=None)
        connection.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(LedgerError):
                self.runner.claim(1)
        finally:
            connection.rollback()
            connection.close()
        self.assertEqual(self.codex.calls, [])
        self.assertEqual(self.ledger.get(1)['attempts'], 0)

    def test_backup_restore_validates_and_quarantines_without_resetting_budget(self):
        self.built()
        backup = Path(self.temp.name) / 'backup.sqlite3'
        destination = Path(self.temp.name) / 'restored.sqlite3'
        self.ledger.backup(backup)
        restored = SQLiteLedger.restore(backup, destination, policy(), clock=self.clock)
        self.assertEqual(restored.get(1)['reserved_cents'], 500)
        self.assertEqual(restored.history(1), self.ledger.history(1))
        self.assertTrue(restored.integrity())
        with self.assertRaises(LedgerError):
            restored.submit(self.event('REQUEST_REVIEW'))
        with self.assertRaises(LedgerError):
            self.ledger.backup(backup)
        with self.assertRaises(LedgerError):
            SQLiteLedger.restore(backup, destination, policy())

    def test_tampered_backup_is_rejected_without_recreating_destination(self):
        backup = Path(self.temp.name) / 'backup.sqlite3'
        destination = Path(self.temp.name) / 'restored.sqlite3'
        self.ledger.backup(backup)
        with backup.open('ab') as handle:
            handle.write(b'changed')
        with self.assertRaises(LedgerError):
            SQLiteLedger.restore(backup, destination, policy())
        self.assertFalse(destination.exists())

    def test_create_never_overwrites_existing_ledger(self):
        before = self.path.read_bytes()
        with self.assertRaises(LedgerError):
            SQLiteLedger.create(self.path, policy())
        self.assertEqual(self.path.read_bytes(), before)

    def test_disabled_policy_and_exact_fake_types_are_mandatory(self):
        with self.assertRaises(LedgerError):
            SQLiteLedger.create(Path(self.temp.name) / 'forbidden.sqlite3', policy() | {'enabled': True})
        class PretendGitHub(FakeGitHub):
            pass
        with self.assertRaises(LedgerError):
            DryRunOrchestrator(self.ledger, PretendGitHub(), self.codex, self.reviewer)

    def test_bodies_not_persisted_and_event_contract_has_no_raw_payload(self):
        body = 'fake-private-event-body-DO-NOT-LOG'
        self.github.add_issue(1, body=body)
        self.prepare()
        with sqlite3.connect(self.path) as connection:
            dump = '\n'.join(connection.iterdump())
        connection.close()
        self.assertNotIn(body, dump)
        self.assertNotIn('approval_comments', dump)
        with self.assertRaises(TypeError):
            Event(str(uuid4()), 1, 'APPROVE', spec_hash(body), body=body)

    def test_cli_normal_revision_failure_and_invalid_args_are_sanitized(self):
        with patch('socket.socket.connect', side_effect=AssertionError('No network')):
            for scenario, state, attempts in [('normal', 'MERGE_ELIGIBLE', 1), ('revision', 'MERGE_ELIGIBLE', 2), ('failure', 'HALTED', 2)]:
                result = demo(Path(self.temp.name) / f'{scenario}.sqlite3', scenario)
                self.assertEqual(result['issues'][0]['state'], state)
                self.assertEqual(result['issues'][0]['attempts'], attempts)
                self.assertEqual(result['actual_api_calls'], 0)
        result = subprocess.run([sys.executable, '-m', 'automation', '--api-key', 'fake-do-not-echo'],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('fake-do-not-echo', result.stdout + result.stderr)

    def test_all_named_states_are_represented_by_transition_contract(self):
        # Combined traces exercise normal/revision/failure and explicit simulated MERGED.
        self.eligible()
        self.runner.simulate_merge(1)
        reached = {row['new_state'] for row in self.ledger.history(1)}
        for scenario in ('revision', 'failure'):
            result = demo(Path(self.temp.name) / f'trace-{scenario}.sqlite3', scenario)
            reached.update(result['states'])
        self.assertEqual(reached, set(STATES))


if __name__ == '__main__':
    unittest.main()
