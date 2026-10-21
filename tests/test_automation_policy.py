from dataclasses import asdict, replace
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from automation.policy import Ledger, allowed_paths, approved, finish, plan, reserve, spec_hash

ROOT = Path(__file__).resolve().parents[1]


def policy():
    value = json.loads((ROOT / '.github/automation/policy.json').read_text(encoding='utf-8'))
    return dict(value, enabled=True, builder_ids=[1001], independent_reviewer_ids=[2002])


def snapshot():
    config = policy()
    issue = {'number': 42, 'state': 'open', 'repository': config['repository'], 'body': 'approved offline fix',
             'labels': [config['approved_label']], 'approval_comments': [
                 {'author_id': 45135977, 'body': '/autodev approve ' + spec_hash('approved offline fix')}]}
    head = 'a' * 40
    pr = {'state': 'open', 'draft': False, 'repository': config['repository'],
          'head_repository': config['repository'], 'author_id': 1001, 'branch': 'autodev/issue-42',
          'base': 'master', 'head_sha': head, 'spec_sha256': spec_hash(issue['body']),
          'changed_paths': ['tests/test_example.py', 'backtesting/example.py']}
    return {'issue': issue, 'ledger': asdict(Ledger()), 'pr': pr,
            'reviews': [{'id': 10, 'author_id': 2002, 'commit_id': head, 'state': 'APPROVED'}],
            'checks': [{'name': name, 'head_sha': head, 'status': 'completed', 'conclusion': 'success',
                        'app_slug': 'github-actions', 'workflow_path': config['trusted_ci_workflow']}
                       for name in config['required_checks']],
            'unresolved_threads': False, 'branch_protection_verified': True, 'base_up_to_date': True}


class AutomationPolicyTests(unittest.TestCase):
    def test_default_configuration_disabled_and_identities_unconfigured(self):
        config = json.loads((ROOT / '.github/automation/policy.json').read_text(encoding='utf-8'))
        self.assertFalse(config['enabled'])
        self.assertEqual(config['builder_ids'], [])
        self.assertEqual(config['independent_reviewer_ids'], [])
        self.assertEqual(plan(snapshot(), config)['reason'], 'automation_disabled')

    def test_approval_binds_exact_spec_and_trusted_maintainer(self):
        issue = snapshot()['issue']
        self.assertTrue(approved(issue, policy()))
        for change in ({'body': issue['body'] + ' '}, {'labels': []}, {'state': 'closed'},
                       {'is_pull_request': True}, {'repository': 'attacker/fork'},
                       {'approval_comments': [{'author_id': 999, 'body': issue['approval_comments'][0]['body']}]}):
            with self.subTest(change=change):
                self.assertFalse(approved(issue | change, policy()))

    def test_only_approved_issue_is_implementation_candidate(self):
        value = snapshot()
        value['pr'] = None
        self.assertEqual(plan(value, policy())['action'], 'implementation_candidate')
        value['issue']['labels'] = []
        self.assertEqual(plan(value, policy())['action'], 'blocked')

    def test_missing_ledger_and_inflight_halt_limits_fail_closed(self):
        value = snapshot()
        del value['ledger']
        self.assertEqual(plan(value, policy())['reason'], 'ledger_unverified')
        for ledger in (Ledger(in_flight=True), Ledger(halted=True), Ledger(failures=2)):
            value['ledger'] = asdict(ledger)
            self.assertEqual(plan(value, policy())['action'], 'blocked')

    def test_third_attempt_can_merge_candidate_but_cannot_start_fourth(self):
        value = snapshot()
        value['ledger'] = asdict(Ledger(attempts=3))
        self.assertEqual(plan(value, policy())['action'], 'external_merge_gate_candidate')
        value['reviews'][0]['state'] = 'CHANGES_REQUESTED'
        self.assertEqual(plan(value, policy())['reason'], 'attempts_exhausted')
        value['pr'] = None
        self.assertEqual(plan(value, policy())['reason'], 'attempts_exhausted')

    def test_reservation_excludes_duplicate_and_concurrent_run(self):
        first = reserve(Ledger(), policy(), event_key='build:42:spec', cents=500)
        self.assertEqual(first.attempts, 1)
        with self.assertRaises(ValueError):
            reserve(first, policy(), event_key='other', cents=500)
        done = finish(first, policy(), success=True)
        with self.assertRaises(ValueError):
            reserve(done, policy(), event_key='build:42:spec', cents=500)
        self.assertEqual(done.reserved_cents, 500)

    def test_reservation_integer_limits_and_no_silent_refund(self):
        for cents in (0, -1, True, 1.1, 501):
            with self.assertRaises(ValueError):
                reserve(Ledger(), policy(), event_key='task', cents=cents)
        for ledger in (Ledger(attempts=3), Ledger(reserved_cents=1400), Ledger(day_reserved_cents=4900)):
            with self.assertRaises(ValueError):
                reserve(ledger, policy(), event_key='task', cents=500)
        with self.assertRaises(ValueError):
            Ledger(attempts=-1)
        self.assertIsInstance(Ledger(seen=['a']).seen, tuple)

    def test_two_consecutive_failures_halt_including_timeouts(self):
        state = Ledger()
        for i in range(2):
            state = finish(reserve(state, policy(), event_key=str(i), cents=500), policy(), success=False)
        self.assertTrue(state.halted)
        self.assertEqual(state.reserved_cents, 1000)
        with self.assertRaises(ValueError):
            reserve(state, policy(), event_key='third', cents=500)
        with self.assertRaises(ValueError):
            finish(state, policy(), success=True)

    def test_latest_trusted_review_wins_and_stale_commit_never_approves(self):
        value = snapshot()
        value['reviews'].append(dict(value['reviews'][0], id=11, state='CHANGES_REQUESTED'))
        self.assertEqual(plan(value, policy())['action'], 'fix_candidate')
        value['reviews'][1]['commit_id'] = 'b' * 40
        self.assertEqual(plan(value, policy())['reason'], 'stale_review')

    def test_fix_event_idempotency_and_untrusted_review_ignored(self):
        value = snapshot()
        value['reviews'][0]['state'] = 'CHANGES_REQUESTED'
        candidate = plan(value, policy())
        value['ledger']['seen'] = [candidate['event_key']]
        self.assertEqual(plan(value, policy())['reason'], 'duplicate_feedback')
        value['reviews'][0]['author_id'] = 999
        self.assertEqual(plan(value, policy())['reason'], 'independent_reviewer_required')

    def test_builder_cannot_review_own_changes(self):
        value = snapshot()
        value['reviews'][0]['author_id'] = 1001
        config = policy() | {'independent_reviewer_ids': [1001]}
        self.assertEqual(plan(value, config)['reason'], 'independent_reviewer_required')

    def test_fork_stale_spec_wrong_branch_draft_closed_pr_blocked(self):
        for change in ({'head_repository': 'attacker/fork'}, {'head_sha': 'bad'},
                       {'spec_sha256': 'old'}, {'branch': 'master'}, {'author_id': 999},
                       {'draft': True}, {'state': 'closed'}, {'base': 'other'}):
            value = snapshot()
            value['pr'].update(change)
            with self.subTest(change=change):
                self.assertEqual(plan(value, policy())['action'], 'blocked')

    def test_protected_paths_and_traversal_blocked(self):
        for path in ('config.py', 'trading/execution.py', '.github/workflows/tests.yml', 'AGENTS.md',
                     'tests/../config.py', 'tests\\bad.py', '/tests/a.py', 'strategies/signals.py', 'README.md'):
            self.assertFalse(allowed_paths([path], policy()), path)
        self.assertFalse(allowed_paths(['tests/a.py'] * 21, policy()))
        self.assertFalse(allowed_paths([], policy()))
        self.assertTrue(allowed_paths(['docs/a.md', 'backtesting/a.py'], policy()))

    def test_ci_pending_failed_skipped_old_or_spoofed_never_allows_merge(self):
        for change in ({'status': 'in_progress'}, {'conclusion': 'failure'}, {'conclusion': 'skipped'},
                       {'head_sha': 'b' * 40}, {'app_slug': 'attacker'}, {'workflow_path': 'fake.yml'}):
            value = snapshot()
            value['checks'][0].update(change)
            with self.subTest(change=change):
                self.assertNotEqual(plan(value, policy())['action'], 'external_merge_gate_candidate')
        value = snapshot()
        value['checks'].append(copy.deepcopy(value['checks'][0]))
        self.assertEqual(plan(value, policy())['reason'], 'missing_or_ambiguous_check')

    def test_unverified_threads_branch_protection_or_old_base_blocks(self):
        for field, changed in (('unresolved_threads', True), ('unresolved_threads', None),
                               ('branch_protection_verified', False), ('base_up_to_date', False)):
            value = snapshot()
            value[field] = changed
            self.assertNotEqual(plan(value, policy())['action'], 'external_merge_gate_candidate')

    def test_gate_is_a_nonexecuting_candidate_not_a_merge(self):
        decision = plan(snapshot(), policy())
        self.assertEqual(decision['action'], 'external_merge_gate_candidate')
        self.assertFalse(decision['execution_enabled'])

    def test_observer_does_not_print_bodies_or_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'event.json'
            path.write_text(json.dumps({'issue': {'number': 42, 'body': 'fake-sensitive-body'},
                                        'comment': {'body': 'fake-sensitive-comment'}}), encoding='utf-8')
            env = dict(os.environ, GITHUB_EVENT_PATH=str(path), GITHUB_EVENT_NAME='issue_comment',
                       GITHUB_REPOSITORY='')  # Explicit offline context, including on Actions.
            result = subprocess.run([sys.executable, str(ROOT / 'tools/automation_plan.py')],
                                    env=env, capture_output=True, text=True, check=True)
            self.assertNotIn('fake-sensitive', result.stdout)
            self.assertIn('automation_disabled', result.stdout)

    def test_inert_action_template_and_secretless_read_only_observer(self):
        template = (ROOT / '.github/automation/codex-worker.yml.example').read_text(encoding='utf-8')
        self.assertIn('if: ${{ false }}', template)
        self.assertIn('safety-strategy: drop-sudo', template)
        self.assertIn('permission-profile:', template)
        self.assertNotIn('contents: write', template)
        workflow = (ROOT / '.github/workflows/automation-observer.yml').read_text(encoding='utf-8')
        self.assertNotIn('secrets.', workflow)
        self.assertIn('pull_request_target:', workflow)
        self.assertNotIn('github.event.pull_request.head', workflow)
        self.assertNotIn('download-artifact', workflow)
        self.assertIn('checks: read', workflow)
        self.assertNotIn('contents: write', workflow)
        self.assertIn('ref: master', workflow)


if __name__ == '__main__':
    unittest.main()
