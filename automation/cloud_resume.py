"""P8 read-only Cloud session summary. Never creates/resets a journal or starts work."""
import json
from .handoff_common import (CachedREST, HandoffReader, Parser, REPOSITORY, REPOSITORY_ID,
    SHA, differences, fingerprint, load_policy, previous_snapshot, require, render, halted)
from .handoff_journal import HandoffJournal

RECENT='/pulls?state=all&sort=updated&direction=desc&per_page=5'


def recent(transport):
    status, _, body=transport.rest('/repos/'+REPOSITORY+RECENT,{})
    require(status==200, {401:'HTTP_401_AUTH_UNVERIFIED',403:'HTTP_403_PERMISSION_OR_RATE_LIMIT',
                         429:'HTTP_429_RATE_LIMIT'}.get(status,'recent_prs_unverified'))
    rows=json.loads(body)
    require(type(rows) is list and len(rows)<=5,'recent_prs_unverified')
    result=[]
    for row in rows:
        require(type(row['number']) is int and row['number']>0 and row['state'] in ('open','closed')
                and all(SHA.fullmatch(row[k]['sha']) and row[k]['repo']['id']==REPOSITORY_ID for k in ('head','base')),
                'recent_prs_unverified')
        result.append({'pr':row['number'],'state':row['state'],'head':row['head']['sha'],
                       'base':row['base']['sha'],'ci':'NOT_COLLECTED_UNLESS_FOCUSED'})
    require(len({r['pr'] for r in result})==len(result),'recent_prs_unverified')
    return result


def historical_ci(reader, report):
    """Display closed-PR run metadata without repairing missing P2 PR linkage."""
    def sample():
        api=CachedREST(reader.transport)
        pull=api.get(f'/pulls/{report["pr"]}')
        require(pull['head']['sha']==report['head'] and pull['base']['sha']==report['base']
                and pull['state']=='closed' and pull['merged']==report['merged'], 'historical_ci_unverified')
        runs=api.pages(f'/actions/runs?head_sha={report["head"]}&event=pull_request','workflow_runs')
        trusted=[r for r in runs if r['path']==reader.policy['trusted_ci_workflow']]
        require(trusted,'historical_ci_unverified')
        latest=max(trusted,key=lambda r:r['id']);run=api.get(f'/actions/runs/{latest["id"]}')
        require(type(run['id']) is int and run['id']==latest['id'] and run['head_sha']==report['head']
                and run['repository']['id']==run['head_repository']['id']==REPOSITORY_ID
                and run['path']==reader.policy['trusted_ci_workflow'] and run['event']=='pull_request'
                and run['head_branch']==pull['head']['ref']
                and type(run['run_attempt']) is int and run['run_attempt']>0,'historical_ci_unverified')
        workflow=api.get(f'/actions/workflows/{run["workflow_id"]}')
        require(workflow['id']==run['workflow_id'] and workflow['path']==run['path']
                and workflow['state']=='active','historical_ci_unverified')
        raw=api.pages(f'/actions/runs/{run["id"]}/attempts/{run["run_attempt"]}/jobs','jobs')
        jobs=[]
        for name in reader.policy['required_checks']:
            matches=[r for r in raw if r['name']==name]
            require(len(matches)==1,'historical_ci_unverified')
            job=matches[0]
            require(job['run_id']==run['id'] and job['head_sha']==report['head']
                    and type(job['id']) is int and job['id']>0,'historical_ci_unverified')
            require(job['status'] in ('queued','in_progress','completed','waiting','pending','requested')
                    and job['conclusion'] in (None,'success','failure','cancelled','skipped','neutral','timed_out','action_required','stale','startup_failure'),
                    'historical_ci_unverified')
            jobs.append({k:job[k] for k in ('id','name','status','conclusion')})
        require(run['status'] in ('queued','in_progress','completed','waiting','pending','requested')
                and run['conclusion'] in (None,'success','failure','cancelled','skipped','neutral','timed_out','action_required','stale','startup_failure'),
                'historical_ci_unverified')
        return {'status':'HISTORICAL_OBSERVATION_NOT_MERGE_EVIDENCE','run_id':run['id'],
                'attempt':run['run_attempt'],'head':run['head_sha'],'workflow':run['path'],
                'run_status':run['status'],'conclusion':run['conclusion'],'jobs':jobs,
                'required_jobs_observed_success':all(j['status']=='completed' and j['conclusion']=='success' for j in jobs),
                'pr_linkage':'PENDING_P2_NOT_SATISFIED','merge_authority':False}
    first=sample();second=sample()
    require(first==second,'historical_ci_unverified')
    return second


def resume(reader, *, pr=None, previous=None, journal_path=None):
    before=recent(reader.transport)
    if pr is None:
        require(before,'no_recent_pr')
        pr=next((r['pr'] for r in before if r['state']=='open'),before[0]['pr'])
    report=reader.collect(pr)
    require(recent(reader.transport)==before,'recent_prs_changed_during_resume')
    prior=previous_snapshot(previous,pr) if previous else None
    report['comparison']=differences(prior,report['snapshot'])
    report['recent_prs']=before
    if report['pr_state']=='closed' and report.get('ci',{}).get('status')=='PENDING':
        try:report['historical_ci']=historical_ci(reader,report)
        except Exception:report['historical_ci']={'status':'PENDING','reason':'HISTORICAL_CI_UNVERIFIED','merge_authority':False}
    report['resume_state']=('MERGED' if report['merged'] else 'CLOSED' if report['pr_state']=='closed'
                            else report['workflow_state'])
    # A valid local copy is evidence of its contents, NOT durable Cloud continuity.
    history={'status':'BLOCKED','reason':'LOCAL_RECEIPTS_MISSING_OR_UNVERIFIED','issued_count':None,
             'remaining_allowance':'UNKNOWN_NOT_RESET','initialization_permitted':False,
             'automatic_rework_enabled':False}
    if journal_path:
        try:
            journal=HandoffJournal(journal_path,reader.policy)
            with journal._transaction() as (_,state):
                count=sum(row['pr']==pr for row in state['requests'])
            history.update(status='LOCAL_COPY_VALID_CONTINUITY_UNPROVEN',reason='CLOUD_DURABILITY_NOT_PROVEN',issued_count=count)
        except Exception:pass  # No original paths, exception text, raw DB data.
    report['rework_history']=history
    report['commands']=[f'python -m automation.review_handoff --pr {pr} --format prompt',
                        f'python -m automation.cloud_resume --pr {pr} --format markdown']
    if report['merged']:
        report['next_action']='GitHub에서 병합을 확인했습니다. 최신 master 기준 다음 사용자 승인 작업을 계획하세요.'
    elif report['pr_state']=='closed':
        report['next_action']='병합되지 않고 닫힌 PR입니다. 재작업하지 말고 다음 작업을 사용자와 확인하세요.'
    elif report['workflow_state']=='CHANGES_REQUESTED':
        report['next_action']='최신 head의 수정 요청을 확인하세요. 영수증/한도 연속성 확인 전 재작업을 발급하거나 초기화하지 마세요.'
    elif report['workflow_state']=='PR_READY_FOR_REVIEW' and report['comparison']['review_invalidated']:
        report['next_action']='이전 검토를 재사용하지 말고 최신 리뷰 요청문을 복사해 ChatGPT에 전달하세요.'
    if report['pr_state']=='closed':
        report['commands']=['python -m automation.cloud_resume --format markdown']
        report['resume_request']=(f'저장소 {REPOSITORY}의 master {report["master"]}와 PR #{pr} 상태를 GitHub에서 다시 확인하세요. '
            'AGENTS.md와 PROJECT_INVARIANTS.md를 따르고 다음 사용자 승인 작업을 계획하세요. '
            '닫힌 PR을 재작업하거나 한도를 초기화하지 마세요. 실제 모델/게시/병합/Slack/거래/배포는 자동 활성화하지 마세요.')
    return report


def main():
    parser=Parser(description=__doc__)
    parser.add_argument('--pr',type=int)
    parser.add_argument('--previous')
    parser.add_argument('--journal')
    parser.add_argument('--format',choices=('json','markdown','prompt'),default='markdown')
    args=parser.parse_args()
    try:
        report=resume(HandoffReader(load_policy()),pr=args.pr,previous=args.previous,journal_path=args.journal)
    except Exception as error:
        report=halted(error)
    if args.format=='json' or report['workflow_state']=='HALTED':
        print(render(report,'json' if args.format=='json' else 'markdown'))
    else:
        print(report['resume_request'] if args.format=='prompt' and 'resume_request' in report else render(report,args.format))
        print('\nCloud 복구: '+report['resume_state'])
        for item in report['recent_prs']:
            print(f'최근 PR #{item["pr"]}: {item["state"]}; head {item["head"]}; base {item["base"]}')
        if 'historical_ci' in report:
            print('병합/닫힘 PR의 과거 CI 관찰 (P2 gate 대체 아님): '+json.dumps(report['historical_ci'],ensure_ascii=False))
        print('재작업 한도: UNKNOWN_NOT_RESET; 새 초기화 금지. 유효한 로컬 복사본도 Cloud 연속성 증명 아님.')
        print(report['next_action'])
        print('\n복사할 명령:\n```text\n'+'\n'.join(report['commands'])+'\n```')
    return 2 if report['workflow_state']=='HALTED' else 0


if __name__=='__main__':
    raise SystemExit(main())
