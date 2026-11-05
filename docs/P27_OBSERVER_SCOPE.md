# P27 수동 PR의 observe 대상 분리 (자동개발 검증은 유지)

이슈 #57. 기존 읽기 전용 관찰기(`.github/workflows/automation-observer.yml` → master의 `tools/automation_plan.py`)가
일반 수동 PR까지 autodev 계약 검증 대상으로 취급해 `PENDING/ISSUE_CONTRACT_MISSING`, exit 2가 되던 **관찰 범위 문제**를 고친다.
autodev 계약 자체(`GitHubVerifier.verify()`)는 바꾸지 않았다.

## 분류표

| 대상 | 판정 | CLI |
| --- | --- | --- |
| 같은 저장소 head, `autodev/` 브랜치 아님, 본문에 `/autodev` 흔적 없음(본문 null/빈 문자열 포함) | `NOT_APPLICABLE` / `manual_pr_outside_autodev` | exit 0 |
| `autodev/` 브랜치(대소문자 무시), 또는 본문에 `/autodev` 문자열(정확·손상·중복 계약 모두) | 기존 엄격 `verify()` 경로 그대로 | 기존과 같음 |
| fork 또는 head repo 불명(null), 필드 누락·자료형 오류, base 저장소 불일치, 본문 과대 | `PENDING` | exit 2 |
| 두 번 읽는 사이 상태 변경, API 인증·권한·rate limit·네트워크 오류 | `PENDING` | exit 2 |

- 분류는 이벤트 본문이 아니라 인증된 GET 재조회로 한다(`/repos/...` 저장소 identity 검사 후 `/pulls/N` 2회, 결과 불일치 시 보류).
- GitHub 정상 스키마에서 nullable인 `body: null`은 허용하지만, 필드가 없거나 자료형이 틀리면 보류한다.
- PR 이벤트, PR 리뷰 이벤트, PR 위 issue_comment 이벤트, workflow_run(연결 PR 정확히 1개)에서 같은 분류를 쓴다.
  - workflow_run은 run의 head SHA가 현재 PR head와 다르면 `stale_run_event`로 보류한다.
  - 연결 PR이 0개 또는 2개 이상이면 기존대로 보류한다.
- 일반 이슈 이벤트 경로는 바꾸지 않았다.

## 비대상은 승인 증거가 아니다

- 결과: `decision=NOT_APPLICABLE`, `reason=manual_pr_outside_autodev`, `scope=observation_scope_only_not_approval_or_ci_evidence`,
  `execution_enabled=false`, `read_only=true`, `model_calls=0`.
- Snapshot·`ISSUE_CANDIDATE`·`REVIEW_CI_VERIFIED`·`MERGE_ELIGIBLE`·실행 권한으로 변환하지 않는다(`Snapshot` decision 목록에도 없음).
- 관찰 journal에는 `NOT_APPLICABLE` 상태로 기록하고 `current` 행은 갱신하지 않는다. delivery 중복 억제와 seq 순서는 그대로다.
  - PR 대상 행은 metadata가 없다. 이 이후 관찰이 VERIFIED가 아니므로 같은 PR의 이전 autodev 후보는 current로 반환되지 않는다.
  - workflow_run 대상 행은 API로 확인한 연결 PR만 `{"pr": N, "scope": "NOT_APPLICABLE"}`(sha 검증)로 남긴다. `current('pr', N)`은
    이 run을 관련 관찰로 포함하므로, 이 run의 seq보다 오래된 후보와 늦게 완료되는 오래된 PR/run 관찰은 current가 되지 못한다.
    이후의 새 VERIFIED 관찰은 막지 않고, 다른 PR은 영향받지 않는다. DB를 다시 열 때 이 형식만 허용한다(리뷰 5778063916).
- PR 본문·토큰·예외 원문은 출력하거나 저장하지 않는다.
- `NOT_APPLICABLE`/exit 0은 관찰 범위 밖이라는 뜻일 뿐이다. 제품 코드가 맞거나 CI가 통과했다는 뜻이 아니며,
  macOS 전체 테스트·Safety gate 결과와 무관하다.

## 보존

- `GitHubVerifier.verify()`, issue hash·승인 댓글·리뷰·CI·보호 규칙 검증은 변경하지 않았다.
- `.github/automation/policy.json`(enabled=false, 빈 builder/reviewer ID), zero-cost 설정은 변경하지 않았다.
- workflow의 master checkout, 읽기 전용 권한, 토큰 취급은 변경하지 않았고 새 schedule·dispatch·retry도 없다.
- P22·P26·거래 전략·비용·회계는 변경하지 않았다.

## 검증 한계

- 운영 observe는 master 코드를 실행한다. 따라서 이 PR 자신의 observe 검사는 병합 전까지 기존(old-master) 결과로 계속 빨갛게 나온다.
  그 결과를 이 PR의 검증 근거로 쓰지 않는다.
- 이 PR에서 검증한 것은 Fake HTTP 기반 오프라인 회귀와 동일 Head CI까지다. 실제 GitHub 이벤트에서의 동작은 병합 후 다음 수동 PR의
  observe 검사에서 확인해야 한다.
