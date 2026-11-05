"""P27 observation scope: does the autodev verifier apply to this PR at all?

A same-repository PR whose re-read, current GitHub state carries no autodev claim (no `autodev/`
branch, no `/autodev` text in the body) is outside the autodev contract: NOT_APPLICABLE.
This is never approval, a Snapshot, a candidate, CI evidence or execution authority, and it is
never derived from the webhook payload. Anything claiming autodev (including malformed or duplicate
contracts) stays on the strict GitHubVerifier path; anything missing or ambiguous stays PENDING.
"""
import re

from .github_read import VerificationPending, positive

NOT_APPLICABLE = 'NOT_APPLICABLE'
REASON = 'manual_pr_outside_autodev'
SCOPE_NOTE = 'observation_scope_only_not_approval_or_ci_evidence'
MAX_BODY_BYTES = 262144
_AUTODEV_TEXT = re.compile(r'/autodev', re.IGNORECASE)


def _identity(pr, repo, number):
    """Strict schema: GitHub's nullable body is allowed; missing/mistyped fields are not."""
    if type(pr) is not dict or pr.get('number') != number:
        raise VerificationPending('pr_mismatch')
    if 'body' not in pr or not (pr['body'] is None or type(pr['body']) is str):
        raise VerificationPending('missing_or_invalid_metadata')
    body = pr['body'] or ''
    if len(body.encode()) > MAX_BODY_BYTES:
        raise VerificationPending('missing_or_invalid_metadata')
    head, base = pr.get('head'), pr.get('base')
    if type(head) is not dict or type(base) is not dict or 'repo' not in head:
        raise VerificationPending('missing_or_invalid_metadata')
    base_repo = base.get('repo')
    if type(base_repo) is not dict or base_repo.get('id') != repo['id'] or base_repo.get('full_name') != repo['full_name']:
        raise VerificationPending('pr_mismatch')
    ref, sha = head.get('ref'), head.get('sha')
    if type(ref) is not str or not ref or type(sha) is not str or not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise VerificationPending('missing_or_invalid_metadata')
    if pr.get('state') not in ('open', 'closed'):
        raise VerificationPending('missing_or_invalid_metadata')
    head_repo = head['repo']
    same_repository = (type(head_repo) is dict and head_repo.get('id') == repo['id']
                       and head_repo.get('full_name') == repo['full_name'])
    # Body text is used only for this in-memory decision; it is never returned, logged or stored.
    claims = ref.lower().startswith('autodev/') or bool(_AUTODEV_TEXT.search(body))
    return {'number': number, 'ref': ref, 'sha': sha, 'state': pr['state'],
            'same_repository': same_repository, 'claims_autodev': claims}


def classify(verifier, number):
    """Return (NOT_APPLICABLE, facts) for a verified manual PR, or (None, facts) for the strict path."""
    positive(number)
    repo = verifier._repository()
    try:
        first = _identity(verifier.api.get(f'/pulls/{number}'), repo, number)
        second = _identity(verifier.api.get(f'/pulls/{number}'), repo, number)
    except VerificationPending:
        raise
    except Exception:
        raise VerificationPending('missing_or_invalid_metadata') from None
    if first != second:
        raise VerificationPending('state_changed_during_read')
    if first['claims_autodev']:
        return None, first  # Exact, malformed or duplicate contracts all keep strict verification.
    if not first['same_repository']:
        raise VerificationPending('pr_head_identity_unverified')  # Fork/unknown head: never assumed manual.
    return NOT_APPLICABLE, first
