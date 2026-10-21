"""Fail-closed deterministic policy. No model, trading, write or merge APIs."""
from dataclasses import dataclass, replace
import hashlib
import re


def spec_hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def approved(issue, policy):
    """The future adapter must obtain these facts directly from authenticated GitHub APIs."""
    if issue.get('state') != 'open' or issue.get('is_pull_request'):
        return False
    if policy['approved_label'] not in issue.get('labels', []):
        return False
    if issue.get('repository') != policy['repository']:
        return False
    digest = spec_hash(issue.get('body', ''))
    return any(comment.get('author_id') in policy['maintainer_ids'] and
               comment.get('body') == f'/autodev approve {digest}'
               for comment in issue.get('approval_comments', []))


@dataclass(frozen=True)
class Ledger:
    attempts: int = 0
    failures: int = 0
    reserved_cents: int = 0
    day_reserved_cents: int = 0
    seen: tuple[str, ...] = ()
    in_flight: bool = False
    halted: bool = False

    def __post_init__(self):
        for value in (self.attempts, self.failures, self.reserved_cents, self.day_reserved_cents):
            if type(value) is not int or value < 0:
                raise ValueError('Invalid ledger counter')
        object.__setattr__(self, 'seen', tuple(self.seen))


def reserve(ledger, policy, *, event_key, cents):
    """Pure admission control; NOT a payment meter or persistent atomic store."""
    if not event_key or event_key in ledger.seen:
        raise ValueError('Duplicate/invalid event')
    if ledger.in_flight or ledger.halted or ledger.failures >= policy['max_consecutive_failures']:
        raise ValueError('Task locked or halted')
    if ledger.attempts >= policy['max_attempts_per_issue']:
        raise ValueError('Attempt limit')
    if type(cents) is not int or not 0 < cents <= policy['max_reserved_cents_per_attempt']:
        raise ValueError('Invalid reservation')
    if ledger.reserved_cents + cents > policy['max_reserved_cents_per_issue']:
        raise ValueError('Issue reservation limit')
    if ledger.day_reserved_cents + cents > policy['max_reserved_cents_per_day']:
        raise ValueError('Daily reservation limit')
    return replace(ledger, attempts=ledger.attempts + 1, reserved_cents=ledger.reserved_cents + cents,
                   day_reserved_cents=ledger.day_reserved_cents + cents, seen=ledger.seen + (event_key,), in_flight=True)


def finish(ledger, policy, *, success):
    if not ledger.in_flight:
        raise ValueError('No reserved task')
    failures = 0 if success else ledger.failures + 1
    # Failure/timeout reservations are never automatically refunded or retried.
    return replace(ledger, in_flight=False, failures=failures,
                   halted=failures >= policy['max_consecutive_failures'])


def allowed_paths(paths, policy):
    if not paths or len(paths) > policy['max_changed_files']:
        return False
    for path in paths:
        if not isinstance(path, str) or '\\' in path or path.startswith('/') or '..' in path.split('/'):
            return False
        if path.startswith(tuple(policy['protected_prefixes'])):
            return False
        if not path.startswith(tuple(policy['allowed_prefixes'])):
            return False
    return True


def latest_reviews(reviews, policy):
    latest = {}
    for review in reviews:
        if review.get('author_id') not in policy['independent_reviewer_ids']:
            continue
        author = review['author_id']
        if author not in latest or review['id'] > latest[author]['id']:
            latest[author] = review
    return list(latest.values())


def plan(snapshot, policy):
    def result(action, reason):
        return {'action': action, 'reason': reason, 'execution_enabled': False}
    if not policy['enabled']:
        return result('blocked', 'automation_disabled')
    issue = snapshot.get('issue', {})
    if not approved(issue, policy):
        return result('blocked', 'no_current_spec_approval')
    if 'ledger' not in snapshot:
        return result('blocked', 'ledger_unverified')
    ledger = Ledger(**snapshot['ledger'])
    if ledger.in_flight or ledger.halted or ledger.failures >= policy['max_consecutive_failures']:
        return result('blocked', 'locked_or_attempts_exhausted')
    pr = snapshot.get('pr')
    if pr is None:
        if ledger.attempts >= policy['max_attempts_per_issue']:
            return result('blocked', 'attempts_exhausted')
        return result('implementation_candidate', 'approved_issue; requires atomic reservation and authenticated worker')
    if pr.get('state') != 'open' or pr.get('draft') or pr.get('base') != policy['base_branch']:
        return result('blocked', 'invalid_pr_state')
    if pr.get('repository') != policy['repository'] or pr.get('head_repository') != policy['repository']:
        return result('blocked', 'fork_or_repository_mismatch')
    if pr.get('author_id') not in policy['builder_ids'] or pr.get('branch') != f"autodev/issue-{issue['number']}":
        return result('blocked', 'untrusted_builder_or_branch')
    head = pr.get('head_sha', '')
    if not re.fullmatch('[0-9a-f]{40}', head) or pr.get('spec_sha256') != spec_hash(issue.get('body', '')):
        return result('blocked', 'stale_head_or_spec')
    if not allowed_paths(pr.get('changed_paths', []), policy):
        return result('blocked', 'protected_or_oversized_diff')
    reviews = latest_reviews(snapshot.get('reviews', []), policy)
    if not reviews or any(r['author_id'] == pr['author_id'] for r in reviews):
        return result('wait', 'independent_reviewer_required')
    current = [r for r in reviews if r.get('commit_id') == head]
    if len(current) != len(reviews):
        return result('wait', 'stale_review')
    if any(r.get('state') == 'CHANGES_REQUESTED' for r in current):
        if ledger.attempts >= policy['max_attempts_per_issue']:
            return result('blocked', 'attempts_exhausted')
        event_key = f"fix:{issue['number']}:{head}:" + ','.join(str(r['id']) for r in current)
        if event_key in ledger.seen:
            return result('blocked', 'duplicate_feedback')
        return {**result('fix_candidate', 'trusted_current_head_feedback'), 'event_key': event_key}
    if any(r.get('state') != 'APPROVED' for r in current):
        return result('wait', 'approval_required')
    checks = snapshot.get('checks', [])
    for name in policy['required_checks']:
        candidates = [c for c in checks if c.get('name') == name and c.get('head_sha') == head]
        if len(candidates) != 1:
            return result('wait', 'missing_or_ambiguous_check')
        check = candidates[0]
        if check.get('status') != 'completed' or check.get('conclusion') != 'success':
            return result('wait', 'ci_not_successful')
        if check.get('app_slug') != 'github-actions' or check.get('workflow_path') != policy['trusted_ci_workflow']:
            return result('blocked', 'untrusted_check_source')
    if snapshot.get('unresolved_threads') is not False:
        return result('wait', 'unresolved_or_unverified_threads')
    if snapshot.get('branch_protection_verified') is not True or snapshot.get('base_up_to_date') is not True:
        return result('blocked', 'protection_or_base_unverified')
    return result('external_merge_gate_candidate', 'independent review and CI pass; this planner cannot merge')
