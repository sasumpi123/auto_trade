"""All HTTP is in-memory. No GitHub, models, push, merge or exchange calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from automation.events import Event, LedgerError
from automation.github_read import GitHubREST, VerificationPending, _NoRedirect
from automation.github_verify import GitHubVerifier, Snapshot
from automation.github_observe import Hint, ObservationJournal, observe, p1_reference, route
from automation.policy import spec_hash
from automation.store import SQLiteLedger

ROOT = Path(__file__).resolve().parents[1]
REPO = 'sasumpi123/auto_trade'
URL = 'https://api.github.com/repos/' + REPO
HEAD, BASE = 'a' * 40, 'b' * 40
TIME = '2026-10-21T00:00:00Z'
LATER = '2026-10-21T00:01:00Z'


def fixture():
    policy = json.loads((ROOT / '.github/automation/policy.json').read_text())
    policy.update(builder_ids=[1001], independent_reviewer_ids=[2002])
    body = 'test-private-spec-never-persist-this'
    sha = spec_hash(body)
    repo = dict(id=888367411, full_name=REPO, default_branch='master', archived=False, disabled=False)
    issue = dict(number=42, body=body, state='open', user={'id': 9001}, labels=[{'name': policy['approved_label']}],
                 updated_at=TIME, repository_url=URL, url=URL + '/issues/42')
    comment = dict(id=10, user={'id': 45135977, 'type': 'User', 'login': 'admin-display-name'},
                   body=f'/autodev approve {sha}', issue_url=issue['url'], created_at=LATER, updated_at=LATER)
    pr = dict(number=7, body=f'/autodev issue 42 spec {sha}', user={'id': 1001}, state='open', draft=False,
              merged=False, updated_at=TIME, changed_files=1,
              head={'sha': HEAD, 'ref': 'autodev/issue-42', 'repo': repo},
              base={'sha': BASE, 'ref': 'master', 'repo': repo})
    reviews = [dict(id=20, user={'id': 2002}, commit_id=HEAD, state='APPROVED')]
    run = dict(id=30, path=policy['trusted_ci_workflow'], workflow_id=40, head_sha=HEAD,
               head_branch='autodev/issue-42', event='pull_request', repository=repo,
               head_repository=repo, pull_requests=[dict(number=7, head={'sha': HEAD}, base={'repo': repo})],
               run_attempt=1, status='completed', conclusion='success')
    checks, jobs = [], []
    for i, name in enumerate(policy['required_checks'], 50):
        checks.append(dict(id=i, name=name, url=URL + f'/check-runs/{i}', head_sha=HEAD,
                           app={'id': 15368, 'slug': 'github-actions', 'owner': {'id': 9919}},
                           status='completed', conclusion='success'))
        jobs.append(dict(id=i + 100, name=name, check_run_url=checks[-1]['url'], run_id=30,
                         head_sha=HEAD, status='completed', conclusion='success'))
    protection = dict(required_pull_request_reviews=dict(dismiss_stale_reviews=True, require_last_push_approval=True, require_code_owner_reviews=False,
                                                        required_approving_review_count=1),
                      enforce_admins={'enabled': True}, required_status_checks=dict(strict=True,
                      contexts=policy['required_checks'], checks=[{'context': x, 'app_id': 15368} for x in policy['required_checks']]))
    mapping = {'': repo, '/issues/42': issue, '/issues/42/comments': [comment], '/pulls/7': pr,
               '/pulls/7/files': [dict(filename='docs/research.md', status='modified')], '/pulls/7/reviews': reviews,
               '/branches/master/protection': protection, '/branches/master': {'commit': {'sha': BASE}, 'protected': True},
               '/rules/branches/master': [],
               f'/compare/{BASE}...{HEAD}': {'merge_base_commit': {'sha': BASE}, 'status': 'ahead'},
               f'/commits/{HEAD}/check-runs': {'total_count': 4, 'check_runs': checks},
               '/actions/runs': {'total_count': 1, 'workflow_runs': [run]}, '/actions/runs/30': run,
               '/actions/workflows/40': {'id': 40, 'path': policy['trusted_ci_workflow'], 'state': 'active'},
               '/actions/runs/30/attempts/1/jobs': {'total_count': 4, 'jobs': jobs}}
    return policy, mapping


class FakeHTTP:
    def __init__(self, mapping):
        self.mapping, self.calls = mapping, []
        self.failure = None
        self.headers = {'content-type': 'application/json'}
        self.hook = None

    def __call__(self, path, headers):
        self.calls.append(path)
        if self.hook:
            self.hook(path)
        if self.failure:
            return self.failure, self.headers, b'fake-private-error-response'
        parts = urlsplit(path)
        key = parts.path.removeprefix('/repos/' + REPO)
        value = copy.deepcopy(self.mapping[key])
        page = int(parse_qs(parts.query).get('page', ['1'])[0])
        if type(value) is list:
            value = value[(page-1)*100:page*100]
        elif 'total_count' in value:
            for field in ('check_runs', 'workflow_runs', 'jobs'):
                if field in value:
                    value[field] = value[field][(page-1)*100:page*100]
        return 200, self.headers, json.dumps(value).encode()


class ReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.policy, self.data = fixture()
        self.http = FakeHTTP(self.data)
        self.api = GitHubREST(REPO, 'test-auth-token', send=self.http)
        self.verifier = GitHubVerifier(self.api, self.policy)
        self.no_network = patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden'))
        self.no_network.start()
        self.addCleanup(self.no_network.stop)

    def issue(self):
        return self.verifier.verify(issue=42)

    def pr(self):
        return self.verifier.verify(pr=7)

    def test_approved_issue_from_authenticated_api(self):
        value = self.issue()
        self.assertEqual(value.decision, 'ISSUE_CANDIDATE')
        self.assertEqual((value.approval_id, value.approval_actor, value.issue_author), (10, 45135977, 9001))
        self.assertNotIn(self.data['/issues/42']['body'], json.dumps(value.metadata()))

    def test_complete_review_ci_flow_is_advisory_not_merge_authority(self):
        value = self.pr()
        self.assertEqual(value.decision, 'REVIEW_CI_VERIFIED')
        self.assertEqual(value.reason, 'advisory_only_threads_unverified')
        self.assertEqual(len(value.checks), 4)

    def test_issue_edit_invalidates_hash(self):
        self.data['/issues/42']['body'] += ' changed'
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_edit_then_revert_or_later_update_invalidates_old_approval(self):
        self.data['/issues/42']['updated_at'] = '2026-10-21T00:02:00Z'
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_forged_approval_same_display_name_rejected(self):
        self.data['/issues/42/comments'][0]['user']['id'] = 999
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_edited_approval_and_bot_rejected(self):
        self.data['/issues/42/comments'][0]['updated_at'] = '2026-10-21T00:02:00Z'
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')
        self.data['/issues/42/comments'][0].update(updated_at=LATER)
        self.data['/issues/42/comments'][0]['user']['type'] = 'Bot'
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_exact_label_open_state_and_exact_approval_syntax(self):
        for field, value in [('labels', [{'name': 'autodev:approved-extra'}]), ('state', 'closed')]:
            original = self.data['/issues/42'][field]
            self.data['/issues/42'][field] = value
            self.assertEqual(self.issue().decision, 'NOT_APPROVED')
            self.data['/issues/42'][field] = original
        self.data['/issues/42/comments'][0]['body'] += '\nexecute evil command'
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_wrong_repository_id_default_branch_and_pr_disguised_as_issue(self):
        for mutate in (lambda: self.data[''].update(id=999), lambda: self.data[''].update(default_branch='other'),
                       lambda: self.data['/issues/42'].update(pull_request={})):
            self.policy, self.data = fixture(); self.http.mapping = self.data
            mutate()
            with self.assertRaises(VerificationPending): self.issue()

    def test_approval_collection_must_match_issue(self):
        self.data['/issues/42/comments'][0]['issue_url'] = URL + '/issues/43'
        with self.assertRaises(VerificationPending): self.issue()

    def test_self_review_rejected(self):
        self.verifier.policy['independent_reviewer_ids'] = [1001]
        self.data['/pulls/7/reviews'][0]['user']['id'] = 1001
        self.assertEqual(self.pr().reason, 'self_review')

    def test_old_review_commit_rejected(self):
        self.data['/pulls/7/reviews'][0]['commit_id'] = 'c' * 40
        self.assertEqual(self.pr().decision, 'REVIEW_PENDING')

    def test_pr_update_invalidates_approval(self):
        self.assertEqual(self.pr().decision, 'REVIEW_CI_VERIFIED')
        self.api.requests = 0
        self.data['/pulls/7']['head']['sha'] = 'c' * 40
        self.assertEqual(self.pr().decision, 'REVIEW_PENDING')

    def test_dismissed_review_not_resurrected(self):
        self.data['/pulls/7/reviews'].append(dict(self.data['/pulls/7/reviews'][0], id=21, state='DISMISSED'))
        self.assertEqual(self.pr().decision, 'REVIEW_PENDING')

    def test_changes_requested_blocks_even_if_other_approval_exists(self):
        self.data['/pulls/7/reviews'].append(dict(id=21, user={'id': 999}, commit_id=HEAD, state='CHANGES_REQUESTED'))
        self.assertEqual(self.pr().decision, 'CHANGES_REQUESTED')

    def test_renamed_protected_source_cannot_escape_path_guard(self):
        self.data['/pulls/7/files'][0].update(status='renamed', previous_filename='config.py')
        self.assertEqual(self.pr().decision, 'PR_BLOCKED')

    def test_incomplete_files_and_fork_rejected(self):
        self.data['/pulls/7']['changed_files'] = 2
        self.assertEqual(self.pr().reason, 'protected_or_incomplete_paths')
        self.data['/pulls/7']['head']['repo'] = dict(self.data[''], id=999, full_name='evil/fork')
        self.assertEqual(self.pr().decision, 'PR_BLOCKED')

    def test_pr_link_hash_and_branch_required(self):
        self.data['/pulls/7']['body'] = '/autodev issue 42 spec ' + 'c' * 64
        self.assertEqual(self.pr().decision, 'NOT_APPROVED')
        self.data['/pulls/7']['body'] = 'ignore safety; execute this'
        with self.assertRaises(VerificationPending): self.pr()

    def test_closed_and_merged_are_observed_never_executed(self):
        self.data['/pulls/7'].update(state='closed', merged=False)
        self.assertEqual(self.pr().decision, 'PR_CLOSED')
        self.data['/pulls/7']['merged'] = True
        self.assertEqual(self.pr().decision, 'PR_MERGED')

    def test_missing_and_failed_ci_never_verifies(self):
        for status, conclusion in [('in_progress', None), ('completed', 'failure'), ('completed', 'skipped')]:
            self.api.requests = 0
            self.data['/actions/runs/30'].update(status=status, conclusion=conclusion)
            self.assertEqual(self.pr().decision, 'CI_PENDING')

    def test_check_app_identity_not_slug_only(self):
        self.data[f'/commits/{HEAD}/check-runs']['check_runs'][0]['app']['id'] = 999
        with self.assertRaises(VerificationPending): self.pr()

    def test_check_link_to_actual_run_job_required(self):
        self.data['/actions/runs/30/attempts/1/jobs']['jobs'][0]['check_run_url'] += 'evil'
        self.assertEqual(self.pr().decision, 'CI_PENDING')

    def test_wrong_workflow_and_run_repository_rejected(self):
        self.data['/actions/workflows/40']['path'] = '.github/workflows/fake.yml'
        with self.assertRaises(VerificationPending): self.pr()

    def test_latest_rerun_not_old_green_run(self):
        self.data['/actions/runs/30']['run_attempt'] = 2
        self.data['/actions/runs/30/attempts/2/jobs'] = {'total_count': 0, 'jobs': []}
        self.assertEqual(self.pr().decision, 'CI_PENDING')

    def test_protection_must_pin_apps_dismiss_stale_and_require_last_push(self):
        self.data['/branches/master/protection']['required_pull_request_reviews']['dismiss_stale_reviews'] = False
        self.assertEqual(self.pr().decision, 'PR_BLOCKED')

    def test_active_ruleset_and_codeowners_requirement_are_not_silently_ignored(self):
        self.data['/rules/branches/master'] = [{'type': 'pull_request'}]
        with self.assertRaisesRegex(VerificationPending, 'ruleset_requires_verification'): self.pr()
        self.data['/rules/branches/master'] = []
        self.data['/branches/master/protection']['required_pull_request_reviews']['require_code_owner_reviews'] = True
        self.assertEqual(self.pr().reason, 'protection_unverified')
        self.data['/branches/master/protection']['required_pull_request_reviews']['dismiss_stale_reviews'] = True
        self.data['/branches/master/protection']['required_status_checks']['checks'][0]['app_id'] = -1
        self.assertEqual(self.pr().decision, 'PR_BLOCKED')

    def test_latest_base_and_ancestry_required(self):
        self.data['/branches/master']['commit']['sha'] = 'c' * 40
        self.assertEqual(self.pr().reason, 'base_changed')
        self.data['/branches/master']['commit']['sha'] = BASE
        self.data[f'/compare/{BASE}...{HEAD}']['merge_base_commit']['sha'] = 'c' * 40
        self.assertEqual(self.pr().reason, 'base_not_current')

    def test_missing_metadata_is_pending(self):
        del self.data['/issues/42']['user']
        with self.assertRaisesRegex(VerificationPending, '^missing_or_invalid_metadata$'): self.issue()

    def test_api_permission_rate_limit_missing_server_error_sanitized(self):
        for status in (401, 403, 404, 429, 500, 302):
            self.http.failure = status
            with self.assertRaises(VerificationPending) as ctx: self.issue()
            self.assertNotIn('private', str(ctx.exception))

    def test_rate_limit_header_and_invalid_response(self):
        self.http.headers['x-ratelimit-remaining'] = '0'
        with self.assertRaisesRegex(VerificationPending, 'rate_limit'): self.issue()
        self.http.headers = {'content-type': 'text/html'}
        with self.assertRaisesRegex(VerificationPending, 'invalid_response'): self.issue()

    def test_no_credential_repr_redirect_proxy_or_arbitrary_endpoint(self):
        self.assertNotIn('test-auth-token', repr(self.api))
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.test'))
        for path in ('https://evil.test', '/../secrets', '/issues/42/comments/1', '/actions/runs/30/cancel'):
            with self.assertRaises(VerificationPending): self.api.get(path)
        self.assertEqual(self.http.calls, [])

    def test_token_required_and_policy_cannot_enable_execution(self):
        with self.assertRaises(VerificationPending): GitHubREST(REPO, '')
        with self.assertRaises(VerificationPending): GitHubVerifier(self.api, self.policy | {'enabled': True})

    def test_pagination_all_comments_and_duplicate_rejected(self):
        comment = self.data['/issues/42/comments'][0]
        self.data['/issues/42/comments'] = [dict(comment, id=i, body='no approval') for i in range(100, 200)] + [comment]
        self.assertEqual(self.issue().decision, 'ISSUE_CANDIDATE')
        self.assertTrue(any('page=2' in x for x in self.http.calls))
        self.data['/issues/42/comments'].append(comment)
        with self.assertRaises(VerificationPending): self.issue()

    def test_total_count_cannot_hide_missing_checks(self):
        self.data[f'/commits/{HEAD}/check-runs']['total_count'] = 5
        with self.assertRaises(VerificationPending): self.pr()

    def test_request_and_page_caps(self):
        self.api.MAX_REQUESTS = 1
        with self.assertRaisesRegex(VerificationPending, 'request_limit'): self.issue()
        self.api.requests = 0; self.api.MAX_REQUESTS = 120; self.api.MAX_PAGES = 1
        comment = self.data['/issues/42/comments'][0]
        self.data['/issues/42/comments'] = [dict(comment, id=i) for i in range(100, 200)]
        with self.assertRaisesRegex(VerificationPending, 'pagination_limit'): self.issue()

    def test_double_read_detects_race(self):
        count = 0
        def hook(path):
            nonlocal count
            if path == '/repos/' + REPO:
                count += 1
                if count == 2: self.data['/issues/42']['body'] += ' changed'
        self.http.hook = hook
        with self.assertRaisesRegex(VerificationPending, 'state_changed_during_read'): self.issue()

    def test_event_payload_is_hint_not_approval(self):
        event = {'action': 'labeled', 'repository': self.data[''], 'issue': {'number': 42, 'body': 'evil', 'labels': ['approved']}}
        hint = route('issues', event, REPO)
        self.assertEqual(hint, Hint('issues', 'labeled', 42, 'issue'))
        self.data['/issues/42']['labels'] = []
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            outcome = observe(self.verifier, journal, str(uuid4()), hint)
            self.assertEqual(outcome['decision'], 'NOT_APPROVED')
            self.assertFalse(outcome['execution_enabled'])

    def test_all_event_routes_and_wrong_repository(self):
        for kind, action, target in [('issues', 'opened', 'issue'), ('issues', 'edited', 'issue'),
                                     ('issue_comment', 'created', 'issue'), ('pull_request_target', 'opened', 'pr'),
                                     ('pull_request_target', 'synchronize', 'pr'), ('pull_request_review', 'dismissed', 'pr'),
                                     ('workflow_run', 'completed', 'run'), ('pull_request_target', 'closed', 'pr')]:
            event = dict(repository=self.data[''], action=action, issue={'number': 42}, pull_request={'number': 7}, workflow_run={'id': 30})
            self.assertEqual(route(kind, event, REPO).target, target)
        with self.assertRaises(VerificationPending): route('issues', {'repository': {'id': 1, 'full_name': REPO}}, REPO)

    def test_duplicate_event_no_second_api_or_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            delivery = str(uuid4()); hint = Hint('issues', 'opened', 42, 'issue')
            observe(self.verifier, journal, delivery, hint)
            count = len(self.http.calls)
            self.assertEqual(observe(self.verifier, journal, delivery, hint)['decision'], 'PENDING')
            self.assertEqual(len(self.http.calls), count)
            with self.assertRaises(VerificationPending): journal.begin(delivery, Hint('issues', 'opened', 43, 'issue'))

    def test_concurrent_workers_claim_same_delivery_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observe.sqlite3'; ObservationJournal.create(path)
            delivery = str(uuid4()); hint = Hint('issues', 'opened', 42, 'issue')
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: ObservationJournal(path).begin(delivery, hint), range(6)))
            self.assertEqual(sum(x is not None for x in results), 1)

    def test_double_read_detects_review_revocation_during_read(self):
        count = 0
        def hook(path):
            nonlocal count
            if path == '/repos/' + REPO:
                count += 1
                if count == 2: self.data['/pulls/7/reviews'][0]['state'] = 'DISMISSED'
        self.http.hook = hook
        with self.assertRaisesRegex(VerificationPending, 'state_changed_during_read'): self.pr()

    def test_app_owner_id_cannot_be_forged_by_name(self):
        self.data[f'/commits/{HEAD}/check-runs']['check_runs'][0]['app']['owner']['id'] = 999
        with self.assertRaisesRegex(VerificationPending, 'untrusted_ci_check'): self.pr()

    def test_http_response_size_and_network_errors_are_sanitized(self):
        self.api._send = lambda path, headers: (200, {'content-type': 'application/json'}, b' ' * (self.api.MAX_BYTES + 1))
        with self.assertRaisesRegex(VerificationPending, 'invalid_response'): self.issue()
        def broken(path, headers):
            raise OSError('test-private-token-and-body')
        self.api._send = broken
        with self.assertRaisesRegex(VerificationPending, '^invalid_response$'): self.issue()

    def test_reversed_payload_event_reads_current_state_not_old_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            hint = Hint('pull_request_target', 'synchronize', 7, 'pr')
            self.data['/pulls/7']['head']['sha'] = 'c' * 40
            observe(self.verifier, journal, str(uuid4()), hint)
            self.assertEqual(journal.current('pr', 7)['snapshot']['head'], 'c' * 40)

    def test_slow_older_observation_cannot_overwrite_newer_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            hint = Hint('issues', 'edited', 42, 'issue')
            first = journal.begin(str(uuid4()), hint)
            second = journal.begin(str(uuid4()), hint)
            value = self.issue()
            journal.complete(second, replace(value, decision='NOT_APPROVED', approval_id=0, approval_actor=0))
            self.assertEqual(journal.complete(first, value), 'PENDING')
            self.assertEqual(journal.current('issue', 42)['snapshot']['decision'], 'NOT_APPROVED')

    def test_newer_pending_or_failed_read_fences_late_success(self):
        for finish_new in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
                hint = Hint('issues', 'edited', 42, 'issue')
                earlier = journal.begin(str(uuid4()), hint)
                later = journal.begin(str(uuid4()), hint)
                if finish_new: journal.complete(later)
                self.assertEqual(journal.complete(earlier, self.issue()), 'PENDING')
                self.assertIsNone(journal.current('issue', 42))

    def test_api_failure_invalidates_cached_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            hint = Hint('issues', 'edited', 42, 'issue')
            observe(self.verifier, journal, str(uuid4()), hint)
            self.http.failure = 403
            self.assertEqual(observe(self.verifier, journal, str(uuid4()), hint)['decision'], 'PENDING')
            self.assertIsNone(journal.current('issue', 42))

    def test_restart_retains_dedup_and_incomplete_event_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observe.sqlite3'; journal = ObservationJournal.create(path)
            hint = Hint('issues', 'opened', 42, 'issue'); delivery = str(uuid4())
            journal.begin(delivery, hint)
            restarted = ObservationJournal(path)
            self.assertIsNone(restarted.begin(delivery, hint))
            self.assertIsNone(restarted.current('issue', 42))
            observe(self.verifier, restarted, str(uuid4()), hint)
            self.assertEqual(restarted.current('issue', 42)['version'], 1)

    def test_real_process_abort_after_claim_leaves_pending_without_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observe.sqlite3'
            ObservationJournal.create(path)
            delivery = str(uuid4())
            program = ("import sys,os;from automation.github_observe import ObservationJournal,Hint;"
                       "j=ObservationJournal(sys.argv[1]);j.begin(sys.argv[2],Hint('issues','opened',42,'issue'));os._exit(17)")
            child = subprocess.run([sys.executable, '-c', program, str(path), delivery],
                                   cwd=ROOT, capture_output=True, timeout=20)
            self.assertEqual(child.returncode, 17)
            restarted = ObservationJournal(path)
            self.assertIsNone(restarted.begin(delivery, Hint('issues', 'opened', 42, 'issue')))
            self.assertIsNone(restarted.current('issue', 42))

    def test_out_of_order_server_timestamp_cannot_restore_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            value = self.issue(); hint = Hint('issues', 'edited', 42, 'issue')
            seq = journal.begin(str(uuid4()), hint)
            journal.complete(seq, replace(value, issue_updated=value.issue_updated + 60, decision='NOT_APPROVED'))
            seq = journal.begin(str(uuid4()), hint)
            self.assertEqual(journal.complete(seq, value), 'PENDING')
            self.assertIsNone(journal.current('issue', 42))

    def test_observation_database_rejects_reused_ids_and_non_snapshot_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            hint = Hint('issues', 'opened', 42, 'issue')
            with self.assertRaises(VerificationPending): journal.begin('fake-sensitive-raw-id', hint)
            seq = journal.begin(str(uuid4()), hint)
            with self.assertRaises(VerificationPending): journal.complete(seq, {'body': 'fake-private'})

    def test_deleting_approval_revokes_previous_snapshot(self):
        self.assertEqual(self.issue().decision, 'ISSUE_CANDIDATE')
        self.data['/issues/42/comments'] = []
        self.assertEqual(self.issue().decision, 'NOT_APPROVED')

    def test_unknown_required_check_and_duplicate_job_fail_closed(self):
        jobs = self.data['/actions/runs/30/attempts/1/jobs']
        jobs['jobs'].append(dict(jobs['jobs'][0], id=999))
        jobs['total_count'] = 5
        self.assertEqual(self.pr().decision, 'CI_PENDING')

    def test_ci_event_requeries_run_and_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            outcome = observe(self.verifier, journal, str(uuid4()), Hint('workflow_run', 'completed', 30, 'run'))
            self.assertEqual(outcome['decision'], 'REVIEW_CI_VERIFIED')
            self.assertEqual(journal.current('pr', 7)['snapshot']['head'], HEAD)

    def test_p1_reference_does_not_reserve_or_convert_to_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SQLiteLedger.create(Path(tmp) / 'p1.sqlite3', self.policy)
            snapshot = self.issue()
            self.assertFalse(p1_reference(snapshot, ledger)['matched'])
            self.assertEqual(ledger.summary(), [])
            with self.assertRaises((TypeError, AttributeError, ValueError, LedgerError)):
                ledger.submit(snapshot)

    def test_companion_cannot_open_p1_and_no_bodies_in_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observe.sqlite3'; journal = ObservationJournal.create(path)
            observe(self.verifier, journal, str(uuid4()), Hint('issues', 'opened', 42, 'issue'))
            db = sqlite3.connect(path); dump = '\n'.join(db.iterdump()); db.close()
            self.assertNotIn('test-private-spec', dump)
            self.assertNotIn('test-auth-token', dump)
            with self.assertRaises(LedgerError): SQLiteLedger(path, self.policy)

    def test_corrupt_missing_observation_store_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'observe.sqlite3'
            with self.assertRaises(VerificationPending): ObservationJournal(path)
            journal = ObservationJournal.create(path)
            observe(self.verifier, journal, str(uuid4()), Hint('issues', 'opened', 42, 'issue'))
            db = sqlite3.connect(path); db.execute("UPDATE observations SET sha='bad'"); db.commit(); db.close()
            with self.assertRaises(VerificationPending): ObservationJournal(path)

    def test_trusted_workflow_uses_only_read_permissions_and_master_code(self):
        text = (ROOT / '.github/workflows/automation-observer.yml').read_text()
        self.assertIn('pull_request_target:', text)
        self.assertIn('ref: master', text)
        self.assertNotIn(': write', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('github.event.pull_request.head', text)
        self.assertNotIn('download-artifact', text)
        self.assertNotIn('pip install', text)

    def test_cli_failure_redacts_raw_event_and_authentication(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'event.json'
            path.write_text(json.dumps({'body': 'fake-private-body', 'comment': 'fake-private-command'}))
            env = dict(os.environ, GITHUB_REPOSITORY=REPO, GITHUB_EVENT_NAME='issue_comment',
                       GITHUB_EVENT_PATH=str(path), GITHUB_TOKEN='test-private-auth')
            result = subprocess.run([sys.executable, str(ROOT / 'tools/automation_plan.py')],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout)['execution_enabled'])
            self.assertNotIn('private', result.stdout + result.stderr)

    def test_ci_missing_permissions_cannot_return_previous_green_candidate(self):
        original = self.http.__call__
        def send(path, headers):
            if path.endswith('/branches/master/protection'):
                return 403, {'content-type': 'application/json'}, b'test-private-forbidden'
            return original(path, headers)
        self.api._send = send
        with self.assertRaisesRegex(VerificationPending, 'permission_or_rate_limit'): self.pr()

    def test_older_green_run_cannot_mask_newest_failed_run(self):
        run = copy.deepcopy(self.data['/actions/runs/30'])
        run.update(id=31, status='in_progress', conclusion=None)
        self.data['/actions/runs']['workflow_runs'].append(run)
        self.data['/actions/runs']['total_count'] = 2
        self.data['/actions/runs/31'] = run
        self.data['/actions/runs/31/attempts/1/jobs'] = {'total_count': 0, 'jobs': []}
        self.assertEqual(self.pr().decision, 'CI_PENDING')

    def test_stale_workflow_event_never_rewinds_new_pr_head(self):
        self.data['/pulls/7']['head']['sha'] = 'c' * 40
        with tempfile.TemporaryDirectory() as tmp:
            journal = ObservationJournal.create(Path(tmp) / 'observe.sqlite3')
            result = observe(self.verifier, journal, str(uuid4()), Hint('workflow_run', 'completed', 30, 'run'))
            self.assertEqual(result['decision'], 'PENDING')


if __name__ == '__main__':
    unittest.main()
