"""P6 advisory evidence, never an activation capability or P2 -> worker bridge.

Uses only existing official gh authentication. No tokens, login, model calls,
settings changes, arbitrary GraphQL, shell commands, or GitHub write operations.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from .github_graphql import ReadOnlyGraphQL, QUERIES
from .github_read import GitHubREST, VerificationPending
from .github_verify import GitHubVerifier
from .observer_diagnostics import diagnostic
from .review_gate import ReviewGate

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'sasumpi123/auto_trade'
REPOSITORY_ID = 888367411
STATES = ('READY', 'PENDING', 'BLOCKED', 'USER_ACTION_REQUIRED')


class GhReadOnly:
    """Bounded fixed GET routes and P4 read queries. Discards all error bodies.

    Injected GitHubREST/GraphQL transport ignores their placeholder auth headers;
    only gh's existing official authentication is used. Never reads a token.
    """
    def __init__(self, run=None):
        self.run = run or subprocess.run
        self.requests = 0

    def _call(self, arguments, payload=None):
        self.requests += 1
        if self.requests > 120:
            raise VerificationPending('request_limit')
        try:
            response = self.run(['gh', 'api', '--include', *arguments], input=payload,
                                capture_output=True, text=True, timeout=15, check=False)
            if len(response.stdout.encode()) > 2_000_000:
                raise VerificationPending('invalid_response')
            sections = re.split(r'\r?\n\r?\n', response.stdout, maxsplit=1)
            match = re.match(r'HTTP/[0-9.]+ ([0-9]{3})\b', sections[0])
            if match and len(sections) == 2:
                status = int(match[1])
                headers = {}
                for line in sections[0].splitlines()[1:]:
                    key, separator, value = line.partition(':')
                    if separator and key.lower() in ('content-type', 'x-ratelimit-remaining'):
                        headers[key.lower()] = value.strip()
                return status, headers, sections[1].encode() if status == 200 else b''
            match = re.search(r'HTTP ([0-9]{3})\b', response.stderr)
            if match:
                return int(match[1]), {}, b''
            raise VerificationPending('api_unavailable')
        except VerificationPending:
            raise
        except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
            raise VerificationPending('api_unavailable') from None

    def rest(self, path, headers=None):
        prefix = '/repos/' + REPOSITORY
        if not path.startswith(prefix):
            raise VerificationPending('endpoint_not_allowed')
        suffix = path[len(prefix):]
        # Use P2's closed route language before delegating to gh.
        if suffix not in ('/actions/permissions/workflow', '/installation'):
            validator = GitHubREST(REPOSITORY, 'not-a-credential', send=lambda p,h: (200, {'content-type':'application/json'}, b'{}'))
            validator.get(suffix)
        return self._call(['--method', 'GET', path])

    def graphql(self, body, headers=None):
        try:
            value = json.loads(body)
            if set(value) != {'query', 'variables'} or value['query'] not in QUERIES.values():
                raise ValueError()
            variables = value['variables']
            if type(variables) is not dict:
                raise ValueError()
            if value['query'] != QUERIES['user'] and (variables.get('owner'), variables.get('name')) != tuple(REPOSITORY.split('/')):
                raise ValueError()
        except (ValueError, TypeError):
            raise VerificationPending('graphql_operation_forbidden') from None
        return self._call(['--method', 'POST', 'graphql', '--input', '-'], json.dumps(value))


def _row(check, state, reason, **facts):
    assert state in STATES
    return dict(check=check, status=state, reason=reason, **facts)


def cost_readiness(rate_limits=None):
    """Partial official rate-limit payload is advisory, never zero-cost proof.

    No CLI/model/server startup here. Fixture or future authenticated SDK caller
    may pass account/rateLimits/read's result; provenance is not asserted here.
    """
    rows = []
    try:
        if type(rate_limits) is not dict:
            raise ValueError()
        bucket = rate_limits['rateLimits']
        windows = [bucket.get('primary'), bucket.get('secondary')]
        present = [w for w in windows if w is not None]
        if not present or any(type(w) is not dict or type(w.get('usedPercent')) not in (int, float)
                              or not 0 <= w['usedPercent'] <= 100 for w in present):
            raise ValueError()
        exhausted = any(w['usedPercent'] >= 100 for w in present)
        rows.append(_row('included_quota', 'BLOCKED' if exhausted else 'PENDING',
                         'QUOTA_EXHAUSTED' if exhausted else 'ADVISORY_SNAPSHOT_NOT_FRESHNESS_OR_BILLING_PROOF'))
    except (KeyError, TypeError, ValueError):
        rows.append(_row('included_quota', 'PENDING', 'OFFICIAL_ACCOUNT_RATE_LIMITS_NOT_OBSERVED'))
    rows.extend([
        _row('purchased_credit_consumption', 'BLOCKED', 'NO_VERIFIED_INCLUDED_ONLY_ENFORCEMENT'),
        _row('auto_recharge', 'USER_ACTION_REQUIRED', 'ACCOUNT_BILLING_CONTROLS_NOT_OBSERVED'),
        _row('paid_reset', 'BLOCKED', 'RESET_OR_EXPANSION_NOT_PERMITTED'),
    ])
    return rows


def review_readiness(reviews, *, author, head, policy):
    """Returns only closed P4 results, immutable IDs and review IDs, never body."""
    gate = ReviewGate(policy).inspect(reviews, author=author, head=head)
    # A same-account COMMENTED also is not independent even though P4 ignores it.
    rows = [_row('standard_independent_review', 'READY' if gate.status == 'APPROVED' else
                 'BLOCKED' if gate.status == 'BLOCKED' else 'PENDING',
                 {'APPROVED':'CURRENT_INDEPENDENT_APPROVAL', 'BLOCKED':'SELF_REVIEW_BLOCKED',
                  'CHANGES_REQUESTED':'INDEPENDENT_REWORK_REQUIRED'}.get(gate.status, 'WAIT_REVIEW'),
                 approved_user_ids=list(gate.approved_ids), feedback_review_ids=list(gate.feedback_ids))]
    actors = sorted({r['user']['id'] for r in reviews})
    rows.append(_row('observed_review_identity', 'BLOCKED' if actors and all(a == author for a in actors) else 'PENDING',
                     'ONLY_PR_AUTHOR_OBSERVED' if actors and all(a == author for a in actors) else 'IDENTITIES_NOT_ACTIVATION_AUTHORIZATION',
                     author_id=author, reviewer_ids=actors))
    return rows


class ReadinessInspector:
    def __init__(self, policy, transport=None):
        if policy.get('enabled') is not False or policy.get('repository') != REPOSITORY:
            raise ValueError('disabled policy required')
        self.policy = json.loads(json.dumps(policy))
        self.transport = transport or GhReadOnly()
        self.api = GitHubREST(REPOSITORY, 'not-a-credential', send=self.transport.rest)
        self.gql = ReadOnlyGraphQL(REPOSITORY, 'not-a-credential', send=self.transport.graphql)
        self.rows = []

    def _probe(self, check, operation):
        try:
            return operation()
        except VerificationPending as error:
            code = str(error)
            # P2 intentionally combines permission and rate-limit; preserve that ambiguity.
            reason = {'permission_or_rate_limit':'API_PERMISSION_OR_RATE_LIMIT',
                      'api_unavailable':'API_NETWORK_UNAVAILABLE', 'api_not_ok':'API_HTTP_UNVERIFIED',
                      'graphql_access_unverified':'GRAPHQL_ACCESS_UNVERIFIED',
                      'untrusted_ci_run':'CI_RUN_PR_HEAD_WORKFLOW_BINDING_UNVERIFIED',
                      'untrusted_ci_check':'CI_CHECK_JOB_APP_BINDING_UNVERIFIED',
                      'rate_limit':'API_RATE_LIMIT', 'request_limit':'API_REQUEST_LIMIT'}.get(code, 'API_EVIDENCE_UNVERIFIED')
            self.rows.append(_row(check, 'PENDING', reason))
        except (KeyError, TypeError, ValueError, AttributeError):
            self.rows.append(_row(check, 'PENDING', 'API_SCHEMA_UNVERIFIED'))
        return None

    def _extra(self, check, suffix):
        status, headers, data = self.transport.rest('/repos/' + REPOSITORY + suffix)
        if status != 200:
            reason = {401:'HTTP_401_AUTH_UNVERIFIED',403:'HTTP_403_PERMISSION_OR_RATE_LIMIT',
                      404:'HTTP_404_ABSENT_OR_HIDDEN',429:'HTTP_429_RATE_LIMIT'}.get(status, 'HTTP_OTHER_UNVERIFIED')
            self.rows.append(_row(check, 'PENDING', reason))
            return
        value = json.loads(data)
        if type(value) is not dict:
            raise ValueError()
        # A successful response proves endpoint access only, not complete App inventory.
        self.rows.append(_row(check, 'READY', 'ENDPOINT_READABLE_NOT_COMPLETE_AUTHORIZATION'))

    def inspect(self, pr, *, observer_ci_run=None):
        self.rows = []
        if type(pr) is not int or pr <= 0:
            raise ValueError('positive PR required')
        repository = self._probe('repository', lambda:self.api.get())
        if repository is not None:
            valid = (repository.get('id') == REPOSITORY_ID and repository.get('full_name') == REPOSITORY
                     and repository.get('private') is False and repository.get('visibility') == 'public')
            self.rows.append(_row('repository', 'READY' if valid else 'BLOCKED',
                                  'PUBLIC_REPOSITORY_ID_VERIFIED' if valid else 'REPOSITORY_ID_OR_PUBLIC_STATUS_MISMATCH',
                                  repository_id=repository.get('id') if type(repository.get('id')) is int else None))
        branch = self._probe('master', lambda:self.api.get('/branches/master'))
        base = None
        if branch is not None:
            base = branch.get('commit', {}).get('sha')
            if type(base) is not str or not re.fullmatch('[0-9a-f]{40}', base):
                self.rows.append(_row('master', 'PENDING', 'API_SCHEMA_UNVERIFIED')); base = None
            else:
                self.rows.append(_row('master', 'READY', 'MASTER_SHA_OBSERVED', sha=base))
            self.rows.append(_row('branch_protection', 'PENDING' if branch.get('protected') is True else 'BLOCKED',
                                  'FULL_PROTECTION_STILL_REQUIRED' if branch.get('protected') is True else 'PROTECTION_NOT_ENABLED_OR_UNVERIFIED'))
        rules = self._probe('branch_rules', lambda:self.api.get('/rules/branches/master?per_page=100&page=1'))
        if type(rules) is list and len(rules) < 100:
            self.rows.append(_row('branch_rules', 'USER_ACTION_REQUIRED' if not rules else 'PENDING',
                                  'NO_ACTIVE_RULES' if not rules else 'RULESET_DETAILS_AND_BYPASS_NOT_VERIFIED', count=len(rules)))
        elif rules is not None:
            self.rows.append(_row('branch_rules','PENDING','RULES_SCHEMA_OR_PAGINATION_UNVERIFIED'))
        for check, suffix in (('protection_endpoint','/branches/master/protection'),
                              ('actions_permissions','/actions/permissions/workflow'),('repository_installation','/installation')):
            self._probe(check, lambda c=check,s=suffix:self._extra(c,s))
        pull = self._probe('pr', lambda:self.api.get(f'/pulls/{pr}'))
        if pull is not None:
            self._probe('pr_evidence', lambda:self._pr(pr,pull))
        if observer_ci_run is not None:
            if type(observer_ci_run) is not int or observer_ci_run <= 0:
                raise ValueError('positive run required')
            self._probe('observer_run_relation',lambda:self._run_relation(observer_ci_run))
        if base:
            self._probe('codeowners', lambda:self._owners(base))
        after = self._probe('master_recheck', lambda:self.api.get('/branches/master'))
        if after is None or base is None or after.get('commit',{}).get('sha') != base:
            self.rows.append(_row('observation_consistency', 'PENDING', 'MASTER_CHANGED_OR_RECHECK_UNAVAILABLE'))
        else:
            self.rows.append(_row('observation_consistency','READY','MASTER_STABLE_DURING_SAMPLE_NOT_ATOMIC'))
        self.rows.extend(cost_readiness())
        self.rows.extend([
            _row('chatgpt_event_task', 'PENDING', 'OFFICIAL_ELIGIBLE_PLAN_SUPPORT_ACCOUNT_UNVERIFIED'),
            _row('chatgpt_review_writer', 'PENDING', 'WORK_STANDARD_REVIEW_AND_DISTINCT_ID_UNVERIFIED'),
            _row('macbook', 'PENDING', 'NOT_CONNECTED_NOT_REQUIRED_FOR_CLOUD_INSPECTION'),
            _row('cloud_durable_controller', 'BLOCKED', 'NO_VERIFIED_ZERO_COST_PERSISTENT_TRIGGER_AND_TRUSTED_CHECKPOINT'),
            _row('activation_approval', 'USER_ACTION_REQUIRED', 'SEPARATE_ACTIVATION_APPROVAL_REQUIRED'),
        ])
        return {'schema_version':1, 'observed_at':datetime.now(timezone.utc).isoformat(),
                'mode':'read-only', 'automation_status':'DISABLED', 'activation_authorized':False,
                'actual_model_calls':0, 'execution_enabled':False, 'publish_enabled':False, 'merge_enabled':False,
                'checks':self.rows}

    def _pr(self, number, pull):
        if pull['number'] != number or pull['base']['repo']['id'] != REPOSITORY_ID:
            raise ValueError()
        head = pull['head']['sha']; author = pull['user']['id']
        if type(head) is not str or not re.fullmatch('[0-9a-f]{40}', head) or type(author) is not int or author <= 0:
            raise ValueError()
        reviews = self.api.pages(f'/pulls/{number}/reviews')
        self.rows.extend(review_readiness(reviews, author=author, head=head, policy=self.policy))
        self.rows.append(_row('pr', 'READY', 'PR_IDENTITY_OBSERVED', number=number, head=head, author_id=author))
        try:
            snapshot = GitHubVerifier(self.api, self.policy).verify(pr=number)
            state = 'READY' if snapshot.decision == 'REVIEW_CI_VERIFIED' else 'BLOCKED' if snapshot.decision in ('NOT_APPROVED','PR_BLOCKED') else 'PENDING'
            self.rows.append(_row('observer', state, snapshot.decision, source='CURRENT_REPLAY_NOT_HISTORICAL_RUN_LOG'))
        except VerificationPending as error:
            self.rows.append(_row('observer', 'PENDING', diagnostic(error)))
        self._probe('required_ci',lambda:self._ci(pull,head))
        self._probe('review_threads',lambda:self._threads(number,head,pull['base']['sha']))
        current = self._probe('pr_recheck',lambda:self.api.get(f'/pulls/{number}'))
        if current is None or (current['head']['sha'],current['base']['sha'],current['user']['id']) != (head,pull['base']['sha'],author):
            for row in self.rows:
                if row['check'] in ('standard_independent_review','required_ci','review_threads','pr'):
                    row.update(status='PENDING', reason='PR_CHANGED_OR_RECHECK_UNAVAILABLE')

    def _ci(self,pull,head):
        checks, complete = GitHubVerifier(self.api, self.policy)._ci(pull, head)
        self.rows.append(_row('required_ci', 'READY' if complete else 'PENDING',
                              'P2_EXACT_RUN_JOB_CHECK_BINDING_VERIFIED' if complete else 'CI_NOT_VERIFIED',
                              required_checks=list(self.policy['required_checks']), workflow=self.policy['trusted_ci_workflow'],
                              run_ids=sorted({row[1] for row in checks})))
    def _threads(self,number,head,base):
        threads = self.gql.connection('threads',number,head,base)
        if any(type(t.get('isResolved')) is not bool or type(t.get('isOutdated')) is not bool for t in threads):
            raise ValueError()
        unresolved = any(not t['isResolved'] for t in threads)
        self.rows.append(_row('review_threads', 'BLOCKED' if unresolved else 'READY',
                              'UNRESOLVED_THREADS' if unresolved else 'THREADS_READ_COMPLETE',count=len(threads)))

    def _run_relation(self, run_id):
        run = self.api.get(f'/actions/runs/{run_id}')
        if run['id'] != run_id or run['repository']['full_name'] != REPOSITORY or type(run['pull_requests']) is not list:
            raise ValueError()
        self.rows.append(_row('observer_run_relation','PENDING' if len(run['pull_requests']) != 1 else 'READY',
                              'RUN_PR_RELATION_UNVERIFIED' if len(run['pull_requests']) != 1 else 'SINGLE_RUN_PR_REFERENCE_ONLY',
                              ci_run_id=run_id,pr_reference_count=len(run['pull_requests'])))

    def _owners(self, base):
        data = self.gql.read('owners',base=base)['repository']
        if data['databaseId'] != REPOSITORY_ID:
            raise ValueError()
        present = [k for k in ('github','root','docs') if data[k] is not None]
        self.rows.append(_row('codeowners', 'PENDING' if present else 'USER_ACTION_REQUIRED',
                              'OWNER_RULES_AND_PERMISSIONS_REQUIRE_P4_VALIDATION' if present else 'CODEOWNERS_ABSENT_AT_BASE_SHA',base=base))


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # Never echo unknown arguments; they might contain accidentally pasted secrets.
        self.exit(2, "Invalid readiness arguments; no inspection performed.\n")


def main():
    parser = Parser(description=__doc__)
    parser.add_argument('--pr', type=int, required=True)
    parser.add_argument('--observer-ci-run', type=int)
    args = parser.parse_args()
    try:
        policy = json.loads((ROOT / '.github/automation/policy.json').read_text())
        if policy.get('enabled') is not False:
            raise ValueError()
        report = ReadinessInspector(policy).inspect(args.pr,observer_ci_run=args.observer_ci_run)
    except Exception:
        report = {'automation_status':'DISABLED','activation_authorized':False,
                  'checks':[_row('inspection','PENDING','INSPECTION_UNAVAILABLE')]}
    print(json.dumps(report, ensure_ascii=False))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
