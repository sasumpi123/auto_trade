"""P4 feedback-to-P3 simulation only. Does not accept a live reader or P2 Snapshot."""
from uuid import uuid5, NAMESPACE_URL
from .fake_merge import FakeMergeWorld
from .fake_pipeline import FakePipeline
from .worker_contracts import Feedback, fingerprint, require, WorkerBlocked


class FakeReviewRelay:
    def __init__(self, pipeline, world, reference):
        require(type(pipeline) is FakePipeline and type(world) is FakeMergeWorld, 'exact_fake_review_scope_required')
        self.pipeline, self.world, self.reference = pipeline, world, reference
        self.policy = dict(pipeline.coordinator.policy, builder_ids=[1001], independent_reviewer_ids=[2002])

    def poll(self):
        c = self.pipeline.coordinator; ref = self.reference
        with c.authority.lock, self.world.lock:
            try:
                require(c.state(ref.issue)['head'] == ref.head and c.state(ref.issue)['head_version'] == ref.head_version, 'feedback_reference_stale')
                outcome = self.world.reader(self.policy).feedback(ref)
                review, body = outcome['review'], outcome['feedback']
                candidates = [r for r in review.opinions if r[1] == 2002 and r[2] == ref.head]
                if not candidates:
                    return {'status': 'WAIT_REVIEW', 'execution_enabled': False}
                row = candidates[-1]
                require(row[3] in ('APPROVED', 'CHANGES_REQUESTED', 'DISMISSED'), 'feedback_unverified')
                if row[3] == 'APPROVED' and review.status != 'APPROVED':
                    return {'status': 'WAIT_REVIEW', 'execution_enabled': False}
                if row[3] == 'CHANGES_REQUESTED':
                    require(body is not None, 'trusted_current_feedback_required')
                item = c.state(ref.issue)
                if row[0] < item['review_id']:
                    return {'status': 'STALE', 'execution_enabled': False}
                text = '\n'.join(body['untrusted_feedback']) if body else ''
                key = fingerprint([ref.task, ref.head, ref.head_version, row, fingerprint(text)])
                current = c.store.read()
                if key in current.get('feedback_seen', {}):
                    return {'status': 'DUPLICATE', 'execution_enabled': False}
                feedback = Feedback(ref.issue, ref.pr, ref.head, ref.head_version, row[0], row[1], row[3], text)
                feedback.validate()
                c.authority.review(feedback)
                if c.state(ref.issue)['status'] == 'RUNNING':
                    # Changed/dismissed feedback during a running task is never an imperative command.
                    c.fail(c.state(ref.issue)['current_task'], unknown=True)
                    return {'status': 'HALTED', 'execution_enabled': False}
                applied = self.pipeline.review(ref.issue)
                def record(state, now):
                    state.setdefault('feedback_seen', {})[key] = {'task': ref.task, 'head': ref.head, 'review_id': row[0]}
                    return {'status': applied['status'], 'execution_enabled': False}
                return c._change('REVIEW_FEEDBACK', [key], record, str(uuid5(NAMESPACE_URL, 'fake-feedback:'+key)))
            except Exception:
                item = c.state(ref.issue)
                if item and item['status'] == 'RUNNING': c.fail(item['current_task'], unknown=True)
                raise WorkerBlocked('fake_feedback_unverified_wait_or_halt') from None
