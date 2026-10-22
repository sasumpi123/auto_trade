"""P5 offline preparation and P0-P4 integration. No live model or writes."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from automation.local_checkpoint import LocalCheckpoint
from automation.local_controller import LocalController, single_writer
from automation.local_preflight import diagnose
from automation.local_github_diagnostics import inspect
from automation.launchd_plan import plan
from automation.subscription_contract import SubscriptionContract
from automation.worker_contracts import Feedback, WorkerBlocked
from automation.worker_store import SQLiteTaskStore
from tests import test_review_merge_gate as p4

ROOT = Path(__file__).resolve().parents[1]


class IntegratedPipeline(p4.MergeFixture):
    def test_normal_p0_through_p4_candidate_only(self):
        value, world, ref = self.candidate()
        result = self.broker(value, world).prepare()
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(world.effects, 0)
        self.assertEqual(self.coordinator.state(42)['status'], 'MERGE_CANDIDATE')

    def test_rework_invalidates_old_head(self):
        value = self.published(); old = self.coordinator.state(42)
        self.authority.review(Feedback(42, old['pr'], old['head'], old['head_version'], 20, 2002, 'CHANGES_REQUESTED', 'Improve note'))
        self.pipeline.review(42)
        self.published(b'second version\n')
        current = self.coordinator.state(42)
        self.assertNotEqual(old['head'], current['head'])
        with self.assertRaises(WorkerBlocked): self.pipeline.review(42)
        self.assertFalse(current['ci_pass'])

    def test_unavailable_plus_or_purchased_credit_risk(self):
        value, world, ref = self.candidate()
        for quota in (replace(p4.QUOTA, included_usage_available=False), replace(p4.QUOTA, purchased_credits_disabled=False), replace(p4.QUOTA, zero_cost_proven=False), replace(p4.QUOTA, authentication_valid=False)):
            with self.subTest(quota=quota):
                with self.assertRaises(WorkerBlocked): self.broker(value, world, quota=quota).prepare()
        self.assertEqual(world.effects, 0)

    def test_unsupported_independent_review_waits(self):
        self.published()
        with self.assertRaises(WorkerBlocked): self.pipeline.review(42)
        self.assertEqual(self.coordinator.state(42)['status'], 'WAIT_REVIEW')

    def test_expired_github_auth_and_protection_disabled(self):
        for failure in (401, 403):
            value, world, ref = self.candidate() if failure == 401 else (value, world, ref)
            world.http_failure = failure
            with self.assertRaises(p4.VerificationPending): world.reader(self.fake_policy).inspect(ref)
        world.http_failure = None
        world.rest_data['/branches/master']['protected'] = False
        with self.assertRaises(p4.VerificationPending): world.reader(self.fake_policy).inspect(ref)
        self.assertEqual(world.effects, 0)

    def test_issue_revocation_and_closed_pr(self):
        value, world, ref = self.candidate()
        world.rest_data[f'/pulls/{ref.pr}']['state'] = 'closed'
        with self.assertRaises(p4.VerificationPending): world.reader(self.fake_policy).inspect(ref)
        self.authority.cancel(42)
        with self.assertRaises(WorkerBlocked): self.coordinator.sync(42)
        self.assertEqual(self.coordinator.state(42)['status'], 'HALTED')

    def test_sleep_expired_lease_not_retried(self):
        self.coordinator.claim(42, self.owner)
        self.now += timedelta(hours=8)
        self.assertEqual(self.coordinator.reconcile()['halted'], 1)
        with self.assertRaises(WorkerBlocked): self.coordinator.claim(42, self.owner)
        self.assertEqual(self.coordinator.state(42)['attempts'], 1)

    def test_duplicate_claim_has_one_reservation(self):
        event = str(uuid4())
        a = self.coordinator.claim(42, self.owner, event=event)
        b = self.coordinator.claim(42, self.owner, event=event)
        self.assertEqual(a, b)
        self.assertEqual(self.coordinator.state(42)['attempts'], 1)

    def test_publish_response_loss_no_resend(self):
        self.publisher.lose_response = True
        with self.assertRaises(WorkerBlocked): self.published()
        item = self.coordinator.state(42)
        self.assertEqual(item['status'], 'HALTED')
        self.pipeline.reconcile_receipt(item['current_task'])
        self.assertEqual(self.publisher.pushes, 1)
        self.assertEqual(self.coordinator.state(42)['status'], 'HALTED')


class DurablePreparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.database = self.directory / 'tasks.sqlite'
        self.checkpoint = self.directory / 'checkpoint.json'
        self.policy = json.loads((ROOT / '.github/automation/policy.json').read_text())
        self.store = SQLiteTaskStore.create(self.database, self.policy, witness=LocalCheckpoint(self.checkpoint))

    def test_fresh_witness_after_restart(self):
        reopened = SQLiteTaskStore(self.database, self.policy, witness=LocalCheckpoint(self.checkpoint))
        self.assertEqual(reopened.read(), self.store.read())

    def test_missing_checkpoint_blocks_not_reinitializes(self):
        self.checkpoint.unlink()
        with self.assertRaises(WorkerBlocked): SQLiteTaskStore(self.database, self.policy, witness=LocalCheckpoint(self.checkpoint))
        self.assertFalse(self.checkpoint.exists())

    def test_db_rollback_blocks(self):
        before = self.database.read_bytes()
        self.store.change(str(uuid4()), 'RECONCILE', [], lambda state: {'ok': True})
        self.database.write_bytes(before)
        with self.assertRaises(WorkerBlocked): self.store.read()

    def test_checkpoint_rollback_blocks(self):
        before = self.checkpoint.read_bytes()
        self.store.change(str(uuid4()), 'RECONCILE', [], lambda state: {'ok': True})
        self.checkpoint.write_bytes(before)
        with self.assertRaises(WorkerBlocked): self.store.read()

    def test_corrupt_database_blocks(self):
        self.database.write_bytes(b'corrupt')
        with self.assertRaises(WorkerBlocked): self.store.read()

    def test_corrupt_checkpoint_blocks(self):
        self.checkpoint.write_text('invalid')
        with self.assertRaises(WorkerBlocked): self.store.read()

    def test_interrupted_checkpoint_does_not_repair(self):
        with patch.object(self.store.witness, 'advance', side_effect=OSError('crash')):
            with self.assertRaises(OSError): self.store.change(str(uuid4()), 'RECONCILE', [], lambda state: {})
        with self.assertRaises(WorkerBlocked): self.store.read()

    @unittest.skipUnless(os.name == 'posix', 'macOS/POSIX lock')
    def test_single_writer_and_controller_are_inert(self):
        with single_writer(self.directory):
            with self.assertRaises(WorkerBlocked):
                with single_writer(self.directory): pass
        result = LocalController(self.database, self.checkpoint, self.policy).inspect()
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(result['status'], 'PENDING')

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions')
    def test_world_readable_checkpoint_rejected(self):
        self.checkpoint.chmod(0o644)
        with self.assertRaises(WorkerBlocked): LocalCheckpoint(self.checkpoint)

    @unittest.skipUnless(os.name == 'posix', 'macOS/POSIX controller')
    def test_restart_with_active_task_does_not_dispatch_or_mutate(self):
        from automation.fake_pipeline import Coordinator, FakeAuthority
        from datetime import datetime, timezone
        authority = FakeAuthority()
        authority.approve(42, 'Note', 'a' * 40)
        coordinator = Coordinator(self.store, authority, self.policy,
                                  clock=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc))
        coordinator.claim(42, str(uuid4()))
        before = self.store.read()
        result = LocalController(self.database, self.checkpoint, self.policy).inspect()
        self.assertEqual(result['status'], 'HALTED')
        self.assertEqual(self.store.read(), before)

    def test_missing_database_not_created(self):
        self.database.unlink()
        with self.assertRaises(WorkerBlocked):
            SQLiteTaskStore(self.database, self.policy, witness=LocalCheckpoint(self.checkpoint))
        self.assertFalse(self.database.exists())

    def test_leftover_checkpoint_write_blocks_without_overwrite(self):
        temporary = self.checkpoint.with_name(self.checkpoint.name + '.next')
        temporary.write_text('interrupted')
        with self.assertRaises(FileExistsError):
            self.store.change(str(uuid4()), 'RECONCILE', [], lambda state: {})
        self.assertEqual(temporary.read_text(), 'interrupted')
        with self.assertRaises(WorkerBlocked): self.store.read()

    def test_joint_rollback_limitation_never_grants_live_execution(self):
        before = self.database.read_bytes(), self.checkpoint.read_bytes()
        self.store.change(str(uuid4()), 'RECONCILE', [], lambda state: {})
        self.database.write_bytes(before[0]); self.checkpoint.write_bytes(before[1])
        self.assertEqual(self.store.read()['revision'], 0)  # Explicit threat-model limit.
        self.assertFalse(plan()['enabled'])


class DiagnosticPreparation(unittest.TestCase):
    def test_preflight_only_allowlisted_commands_and_no_output_leak(self):
        calls = []
        def run(args, cwd=None):
            calls.append(args)
            return 0, 'secret-value 1.2.3'
        with patch('automation.local_preflight.probe', side_effect=run), patch('automation.local_preflight.shutil.which', return_value='/tool'):
            report = diagnose()
        self.assertNotIn('secret-value', json.dumps(report))
        self.assertEqual(report['status'], 'HALTED')
        self.assertFalse(report['execution_enabled'])
        self.assertIn(['codex', 'login', 'status'], calls)
        self.assertTrue(all(a[0] in ('git', 'gh', 'codex') for a in calls))
        self.assertNotIn(['codex', 'exec'], calls)

    def test_api_errors_are_not_missing_issue_contract(self):
        with patch('automation.local_github_diagnostics.subprocess.run', return_value=type('R', (), {'returncode': 1})()):
            result = inspect()
        self.assertTrue(all(r['reason'] == 'API_ACCESS_UNVERIFIED' for r in result['checks'][:6]))

    def test_api_get_only(self):
        with patch('automation.local_github_diagnostics.subprocess.run', return_value=type('R', (), {'returncode': 0, 'stdout': '{}'})()) as run:
            report = inspect()
        self.assertTrue(all(call.args[0][3] == 'GET' for call in run.call_args_list))
        self.assertFalse(report['merge_enabled'])

    def test_launchd_inert_no_timer_or_run_at_load(self):
        value = plistlib.loads((ROOT / 'ops/local/com.auto-trade.dry-run.plist.example').read_bytes())
        self.assertTrue(value['Disabled'])
        self.assertFalse(value['RunAtLoad']); self.assertFalse(value['KeepAlive'])
        self.assertNotIn('StartInterval', value); self.assertNotIn('StartCalendarInterval', value)
        self.assertIn('automation.local_preflight', value['ProgramArguments'])

    def test_api_fallback_names_block_even_with_fake_evidence(self):
        policy = json.loads((ROOT / '.github/automation/zero-cost.json').read_text())
        for name in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            with self.subTest(name=name), self.assertRaises(WorkerBlocked): SubscriptionContract(policy).preflight(p4.QUOTA, [name])
