"""Independent immutable-ID, exact-commit standard Review verification; no review writer."""
import copy
from dataclasses import dataclass
from .events import digest
from .github_read import VerificationPending, positive
from .worker_contracts import fingerprint


@dataclass(frozen=True)
class ReviewEvidence:
    status: str
    reason: str
    opinions: tuple
    approved_ids: tuple
    feedback_ids: tuple
    fingerprint: str


class ReviewGate:
    def __init__(self, policy):
        self.policy = copy.deepcopy(policy)

    def inspect(self, reviews, *, author, head, minimum=1):
        positive(author); positive(minimum); digest(head, 40)
        opinions, seen = {}, set()
        for review in sorted(reviews, key=lambda r: positive(r['id'])):
            identity = positive(review['user']['id'])
            review_id = positive(review['id'])
            if review_id in seen:
                raise VerificationPending('duplicate_review')
            seen.add(review_id)
            state = review['state']
            if state not in ('APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'):
                raise VerificationPending('review_schema_unverified')
            if state == 'PENDING':
                continue
            commit = digest(review['commit_id'], 40)
            if state != 'COMMENTED':
                opinions[identity] = (review_id, identity, commit, state)
        values = tuple(sorted(opinions.values()))
        trusted = [r for r in values if r[1] in self.policy['independent_reviewer_ids']]
        approvals = tuple(r[1] for r in trusted if r[2] == head and r[3] == 'APPROVED' and r[1] != author)
        changes = tuple(r[0] for r in trusted if r[2] == head and r[3] == 'CHANGES_REQUESTED' and r[1] != author)

        def result(status, reason):
            return ReviewEvidence(status, reason, values, approvals, changes, fingerprint(values))

        if any(r[1] == author for r in trusted):
            return result('BLOCKED', 'self_review')
        if any(r[3] == 'CHANGES_REQUESTED' for r in values):
            return result('CHANGES_REQUESTED', 'unresolved_changes_requested')
        if any(r[3] == 'DISMISSED' or r[2] != head for r in trusted):
            return result('WAIT_REVIEW', 'review_revoked_or_old_commit')
        if len(approvals) < minimum:
            return result('WAIT_REVIEW', 'independent_standard_approval_required')
        return result('APPROVED', 'independent_current_commit_approved')

    def feedback(self, reviews, *, author, head):
        evidence = self.inspect(reviews, author=author, head=head)
        if evidence.status != 'CHANGES_REQUESTED' or not evidence.feedback_ids:
            raise VerificationPending('current_independent_feedback_required')
        rows = [r for r in reviews if r['id'] in evidence.feedback_ids]
        text = []
        for row in rows:
            body = row['body']
            if type(body) is not str or len(body.encode()) > 8192:
                raise VerificationPending('review_feedback_limit')
            text.append(body)
        # Data only. No command/argument/shell conversion, no plaintext persistence.
        return {'review_ids': evidence.feedback_ids, 'head': head, 'digest': fingerprint([evidence.fingerprint, text]),
                'untrusted_feedback': tuple(text), 'execution_enabled': False}
