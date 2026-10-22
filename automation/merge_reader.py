"""Read-only final evidence collection. Never returns MERGE_ELIGIBLE or invokes a writer."""
from dataclasses import asdict, dataclass
import copy
import re

from .events import canonical, digest
from .github_graphql import ReadOnlyGraphQL
from .github_read import GitHubREST, VerificationPending, positive
from .github_verify import GitHubVerifier
from .policy import allowed_paths
from .review_gate import ReviewGate
from .worker_contracts import fingerprint, uid


def need(condition, reason):
    if not condition:
        raise VerificationPending(reason)


@dataclass(frozen=True)
class TaskReference:
    task: str
    issue: int
    pr: int
    spec: str
    spec_version: int
    base: str
    head: str
    head_version: int
    paths_sha: str

    def validate(self):
        uid(self.task)
        for v in (self.issue, self.pr, self.spec_version, self.head_version): positive(v)
        for v, size in ((self.spec, 64), (self.base, 40), (self.head, 40), (self.paths_sha, 64)): digest(v, size)


@dataclass(frozen=True)
class MergeEvidence:
    reference: TaskReference
    evidence_sha: str
    reviews_sha: str
    ci_sha: str
    rules_sha: str
    owners_sha: str
    threads_sha: str
    pr_id: int
    status: str = 'READ_ONLY_VERIFIED'
    execution_enabled: bool = False


class GitHubMergeReader:
    def __init__(self, rest, graphql, policy):
        need(isinstance(rest, GitHubREST) and type(graphql) is ReadOnlyGraphQL, 'read_clients_required')
        need(policy.get('enabled') is False and rest.repository == graphql.repository == policy['repository'], 'readonly_policy_required')
        self.rest, self.graphql, self.policy = rest, graphql, copy.deepcopy(policy)
        self.p2 = GitHubVerifier(rest, policy)
        self.reviews = ReviewGate(policy)

    def _protection(self, repo):
        classic = self.rest.get('/branches/' + repo['default_branch'] + '/protection')
        reviews = classic['required_pull_request_reviews']
        checks = classic['required_status_checks']
        need(classic['enforce_admins']['enabled'] is True and checks['strict'] is True, 'protection_not_strict')
        need(classic['allow_force_pushes']['enabled'] is False and classic['allow_deletions']['enabled'] is False
             and classic['required_conversation_resolution']['enabled'] is True, 'protection_bypass_or_resolution_unverified')
        need(reviews['dismiss_stale_reviews'] is True and reviews['require_last_push_approval'] is True, 'review_protection_unverified')
        need(type(reviews['require_code_owner_reviews']) is bool, 'codeowner_requirement_unverified')
        minimum = positive(reviews['required_approving_review_count'])
        need('bypass_pull_request_allowances' in reviews, 'protection_bypass_unverified')
        allowances = reviews['bypass_pull_request_allowances']
        need(all(type(allowances[k]) is list and not allowances[k] for k in ('users', 'teams', 'apps')), 'protection_bypass_unverified')
        expected = set(self.policy['required_checks'])
        need(set(checks['contexts']) == expected and len(checks['checks']) == len(expected)
             and {c['context'] for c in checks['checks'] if c['app_id'] == 15368} == expected, 'required_check_source_unverified')
        # Applicable active rules from every level; unknown semantics never silently disappear.
        active = self.rest.pages('/rules/branches/' + repo['default_branch'], identity=None)
        need(len({fingerprint(r) for r in active}) == len(active), 'duplicate_ruleset_evidence')
        # Multiple rulesets can supply the same rule type. Fetch pages without assuming unique type.
        details = []
        codeowners = reviews['require_code_owner_reviews']
        for rule in active:
            if rule['type'] == 'merge_queue':
                raise VerificationPending('merge_queue_requires_separate_gate')
            need(rule['type'] in ('pull_request', 'required_status_checks', 'non_fast_forward', 'deletion'), 'ruleset_semantics_unsupported')
        for rule_id in sorted({positive(r['ruleset_id']) for r in active}):
            detail = self.rest.get(f'/rulesets/{rule_id}')
            need(detail['id'] == rule_id and detail['enforcement'] == 'active' and detail['target'] == 'branch', 'ruleset_identity_unverified')
            # GitHub may omit bypass_actors for read-only callers. That cannot prove no bypass.
            need('bypass_actors' in detail and type(detail['bypass_actors']) is list and not detail['bypass_actors'], 'ruleset_bypass_unverified')
            for rule in detail['rules']:
                kind = rule['type']
                need(kind in ('pull_request', 'required_status_checks', 'non_fast_forward', 'deletion'), 'ruleset_semantics_unsupported')
                if kind == 'pull_request':
                    p = rule['parameters']
                    need(set(p).issubset({'dismiss_stale_reviews_on_push', 'require_last_push_approval',
                         'required_review_thread_resolution', 'required_approving_review_count', 'require_code_owner_review',
                         'allowed_merge_methods'}), 'ruleset_review_requirement_unsupported')
                    if 'allowed_merge_methods' in p:
                        need('squash' in p['allowed_merge_methods'], 'squash_merge_not_allowed')
                    need(p['dismiss_stale_reviews_on_push'] is True and p['require_last_push_approval'] is True
                         and p['required_review_thread_resolution'] is True, 'ruleset_review_requirement_unverified')
                    minimum = max(minimum, positive(p['required_approving_review_count']))
                    need(type(p['require_code_owner_review']) is bool, 'codeowner_requirement_unverified')
                    codeowners = codeowners or p['require_code_owner_review']
                if kind == 'required_status_checks':
                    p = rule['parameters']
                    need(p['strict_required_status_checks_policy'] is True and len(p['required_status_checks']) == len(expected)
                         and {c['context'] for c in p['required_status_checks'] if c['integration_id'] == 15368} == expected,
                         'ruleset_check_requirement_unverified')
            details.append(detail)
        return minimum, codeowners, fingerprint([classic, active, details])

    def _owners(self, repo, base, paths, review_evidence, required, head, author):
        data = self.graphql.read('owners', base=base)['repository']
        need(data['databaseId'] == repo['id'], 'codeowners_repository_unverified')
        selected = next((data[k] for k in ('github', 'root', 'docs') if data[k] is not None), None)
        if selected is None:
            need(not required, 'required_codeowners_missing')
            return fingerprint(['absent', base])
        need(selected['__typename'] == 'Blob' and selected['isTruncated'] is False and type(selected['byteSize']) is int
             and 0 <= selected['byteSize'] <= 65536, 'codeowners_blob_unverified')
        text = selected['text']
        need(type(text) is str and len(text.encode()) == selected['byteSize'], 'codeowners_blob_unverified')
        from .patch_guard import blob_sha
        need(blob_sha(text.encode()) == digest(selected['oid'], 40), 'codeowners_blob_unverified')
        # Even when not required, retain immutable base-blob identity. No invented approvals.
        if not required:
            return fingerprint([base, selected['oid'], False])
        matches = {path: [] for path in paths}
        for raw in text.splitlines():
            line = raw.split('#', 1)[0].strip()
            if not line: continue
            pattern, *owners = line.split()
            need(owners and all(re.fullmatch('@[A-Za-z0-9-]{1,39}', owner) for owner in owners), 'codeowners_syntax_or_team_unsupported')
            need(pattern == '*' or re.fullmatch(r'/[A-Za-z0-9_.\-/]+', pattern), 'codeowners_pattern_unsupported')
            need('..' not in pattern.split('/') and '//' not in pattern, 'codeowners_pattern_unsupported')
            for path in paths:
                literal = pattern.lstrip('/')
                if pattern == '*' or path == literal or (literal.endswith('/') and path.startswith(literal)):
                    matches[path] = owners  # Last matching rule, as GitHub CODEOWNERS.
        latest = {r[1]: r for r in review_evidence.opinions}
        ids = {}
        for owners in matches.values():
            need(owners, 'changed_path_codeowner_unverified')
            for owner in owners:
                if owner in ids: continue
                user = self.graphql.read('user', login=owner[1:])['user']
                identity = positive(user['databaseId'])
                need(user['login'].casefold() == owner[1:].casefold(), 'codeowner_identity_unverified')
                permission = self.rest.get('/collaborators/' + owner[1:] + '/permission')
                need(permission['user']['id'] == identity and permission['permission'] in ('write', 'admin', 'maintain'), 'codeowner_permission_unverified')
                ids[owner] = identity
            need(any(ids[o] in latest and latest[ids[o]][2] == head and latest[ids[o]][3] == 'APPROVED'
                     and ids[o] != author for o in owners), 'codeowners_approval_missing')
        return fingerprint([base, selected['oid'], sorted(ids.items()), review_evidence.fingerprint])

    def _read(self, reference):
        reference.validate()
        repo = self.p2._repository()
        need(repo['private'] is False and repo['allow_squash_merge'] is True, 'zero_cost_repository_or_merge_method_unverified')
        pr = self.rest.get(f'/pulls/{reference.pr}')
        need(pr['number'] == reference.pr and pr['base']['repo']['id'] == repo['id']
             and pr['head']['repo']['id'] == repo['id'] and pr['base']['ref'] == repo['default_branch']
             and pr['head']['ref'] == f'autodev/issue-{reference.issue}' and pr['user']['id'] in self.policy['builder_ids'], 'pr_identity_unverified')
        need(pr['state'] == 'open' and pr['draft'] is False and pr['merged'] is False and pr['mergeable'] is True, 'pr_not_mergeable')
        markers = re.findall(r'^/autodev issue ([1-9][0-9]*) spec ([0-9a-f]{64})$', pr['body'] or '', re.M)
        task_marker = re.findall(r'^/autodev task ([0-9a-f-]{36}) version ([1-9][0-9]*)$', pr['body'] or '', re.M)
        need(markers == [(str(reference.issue), reference.spec)] and task_marker == [(reference.task, str(reference.spec_version))], 'task_binding_unverified')
        issue, spec, approval = self.p2._issue(reference.issue)
        need(approval is not None and spec == reference.spec, 'current_issue_approval_missing')
        head, base = pr['head']['sha'], pr['base']['sha']
        need((head, base) == (reference.head, reference.base), 'head_or_base_changed')
        files = self.rest.pages(f'/pulls/{reference.pr}/files', identity='filename')
        paths = [f['filename'] for f in files]
        paths.extend(f['previous_filename'] for f in files if f['status'] == 'renamed')
        need(len(files) == pr['changed_files'] and allowed_paths(paths, self.policy)
             and fingerprint(sorted(paths)) == reference.paths_sha, 'changed_paths_unverified')
        minimum, owners_required, rules_sha = self._protection(repo)
        reviews = self.rest.pages(f'/pulls/{reference.pr}/reviews')
        review = self.reviews.inspect(reviews, author=pr['user']['id'], head=head, minimum=minimum)
        need(review.status == 'APPROVED', review.reason)
        branch = self.rest.get('/branches/' + repo['default_branch'])
        compare = self.rest.get(f'/compare/{base}...{head}')
        need(branch['protected'] is True and branch['commit']['sha'] == base
             and compare['merge_base_commit']['sha'] == base and compare['status'] in ('ahead', 'identical'), 'master_base_changed')
        checks, passed = self.p2._ci(pr, head)
        need(passed, 'latest_ci_not_successful')
        gql_repo = self.graphql.read('state', number=reference.pr)['repository']
        gql_pr = gql_repo['pullRequest']
        need(gql_repo['databaseId'] == repo['id'] and gql_pr['number'] == reference.pr
             and gql_pr['databaseId'] == pr['id'] and gql_pr['headRefOid'] == head and gql_pr['baseRefOid'] == base,
             'graphql_pr_identity_unverified')
        need(gql_pr['state'] == 'OPEN' and gql_pr['isDraft'] is False and gql_pr['reviewDecision'] == 'APPROVED'
             and gql_pr['mergeable'] == 'MERGEABLE' and gql_pr['mergeStateStatus'] == 'CLEAN', 'github_server_requirements_unmet')
        need(gql_pr['mergeQueueEntry'] is None and gql_pr['autoMergeRequest'] is None, 'merge_queue_or_auto_merge_unverified')
        threads = self.graphql.connection('threads', reference.pr, head, base)
        need(all(type(t['isOutdated']) is bool and t['isResolved'] is True for t in threads), 'unresolved_review_threads')
        requests = self.graphql.connection('requests', reference.pr, head, base)
        need(all(type(r['asCodeOwner']) is bool for r in requests) and not requests, 'pending_review_requests')
        owner_sha = self._owners(repo, base, paths, review, owners_required, head, pr['user']['id'])
        # Hash complete normalized evidence, including updated_at and approval/comment identity.
        data = [asdict(reference), repo['id'], pr['id'], pr['updated_at'], issue['updated_at'], approval['id'],
                approval['user']['id'], review.fingerprint, checks, rules_sha, owner_sha, threads, requests, gql_pr]
        need(self.rest.requests + self.graphql.requests <= 120, 'merge_reader_request_limit')
        return MergeEvidence(reference, fingerprint(data), review.fingerprint, fingerprint(checks), rules_sha,
                             owner_sha, fingerprint(threads), positive(pr['id']))

    def inspect(self, reference):
        try:
            first, second = self._read(reference), self._read(reference)
            need(first == second, 'merge_evidence_changed_during_read')
            return second
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('merge_schema_or_binding_unverified') from None

    def feedback(self, reference):
        """Fresh untrusted feedback DATA. Never creates work, reservations or a P3 authority."""
        try:
            reference.validate()
            self.p2._repository()
            pr = self.rest.get(f'/pulls/{reference.pr}')
            need(pr['number'] == reference.pr and pr['state'] == 'open' and pr['head']['sha'] == reference.head
                 and pr['base']['sha'] == reference.base and pr['base']['repo']['id'] == 888367411
                 and pr['head']['repo']['id'] == 888367411 and pr['user']['id'] in self.policy['builder_ids'], 'feedback_pr_unverified')
            markers = re.findall(r'^/autodev issue ([1-9][0-9]*) spec ([0-9a-f]{64})$', pr['body'] or '', re.M)
            tasks = re.findall(r'^/autodev task ([0-9a-f-]{36}) version ([1-9][0-9]*)$', pr['body'] or '', re.M)
            need(markers == [(str(reference.issue), reference.spec)] and tasks == [(reference.task, str(reference.spec_version))]
                 and pr['head']['ref'] == f'autodev/issue-{reference.issue}' and pr['base']['ref'] == self.policy['base_branch'], 'feedback_task_unverified')
            issue, spec, approval = self.p2._issue(reference.issue)
            need(approval is not None and spec == reference.spec, 'current_issue_approval_missing')
            reviews = self.rest.pages(f'/pulls/{reference.pr}/reviews')
            result = self.reviews.inspect(reviews, author=pr['user']['id'], head=reference.head)
            data = self.reviews.feedback(reviews, author=pr['user']['id'], head=reference.head) if result.status == 'CHANGES_REQUESTED' else None
            return {'review': result, 'feedback': data, 'execution_enabled': False}
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('review_feedback_unverified') from None
