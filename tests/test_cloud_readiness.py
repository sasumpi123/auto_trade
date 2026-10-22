"""P6 tests: exact Fake HTTP evidence, no network/model/GitHub writer."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
from uuid import uuid4

from automation.cloud_readiness import (GhReadOnly, ReadinessInspector, cost_readiness,
                                         review_readiness, REPOSITORY, STATES)
from automation.github_graphql import QUERIES
from automation.github_read import VerificationPending
from automation.worker_contracts import Feedback, WorkerBlocked
from tests import test_review_merge_gate as p4

ROOT = Path(__file__).resolve().parents[1]


class ReadinessReportTests(p4.MergeFixture):
    def fixture(self):
        value, world, ref = self.candidate()
        world.rest_data['']['visibility'] = 'public'
        world.rest_data['/actions/permissions/workflow'] = {}
        world.rest_data['/installation'] = {}
        inspector = ReadinessInspector(self.fake_policy,world)
        return value,world,ref,inspector

    def rows(self, inspector):
        report = inspector.inspect(10042)
        self.assertEqual(report['automation_status'],'DISABLED')
        for key in ('execution_enabled','publish_enabled','merge_enabled','activation_authorized'):
            self.assertIs(report[key],False)
        self.assertTrue(all(row['status'] in STATES for row in report['checks']))
        return {row['check']:row for row in report['checks']}

    def test_good_evidence_remains_disabled(self):
        _,world,_,inspector = self.fixture()
        rows = self.rows(inspector)
        for name in ('repository','master','required_ci','review_threads','standard_independent_review'):
            self.assertEqual(rows[name]['status'],'READY',name)
        self.assertEqual(world.effects,0)
        self.assertEqual(rows['purchased_credit_consumption']['status'],'BLOCKED')

    def test_missing_master_protection_is_not_api_error(self):
        _,world,_,inspector=self.fixture()
        world.rest_data['/branches/master']['protected']=False
        rows=self.rows(inspector)
        self.assertEqual(rows['branch_protection']['status'],'BLOCKED')
        self.assertEqual(rows['branch_rules']['reason'],'NO_ACTIVE_RULES')

    def test_missing_issue_contract_does_not_hide_ci_or_graphql(self):
        _,world,ref,inspector=self.fixture()
        world.rest_data[f'/pulls/{ref.pr}']['body']='manual PR, no authorization'
        rows=self.rows(inspector)
        self.assertEqual(rows['observer']['reason'],'ISSUE_CONTRACT_MISSING')
        self.assertEqual(rows['required_ci']['status'],'READY')
        self.assertEqual(rows['review_threads']['status'],'READY')

    def test_merged_run_missing_pr_relation_stays_pending(self):
        _,world,_,inspector=self.fixture()
        world.rest_data['/actions/runs/30']['pull_requests']=[]
        rows=self.rows(inspector)
        self.assertEqual(rows['required_ci']['reason'],'CI_RUN_PR_HEAD_WORKFLOW_BINDING_UNVERIFIED')
        self.assertEqual(rows['review_threads']['status'],'READY')

    def test_api_authentication_and_permission_not_contract(self):
        _,world,_,inspector=self.fixture()
        for code in (401,403,429):
            with self.subTest(code=code):
                # Reuse one fixture without claiming additional fake tasks.
                world.http_failure=code
                rows=self.rows(inspector)
                self.assertNotEqual(rows['repository']['status'],'READY')
                self.assertNotIn('ISSUE_CONTRACT_MISSING',json.dumps(rows))

    def test_observer_workflow_run_without_pr_is_separate_cause(self):
        _,world,_,inspector=self.fixture()
        world.rest_data['/actions/runs/30']['pull_requests']=[]
        result=inspector.inspect(10042,observer_ci_run=30)
        row=next(r for r in result['checks'] if r['check']=='observer_run_relation')
        self.assertEqual(row['reason'],'RUN_PR_RELATION_UNVERIFIED')

    def test_same_account_approval_blocked(self):
        _,world,ref,inspector=self.fixture()
        world.rest_data[f'/pulls/{ref.pr}/reviews'][0]['user']['id']=1001
        inspector.policy['independent_reviewer_ids']=[1001]
        rows=self.rows(inspector)
        self.assertEqual(rows['standard_independent_review']['status'],'BLOCKED')
        self.assertEqual(rows['observed_review_identity']['status'],'BLOCKED')

    def test_comment_not_standard_approval(self):
        _,world,ref,inspector=self.fixture()
        world.rest_data[f'/pulls/{ref.pr}/reviews'][0]['state']='COMMENTED'
        self.assertEqual(self.rows(inspector)['standard_independent_review']['reason'],'WAIT_REVIEW')

    def test_unknown_account_features_and_no_mac_are_pending(self):
        _,_,_,inspector=self.fixture()
        rows=self.rows(inspector)
        for name in ('chatgpt_event_task','chatgpt_review_writer','macbook','included_quota'):
            self.assertEqual(rows[name]['status'],'PENDING')

    def test_changed_head_invalidates_prior_readiness(self):
        _,world,ref,inspector=self.fixture()
        calls=0
        original=world.rest
        def rest(path,headers):
            nonlocal calls
            if path.endswith(f'/pulls/{ref.pr}'):
                calls+=1
                # verifier's double reads occur before final recheck.
                if calls>=4:world.rest_data[f'/pulls/{ref.pr}']['head']['sha']='d'*40
            return original(path,headers)
        inspector.transport.rest=rest
        inspector.api._send=rest
        rows=self.rows(inspector)
        self.assertEqual(rows['required_ci']['status'],'PENDING')

    def test_unknown_graphql_schema_is_not_ready(self):
        _,world,_,inspector=self.fixture()
        world.threads=[dict(id='T1',isResolved='true',isOutdated=False)]
        rows=self.rows(inspector)
        self.assertEqual(rows['review_threads']['status'],'PENDING')

    def test_unresolved_thread_even_outdated_blocks(self):
        _,world,_,inspector=self.fixture()
        world.threads=[dict(id='T1',isResolved=False,isOutdated=True)]
        self.assertEqual(self.rows(inspector)['review_threads']['status'],'BLOCKED')

    def test_disclosure_does_not_include_body_or_feedback(self):
        _,world,ref,inspector=self.fixture()
        world.rest_data[f'/pulls/{ref.pr}/reviews'][0]['body']='private-canary'
        report=inspector.inspect(ref.pr)
        self.assertNotIn('private-canary',json.dumps(report))

    def test_revoked_issue_cannot_be_approval(self):
        _,world,_,inspector=self.fixture()
        world.rest_data['/issues/42']['labels']=[]
        rows=self.rows(inspector)
        self.assertNotEqual(rows['observer']['status'],'READY')

    def test_full_fake_pipeline_candidate_with_rework(self):
        first=self.published()
        item=self.coordinator.state(42)
        self.authority.review(Feedback(42,item['pr'],item['head'],item['head_version'],20,2002,'CHANGES_REQUESTED','Revise note'))
        self.pipeline.review(42)
        second=self.published(b'revised note\n')
        self.assertNotEqual(first.patch.head,second.patch.head)
        item=self.coordinator.state(42)
        self.authority.review(Feedback(42,item['pr'],item['head'],item['head_version'],21,2002,'APPROVED',''))
        self.pipeline.review(42);self.pipeline.ci(42,run_id=2)
        world,_=self.world(second)
        result=self.broker(second,world).prepare()
        self.assertEqual(result['status'],'MERGE_ELIGIBLE')
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(world.effects,0)

    def test_duplicate_event_does_not_dispatch_second_plan(self):
        event=str(uuid4())
        first=self.coordinator.claim(42,self.owner,event=event)
        again=self.coordinator.claim(42,self.owner,event=event)
        self.assertEqual(first,again)
        plan=self.coordinator.dispatch(first['id'],self.owner)
        self.assertEqual(plan.task,first['id'])
        with self.assertRaises(WorkerBlocked):self.coordinator.dispatch(again['id'],self.owner)


class CostContractTests(unittest.TestCase):
    def test_unknown_quota_stays_pending(self):
        self.assertEqual(cost_readiness()[0]['status'],'PENDING')

    def test_exhausted_usage_blocks(self):
        result=cost_readiness({'rateLimits':{'primary':{'usedPercent':100}}})
        self.assertEqual(result[0]['reason'],'QUOTA_EXHAUSTED')

    def test_non_exhausted_quota_cannot_prove_zero_cost(self):
        result=cost_readiness({'rateLimits':{'primary':{'usedPercent':1},'credits':{'balance':'100','hasCredits':True}}})
        self.assertNotIn('READY',[r['status'] for r in result])
        self.assertEqual(result[1]['status'],'BLOCKED')
        self.assertNotIn('100',json.dumps(result))

    def test_forged_proof_booleans_never_enable(self):
        result=cost_readiness({'zero_cost_proven':True,'purchased_credits_disabled':True,
                              'rateLimits':{'primary':{'usedPercent':0}}})
        self.assertEqual(result[1]['status'],'BLOCKED')

    def test_malformed_or_missing_quota_rejected(self):
        for value in ({},{'rateLimits':{}},{'rateLimits':{'primary':{'usedPercent':True}}},
                      {'rateLimits':{'primary':{'usedPercent':-1}}},{'rateLimits':{'primary':{'usedPercent':float('nan')}}}):
            self.assertEqual(cost_readiness(value)[0]['status'],'PENDING')

    def test_secondary_exhaustion_blocks(self):
        self.assertEqual(cost_readiness({'rateLimits':{'primary':{'usedPercent':0},'secondary':{'usedPercent':100}}})[0]['status'],'BLOCKED')


class GhTransportTests(unittest.TestCase):
    def response(self,status=200,body='{}'):
        return subprocess.CompletedProcess([],0,f'HTTP/2.0 {status} OK\nContent-Type: application/json\n\n{body}','')

    def test_uses_existing_gh_no_token_or_shell(self):
        with patch('subprocess.run',return_value=self.response()) as run:
            client=GhReadOnly();client.rest('/repos/'+REPOSITORY)
        args,kwargs=run.call_args
        self.assertEqual(args[0],['gh','api','--include','--method','GET','/repos/'+REPOSITORY])
        self.assertNotIn('shell',kwargs)

    def test_forbidden_writes_and_other_repository(self):
        client=GhReadOnly(run=lambda *a,**k:self.fail('network must not run'))
        for suffix in ('/merge','/secrets','/issues','/pulls/1/comments'):
            with self.assertRaises(VerificationPending):client.rest('/repos/'+REPOSITORY+suffix)
        with self.assertRaises(VerificationPending):client.rest('/repos/other/repo')

    def test_fixed_readonly_graphql_post(self):
        with patch('subprocess.run',return_value=self.response()) as run:
            GhReadOnly().graphql(json.dumps({'query':QUERIES['state'],'variables':{'owner':'sasumpi123','name':'auto_trade','number':14}}))
        self.assertIn('POST',run.call_args.args[0])

    def test_graphql_mutation_and_other_scope_rejected(self):
        client=GhReadOnly(run=lambda *a,**k:self.fail('must not run'))
        for value in ({'query':'mutation { deleteRepository }','variables':{}},
                      {'query':QUERIES['state'],'variables':{'owner':'other','name':'repo'}}):
            with self.assertRaises(VerificationPending):client.graphql(json.dumps(value))

    def test_auth_failure_discards_error_body(self):
        status,_,body=GhReadOnly(run=lambda *a,**k:self.response(401,'private-canary')).rest('/repos/'+REPOSITORY)
        self.assertEqual((status,body),(401,b''))

    def test_network_error_does_not_reflect_error_text(self):
        result=subprocess.CompletedProcess([],1,'','signed-url-canary Forbidden')
        with self.assertRaises(VerificationPending) as error:GhReadOnly(run=lambda *a,**k:result).rest('/repos/'+REPOSITORY)
        self.assertEqual(str(error.exception),'api_unavailable')

    def test_timeout_closed(self):
        with self.assertRaises(VerificationPending):
            GhReadOnly(run=lambda *a,**k:(_ for _ in ()).throw(subprocess.TimeoutExpired('canary',15))).rest('/repos/'+REPOSITORY)

    def test_request_limit(self):
        client=GhReadOnly(run=lambda *a,**k:self.fail('must not run'));client.requests=120
        with self.assertRaises(VerificationPending):client.rest('/repos/'+REPOSITORY)

    def test_http_403_and_401_remain_distinct_in_extra_checks(self):
        policy=json.loads((ROOT/'.github/automation/policy.json').read_text())
        for status,reason in ((401,'HTTP_401_AUTH_UNVERIFIED'),(403,'HTTP_403_PERMISSION_OR_RATE_LIMIT'),(404,'HTTP_404_ABSENT_OR_HIDDEN')):
            client=GhReadOnly(run=lambda *a,**k:self.response(status))
            inspector=ReadinessInspector(policy,client)
            inspector._extra('access','/installation')
            self.assertEqual(inspector.rows[0]['reason'],reason)

    def test_enabled_policy_rejected(self):
        with self.assertRaises(ValueError):ReadinessInspector({'enabled':True})
