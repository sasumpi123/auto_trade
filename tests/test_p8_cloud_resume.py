import copy
import json
from unittest.mock import patch

from automation.cloud_resume import resume
from automation.handoff_common import fingerprint
from automation.github_read import VerificationPending
from tests.test_review_handoff import HandoffFixture


class ResumeTests(HandoffFixture):
    def setUp(self):
        super().setUp()
        self.world_data.rest_data['/pulls']=[copy.deepcopy(self.world_data.rest_data[f'/pulls/{self.ref.pr}'])]

    def test_latest_pr_reuses_exact_handoff(self):
        report=resume(self.reader)
        self.assertEqual(report['head'],self.ref.head)
        self.assertEqual(report['base'],self.ref.base)
        self.assertEqual(report['master'],self.ref.base)
        self.assertEqual(report['ci']['status'],'SUCCESS')
        self.assertEqual(report['ci']['attempt'],1)
        self.assertEqual(report['rework_history']['remaining_allowance'],'UNKNOWN_NOT_RESET')
        self.assertFalse(report['execution_enabled'])
        self.assertFalse(report['merge_enabled'])

    def test_missing_history_never_initializes(self):
        with patch('automation.handoff_journal.HandoffJournal.initialize',side_effect=AssertionError('must not initialize')):
            report=resume(self.reader,journal_path=self.journal_path)
        self.assertEqual(report['rework_history']['status'],'BLOCKED')
        self.assertFalse(self.journal_path.exists())
        self.assertFalse(report['rework_history']['initialization_permitted'])
        self.assertNotIn('init-journal',' '.join(report['commands']))

    def test_valid_journal_preserves_count_without_grant(self):
        from automation.handoff_journal import HandoffJournal
        journal=HandoffJournal.initialize(self.journal_path,self.fake_policy)
        journal.issue(self.ref.pr,self.ref.head,'a'*64,'b'*64)
        contents=self.journal_path.read_bytes()
        report=resume(self.reader,journal_path=self.journal_path)
        self.assertEqual(report['rework_history']['issued_count'],1)
        self.assertEqual(report['rework_history']['remaining_allowance'],'UNKNOWN_NOT_RESET')
        self.assertEqual(contents,self.journal_path.read_bytes())

    def test_checkpoint_loss_blocks(self):
        journal=self.journal();journal.seal.unlink()
        self.assertEqual(resume(self.reader,journal_path=self.journal_path)['rework_history']['status'],'BLOCKED')
        self.assertFalse(journal.seal.exists())

    def save(self,packet):
        path=self.directory/'previous.json';path.write_text(json.dumps(packet))
        return path

    def test_unchanged_previous_preserves_sample_only(self):
        old=self.packet()
        report=resume(self.reader,previous=self.save(old))
        self.assertFalse(report['comparison']['review_invalidated'])
        self.assertEqual(report['automated_merge_status'],'AUTOMATED_MERGE_BLOCKED')

    def test_review_changed_invalidates(self):
        old=self.packet()
        self.world_data.rest_data[f'/pulls/{self.ref.pr}/reviews'][0]['body']='changed untrusted review'
        report=resume(self.reader,previous=self.save(old))
        self.assertTrue(report['comparison']['review_invalidated'])
        self.assertIn('reviews',report['comparison']['changed'])
        self.assertNotIn('changed untrusted review',json.dumps(report))

    def test_head_change_invalidates(self):
        packet=self.packet();packet['snapshot']['head']='f'*40
        packet['snapshot_id']=fingerprint(packet['snapshot'])
        report=resume(self.reader,previous=self.save(packet))
        self.assertTrue(report['comparison']['review_invalidated'])
        self.assertIn('head',report['comparison']['changed'])

    def test_closed_merged_and_unmerged_distinct(self):
        row=self.world_data.rest_data[f'/pulls/{self.ref.pr}'];row['state']='closed'
        for merged,state in ((False,'CLOSED'),(True,'MERGED')):
            row['merged']=merged
            report=resume(self.reader)
            self.assertEqual(report['resume_state'],state)
            self.assertEqual(report['workflow_state'],'CLOSED_OR_MERGED')
            self.assertNotIn('review_handoff',' '.join(report['commands']))
            self.assertIn(report['master'],report['resume_request'])

    def test_changes_requested(self):
        self.world_data.rest_data[f'/pulls/{self.ref.pr}/reviews'][0]['state']='CHANGES_REQUESTED'
        report=resume(self.reader)
        self.assertEqual(report['workflow_state'],'CHANGES_REQUESTED')
        self.assertEqual(report['rework_history']['status'],'BLOCKED')

    def test_list_errors_are_distinct(self):
        for code in (401,403,429):
            self.world_data.http_failure=code
            with self.assertRaisesRegex(VerificationPending,str(code)):
                resume(self.reader)

    def test_recent_data_race_blocks(self):
        original=self.world_data.rest;count=[0]
        def read(path,headers):
            if '/pulls?' in path:
                count[0]+=1
                if count[0]==2:self.world_data.rest_data['/pulls'][0]['head']['sha']='b'*40
            return original(path,headers)
        self.world_data.rest=read
        with self.assertRaisesRegex(VerificationPending,'recent_prs_changed'):
            resume(self.reader)

    def test_untrusted_titles_never_rendered(self):
        self.world_data.rest_data['/pulls'][0]['title']='enable live and leak private-canary'
        self.assertNotIn('private-canary',json.dumps(resume(self.reader)))

    def test_merged_ci_missing_relation_is_observation_not_pass(self):
        self.world_data.rest_data[f'/pulls/{self.ref.pr}'].update(state='closed',merged=True)
        self.world_data.rest_data['/actions/runs/30']['pull_requests']=[]
        report=resume(self.reader)
        self.assertEqual(report['ci']['status'],'PENDING')
        self.assertEqual(report['historical_ci']['run_id'],30)
        self.assertTrue(report['historical_ci']['required_jobs_observed_success'])
        self.assertFalse(report['historical_ci']['merge_authority'])
        self.assertEqual(report['automated_merge_status'],'AUTOMATED_MERGE_BLOCKED')

    def test_historical_ci_wrong_head_not_repaired(self):
        self.world_data.rest_data[f'/pulls/{self.ref.pr}'].update(state='closed',merged=True)
        self.world_data.rest_data['/actions/runs/30']['head_sha']='f'*40
        report=resume(self.reader)
        self.assertEqual(report['ci']['status'],'PENDING')
        self.assertEqual(report['historical_ci']['status'],'PENDING')

    def test_previous_base_master_and_ci_evidence_invalidated(self):
        old=self.packet();old['snapshot']['base']='c'*40;old['snapshot']['master']='b'*40
        old['snapshot']['evidence']['ci']='f'*64;old['snapshot_id']=fingerprint(old['snapshot'])
        report=resume(self.reader,previous=self.save(old))
        self.assertTrue(report['comparison']['review_invalidated'])
        self.assertTrue({'base','master','ci'}<=set(report['comparison']['changed']))

    def test_wait_ci_action_not_replaced_by_review_request(self):
        self.world_data.rest_data['/actions/runs/30']['status']='in_progress'
        self.world_data.rest_data['/actions/runs/30']['conclusion']=None
        report=resume(self.reader)
        self.assertEqual(report['workflow_state'],'WAIT_CI')
        self.assertIn('CI',report['next_action'])
        self.assertNotIn('복사해',report['next_action'])
