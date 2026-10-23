"""Cold-file recovery tests reuse the real P7 journal and P8 Fake GitHub world."""
import copy
import json
from automation.cloud_resume import resume
from automation.handoff_journal import HandoffJournal
from automation.github_read import VerificationPending
from tests.test_review_handoff import HandoffFixture


class RecoveryTests(HandoffFixture):
    def setUp(self):
        super().setUp()
        self.world_data.rest_data['/pulls']=[copy.deepcopy(self.world_data.rest_data[f'/pulls/{self.ref.pr}'])]
        self.j=HandoffJournal.initialize(self.journal_path,self.fake_policy)

    def issue(self,head=None):
        return self.j.issue(self.ref.pr,head or self.ref.head,'a'*64,'b'*64)

    def image(self):return self.journal_path.read_bytes(),self.j.seal.read_bytes()

    def restore(self,pair):
        self.journal_path.write_bytes(pair[0]);self.journal_path.chmod(0o600)
        self.j.seal.write_bytes(pair[1]);self.j.seal.chmod(0o600)

    def packet(self):return resume(self.reader,journal_path=self.journal_path)

    def saved(self):
        packet=self.packet();path=self.directory/'recovery.json';path.write_text(json.dumps(packet))
        return path

    def halted(self,report):
        h=report['rework_history']
        self.assertEqual(h['rework_state'],'HALTED');self.assertEqual(h['remaining_allowance'],'UNKNOWN_NOT_RESET')
        self.assertFalse(h['initialization_permitted']);self.assertFalse(h['automatic_rework_enabled'])
        self.assertEqual(h['rollback_resistance'],'UNPROVEN')

    def test_restart_preserves_receipt_and_budget(self):
        receipt=self.issue();self.j=HandoffJournal(self.journal_path,self.fake_policy)
        self.assertEqual(self.issue()['receipt'],receipt['receipt'])
        self.assertTrue(self.issue()['duplicate'])
        self.issue('f'*40)
        with self.assertRaisesRegex(VerificationPending,'rework_limit'):self.issue('e'*40)
        self.assertEqual(self.packet()['rework_history']['issued_count'],2)
        self.halted(self.packet())

    def test_missing_either_or_both_does_not_recreate(self):
        original=self.image()
        for target in ('db','seal','both'):
            self.restore(original)
            if target in ('db','both'):self.journal_path.unlink()
            if target in ('seal','both'):self.j.seal.unlink()
            r=self.packet();self.halted(r)
            self.assertEqual(r['rework_history']['status'],'BLOCKED')
            if target in ('db','both'):self.assertFalse(self.journal_path.exists())
            if target in ('seal','both'):self.assertFalse(self.j.seal.exists())

    def test_db_and_checkpoint_independent_corruption(self):
        original=self.image()
        for path in (self.journal_path,self.j.seal):
            self.restore(original);path.write_bytes(b'private-error-canary')
            r=self.packet();self.halted(r)
            self.assertEqual(r['rework_history']['status'],'BLOCKED')
            self.assertNotIn('private-error-canary',json.dumps(r))

    def test_single_file_rollback_detected(self):
        old=self.image();self.issue();new=self.image()
        for pair in ((old[0],new[1]),(new[0],old[1])):
            self.restore(pair);r=self.packet();self.halted(r)
            self.assertEqual(r['rework_history']['status'],'BLOCKED')

    def test_joint_rollback_without_anchor_is_undetectable_but_not_authorized(self):
        old=self.image();self.issue();self.restore(old)
        # Demonstrates a limitation; do not assert a detector that does not exist.
        r=self.packet();self.halted(r)
        self.assertEqual(r['rework_history']['issued_count'],0)
        self.assertEqual(r['rework_history']['status'],'LOCAL_COPY_VALID_CONTINUITY_UNPROVEN')

    def test_newer_user_retained_summary_detects_joint_rollback(self):
        old=self.image();self.issue();anchor=self.saved();self.restore(old)
        r=resume(self.reader,journal_path=self.journal_path,recovery_evidence=anchor)
        self.halted(r)
        self.assertEqual(r['rework_history']['evidence_comparison'],'BLOCKED_UNVERIFIED')

    def test_anchor_rolled_back_together_cannot_detect_it(self):
        old=self.image();anchor=self.saved();self.issue();self.restore(old)
        r=resume(self.reader,journal_path=self.journal_path,recovery_evidence=anchor)
        self.halted(r)
        self.assertEqual(r['rework_history']['evidence_comparison'],'LOCAL_PREFIX_MATCH_NOT_TRUSTED_WITNESS')

    def test_summary_then_append_is_consistent_not_execution_permission(self):
        self.issue();anchor=self.saved();self.issue('f'*40)
        r=resume(self.reader,journal_path=self.journal_path,recovery_evidence=anchor)
        self.halted(r);self.assertEqual(r['rework_history']['issued_count'],2)
        self.assertEqual(r['rework_history']['evidence_comparison'],'LOCAL_PREFIX_MATCH_NOT_TRUSTED_WITNESS')

    def test_fresh_epoch_after_reset_does_not_match_anchor(self):
        self.issue();anchor=self.saved();self.journal_path.unlink();self.j.seal.unlink()
        # Only a test simulating prohibited manual reset. Production does not reset.
        self.j=HandoffJournal.initialize(self.journal_path,self.fake_policy)
        r=resume(self.reader,journal_path=self.journal_path,recovery_evidence=anchor)
        self.halted(r);self.assertEqual(r['rework_history']['status'],'BLOCKED')

    def test_summary_no_sensitive_payload_and_no_writes(self):
        self.issue();before=self.image();summary=self.j.recovery_summary()
        self.assertEqual(before,self.image())
        self.assertEqual(set(summary),{'version','epoch','revision','policy','state_digest','receipts'})
        self.assertEqual(set(summary['receipts'][0]),{'pr','head','receipt'})
        self.assertNotIn('feedback',json.dumps(summary));self.assertNotIn(str(self.journal_path),json.dumps(summary))

    def test_local_head_compared_to_fresh_github_state(self):
        self.issue();r=self.packet()
        self.assertEqual(r['rework_history']['receipt_head_relation'],'CURRENT_HEAD_RECORDED')
        self.assertEqual(r['ci']['run_id'],30);self.assertEqual(r['ci']['attempt'],1)
        self.issue('f'*40);r=self.packet();self.halted(r)
        self.assertEqual(r['rework_history']['receipt_head_relation'],'OTHER_HEAD_OR_STALE_REQUERY_REQUIRED')
        self.assertEqual(r['head'],self.ref.head)

    def test_forged_anchor_or_wrong_pr_fail_closed(self):
        self.issue();anchor=self.saved();data=json.loads(anchor.read_text())
        data['rework_history']['recovery_evidence']['receipts'][0]['receipt']='f'*64
        anchor.write_text(json.dumps(data))
        r=resume(self.reader,journal_path=self.journal_path,recovery_evidence=anchor)
        self.halted(r);self.assertEqual(r['rework_history']['status'],'BLOCKED')

    def test_recovery_cli_exit_two_even_when_local_files_are_valid(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        import sys
        from automation.cloud_resume import main
        with patch('automation.cloud_resume.HandoffReader',return_value=self.reader),patch('automation.cloud_resume.load_policy',return_value=self.fake_policy):
            with patch.object(sys,'argv',['cloud_resume','--pr',str(self.ref.pr),'--journal',str(self.journal_path),'--format','json']):
                stream=StringIO()
                with redirect_stdout(stream):code=main()
        self.assertEqual(code,2)
        self.halted(json.loads(stream.getvalue()))
