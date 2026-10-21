"""Authenticated REST facts -> metadata-only advisory snapshots, NEVER P1 Events."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from .events import canonical, digest
from .github_read import GitHubREST, VerificationPending, positive
from .policy import allowed_paths, spec_hash

DECISIONS = ('PENDING', 'NOT_APPROVED', 'ISSUE_CANDIDATE', 'PR_CLOSED', 'PR_MERGED',
             'PR_BLOCKED', 'REVIEW_PENDING', 'CHANGES_REQUESTED', 'CI_PENDING', 'REVIEW_CI_VERIFIED')


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise VerificationPending('invalid_timestamp')
    return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class Snapshot:
    repository_id: int
    issue: int
    spec_sha256: str
    issue_author: int
    approval_id: int
    approval_actor: int
    issue_updated: int
    pr: int = 0
    pr_author: int = 0
    head: str = ''
    base: str = ''
    pr_updated: int = 0
    reviews: tuple = ()
    checks: tuple = ()
    paths_sha256: str = ''
    protection_sha256: str = ''
    decision: str = 'PENDING'
    reason: str = 'verification_pending'

    def __post_init__(self):
        for value in (self.repository_id, self.issue, self.issue_author):
            positive(value)
        digest(self.spec_sha256, 64)
        for value in (self.approval_id, self.approval_actor, self.issue_updated, self.pr,
                      self.pr_author, self.pr_updated):
            if type(value) is not int or value < 0:
                raise ValueError('Invalid snapshot metadata')
        for value, size in ((self.head, 40), (self.base, 40), (self.paths_sha256, 64), (self.protection_sha256, 64)):
            if value:
                digest(value, size)
        if self.decision not in DECISIONS or not re.fullmatch('[a-z_]{1,64}', self.reason):
            raise ValueError('Invalid snapshot decision')
        for row in self.reviews:
            if len(row) != 4 or row[3] not in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED', 'COMMENTED'):
                raise ValueError('Invalid review metadata')
            positive(row[0]); positive(row[1]); digest(row[2], 40)
        for row in self.checks:
            if len(row) != 5 or type(row[4]) is not bool:
                raise ValueError('Invalid check metadata')
            for value in row[:4]:
                positive(value)

    def metadata(self):
        return asdict(self)


class GitHubVerifier:
    def __init__(self, api, policy):
        if not isinstance(api, GitHubREST) or policy.get('enabled') is not False:
            raise VerificationPending('readonly_policy_required')
        self.api = api
        self.policy = json.loads(canonical(policy))
        if self.api.repository != policy['repository']:
            raise VerificationPending('repository_mismatch')

    def _repository(self):
        repo = self.api.get()
        if (repo['full_name'] != self.policy['repository'] or repo['default_branch'] != self.policy['base_branch']
                or repo['archived'] is not False or repo['disabled'] is not False):
            raise VerificationPending('repository_mismatch')
        if repo['id'] != 888367411:
            raise VerificationPending('repository_id_mismatch')
        return repo

    def _issue(self, number):
        positive(number)
        issue = self.api.get(f'/issues/{number}')
        if issue['number'] != number or 'pull_request' in issue or issue['repository_url'] != 'https://api.github.com/repos/' + self.policy['repository']:
            raise VerificationPending('issue_mismatch')
        positive(issue['user']['id'])
        if type(issue['body']) is not str or len(issue['body'].encode()) > 262144:
            raise VerificationPending('invalid_issue')
        timestamp(issue['updated_at'])
        comments = self.api.pages(f'/issues/{number}/comments')
        current = spec_hash(issue['body'])
        approvals = []
        for comment in comments:
            if comment['issue_url'] != issue['url']:
                raise VerificationPending('comment_mismatch')
            actor = positive(comment['user']['id'])
            if (actor in self.policy['maintainer_ids'] and comment['user']['type'] == 'User'
                    and comment['body'] == f'/autodev approve {current}'):
                # An edited approval is not an immutable authorization record.
                if (comment['created_at'] == comment['updated_at']
                        and timestamp(comment['created_at']) >= timestamp(issue['updated_at'])):
                    timestamp(comment['created_at'])
                    approvals.append(comment)
        ok = (issue['state'] == 'open' and any(x['name'] == self.policy['approved_label'] for x in issue['labels'])
              and bool(approvals))
        approval = max(approvals, key=lambda x: x['id']) if ok else None
        return issue, current, approval

    def _ci(self, pr, head):
        checks = self.api.pages(f'/commits/{head}/check-runs?filter=all', 'check_runs')
        runs = self.api.pages(f'/actions/runs?head_sha={head}&event=pull_request', 'workflow_runs')
        trusted = [r for r in runs if r['path'] == self.policy['trusted_ci_workflow']]
        if not trusted:
            return (), False
        # Latest run AND latest rerun attempt only; old green runs never mask a rerun.
        latest = max(trusted, key=lambda x: positive(x['id']))
        run = self.api.get(f"/actions/runs/{positive(latest['id'])}")
        workflow = self.api.get(f"/actions/workflows/{positive(run['workflow_id'])}")
        repo = self.policy['repository']
        if (run['id'] != latest['id'] or run['head_sha'] != head or run['event'] != 'pull_request'
                or run['repository']['full_name'] != repo or run['head_repository']['full_name'] != repo
                or run['head_branch'] != pr['head']['ref'] or run['path'] != self.policy['trusted_ci_workflow']
                or workflow['id'] != run['workflow_id'] or workflow['path'] != run['path'] or workflow['state'] != 'active'
                or not any(x['number'] == pr['number'] and x['head']['sha'] == head
                           and x['base']['repo']['id'] == pr['base']['repo']['id'] for x in run['pull_requests'])):
            raise VerificationPending('untrusted_ci_run')
        attempt = positive(run['run_attempt'])
        jobs = self.api.pages(f"/actions/runs/{run['id']}/attempts/{attempt}/jobs", 'jobs')
        facts = []
        complete = run['status'] == 'completed' and run['conclusion'] == 'success'
        for name in self.policy['required_checks']:
            matching = [j for j in jobs if j['name'] == name]
            if len(matching) != 1:
                return tuple(facts), False
            job = matching[0]
            url = 'https://api.github.com/repos/' + repo + '/check-runs/'
            matches = [c for c in checks if c['url'] == job['check_run_url']]
            if len(matches) != 1:
                return tuple(facts), False
            check = matches[0]
            app = check['app']
            if (check['url'] != url + str(positive(check['id'])) or check['name'] != name or check['head_sha'] != head
                    or app['id'] != 15368 or app['slug'] != 'github-actions' or app['owner']['id'] != 9919
                    or job['run_id'] != run['id'] or job['head_sha'] != head):
                raise VerificationPending('untrusted_ci_check')
            passed = (job['status'] == check['status'] == 'completed'
                      and job['conclusion'] == check['conclusion'] == 'success')
            facts.append((positive(check['id']), positive(run['id']), attempt, positive(job['id']), passed))
            complete = complete and passed
        return tuple(facts), complete

    def _read(self, issue_number=None, pr_number=None):
        repo = self._repository()
        pr = None
        if pr_number is not None:
            positive(pr_number)
            pr = self.api.get(f'/pulls/{pr_number}')
            # Strict machine metadata, never execute or trust a PR body as approval.
            markers = re.findall(r'^/autodev issue ([1-9][0-9]*) spec ([0-9a-f]{64})$', pr['body'] or '', re.M)
            if len(markers) != 1:
                raise VerificationPending('issue_link_required')
            linked, spec = markers[0]
            if issue_number is not None and issue_number != int(linked):
                raise VerificationPending('issue_link_mismatch')
            issue_number = int(linked)
        issue, spec_hash_value, approval = self._issue(issue_number)
        fields = dict(repository_id=repo['id'], issue=issue_number, spec_sha256=spec_hash_value,
                      issue_author=issue['user']['id'], approval_id=approval['id'] if approval else 0,
                      approval_actor=approval['user']['id'] if approval else 0,
                      issue_updated=timestamp(issue['updated_at']))
        if not pr:
            return Snapshot(**fields, decision='ISSUE_CANDIDATE' if approval else 'NOT_APPROVED',
                            reason='current_approval_verified' if approval else 'no_current_approval')
        head = digest(pr['head']['sha'], 40)
        base = digest(pr['base']['sha'], 40)
        if pr['number'] != pr_number or pr['base']['repo']['id'] != repo['id']:
            raise VerificationPending('pr_mismatch')
        fields.update(pr=pr_number, pr_author=positive(pr['user']['id']), head=head, base=base,
                      pr_updated=timestamp(pr['updated_at']))

        def result(decision, reason):
            return Snapshot(**fields, decision=decision, reason=reason)

        if pr['state'] == 'closed':
            if type(pr['merged']) is not bool:
                raise VerificationPending('invalid_pr_state')
            return result('PR_MERGED' if pr['merged'] else 'PR_CLOSED', 'terminal_pr_observed')
        if not approval or spec != spec_hash_value:
            return result('NOT_APPROVED', 'no_current_approval')
        if (pr['state'] != 'open' or pr['draft'] is not False or pr['head']['repo']['id'] != repo['id']
                or pr['head']['repo']['full_name'] != repo['full_name'] or pr['base']['ref'] != repo['default_branch']
                or pr['head']['ref'] != f'autodev/issue-{issue_number}' or pr['user']['id'] not in self.policy['builder_ids']):
            return result('PR_BLOCKED', 'pr_identity_or_branch_unverified')
        files = self.api.pages(f'/pulls/{pr_number}/files', identity='filename')
        paths = [x['filename'] for x in files]
        paths.extend(x['previous_filename'] for x in files if x['status'] == 'renamed')
        if len(files) != pr['changed_files'] or not allowed_paths(paths, self.policy):
            return result('PR_BLOCKED', 'protected_or_incomplete_paths')
        fields['paths_sha256'] = spec_hash(canonical(sorted(paths)))
        reviews = self.api.pages(f'/pulls/{pr_number}/reviews')
        opinions = {}
        normalized = []
        for review in sorted(reviews, key=lambda r: positive(r['id'])):
            actor = positive(review['user']['id'])
            state = review['state']
            if state == 'PENDING':
                continue
            commit = digest(review['commit_id'], 40)
            normalized.append((review['id'], actor, commit, state))
            if state != 'COMMENTED':
                opinions[actor] = review
        fields['reviews'] = tuple(normalized)
        independent = [r for actor, r in opinions.items() if actor in self.policy['independent_reviewer_ids']]
        if any(r['user']['id'] == pr['user']['id'] for r in independent):
            return result('PR_BLOCKED', 'self_review')
        if any(r['state'] == 'CHANGES_REQUESTED' for r in opinions.values()):
            return result('CHANGES_REQUESTED', 'review_changes_requested')
        if not independent or any(r['state'] != 'APPROVED' or r['commit_id'] != head for r in independent):
            return result('REVIEW_PENDING', 'current_independent_approval_required')
        protection = self.api.get('/branches/' + repo['default_branch'] + '/protection')
        # Unknown active rulesets are not silently reduced to classic protection.
        active_rules = self.api.get('/rules/branches/' + repo['default_branch'])
        if type(active_rules) is not list or active_rules:
            raise VerificationPending('ruleset_requires_verification')
        rules = protection['required_pull_request_reviews']
        required = protection['required_status_checks']
        fields['protection_sha256'] = spec_hash(canonical(protection))
        expected = set(self.policy['required_checks'])
        pinned = {x['context'] for x in required['checks'] if x['app_id'] == 15368}
        if (rules['dismiss_stale_reviews'] is not True or rules['require_last_push_approval'] is not True
                or type(rules['required_approving_review_count']) is not int
                or rules['required_approving_review_count'] < 1 or len(independent) < rules['required_approving_review_count']
                or rules['require_code_owner_reviews'] is not False
                or any(rules.get('bypass_pull_request_allowances', {}).get(k, []) for k in ('users', 'teams', 'apps'))
                or protection['enforce_admins']['enabled'] is not True or required['strict'] is not True
                or not expected.issubset(pinned) or set(required['contexts']) != expected
                or pinned != expected):
            return result('PR_BLOCKED', 'protection_unverified')
        branch = self.api.get('/branches/' + repo['default_branch'])
        if branch['protected'] is not True or branch['commit']['sha'] != base:
            return result('PR_BLOCKED', 'base_changed')
        compare = self.api.get(f'/compare/{base}...{head}')
        if compare['merge_base_commit']['sha'] != base or compare['status'] not in ('ahead', 'identical'):
            return result('PR_BLOCKED', 'base_not_current')
        fields['checks'], passed = self._ci(pr, head)
        if not passed:
            return result('CI_PENDING', 'ci_unverified_or_unsuccessful')
        # REST cannot establish all resolved review threads or atomic final merge state.
        return result('REVIEW_CI_VERIFIED', 'advisory_only_threads_unverified')

    def verify(self, *, issue=None, pr=None):
        try:
            # Re-read every fact, not only webhook timestamps; bound both passes.
            first = self._read(issue, pr)
            second = self._read(issue, pr)
            if first != second:
                raise VerificationPending('state_changed_during_read')
            return second
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('missing_or_invalid_metadata') from None
