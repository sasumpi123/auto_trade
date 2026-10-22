"""Exact Fake World/Broker only. No GitHub mutation or merge endpoint exists here."""
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import threading
from uuid import uuid4, uuid5, NAMESPACE_URL

from .events import canonical
from .fake_pipeline import FakePipeline
from .github_graphql import QUERIES, ReadOnlyGraphQL
from .github_read import GitHubREST, VerificationPending
from .merge_reader import GitHubMergeReader, TaskReference
from .patch_guard import ValidatedPatch
from .subscription_contract import SubscriptionContract, SubscriptionEvidence
from .worker_contracts import Feedback, WorkerBlocked, fingerprint, require


class FakeMergeWorld:
    """In-memory HTTP fixture service; contains no real client/credentials/network path."""
    def __init__(self, reference, policy, *, body, paths):
        self.lock = threading.RLock()
        self.receipts = {}
        self.effects = 0
        self.lose_response = False
        self.before_compare = None
        self.http_failure = None
        repo = dict(id=888367411, full_name=policy['repository'], default_branch='master', archived=False,
                    disabled=False, private=False, allow_squash_merge=True)
        url = 'https://api.github.com/repos/' + policy['repository']
        issue = dict(number=reference.issue, body=body, state='open', user={'id': 9001}, updated_at='2026-10-21T00:00:00Z',
                     url=f'{url}/issues/{reference.issue}', repository_url=url, labels=[{'name': policy['approved_label']}])
        comment = dict(id=10, user={'id': 45135977, 'type': 'User'}, issue_url=issue['url'],
                       body='/autodev approve '+reference.spec, created_at='2026-10-21T00:01:00Z', updated_at='2026-10-21T00:01:00Z')
        pr = dict(id=700, number=reference.pr, body=f'/autodev issue {reference.issue} spec {reference.spec}\n/autodev task {reference.task} version {reference.spec_version}',
                  user={'id': 1001}, state='open', merged=False, mergeable=True, draft=False, updated_at='2026-10-21T00:02:00Z', changed_files=len(paths),
                  head={'sha': reference.head, 'ref': f'autodev/issue-{reference.issue}', 'repo': repo},
                  base={'sha': reference.base, 'ref': 'master', 'repo': repo})
        review = dict(id=20, user={'id': 2002}, commit_id=reference.head, state='APPROVED', body='')
        protection = dict(enforce_admins={'enabled': True}, allow_force_pushes={'enabled': False}, allow_deletions={'enabled': False},
             required_conversation_resolution={'enabled': True}, required_pull_request_reviews=dict(dismiss_stale_reviews=True,
             require_last_push_approval=True, require_code_owner_reviews=False, required_approving_review_count=1,
             bypass_pull_request_allowances={'users': [], 'teams': [], 'apps': []}),
             required_status_checks=dict(strict=True, contexts=policy['required_checks'],
             checks=[dict(context=n, app_id=15368) for n in policy['required_checks']]))
        run = dict(id=30, workflow_id=40, path=policy['trusted_ci_workflow'], head_sha=reference.head,
                   head_branch=pr['head']['ref'], event='pull_request', repository=repo, head_repository=repo,
                   pull_requests=[dict(number=reference.pr, head={'sha': reference.head}, base={'repo': repo})],
                   run_attempt=1, status='completed', conclusion='success')
        checks, jobs = [], []
        for i, name in enumerate(policy['required_checks'], 50):
            checks.append(dict(id=i, name=name, url=url+f'/check-runs/{i}', head_sha=reference.head, status='completed', conclusion='success',
                               app={'id': 15368, 'slug': 'github-actions', 'owner': {'id': 9919}}))
            jobs.append(dict(id=i+100, name=name, run_id=30, head_sha=reference.head, status='completed', conclusion='success', check_run_url=checks[-1]['url']))
        self.rest_data = {'': repo, f'/issues/{reference.issue}': issue, f'/issues/{reference.issue}/comments': [comment],
            f'/pulls/{reference.pr}': pr, f'/pulls/{reference.pr}/reviews': [review],
            f'/pulls/{reference.pr}/files': [dict(filename=p, status='modified') for p in paths],
            '/branches/master/protection': protection, '/rules/branches/master': [],
            '/branches/master': {'protected': True, 'commit': {'sha': reference.base}},
            f'/compare/{reference.base}...{reference.head}': {'status': 'ahead', 'merge_base_commit': {'sha': reference.base}},
            f'/commits/{reference.head}/check-runs': {'total_count': len(checks), 'check_runs': checks},
            '/actions/runs': {'total_count': 1, 'workflow_runs': [run]}, '/actions/runs/30': run,
            '/actions/workflows/40': {'id': 40, 'path': policy['trusted_ci_workflow'], 'state': 'active'},
            '/actions/runs/30/attempts/1/jobs': {'total_count': len(jobs), 'jobs': jobs}}
        self.gql_state = dict(databaseId=700, number=reference.pr, state='OPEN', isDraft=False,
            headRefOid=reference.head, baseRefOid=reference.base, reviewDecision='APPROVED', mergeable='MERGEABLE',
            mergeStateStatus='CLEAN', mergeQueueEntry=None, autoMergeRequest=None)
        self.threads, self.requests = [], []
        self.owners = {'github': None, 'root': None, 'docs': None}
        self.users = {}
        self.http_hook = None
        self.calls = 0

    def digest(self):
        return fingerprint([self.rest_data, self.gql_state, self.threads, self.requests, self.owners, self.users])

    def _response(self, value):
        return 200, {'content-type': 'application/json'}, json.dumps(value).encode()

    def rest(self, path, headers):
        from urllib.parse import urlsplit, parse_qs
        with self.lock:
            self.calls += 1
            if self.http_hook: self.http_hook('rest', path)
            if self.http_failure: return self.http_failure, {}, b''
            parsed = urlsplit(path)
            key = parsed.path.removeprefix('/repos/sasumpi123/auto_trade')
            value = copy.deepcopy(self.rest_data[key])
            page = int(parse_qs(parsed.query).get('page', ['1'])[0]); start = (page-1)*100
            if type(value) is list: value = value[start:start+100]
            elif 'total_count' in value:
                for name in ('check_runs', 'workflow_runs', 'jobs'):
                    if name in value: value[name] = value[name][start:start+100]
            return self._response(value)

    def graphql(self, body, headers):
        with self.lock:
            self.calls += 1
            request = json.loads(body); operation = next(k for k, v in QUERIES.items() if v == request['query'])
            if self.http_hook: self.http_hook('graphql', operation)
            if self.http_failure: return self.http_failure, {}, b''
            if operation == 'user': return self._response({'data': {'user': self.users[request['variables']['login']]}})
            repo = {'databaseId': 888367411}
            if operation == 'owners': repo.update(copy.deepcopy(self.owners))
            elif operation == 'state': repo['pullRequest'] = copy.deepcopy(self.gql_state)
            else:
                rows = self.threads if operation == 'threads' else self.requests
                start = int((request['variables'].get('cursor') or 'p0')[1:])
                page = copy.deepcopy(rows[start:start+100]); more = start+100 < len(rows)
                repo['pullRequest'] = dict(headRefOid=self.gql_state['headRefOid'], baseRefOid=self.gql_state['baseRefOid'])
                repo['pullRequest']['reviewThreads' if operation == 'threads' else 'reviewRequests'] = dict(totalCount=len(rows), nodes=page,
                    pageInfo={'hasNextPage': more, 'endCursor': f'p{start+100}' if more else None})
            return self._response({'data': {'repository': repo}})

    def reader(self, policy):
        return GitHubMergeReader(GitHubREST(policy['repository'], 'test-fake-auth', send=self.rest),
                                ReadOnlyGraphQL(policy['repository'], 'test-fake-auth', send=self.graphql), policy)

    def compare_and_merge(self, reference, expected_world):
        with self.lock:
            if self.before_compare: self.before_compare()
            require(self.digest() == expected_world, 'fake_server_conditions_changed')
            require(reference.task not in self.receipts, 'fake_merge_already_applied')
            receipt = dict(task=reference.task, head=reference.head, base=reference.base, issue=reference.issue, pr=reference.pr,
                           fake_commit=hashlib.sha1(('FAKE-MERGE:'+reference.task+reference.head+reference.base).encode()).hexdigest())
            self.receipts[reference.task] = receipt; self.effects += 1
            self.gql_state['state'] = 'MERGED'
            self.rest_data[f'/pulls/{reference.pr}'].update(state='closed', merged=True)
            if self.lose_response: raise WorkerBlocked('fake_merge_reply_lost')
            return copy.deepcopy(receipt)


class FakeMergeBroker:
    def __init__(self, pipeline, world, validated, zero_policy, *, quota=SubscriptionEvidence()):
        require(type(pipeline) is FakePipeline and type(world) is FakeMergeWorld and type(validated) is ValidatedPatch, 'exact_fake_merge_scope_required')
        self.pipeline, self.world, self.validated = pipeline, world, validated
        self.contract, self.quota = SubscriptionContract(zero_policy), quota
        self.policy = dict(pipeline.coordinator.policy, builder_ids=[1001], independent_reviewer_ids=[2002])
        self.reference = self._binding()

    def _binding(self):
        c = self.pipeline.coordinator; patch = self.validated.patch
        c.sync(patch.issue)
        item = c.state(patch.issue); task = c.store.read()['tasks'][patch.task]
        require(item['current_task'] == patch.task and item['status'] == 'MERGE_CANDIDATE' and task['status'] == 'PUBLISHED', 'p3_current_candidate_required')
        require(item['head'] == patch.head and item['spec'] == patch.spec_sha256 and item['spec_version'] == patch.spec_version
                and c.authority.reviews.get(patch.issue) is not None, 'p3_task_binding_unverified')
        source = c.authority.reviews[patch.issue]
        require(fingerprint([source.review_id, source.state, source.text, source.head]) == item['review_hash'], 'p3_review_changed')
        receipt = self.pipeline.publisher.lookup(patch.task)
        require(receipt == task['receipt'] and receipt['patch'] == self.validated.digest and receipt['head'] == patch.head
                and self.pipeline.publisher.refs.get(patch.issue) == patch.head and self.pipeline.publisher.base == patch.approved_base, 'p3_publication_unverified')
        return TaskReference(patch.task, patch.issue, item['pr'], item['spec'], item['spec_version'], item['base'],
                             item['head'], item['head_version'], fingerprint(sorted(x.path for x in patch.changes)))

    def _record(self, kind, apply, event=None):
        return self.pipeline.coordinator._change(kind, [self.reference.task, asdict(self.reference)], apply, event)

    def prepare(self):
        with self.pipeline.coordinator.authority.lock, self.world.lock:
            return self._prepare()

    def _prepare(self):
        require(self._binding() == self.reference, 'task_binding_changed')
        self.contract.preflight(self.quota)
        with self.world.lock:
            before = self.world.digest()
            evidence = self.world.reader(self.policy).inspect(self.reference)
            require(before == self.world.digest(), 'fake_world_changed_during_read')
        def apply(state, now):
            rows = state.setdefault('merge', {})
            require(self.reference.task not in rows, 'merge_stage_already_recorded')
            rows[self.reference.task] = dict(status='MERGE_ELIGIBLE', head=self.reference.head, base=self.reference.base,
                 evidence=evidence.evidence_sha, world=before, expires=now+15, reference=asdict(self.reference), receipt=None)
            return {'status': 'MERGE_ELIGIBLE', 'fake_only': True, 'execution_enabled': False}
        return self._record('MERGE_GATE', apply)

    def simulate(self, *, event=None):
        with self.pipeline.coordinator.authority.lock, self.world.lock:
            return self._simulate(event=event)

    def _simulate(self, *, event=None):
        task = self.reference.task; c = self.pipeline.coordinator
        row = c.store.read().get('merge', {}).get(task)
        require(row is not None, 'merge_candidate_missing')
        if row['status'] == 'FAKE_MERGED':
            return {'status': 'FAKE_MERGED', 'duplicate': True, 'execution_enabled': False}
        try:
            require(row['status'] == 'MERGE_ELIGIBLE' and int(c.clock().timestamp()) < row['expires'], 'merge_candidate_expired_or_ambiguous')
            require(self._binding() == self.reference, 'task_binding_changed')
            self.contract.preflight(self.quota)
            fresh = self.world.reader(self.policy).inspect(self.reference)
            require(fresh.evidence_sha == row['evidence'] and self.world.digest() == row['world'], 'merge_evidence_changed')
            def intent(state, now):
                current = state['merge'][task]
                require(current['status'] == 'MERGE_ELIGIBLE' and now < current['expires'], 'merge_intent_cas_failed')
                current['status'] = 'MERGE_INTENT'
                return {'status': 'MERGE_INTENT'}
            self._record('MERGE_INTENT', intent, event)
            receipt = self.world.compare_and_merge(self.reference, row['world'])
            def finish(state, now):
                require(state['merge'][task]['status'] == 'MERGE_INTENT', 'merge_finish_cas_failed')
                state['merge'][task].update(status='FAKE_MERGED', receipt=receipt)
                return {'status': 'FAKE_MERGED', 'execution_enabled': False}
            return self._record('FAKE_MERGED', finish)
        except Exception:
            self.halt()
            raise WorkerBlocked('fake_merge_halted_reconciliation_required') from None

    def halt(self):
        def apply(state, now):
            record = state.setdefault('merge', {}).get(self.reference.task)
            if record and record['status'] != 'FAKE_MERGED': record['status'] = 'HALTED'
            return {'status': record['status'] if record else 'HALTED', 'execution_enabled': False}
        return self._record('MERGE_HALT', apply)

    def reconcile(self):
        # No replay of an intent, even after process interruption or an affirmative receipt.
        self.halt()
        require(self.reference.task in self.pipeline.coordinator.store.read().get('merge', {}), 'merge_candidate_missing')
        receipt = self.world.receipts.get(self.reference.task)
        matched = bool(receipt and receipt['head'] == self.reference.head and receipt['base'] == self.reference.base
                       and receipt['task'] == self.reference.task and receipt['pr'] == self.reference.pr)
        def apply(state, now):
            record = state['merge'][self.reference.task]
            if matched: record['receipt'] = copy.deepcopy(receipt)
            return {'status': record['status'], 'receipt_confirmed': matched, 'execution_enabled': False}
        return self._record('MERGE_RECEIPT', apply)


class DisabledLiveMergeBroker:
    def merge(self, *args, **kwargs):
        raise WorkerBlocked('live_merge_permanently_disabled_in_p4')
