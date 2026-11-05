"""P27 observer scope. All HTTP is in-memory Fake; no GitHub, network, model, write or merge call."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from automation.github_read import GitHubREST, VerificationPending
from automation.github_verify import GitHubVerifier
from automation.github_observe import Hint, ObservationJournal, observe
from tests.test_github_readonly import FakeHTTP, REPO, HEAD, fixture

MANUAL_BODY = 'Closes #55 private-manual-body-never-persist'


def manualize(data, body=MANUAL_BODY, ref='feat/p26-manual-paper-continuation'):
    data['/pulls/7']['body'] = body
    data['/pulls/7']['head']['ref'] = ref


class Base(unittest.TestCase):
    def setUp(self):
        self.policy, self.data = fixture()
        self.http = FakeHTTP(self.data)
        self.api = GitHubREST(REPO, 'test-auth-token', send=self.http)
        self.verifier = GitHubVerifier(self.api, self.policy)
        blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        blocker.start(); self.addCleanup(blocker.stop)
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.journal = ObservationJournal.create(Path(self.tmp.name) / 'observe.sqlite3')

    def run_hint(self, hint):
        return observe(self.verifier, self.journal, str(uuid4()), hint)


class EntrypointTests(Base):
    """tools/automation_plan.main() with the real routing/verifier/journal and a Fake transport."""
    def main(self, kind, event):
        from tools import automation_plan as plan
        path = Path(self.tmp.name) / 'event.json'; path.write_text(json.dumps(event), encoding='utf-8')
        env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_EVENT_PATH': str(path), 'GITHUB_EVENT_NAME': kind,
               'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_TOKEN': 'test-private-auth'}
        http = self.http; db = Path(self.tmp.name) / 'entry.sqlite3'
        class Journal(ObservationJournal):
            def __init__(self, _path):super().__init__(db)
            @classmethod
            def create(cls, _path):return ObservationJournal.create.__func__(cls, db) if not db.exists() else cls(db)
        out = io.StringIO()
        with patch.dict(os.environ, env, clear=False), \
             patch.object(plan, 'GitHubREST', lambda repo, token: GitHubREST(repo, token, send=http)), \
             patch.object(plan, 'ObservationJournal', Journal), contextlib.redirect_stdout(out):
            code = plan.main()
        return code, json.loads(out.getvalue())

    def event(self, **extra):
        return {'action': 'synchronize', 'repository': self.data[''], 'pull_request': {'number': 7, 'body': 'evil'}, **extra}

    def test_manual_same_repository_pr_is_not_applicable_exit_0(self):
        manualize(self.data)
        code, out = self.main('pull_request_target', self.event())
        self.assertEqual(code, 0)
        self.assertEqual({k: out[k] for k in ('decision', 'reason', 'execution_enabled', 'read_only', 'model_calls')},
                         {'decision': 'NOT_APPLICABLE', 'reason': 'manual_pr_outside_autodev', 'execution_enabled': False,
                          'read_only': True, 'model_calls': 0})
        self.assertNotIn('private', json.dumps(out))

    def test_autodev_branch_without_contract_stays_pending_exit_2(self):
        self.data['/pulls/7']['body'] = 'Closes #55'
        code, out = self.main('pull_request_target', self.event())
        self.assertEqual((code, out['decision'], out['diagnostic']), (2, 'PENDING', 'ISSUE_CONTRACT_MISSING'))

    def test_api_failure_stays_fail_closed(self):
        manualize(self.data); self.http.failure = 403
        code, out = self.main('pull_request_target', self.event())
        self.assertEqual((code, out['decision']), (2, 'PENDING'))
        self.assertNotIn('private', json.dumps(out))


class ClassificationTests(Base):
    def test_nullable_or_empty_body_is_manual_only_with_valid_schema(self):
        for body in (None, '', 'Closes #55'):
            with self.subTest(body=body):
                manualize(self.data, body=body)
                self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'NOT_APPLICABLE')
        for mutate in ('missing_body', 'int_body', 'missing_head_repo', 'bad_sha', 'bad_state', 'other_base', 'oversized'):
            with self.subTest(mutate=mutate):
                manualize(self.data); pr = self.data['/pulls/7']
                if mutate == 'missing_body': pr.pop('body')
                elif mutate == 'int_body': pr['body'] = 5
                elif mutate == 'missing_head_repo': pr['head'].pop('repo')
                elif mutate == 'bad_sha': pr['head']['sha'] = 'xyz'
                elif mutate == 'bad_state': pr['state'] = 'merged'
                elif mutate == 'other_base': pr['base']['repo'] = dict(pr['base']['repo'], id=1)
                else: pr['body'] = 'x' * 262145
                self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'PENDING')
                self.setUp()

    def test_any_autodev_claim_keeps_strict_verification(self):
        sha = self.data['/pulls/7']['body'].split()[-1]
        cases = {'malformed': '/autodev issue x spec y', 'duplicate': f'/autodev issue 42 spec {sha}\n/autodev issue 42 spec {sha}',
                 'case': '/AUTODEV approve something', 'valid_contract_manual_branch': f'/autodev issue 42 spec {sha}'}
        for name, body in cases.items():
            with self.subTest(case=name):
                manualize(self.data, body=body)
                out = self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))
                self.assertNotEqual(out['decision'], 'NOT_APPLICABLE')
                if name == 'valid_contract_manual_branch':self.assertEqual(out['decision'], 'PR_BLOCKED')
                else:self.assertEqual(out['decision'], 'PENDING')
                self.setUp()
        manualize(self.data, body='Closes #55', ref='Autodev/issue-42')
        self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'PENDING')

    def test_fork_or_unknown_head_is_not_assumed_manual(self):
        for head_repo in (None, dict(self.data[''], id=1, full_name='someone/auto_trade')):
            with self.subTest(head_repo=bool(head_repo)):
                manualize(self.data); self.data['/pulls/7']['head']['repo'] = head_repo
                out = self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))
                self.assertEqual(out['decision'], 'PENDING')
                self.setUp()

    def test_state_change_between_reads_is_pending(self):
        manualize(self.data); seen = []
        def hook(path):
            if path.endswith('/pulls/7'):
                seen.append(1)
                if len(seen) == 2:self.data['/pulls/7']['body'] = '/autodev issue 42 spec ' + 'c' * 64
        self.http.hook = hook
        out = self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))
        self.assertEqual((out['decision'], out['diagnostic']), ('PENDING', 'STATE_CHANGED_DURING_READ'))

    def test_event_payload_cannot_make_a_pr_manual(self):
        # The routed hint carries only a number; classification re-reads the current API state.
        self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'REVIEW_CI_VERIFIED')


class EventConsistencyTests(Base):
    def test_pr_review_comment_and_run_events_agree(self):
        manualize(self.data)
        for hint in (Hint('pull_request_target', 'opened', 7, 'pr'), Hint('pull_request_review', 'submitted', 7, 'pr'),
                     Hint('issue_comment', 'created', 7, 'pr'), Hint('workflow_run', 'completed', 30, 'run')):
            with self.subTest(kind=hint.kind):self.assertEqual(self.run_hint(hint)['decision'], 'NOT_APPLICABLE')

    def test_stale_ambiguous_or_prless_runs_are_not_success(self):
        manualize(self.data)
        self.data['/actions/runs/30']['head_sha'] = 'c' * 40
        self.assertEqual(self.run_hint(Hint('workflow_run', 'completed', 30, 'run'))['diagnostic'], 'STALE_RUN_EVENT')
        for prs in ([], [{'number': 7}, {'number': 8}]):
            with self.subTest(prs=len(prs)):
                self.data['/actions/runs/30']['head_sha'] = HEAD; self.data['/actions/runs/30']['pull_requests'] = prs
                self.assertEqual(self.run_hint(Hint('workflow_run', 'completed', 30, 'run'))['diagnostic'], 'RUN_PR_RELATION_UNVERIFIED')


class JournalTests(Base):
    def test_not_applicable_never_becomes_or_revives_a_candidate(self):
        verified = self.run_hint(Hint('pull_request_target', 'opened', 7, 'pr'))
        self.assertEqual(verified['decision'], 'REVIEW_CI_VERIFIED');self.assertIsNotNone(self.journal.current('pr', 7))
        manualize(self.data)
        self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'NOT_APPLICABLE')
        self.assertIsNone(self.journal.current('pr', 7))  # Old autodev candidate no longer current.
        self.data['/pulls/7'] = fixture()[1]['/pulls/7']  # Back to the exact autodev contract.
        self.assertIsNone(self.journal.current('pr', 7))  # Nothing revives without a fresh verified read.
        with sqlite3.connect(self.journal.path) as db:
            rows = db.execute('SELECT status,metadata FROM observations ORDER BY seq').fetchall()
        self.assertEqual(rows[-1], ('NOT_APPLICABLE', None))
        self.assertNotIn(b'private-manual-body', self.journal.path.read_bytes())

    def add_run(self, run_id, pr=7):
        run = copy.deepcopy(self.data['/actions/runs/30']); run['id'] = run_id
        run['pull_requests'] = [dict(run['pull_requests'][0], number=pr)]
        self.data[f'/actions/runs/{run_id}'] = run

    def test_not_applicable_run_fences_pr_candidate_from_pr_or_run_origin(self):
        """Review 5778063916: a new non-target run must also fence the linked PR's old candidate."""
        for origin in ('pr', 'run'):
            with self.subTest(origin=origin):
                self.setUp()
                first = Hint('pull_request_target', 'opened', 7, 'pr') if origin == 'pr' else Hint('workflow_run', 'completed', 30, 'run')
                self.assertEqual(self.run_hint(first)['decision'], 'REVIEW_CI_VERIFIED')
                self.assertIsNotNone(self.journal.current('pr', 7))
                manualize(self.data); self.add_run(31)
                self.assertEqual(self.run_hint(Hint('workflow_run', 'completed', 31, 'run'))['decision'], 'NOT_APPLICABLE')
                self.assertIsNone(self.journal.current('pr', 7))
                self.assertIsNone(ObservationJournal(self.journal.path).current('pr', 7))  # DB reopen keeps the fence.

    def test_late_older_completion_cannot_revive_and_unrelated_pr_is_kept(self):
        from automation.github_verify import Snapshot
        # PR 8: an unrelated verified candidate that must survive.
        pr8 = copy.deepcopy(self.data['/pulls/7']); pr8['number'] = 8
        self.data['/pulls/8'] = pr8
        for suffix in ('/files', '/reviews'):self.data['/pulls/8' + suffix] = copy.deepcopy(self.data['/pulls/7' + suffix])
        snap7 = self.verifier.verify(pr=7)
        late = self.journal.begin(str(uuid4()), Hint('pull_request_target', 'opened', 7, 'pr'))  # In flight, older.
        late_run = self.journal.begin(str(uuid4()), Hint('workflow_run', 'completed', 30, 'run'))
        self.journal.complete(self.journal.begin(str(uuid4()), Hint('pull_request_target', 'opened', 8, 'pr')),
                              Snapshot(**{**snap7.metadata(), 'pr': 8}))
        manualize(self.data); self.add_run(31)
        self.assertEqual(self.run_hint(Hint('workflow_run', 'completed', 31, 'run'))['decision'], 'NOT_APPLICABLE')
        self.journal.complete(late, snap7);self.journal.complete(late_run, snap7)  # Late completions of older reads.
        self.assertIsNone(self.journal.current('pr', 7))
        self.assertIsNone(ObservationJournal(self.journal.path).current('pr', 7))
        self.assertIsNotNone(self.journal.current('pr', 8))  # Unrelated PR not fenced.
        # A NEWER genuine verified read is not over-fenced.
        self.data['/pulls/7'] = fixture()[1]['/pulls/7']
        self.assertEqual(self.run_hint(Hint('pull_request_target', 'edited', 7, 'pr'))['decision'], 'REVIEW_CI_VERIFIED')
        self.assertIsNotNone(self.journal.current('pr', 7))

    def test_run_alias_metadata_is_minimal_and_validated_on_reopen(self):
        manualize(self.data); self.add_run(31)
        self.run_hint(Hint('workflow_run', 'completed', 31, 'run'))
        with sqlite3.connect(self.journal.path) as db:
            row = db.execute("SELECT target,status,metadata FROM observations ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual((row[0], row[1], json.loads(row[2])), ('run', 'NOT_APPLICABLE', {'pr': 7, 'scope': 'NOT_APPLICABLE'}))
        with sqlite3.connect(self.journal.path) as db:  # Tampered alias (sha no longer matches) fails closed on reopen.
            db.execute("UPDATE observations SET metadata=? WHERE status='NOT_APPLICABLE'", (json.dumps({'pr': 8, 'scope': 'NOT_APPLICABLE'}),))
        with self.assertRaises(VerificationPending):ObservationJournal(self.journal.path)

    def test_complete_rejects_scope_mixing_and_duplicates_need_fresh_poll(self):
        seq = self.journal.begin(str(uuid4()), Hint('pull_request_target', 'opened', 7, 'pr'))
        from automation.github_verify import Snapshot
        with self.assertRaises(VerificationPending):self.journal.complete(seq, scope='VERIFIED')
        manualize(self.data); delivery = str(uuid4())
        self.assertEqual(observe(self.verifier, self.journal, delivery, Hint('pull_request_target', 'opened', 7, 'pr'))['decision'], 'NOT_APPLICABLE')
        self.assertEqual(observe(self.verifier, self.journal, delivery, Hint('pull_request_target', 'opened', 7, 'pr'))['reason'],
                         'duplicate_requires_fresh_poll')

    def test_policy_and_verifier_contract_untouched(self):
        policy = json.loads((Path(__file__).resolve().parents[1] / '.github/automation/policy.json').read_text())
        self.assertIs(policy['enabled'], False);self.assertEqual((policy['builder_ids'], policy['independent_reviewer_ids']), ([], []))
        manualize(self.data)
        with self.assertRaisesRegex(VerificationPending, 'issue_link_required'):self.verifier.verify(pr=7)  # verify() unchanged.
        self.assertNotIn('NOT_APPLICABLE', __import__('automation.github_verify', fromlist=['DECISIONS']).DECISIONS)


if __name__ == '__main__':
    unittest.main()
