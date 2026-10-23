# P9-B Cloud 기록 복구: 검증 가능 범위와 한계

기존 HandoffJournal/Cloud Resume를 재사용합니다. 새 원장·서버·DB·자동화 프레임워크를 만들지 않습니다. 실제 모델/게시/병합/Slack/거래/배포/스케줄은 계속 비활성입니다. 기록 복구 관찰 성공은 실행 허가가 아닙니다.

## 복구 결과

| 상황 | 확인한 동작 | 실행 판단 |
| --- | --- | --- |
| 정상 재시작 | 같은 receipt 중복 발급 억제, 기존 issued_count와 정책 상한 유지 | Cloud 연속성 미증명, HALTED |
| DB 또는 checkpoint 소실/손상 | 기존 검증이 차단, 새 파일/한도 생성 없음 | UNKNOWN_NOT_RESET |
| DB만 또는 checkpoint만 롤백 | epoch/revision/state hash 불일치 차단 | HALTED |
| DB+checkpoint 함께 롤백 | 일관된 과거 쌍은 기존 검증을 통과할 수 있음 | **탐지 보장 불가**, HALTED |
| 위 쌍보다 최신의 별도 보관 요약 존재 | revision 감소/epoch 변경/receipt prefix·state digest 불일치 확인 가능 | 비교 실패 시 BLOCKED |
| 요약까지 함께 롤백 또는 위조 | 과거 일관된 표본과 새 세션을 구분할 신뢰 근거 없음 | **탐지 불가**, UNKNOWN_NOT_RESET |

위 결과는 Linux Cloud의 실제 SQLite/파일 I/O와 Fake GitHub 테스트에서 확인했습니다. MacBook 파일시스템·전원 종료·실제 장치 백업 복원 검증은 아닙니다. 최종 CI의 OS별 테스트 범위는 PR에 기록합니다.

## 비밀 없는 사용자 보관용 비교 증빙

`cloud_resume --journal ... --format json`의 `rework_history.recovery_evidence`는 다음 필드만 포함합니다.

```json
{
  "version": 1,
  "epoch": "저널 UUID",
  "revision": 1,
  "policy": "정책 SHA256",
  "state_digest": "전체 상태 SHA256",
  "receipts": [
    {"pr": 20, "head": "40자리 SHA", "receipt": "64자리 digest"}
  ]
}
```

위 JSON은 형식 설명용이며 실제 복구 입력은 기존 명령의 전체 JSON 보고서입니다. 값을 수동 작성해 증빙으로 간주하지 않습니다.

피드백/PR 본문/로컬 경로/계정/토큰/인증정보/거래 원문/DB 내용은 포함하지 않습니다. receipt는 P7의 기존 발급 식별자이며 새 승인 토큰이 아닙니다. 해시는 서명·신뢰 시계·롤백 방지 장치가 아닙니다. epoch/revision/hash/목록 비교가 통과해도 `LOCAL_PREFIX_MATCH_NOT_TRUSTED_WITNESS`일 뿐입니다. 공개 PR 번호/SHA 외에도 작업 이력 메타데이터이므로 사용자 통제의 비공개 보관을 권장합니다. 원본 SQLite/checkpoint/거래 원장/토큰은 공개 GitHub에 올리지 않습니다.

새 내보내기 writer는 추가하지 않았습니다. 기존 JSON 출력을 사용자 지정 새 파일로 보관할 수 있습니다. 같은 PR에 대한 이전 전체 JSON을 `--recovery-evidence`로 읽으면 기존 PR/repository/snapshot binding을 확인한 뒤 요약을 비교합니다. 파일은 개인 전용 디렉터리에 보관하고 기존 증빙을 덮어쓰지 않습니다. 예시의 PR20과 경로는 사용자 환경에 맞게 바꿉니다.

```text
python -m automation.cloud_resume --pr 20 --journal runtime/review-handoff/receipts.sqlite --format json
python -m automation.cloud_resume --pr 20 --journal runtime/review-handoff/receipts.sqlite --recovery-evidence /private/user-kept/pr20-recovery-r1.json --format json
```

`--journal` 또는 `--recovery-evidence`를 지정한 복구 점검은 **exit2**입니다. 유효한 사본도 Cloud 연속성/롤백 방지가 미증명이므로 재작업 상태는 항상 HALTED, remaining_allowance는 UNKNOWN_NOT_RESET입니다. 일반 PR 관찰 workflow_state는 별도로 유지해 사용자가 코드를 검토할 수 있게 합니다. 이 둘을 혼동하지 않습니다. 초기화 명령은 출력하지 않습니다. 유효한 사본의 issued_count는 관찰 값이며 남은 실행 한도0/2 등을 부여하지 않습니다.

이전 증빙이 최신 head가 아니어도 재작업 영수증을 삭제하지 않습니다. `receipt_head_relation`은 CURRENT_HEAD_RECORDED / OTHER_HEAD_OR_STALE_REQUERY_REQUIRED / NO_RECEIPTS를 구분합니다. 현재 head와 다르다는 이유로 이전 작업 횟수를 환급하지 않습니다. 오래된 head의 조상 관계나 당시 권한을 검증했다고 주장하지 않습니다.

## GitHub 실환경 대조

읽기 전용으로 master `5404c7fd64318180cd79d2e6e4f83b9c52aa8361`, PR19 MERGED, head `93ca51308aa354aaebddab31a29d425d9747be1e`, base `9a0a1780ed217d11cdbf5daa5ae60ffb27ad96e3`를 확인했습니다. Cloud Resume의 fresh PR/commit SHA·최신 CI run/attempt와 로컬 receipt head를 대조합니다. P8의 두 번 수집/불일치 차단과 P2/P4 조건을 유지합니다.

병합 후 GitHub run의 PR 연결 소실은 P8/P9와 동일하게 과거 CI 관찰만 별도 표시하며 엄격한 게이트는 PENDING입니다. GitHub 커밋/댓글/PR이 존재한다는 사실은 receipt 발급·소모 횟수·복구 최신성·독립 리뷰/비용 증명이 아닙니다. 이 자료를 신뢰 가능한 영수증 원장으로 승격하지 않습니다.

## 공식 저장 후보 조사: 채택/업로드 없음

공식 GitHub 문서에서 [Workflow artifacts](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflow-artifacts)로 job 후 파일을 보관할 수 있음과 [삭제/만료](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts), [보관 기간 설정](https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization)을 확인했습니다. 문서 원문은 공식 github/docs 저장소의 해당 content 경로로 읽었습니다. 실제 저장소 artifact GET 결과는 total_count=0이었습니다.

하지만 파일의 만료·삭제와 workflow 삭제 가능성이 있어 영구·단조 증가 기준이 아닙니다. 계정 저장 한도/실제 과금 조건/독립 최신성 증거도 미검증입니다. 따라서 **무료 실행 보장 및 롤백 방지 기준으로 채택하지 않았습니다**. 업로드/download workflow/App/Secret/권한/과금 변경은 없습니다. 공개 커밋·댓글도 변경·삭제·재작성·권한/신원/원본 신뢰 문제가 있고 자동 영수증 원장으로 쓰지 않습니다.

## 백업과 재개 순서

1. 모든 원장 writer와 outbox 소비자를 멈추고 소유자가 한 명인지 확인합니다. 살아 있는 SQLite/sidecar를 임의 복사하지 않습니다.
2. 로컬 DB/checkpoint를 일관된 cold pair로 개인 저장소에 보관합니다. 거래 StateStore와 Slack outbox는 각각 별개 원장입니다. DB+checkpoint 동시 롤백·장치 간 복사 한계는 남습니다.
3. 가능한 경우 별도 장애/롤백 범위의 사용자 통제 장소에 위 메타데이터 요약을 보관합니다. 같은 Cloud 폴더 안의 복사본은 독립 기준이 아닙니다. 요약 자체가 최신/진짜인지 증명하지 못하면 재작업 중단입니다.
4. 새 세션에서 GitHub 상태를 재조회하고 개인 사본/보관 요약을 비교합니다. 손상/소실/불일치 시 삭제·재초기화·수동 SQL 수정·한도 환급을 하지 않습니다.
5. 사용자는 새 코드 검토를 진행할 수 있지만 무인 재작업/자동 모델/게시·병합은 비활성입니다. 무료 내구성과 독립 anti-rollback 기준은 다음 단계의 미해결 조건입니다.
