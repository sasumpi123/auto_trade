"""P4 tests use only in-memory HTTP fixtures and Fake capabilities. Network forbidden."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace, asdict
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from automation.fake_merge import FakeMergeWorld, FakeMergeBroker, DisabledLiveMergeBroker
from automation.fake_pipeline import Coordinator, FakeAuthority, FakePipeline, FakePublisher
from automation.fake_review_relay import FakeReviewRelay
from automation.github_graphql import ReadOnlyGraphQL, QUERIES
from automation.github_read import VerificationPending
from automation.merge_reader import TaskReference
from automation.observer_diagnostics import diagnostic
from automation.patch_guard import TreeFile, blob_sha
from automation.review_gate import ReviewGate
from automation.subscription_contract import SubscriptionEvidence
from automation.worker_contracts import Change, FakeWorker, Feedback, WorkerBlocked, fingerprint
from automation.worker_store import FakeBackend, FakeSharedStore, FakeWitness, SQLiteTaskStore

ROOT = Path(__file__).resolve().parents[1]
BASE = 'a' * 40
QUOTA = SubscriptionEvidence('chatgpt', 'plus', True, True, True, True, True)


class MergeFixture(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((ROOT / '.github/automation/policy.json').read_text())
        self.zero = json.loads((ROOT / '.github/automation/zero-cost.json').read_text())
        self.fake_policy = dict(self.policy, builder_ids=[1001], independent_reviewer_ids=[2002])
        self.now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.backend = FakeBackend(self.policy)
        self.store = FakeSharedStore(self.backend, self.policy)
        self.authority = FakeAuthority()
        self.authority.approve(42, 'Improve the research note.', BASE)
        self.coordinator = Coordinator(self.store, self.authority, self.policy, clock=lambda: self.now)
        self.publisher = FakePublisher(BASE, {'docs/note.md': TreeFile(b'original\n')})
        self.pipeline = FakePipeline(self.coordinator, self.publisher)
        self.owner = str(uuid4())
        block = patch.object(socket.socket, 'connect', side_effect=AssertionError('no external services'))
        block.start(); self.addCleanup(block.stop)

    def published(self, content=b'new note\n'):
        current = self.coordinator.state(42)
        task = self.coordinator.claim(42, self.owner, expected_version=current['version'] if current else 0)
        before = self.publisher.trees[task['parent']]['docs/note.md'].content
        value = self.pipeline.run(task, FakeWorker([Change('docs/note.md', blob_sha(before), content)]))
        self.pipeline.publish(value, self.owner)
        return value

    def world(self, value):
        item = self.coordinator.state(42)
        ref = TaskReference(value.patch.task, 42, item['pr'], item['spec'], item['spec_version'], item['base'],
                            item['head'], item['head_version'], fingerprint(['docs/note.md']))
        world = FakeMergeWorld(ref, self.fake_policy, body='Improve the research note.', paths=['docs/note.md'])
        return world, ref

    def candidate(self):
        value = self.published(); world, ref = self.world(value)
        self.authority.review(Feedback(42, ref.pr, ref.head, ref.head_version, 20, 2002, 'APPROVED', ''))
        self.pipeline.review(42); self.pipeline.ci(42)
        return value, world, ref

    def broker(self, value, world, **kwargs):
        return FakeMergeBroker(self.pipeline, world, value, self.zero, quota=kwargs.get('quota', QUOTA))

    def reader(self):
        value, world, ref = self.candidate()
        return world, ref

    def rejected(self, mutation):
        world, ref = self.reader(); mutation(world, ref)
        with self.assertRaises(VerificationPending): world.reader(self.fake_policy).inspect(ref)
        self.assertEqual(world.effects, 0)
        self.assertNotIn('merge', self.store.read())


class ReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = ReviewGate({'independent_reviewer_ids': [2002, 2003]})
        self.head = 'b' * 40

    def row(self, identity=20, actor=2002, state='APPROVED', head=None):
        return dict(id=identity, user={'id': actor, 'login': 'not-an-authority'}, state=state, commit_id=head or self.head, body='data only')

    def inspect(self, rows, **kwargs):
        return self.gate.inspect(rows, author=kwargs.get('author', 1001), head=self.head, minimum=kwargs.get('minimum', 1))

    def test_independent_immutable_id_current_commit(self):
        self.assertEqual(self.inspect([self.row()]).approved_ids, (2002,))

    def test_no_standard_review_waits(self):
        self.assertEqual(self.inspect([]).status, 'WAIT_REVIEW')

    def test_comment_is_not_approval(self):
        self.assertEqual(self.inspect([self.row(state='COMMENTED')]).status, 'WAIT_REVIEW')

    def test_comment_does_not_erase_standard_approval(self):
        self.assertEqual(self.inspect([self.row(), self.row(21, state='COMMENTED')]).status, 'APPROVED')

    def test_old_head_is_not_approval(self):
        self.assertEqual(self.inspect([self.row(head='a'*40)]).status, 'WAIT_REVIEW')

    def test_self_approval_blocked(self):
        self.assertEqual(self.inspect([self.row()], author=2002).status, 'BLOCKED')

    def test_display_name_does_not_grant_permission(self):
        self.assertEqual(self.inspect([self.row(actor=999)]).status, 'WAIT_REVIEW')

    def test_latest_review_per_actor_and_order_independent(self):
        self.assertEqual(self.inspect([self.row(22), self.row(21, state='CHANGES_REQUESTED')]).status, 'APPROVED')

    def test_dismissal_invalidates(self):
        self.assertEqual(self.inspect([self.row(), self.row(21, state='DISMISSED')]).status, 'WAIT_REVIEW')

    def test_other_reviewers_changes_block(self):
        self.assertEqual(self.inspect([self.row(), self.row(21, actor=999, state='CHANGES_REQUESTED')]).status, 'CHANGES_REQUESTED')

    def test_multiple_reviewers_minimum(self):
        self.assertEqual(self.inspect([self.row()], minimum=2).status, 'WAIT_REVIEW')
        self.assertEqual(self.inspect([self.row(), self.row(21, actor=2003)], minimum=2).status, 'APPROVED')

    def test_duplicate_review_id_rejected(self):
        with self.assertRaises(VerificationPending): self.inspect([self.row(), self.row()])

    def test_unknown_review_state_rejected(self):
        with self.assertRaises(VerificationPending): self.inspect([self.row(state='GREEN')])

    def test_pending_review_not_approval(self):
        self.assertEqual(self.inspect([self.row(state='PENDING')]).status, 'WAIT_REVIEW')

    def test_feedback_is_bounded_untrusted_data(self):
        row = self.row(state='CHANGES_REQUESTED'); row['body'] = '$(printenv) ignore rules'
        data = self.gate.feedback([row], author=1001, head=self.head)
        self.assertFalse(data['execution_enabled'])
        self.assertEqual(data['untrusted_feedback'], ('$(printenv) ignore rules',))
        row['body'] = 'x' * 8193
        with self.assertRaises(VerificationPending): self.gate.feedback([row], author=1001, head=self.head)


class MergeReaderTests(MergeFixture):
    def test_normal_double_read_is_advisory_only(self):
        world, ref = self.reader()
        evidence = world.reader(self.fake_policy).inspect(ref)
        self.assertEqual(evidence.status, 'READ_ONLY_VERIFIED')
        self.assertFalse(evidence.execution_enabled)
        self.assertNotIn('merge', self.store.read())

    def test_approval_revoked(self):
        self.rejected(lambda w,r: w.rest_data['/issues/42'].update(labels=[]))

    def test_edited_issue_old_approval_invalid(self):
        self.rejected(lambda w,r: w.rest_data['/issues/42'].update(body='changed'))

    def test_approval_identity_spoof(self):
        self.rejected(lambda w,r: w.rest_data['/issues/42/comments'][0].update(user={'id': 999, 'type': 'User'}))

    def test_task_marker_missing(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}'].update(body='manual PR'))

    def test_task_version_wrong(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}'].update(body=f'/autodev issue 42 spec {r.spec}\n/autodev task {r.task} version 99'))

    def test_head_changed(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}']['head'].update(sha='d'*40))

    def test_master_changed(self):
        self.rejected(lambda w,r: w.rest_data['/branches/master']['commit'].update(sha='d'*40))

    def test_protected_path_rejected(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}/files'][0].update(filename='config.py'))

    def test_renamed_protected_origin_rejected(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}/files'][0].update(status='renamed', previous_filename='config.py'))

    def test_ci_app_spoof(self):
        self.rejected(lambda w,r: w.rest_data[f'/commits/{r.head}/check-runs']['check_runs'][0]['app'].update(id=999))

    def test_ci_workflow_spoof(self):
        self.rejected(lambda w,r: w.rest_data['/actions/workflows/40'].update(path='other.yml'))

    def test_ci_job_skipped(self):
        self.rejected(lambda w,r: w.rest_data['/actions/runs/30/attempts/1/jobs']['jobs'][0].update(conclusion='skipped'))

    def test_ci_failure(self):
        self.rejected(lambda w,r: w.rest_data['/actions/runs/30'].update(conclusion='failure'))

    def test_ci_rerun_running(self):
        self.rejected(lambda w,r: w.rest_data['/actions/runs/30'].update(status='in_progress', conclusion=None, run_attempt=2))

    def test_ci_old_check_head(self):
        self.rejected(lambda w,r: w.rest_data[f'/commits/{r.head}/check-runs']['check_runs'][0].update(head_sha=BASE))

    def test_review_old_commit(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}/reviews'][0].update(commit_id=BASE))

    def test_review_canceled(self):
        self.rejected(lambda w,r: w.rest_data[f'/pulls/{r.pr}/reviews'][0].update(state='DISMISSED'))

    def test_rest_and_graphql_identity_disagree(self):
        self.rejected(lambda w,r: w.gql_state.update(databaseId=701))

    def test_review_thread_unresolved_even_if_outdated(self):
        self.rejected(lambda w,r: w.threads.append(dict(id='T1', isResolved=False, isOutdated=True)))

    def test_all_pages_of_threads_checked(self):
        self.rejected(lambda w,r: w.threads.extend([dict(id=f'T{i}', isResolved=i<100, isOutdated=False) for i in range(101)]))

    def test_resolved_threads_multiple_pages(self):
        world, ref = self.reader()
        world.threads = [dict(id=f'T{i}', isResolved=True, isOutdated=False) for i in range(101)]
        self.assertEqual(world.reader(self.fake_policy).inspect(ref).status, 'READ_ONLY_VERIFIED')

    def test_pending_codeowner_request(self):
        self.rejected(lambda w,r: w.requests.append(dict(id='R1', asCodeOwner=True)))

    def test_merge_queue_pending(self):
        self.rejected(lambda w,r: w.gql_state.update(mergeQueueEntry={'id': 'Q1'}))

    def test_auto_merge_request_pending(self):
        self.rejected(lambda w,r: w.gql_state.update(autoMergeRequest={'enabledAt': 'now'}))

    def test_hidden_admin_bypass_pending(self):
        self.rejected(lambda w,r: w.rest_data['/branches/master/protection']['required_pull_request_reviews'].pop('bypass_pull_request_allowances'))

    def test_admin_bypass_present_pending(self):
        self.rejected(lambda w,r: w.rest_data['/branches/master/protection']['required_pull_request_reviews']['bypass_pull_request_allowances']['users'].append({'id': 1}))

    def test_non_strict_ci_pending(self):
        self.rejected(lambda w,r: w.rest_data['/branches/master/protection']['required_status_checks'].update(strict=False))

    def test_rule_unknown_pending(self):
        self.rejected(lambda w,r: w.rest_data['/rules/branches/master'].append({'type': 'workflows', 'ruleset_id': 90}))

    def test_rule_merge_queue_pending(self):
        self.rejected(lambda w,r: w.rest_data['/rules/branches/master'].append({'type': 'merge_queue', 'ruleset_id': 90}))

    def ruleset(self, world, bypass=()):
        world.rest_data['/rules/branches/master'] = [{'type': 'deletion', 'ruleset_id': 90}]
        world.rest_data['/rulesets/90'] = {'id': 90, 'target': 'branch', 'enforcement': 'active',
                                        'bypass_actors': list(bypass), 'rules': [{'type': 'deletion'}]}

    def test_ruleset_hidden_bypass_pending(self):
        world, ref = self.reader(); self.ruleset(world)
        world.rest_data['/rulesets/90'].pop('bypass_actors')
        with self.assertRaisesRegex(VerificationPending, 'ruleset_bypass_unverified'): world.reader(self.fake_policy).inspect(ref)

    def test_ruleset_no_bypass_readable(self):
        world, ref = self.reader(); self.ruleset(world)
        self.assertEqual(world.reader(self.fake_policy).inspect(ref).status, 'READ_ONLY_VERIFIED')

    def owners(self, world, text='* @reviewer\n'):
        world.rest_data['/branches/master/protection']['required_pull_request_reviews']['require_code_owner_reviews'] = True
        world.owners['github'] = {'__typename': 'Blob', 'text': text, 'byteSize': len(text.encode()), 'isTruncated': False, 'oid': blob_sha(text.encode())}
        world.users['reviewer'] = {'databaseId': 2002, 'login': 'reviewer'}
        world.rest_data['/collaborators/reviewer/permission'] = {'user': {'id': 2002}, 'permission': 'write'}

    def test_codeowner_base_blob_and_immutable_approval(self):
        world, ref = self.reader(); self.owners(world, '/docs/ @reviewer\n')
        self.assertEqual(world.reader(self.fake_policy).inspect(ref).status, 'READ_ONLY_VERIFIED')

    def test_codeowner_missing_pending(self):
        self.rejected(lambda w,r: w.rest_data['/branches/master/protection']['required_pull_request_reviews'].update(require_code_owner_reviews=True))

    def test_codeowner_team_unsupported_pending(self):
        world, ref = self.reader(); self.owners(world, '* @org/team\n')
        with self.assertRaises(VerificationPending): world.reader(self.fake_policy).inspect(ref)

    def test_codeowner_blob_hash_wrong_pending(self):
        world, ref = self.reader(); self.owners(world); world.owners['github']['oid'] = BASE
        with self.assertRaises(VerificationPending): world.reader(self.fake_policy).inspect(ref)

    def test_codeowner_read_only_permission_pending(self):
        world, ref = self.reader(); self.owners(world); world.rest_data['/collaborators/reviewer/permission']['permission'] = 'read'
        with self.assertRaises(VerificationPending): world.reader(self.fake_policy).inspect(ref)

    def test_codeowner_last_rule_requires_its_owner(self):
        world, ref = self.reader(); self.owners(world, '* @reviewer\n/docs/ @other\n')
        world.users['other'] = {'databaseId': 3004, 'login': 'other'}
        world.rest_data['/collaborators/other/permission'] = {'user': {'id': 3004}, 'permission': 'write'}
        with self.assertRaisesRegex(VerificationPending, 'codeowners_approval_missing'): world.reader(self.fake_policy).inspect(ref)

    def test_public_repo_zero_cost_required(self):
        self.rejected(lambda w,r: w.rest_data[''].update(private=True))

    def test_permission_failure_pending_not_allowed(self):
        self.rejected(lambda w,r: setattr(w, 'http_failure', 403))

    def test_rate_limit_pending_not_allowed(self):
        self.rejected(lambda w,r: setattr(w, 'http_failure', 429))

    def test_state_change_during_double_read(self):
        world, ref = self.reader()
        def hook(kind, value):
            if kind == 'graphql' and value == 'owners':
                world.rest_data[f'/pulls/{ref.pr}']['updated_at'] = '2026-10-21T00:09:00Z'
        world.http_hook = hook
        with self.assertRaises(VerificationPending): world.reader(self.fake_policy).inspect(ref)


class FakeMergeTests(MergeFixture):
    def test_normal_e2e_merge_is_only_a_fake_effect(self):
        value, world, ref = self.candidate(); broker = self.broker(value, world)
        self.assertEqual(broker.prepare()['status'], 'MERGE_ELIGIBLE')
        self.assertFalse(broker.simulate()['execution_enabled'])
        self.assertEqual(self.store.read()['merge'][ref.task]['status'], 'FAKE_MERGED')
        self.assertEqual(world.effects, 1)

    def test_duplicate_merge_does_not_repeat_effect(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world)
        broker.prepare(); broker.simulate(); result = broker.simulate()
        self.assertTrue(result['duplicate']); self.assertEqual(world.effects, 1)

    def test_concurrent_brokers_one_effect(self):
        value, world, _ = self.candidate(); first = self.broker(value, world); second = self.broker(value, world)
        first.prepare()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda b: b.simulate(), [first, second]))
        self.assertEqual(world.effects, 1)
        self.assertEqual(sum(r.get('duplicate', False) for r in results), 1)

    def test_expired_candidate_halted(self):
        value, world, ref = self.candidate(); broker = self.broker(value, world); broker.prepare()
        self.now += timedelta(seconds=15)
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(self.store.read()['merge'][ref.task]['status'], 'HALTED'); self.assertEqual(world.effects, 0)

    def test_new_head_after_candidate_halts(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world); broker.prepare()
        world.gql_state['headRefOid'] = BASE
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 0)

    def test_review_revocation_after_candidate_halts(self):
        value, world, ref = self.candidate(); broker = self.broker(value, world); broker.prepare()
        world.rest_data[f'/pulls/{ref.pr}/reviews'][0]['state'] = 'DISMISSED'
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 0)

    def test_issue_cancel_after_candidate_halts(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world); broker.prepare(); self.authority.cancel(42)
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 0)

    def test_immediately_before_fake_effect_cas(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world); broker.prepare()
        world.before_compare = lambda: world.rest_data['/branches/master']['commit'].update(sha='d'*40)
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 0)

    def test_unknown_plus_quota_blocks_even_fake_eligibility(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world, quota=SubscriptionEvidence())
        with self.assertRaises(WorkerBlocked): broker.prepare()
        self.assertNotIn('merge', self.store.read())

    def test_exhausted_plus_quota_blocks(self):
        value, world, _ = self.candidate(); broker = self.broker(value, world, quota=replace(QUOTA, included_usage_available=False))
        with self.assertRaises(WorkerBlocked): broker.prepare()

    def test_lost_response_requires_reconciliation_no_retry(self):
        value, world, ref = self.candidate(); broker = self.broker(value, world); broker.prepare(); world.lose_response = True
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 1)
        self.assertEqual(self.store.read()['merge'][ref.task]['status'], 'HALTED')
        self.assertTrue(broker.reconcile()['receipt_confirmed'])
        with self.assertRaises(WorkerBlocked): broker.simulate()
        self.assertEqual(world.effects, 1)

    def test_intent_restart_never_replays(self):
        value, world, ref = self.candidate(); broker = self.broker(value, world); broker.prepare()
        def interrupted(state, now):
            state['merge'][ref.task]['status'] = 'MERGE_INTENT'
            return {'status': 'MERGE_INTENT'}
        broker._record('MERGE_INTENT', interrupted)
        # New coordinator instance shares only the durable abstract store, not a worker lease.
        self.coordinator = Coordinator(FakeSharedStore(self.backend, self.policy), self.authority, self.policy, clock=lambda: self.now)
        self.pipeline = FakePipeline(self.coordinator, self.publisher)
        recovered = self.broker(value, world)
        with self.assertRaises(WorkerBlocked): recovered.simulate()
        self.assertFalse(recovered.reconcile()['receipt_confirmed']); self.assertEqual(world.effects, 0)

    def test_sqlite_close_reopen_preserves_merge_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            witness = FakeWitness(); path = Path(directory) / 'local.db'
            self.store = SQLiteTaskStore.create(path, self.policy, witness=witness)
            self.coordinator = Coordinator(self.store, self.authority, self.policy, clock=lambda: self.now)
            self.pipeline = FakePipeline(self.coordinator, self.publisher)
            value, world, ref = self.candidate(); broker = self.broker(value, world); broker.prepare()
            def interrupt(state, now):
                state['merge'][ref.task]['status'] = 'MERGE_INTENT'; return {'status': 'MERGE_INTENT'}
            broker._record('MERGE_INTENT', interrupt)
            self.store = SQLiteTaskStore(path, self.policy, witness=witness)
            self.coordinator = Coordinator(self.store, self.authority, self.policy, clock=lambda: self.now)
            self.pipeline = FakePipeline(self.coordinator, self.publisher)
            recovered = self.broker(value, world)
            with self.assertRaises(WorkerBlocked): recovered.simulate()
            self.assertEqual(self.store.read()['merge'][ref.task]['status'], 'HALTED')

    def test_live_merge_broker_is_unconditionally_disabled(self):
        with self.assertRaises(WorkerBlocked): DisabledLiveMergeBroker().merge(head=BASE)

    def test_process_crash_after_intent_requires_checkpoint_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'crash.db'; witness = FakeWitness()
            self.store = SQLiteTaskStore.create(path, self.policy, witness=witness)
            self.coordinator = Coordinator(self.store, self.authority, self.policy, clock=lambda: self.now)
            self.pipeline = FakePipeline(self.coordinator, self.publisher)
            value, world, ref = self.candidate(); self.broker(value, world).prepare()
            code = ("import json,sys,os;from uuid import uuid4;"
                    "from automation.worker_store import SQLiteTaskStore,FakeWitness;"
                    "p=json.loads(sys.argv[2]);w=FakeWitness();w.value=tuple(json.loads(sys.argv[3]));"
                    "s=SQLiteTaskStore(sys.argv[1],p,witness=w);t=sys.argv[4];"
                    "s.change(str(uuid4()),'MERGE_INTENT',[t],lambda state:state['merge'][t].update(status='MERGE_INTENT'));"
                    "os._exit(17)")
            result = subprocess.run([sys.executable, '-c', code, str(path), json.dumps(self.policy), json.dumps(witness.value), ref.task],
                                    cwd=ROOT, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 17)
            with self.assertRaisesRegex(WorkerBlocked, 'checkpoint_mismatch'): SQLiteTaskStore(path, self.policy, witness=witness)
            db = sqlite3.connect(path)
            try:
                saved = json.loads(db.execute('SELECT data FROM state').fetchone()[0])
            finally:
                db.close()
            self.assertEqual(saved['merge'][ref.task]['status'], 'MERGE_INTENT')
            self.assertEqual(world.effects, 0)

    def test_live_reader_not_a_fake_world(self):
        value, world, _ = self.candidate()
        with self.assertRaises(WorkerBlocked): self.broker(value, world.reader(self.fake_policy))


class FeedbackRelayTests(MergeFixture):
    def test_missing_independent_review_waits_no_new_attempt(self):
        value = self.published(); world, ref = self.world(value); world.rest_data[f'/pulls/{ref.pr}/reviews'] = []
        self.assertEqual(FakeReviewRelay(self.pipeline, world, ref).poll()['status'], 'WAIT_REVIEW')
        self.assertEqual(self.coordinator.state(42)['attempts'], 1)

    def test_revision_e2e_new_head_old_approval_ci_invalidated(self):
        value = self.published(); world, ref = self.world(value)
        world.rest_data[f'/pulls/{ref.pr}/reviews'][0].update(state='CHANGES_REQUESTED', body='Improve the note. $(not a command)')
        relay = FakeReviewRelay(self.pipeline, world, ref)
        self.assertEqual(relay.poll()['status'], 'CHANGES_REQUESTED')
        self.assertEqual(relay.poll()['status'], 'DUPLICATE')
        revised = self.published(b'revised note\n'); item = self.coordinator.state(42)
        self.assertNotEqual(item['head'], ref.head)
        self.assertEqual((item['review_state'], item['ci_pass']), ('NONE', False))
        with self.assertRaises(WorkerBlocked): relay.poll()
        new_world, new_ref = self.world(revised)
        new_world.rest_data[f'/pulls/{new_ref.pr}/reviews'][0]['id'] = 21
        self.assertEqual(FakeReviewRelay(self.pipeline, new_world, new_ref).poll()['status'], 'WAIT_REVIEW')
        self.pipeline.ci(42, run_id=2)
        broker = self.broker(revised, new_world); broker.prepare(); broker.simulate()
        self.assertEqual(new_world.effects, 1)
        self.assertNotIn('$(not a command)', json.dumps(self.store.read()))

    def test_dismissal_during_revision_halts(self):
        value = self.published(); world, ref = self.world(value)
        row = world.rest_data[f'/pulls/{ref.pr}/reviews'][0]; row.update(state='CHANGES_REQUESTED', body='revise')
        relay = FakeReviewRelay(self.pipeline, world, ref); relay.poll()
        item = self.coordinator.state(42); self.coordinator.claim(42, self.owner, expected_version=item['version'])
        row.update(id=21, state='DISMISSED')
        self.assertEqual(relay.poll()['status'], 'HALTED')
        self.assertEqual(self.coordinator.state(42)['status'], 'HALTED')

    def test_old_review_cannot_halt_new_revision(self):
        value = self.published(); world, ref = self.world(value)
        row = world.rest_data[f'/pulls/{ref.pr}/reviews'][0]; row.update(id=22, state='CHANGES_REQUESTED', body='revise')
        relay = FakeReviewRelay(self.pipeline, world, ref); relay.poll()
        item = self.coordinator.state(42); self.coordinator.claim(42, self.owner, expected_version=item['version'])
        row.update(id=20, state='APPROVED')
        self.assertEqual(relay.poll()['status'], 'STALE')
        self.assertEqual(self.coordinator.state(42)['status'], 'RUNNING')

    def test_foreign_changes_do_not_become_trusted_approval(self):
        value = self.published(); world, ref = self.world(value)
        world.rest_data[f'/pulls/{ref.pr}/reviews'].append(dict(id=21, user={'id': 999}, state='CHANGES_REQUESTED', commit_id=ref.head, body='untrusted'))
        with self.assertRaises(WorkerBlocked): FakeReviewRelay(self.pipeline, world, ref).poll()
        self.assertEqual(self.coordinator.state(42)['status'], 'WAIT_REVIEW')

    def test_feedback_requires_current_task_marker(self):
        value = self.published(); world, ref = self.world(value)
        world.rest_data[f'/pulls/{ref.pr}']['body'] = 'unbound'
        with self.assertRaises(WorkerBlocked): FakeReviewRelay(self.pipeline, world, ref).poll()


class GraphQLTransportTests(unittest.TestCase):
    def client(self, status=200, body=None, headers=None):
        body = body if body is not None else {'data': {'repository': {}}}
        return ReadOnlyGraphQL('sasumpi123/auto_trade', 'fake-auth', send=lambda b,h: (status, headers or {'Content-Type': 'application/json'}, json.dumps(body).encode()))

    def test_arbitrary_query_or_mutation_forbidden(self):
        with self.assertRaises(VerificationPending): self.client().read('mutation Merge {mergePullRequest{}}')
        self.assertTrue(all(v.startswith('query ') and 'mutation' not in v for v in QUERIES.values()))

    def test_partial_graphql_data_with_errors_rejected(self):
        with self.assertRaises(VerificationPending): self.client(body={'data': {'repository': {}}, 'errors': [{'message': 'sensitive'}]}).read('state', number=1)

    def test_graphql_permissions_rejected(self):
        with self.assertRaises(VerificationPending): self.client(status=403).read('state', number=1)

    def test_graphql_rate_limit_rejected(self):
        with self.assertRaises(VerificationPending): self.client(status=429).read('state', number=1)

    def test_graphql_auth_not_in_repr(self):
        self.assertNotIn('fake-auth', repr(self.client()))

    def test_graphql_request_limit(self):
        client = self.client(); client.requests = client.MAX_REQUESTS
        with self.assertRaises(VerificationPending): client.read('state', number=1)

    def test_graphql_owner_path_pinned_base(self):
        captured = []
        def send(b,h):
            captured.append(json.loads(b)); return 200, {'content-type': 'application/json'}, b'{"data":{"repository":{}}}'
        client = ReadOnlyGraphQL('sasumpi123/auto_trade', 'fake-auth', send=send)
        client.read('owners', base=BASE)
        self.assertEqual(captured[0]['variables']['githubPath'], BASE+':.github/CODEOWNERS')


class ObserverDiagnosticsTests(unittest.TestCase):
    def test_known_missing_contract_has_specific_safe_code(self):
        self.assertEqual(diagnostic(VerificationPending('issue_link_required')), 'ISSUE_CONTRACT_MISSING')

    def test_permission_error_kept_separate(self):
        self.assertEqual(diagnostic(VerificationPending('permission_or_rate_limit')), 'API_PERMISSION_OR_RATE_LIMIT')

    def test_unknown_error_not_reflected(self):
        self.assertEqual(diagnostic(VerificationPending('token=secret raw issue text')), 'VERIFICATION_PENDING')

    def test_programming_error_never_greenwashed(self):
        self.assertEqual(diagnostic(KeyError('secret')), 'OBSERVER_PROGRAM_ERROR')

    def observe(self, mutate=None, broken=None):
        from tests.test_github_readonly import fixture, FakeHTTP
        from automation.github_read import GitHubREST
        from automation.github_verify import GitHubVerifier
        from automation.github_observe import Hint, ObservationJournal, observe
        policy, data = fixture()
        if mutate: mutate(data)
        verifier = GitHubVerifier(GitHubREST(policy['repository'], 'fake-auth', send=FakeHTTP(data)), policy)
        if broken:
            verifier.verify = lambda **kw: (_ for _ in ()).throw(broken)
        with tempfile.TemporaryDirectory() as directory:
            return observe(verifier, ObservationJournal.create(Path(directory)/'observer.db'), str(uuid4()), Hint('pull_request', 'opened', 7, 'pr'))

    def test_manual_pr_without_issue_contract_reproduces_pending(self):
        result = self.observe(lambda data: data['/pulls/7'].update(body='A manually developed PR.'))
        self.assertEqual(result['decision'], 'PENDING')
        self.assertEqual(result['diagnostic'], 'ISSUE_CONTRACT_MISSING')
        self.assertFalse(result['execution_enabled'])

    def test_observer_does_not_expose_unexpected_exception(self):
        result = self.observe(broken=RuntimeError('fake-private-body-and-token'))
        self.assertEqual(result['diagnostic'], 'OBSERVER_PROGRAM_ERROR')
        self.assertNotIn('fake-private', json.dumps(result))
        self.assertEqual(result['decision'], 'PENDING')
