"""Manual decision aid only. No merge API, automatic eligibility or approval token."""
from .handoff_common import (HandoffReader, Parser, WORKFLOW_STATES, differences, halted,
                             load_policy, previous_snapshot, render, require)


def assess(reader,pr,previous=None,reviewed_head=None):
    report=reader.collect(pr)
    report['comparison']=differences(previous,report['snapshot'])
    blockers=['AUTOMATION_DISABLED','ZERO_COST_EXECUTION_UNPROVEN','P4_TRUSTED_TASK_LEDGER_NOT_BOUND']
    if report.get('reviews',{}).get('status')!='APPROVED':blockers.append('P4_INDEPENDENT_REVIEW_UNSATISFIED')
    if report.get('protection',{}).get('status')!='VERIFIED':blockers.append('PROTECTION_UNSATISFIED_OR_UNREADABLE')
    if report.get('issue',{}).get('status')!='VERIFIED':blockers.append('ISSUE_APPROVAL_UNSATISFIED')
    if report.get('ci',{}).get('status')!='SUCCESS':blockers.append('LATEST_CI_UNSATISFIED')
    if report.get('threads',{}).get('status')!='VERIFIED':blockers.append('THREADS_UNSATISFIED_OR_UNREADABLE')
    if report.get('protection',{}).get('codeowners_required'):
        blockers.append('CODEOWNERS_OWNER_APPROVAL_NOT_VERIFIED_BY_HANDOFF')
    if report['protected_paths_changed']:blockers.append('POLICY_PROTECTED_PATHS_REQUIRE_MANUAL_REVIEW')
    if report['base']!=report['master']:blockers.append('MASTER_BASE_CHANGED')
    if report['mergeable'] is not True or report['draft']:blockers.append('PR_NOT_CONFIRMED_MERGEABLE')
    report['automated_blockers']=blockers
    report['manual_review_claim']='NOT_PROVIDED'
    if report['pr_state']=='closed':return report
    if reviewed_head is not None:
        require(reviewed_head==report['head'],'stale_reviewed_head')
        require(previous is not None and report['comparison']['status']=='UNCHANGED','stale_review_snapshot')
        # This is a user-declared UI milestone, never a GitHub Review or P4 proof.
        report['manual_review_claim']='USER_DECLARED_FOR_UNCHANGED_SAMPLE_NOT_GITHUB_APPROVAL'
        if (report.get('ci',{}).get('status')=='SUCCESS' and report.get('threads',{}).get('status')=='VERIFIED'
            and report.get('reviews',{}).get('status') not in ('PENDING','CHANGES_REQUESTED','BLOCKED')
            and (report.get('issue',{}).get('status')=='VERIFIED' or report.get('issue',{}).get('reason')=='ISSUE_CONTRACT_MISSING')
            and report['mergeable'] is True and not report['draft'] and report['base']==report['master']):
            report['workflow_state']='REVIEW_COMPLETE_MANUAL_DECISION_REQUIRED'
        else:
            report['workflow_state']=('CHANGES_REQUESTED' if report.get('reviews',{}).get('status')=='CHANGES_REQUESTED' else
                                      'WAIT_CI' if report.get('ci',{}).get('status')!='SUCCESS' else 'HALTED')
    else:
        if report['workflow_state']=='PR_READY_FOR_REVIEW':report['workflow_state']='WAIT_CHATGPT_REVIEW'
    report['next_action']=WORKFLOW_STATES[report['workflow_state']]
    return report


def main():
    parser=Parser(description=__doc__)
    parser.add_argument('--pr',type=int,required=True)
    parser.add_argument('--previous')
    parser.add_argument('--reviewed-head')
    parser.add_argument('--format',choices=('json','markdown'),default='markdown')
    args=parser.parse_args()
    try:
        prior=previous_snapshot(args.previous,args.pr) if args.previous else None
        report=assess(HandoffReader(load_policy()),args.pr,prior,args.reviewed_head)
    except Exception as error:report=halted(error)
    print(render(report,args.format))
    # Exit 2 is deliberate: even manual-ready never authorizes automatic merge.
    return 2


if __name__=='__main__':
    raise SystemExit(main())
