# P6-A: MacBook 없이 Cloud 통합 준비

판정은 **자동화 DISABLED**입니다. 기존 ChatGPT Plus 구독료 외 추가 지출 허용액은 0원입니다. GitHub 읽기와 Fake 개발/리뷰/CI/병합 후보 검증은 가능하지만, 구매 크레딧 비사용의 강제 증거·독립 표준 승인·보호 규칙·지속 Controller가 없어 실제 무인 운영을 허가하지 않습니다. API 과금, 크레딧 구매/자동 충전/소비, 유료 리셋, 별도 구독/Runner/서버/DB는 허용하지 않습니다.

이번 작업은 master `c106207eb1152c97c8169b4a96f3b8f2c70f66d1`에서 시작했습니다. 실제 MacBook 접근/모델 호출/무인 작업 생성/설정 변경은 없습니다. 요청받은 개발 브랜치 push와 개발 PR 제출만 수행하며 직접 병합하지 않습니다.

## 재현 가능한 읽기 전용 보고서

```bash
# 저장소 루트, 기존 공식 gh 인증 그대로 사용. 추가 로그인/토큰 입력 불필요.
python -m automation.cloud_readiness --pr 14 --observer-ci-run 39712609870
```

exit 2는 **준비 상태 보고이며 활성화 허가가 아님**을 나타냅니다. `--observer-ci-run`은 observer 자체 run ID가 아니라 관측 대상 CI run ID입니다. 실제 모델이나 app-server를 시작하지 않습니다. `cost_readiness`는 공식 quota 응답 모양의 fixture를 분류할 뿐 계정 증거를 수집하거나 신뢰하지 않습니다. 수동 boolean을 true로 바꾸어 실행시키는 경로가 없습니다.

[실측 JSON](evidence/p6-github-readiness.json)은 2026-10-22 13:40 KST에 생성한 **과거 표본**입니다. 다음 실행의 최신 증거로 재사용하지 않습니다. 출력은 ID/SHA/고정 상태 코드만 남깁니다. PR/리뷰 본문, token, stderr 원문, 환경변수 값, App 비밀정보를 저장하지 않습니다. HTTP GET은 P2 고정 endpoint 언어와 추가 2개 진단 endpoint로 제한하고, GraphQL POST는 P4의 고정 read query만 허용합니다. `gh api`의 기존 공식 인증을 사용하며 placeholder 인증 헤더는 실제 전송하지 않습니다. auth.json/Keychain/세션 토큰을 읽지 않습니다.

상태 의미:

| 상태 | 의미 |
|---|---|
| READY | 이름에 적힌 좁은 조건을 실제 표본 또는 검증된 Fake 입력에서 확인. 실행 허가가 아님 |
| PENDING | 기능/권한/스키마/최신성/증거 미확인 |
| BLOCKED | 알려진 필수 조건 불충족 또는 0원 정책 위반 위험 |
| USER_ACTION_REQUIRED | 사용자 설정/확인/별도 승인이 필요한 조건 |

전체 `automation_status=DISABLED`, `activation_authorized=false`, execution/publish/merge=false는 모든 결과에서 유지합니다. 일부 READY를 실행 Event/예약/worker 입력으로 변환하지 않습니다. 읽기 도구는 설정/쓰기/모델/launchd 기능을 가지지 않습니다. 최대 120회 요청, 요청당 15초, 응답 2MB 검증, 기존 P2/P4 페이지 제한을 사용합니다. subprocess는 timeout 후 출력 크기를 검사하므로 스트림 단위 메모리 상한 보장은 아닙니다. master/PR 재조회는 표본 안정성 검사이며 서버의 원자적 검증이 아닙니다.

## P0~P5 데이터 흐름과 실제 구현 경계

```text
승인 Issue 계약
  ├─ P2 GitHubVerifier / P4 GraphQL·MergeReader → 읽기 전용 표본 (실제 API 가능)
  └─ FakeAuthority → Coordinator의 CAS/lease/예약 → FakeWorker의 WorkInput
       → PatchGuard → FakePublisher → WAIT_REVIEW
       → FakeIndependentReviewer / FakeReviewRelay → 재작업 또는 승인
       → P2 규격 Fake CI → FakeMergeBroker → MERGE_ELIGIBLE (모의 후보)
```

| 계층 | 구현 | 실제 운영 제한 |
|---|---|---|
| P0 | paper 모드, 주문/복구 안전 정책·tests | 기존 거래 모드/전략 변경 없음. 실제 주문 금지 |
| P1 | SQLite 영속 원장, 중복 event, 예약/감사, 복구 검사 | Fake-only. 예약 cents는 실제 지출 한도가 아님 |
| P2 | 공식 REST 읽기, 신뢰된 workflow observer, 별도 관측 DB | 관측 결과를 실행 원장 Event로 승격하지 않음 |
| P3 | Coordinator/worker/publisher 계약, lease/CAS, patch 검증 | exact Fake만 수락. 실제 Codex worker/publisher 없음 |
| P4 | 불변 ID/표준 Review/head/CI/threads/보호 검증, Fake merge | 실제 merge broker 비활성. GitHub expected-head만으로 base CAS를 보장하지 못함 |
| P5 | Mac Preflight, POSIX writer lock, 디스크 checkpoint, launchd 예제 | Controller는 기존 Fake 원장 검사만. 공동 DB/checkpoint rollback 감지 불가 |
| P6-A | Cloud 읽기 표본·비용/독립성 판정·준비 상태 보고 | 새 실행 backend/스케줄러/쓰기 연결 없음 |

로컬 Controller는 Python 3.12·로컬 SQLite·0700/0600·단일 POSIX writer·별도 checkpoint·사용자 검토 복구에 의존합니다. Cloud 작업별 파일 snapshot이나 GitHub-hosted runner의 companion SQLite를 공유 실행 원장으로 쓰지 않습니다. 작업 종료 후 열린 process/lock/network session이 유지된다고 가정하지 않습니다. B안에 로컬 Controller를 단순 복사해 내구성 문제가 해결됐다고 주장하지 않습니다.

## 실제 GitHub 검증 결과

사용자가 보고한 대화상의 독립 ChatGPT 리뷰와 **GitHub 표준 승인 증거**는 구분합니다. GitHub API만으로 어떤 대화가 해당 리뷰를 작성했는지 증명하지 않습니다.

| 점검 | 실제 결과 | 판정 |
|---|---|---|
| 저장소 | public, private=false, ID **888367411** | READY |
| master SHA | **c106207eb1152c97c8169b4a96f3b8f2c70f66d1** | READY (관측 시점) |
| master 보호 | protected=false, protection.enabled=false, required checks/contexts=[] | BLOCKED |
| 활성 branch rules | `/rules/branches/master` → [] | USER_ACTION_REQUIRED |
| 상세 protection | HTTP 403 Resource not accessible by integration | PENDING; 보호 없음과 다른 접근 제한 |
| Actions 기본 workflow 권한 | HTTP 403 Resource not accessible by integration | PENDING; scope를 추정하지 않음 |
| repository installation | HTTP 401 | PENDING; 전체 GitHub 인증 만료로 단정하지 않음 |
| PR #14 작성자 | user.id **45135977** | 실제 ID 확인 |
| PR #14 표준 Review | ID **5608643653**, actor **45135977**, state **COMMENTED**, head **881aed0d37e38e789a051168246d3516c3ca5474** | 독립 승인 증거 아님 |
| 정책 builder/reviewer 목록 | 모두 빈 배열 | USER_ACTION_REQUIRED; 실제 계정 ID 임의 등록 없음 |
| GraphQL reviewThreads | 정확한 PR #14 head/base, 전체 0개 | READY; 조회 가능 확인 |
| CODEOWNERS | master의 `.github/CODEOWNERS`, `CODEOWNERS`, `docs/CODEOWNERS` 모두 object=null | USER_ACTION_REQUIRED (소유자 리뷰를 요구할 경우) |
| CODEOWNERS 사용자/팀 권한 | 파일/실제 owner 미설정, 별도 확인 없음 | PENDING |

`/installation` 한 endpoint로 전체 App 목록이나 App별 권한을 증명하지 않습니다. repository 응답의 admin=true도 통합 인증의 모든 endpoint 권한을 뜻하지 않습니다. 새 Secret, App 설치, 토큰 scope 확대를 하지 않았습니다.

### CI 연결과 무료 Runner

기존 master push CI [39712609870](https://github.com/sasumpi123/auto_trade/actions/runs/39712609870)는 위 master SHA와 `.github/workflows/tests.yml`에 연결되며 네 job이 success입니다. Check App은 실제 **15368 / github-actions**, App owner ID **9919**입니다.

정책상 정확한 검사 이름은 다음 4개입니다.

```text
Python 3.12 (ubuntu-latest)
Python 3.12 (macos-latest)
Python 3.12 (windows-latest)
Python 3.12 safety gate
```

workflow는 standard `ubuntu-latest`, `macos-latest`, `windows-latest` Python 3.12를 사용하고 contents:read입니다. observer workflow는 contents/issues/pull-requests/actions/checks:read를 명시합니다. 이는 저장소 파일의 선언이며 App 설치 전체 권한/계정 기본 권한 실측의 대체가 아닙니다. `observe`는 위 필수 네 검사에 포함하지 않습니다.

GitHub [공식 Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) 본문은 **공개 저장소의 standard hosted runner 사용 무료**를 명시합니다. 이 저장소 공개 여부와 workflow의 standard runner를 확인했으므로 이 CI 실행 조건은 일치합니다. larger runner/유료 self-hosted 인프라를 도입하지 않습니다. artifact/cache/Packages/다른 workflow나 계정 전체 과금 0원을 검증했다고 확대하지 않습니다. 이번 변경은 유료 저장 경로를 추가하지 않습니다.

### observer PENDING을 구분한 재현

- PR #14를 현재 공식 API로 다시 읽고 기존 GitHubVerifier를 적용하면 정확한 `/autodev issue N spec SHA256` 계약이 없어 **ISSUE_CONTRACT_MISSING**입니다. 수동 PR을 자동화 승인 Issue로 취급하지 않는 정상 보류입니다.
- master push CI 39712609870의 현재 `pull_requests=[]`입니다. 이를 관측 대상으로 한 workflow_run 관계 검사는 **RUN_PR_RELATION_UNVERIFIED**입니다. 승인 PR과 관계가 확인되지 않은 push CI를 실행 신호로 만들지 않습니다.
- PR #14의 이전 PR CI 39708841712도 현재 API에서 `pull_requests=[]`로 반환됩니다. 따라서 해당 job들의 과거 성공 자체와 별개로 기존 P2의 정확한 run↔PR/head 결합 재검증은 **CI_RUN_PR_HEAD_WORKFLOW_BINDING_UNVERIFIED**로 PENDING입니다. 결합 검증을 완화하지 않습니다.
- 상세 protection/Actions 권한 403 및 installation 401은 **API 접근 결과**이며 위 계약/관계 보류와 다릅니다. 네트워크 오류/429/schema/GraphQL errors도 각자 미검증 상태입니다.

실제 observer run 39712775975 등의 conclusion=failure와 현재 표본 재현을 구분합니다. 이전 세션에서 로그 다운로드가 차단됐고 이번에는 과거 run의 최초 실패 trace를 확인한 것으로 주장하지 않습니다. 과거 로그의 고정 diagnostic을 확인하기 전 “그 run의 유일한 원인”이라고 단정하지 않습니다. 진단을 통과시키려고 exit 2를 0으로 바꾸지 않습니다.

## ChatGPT ↔ Codex 공식 지원 매트릭스

2026-10-22 다음 공식 문서 본문을 실제 조회했습니다.

- [Auth](https://developers.openai.com/codex/auth/): ChatGPT subscription 로그인과 API 사용량 과금은 별도. `codex login`, `codex login status`, forced_login_method=chatgpt 지원
- [Pricing](https://developers.openai.com/codex/pricing/): Plus에 Codex CLI/웹/IDE, cloud code-review 통합 포함. Work/Codex는 pricing/credits/limits 공유
- [Scheduled/Automations](https://learn.chatgpt.com/docs/automations): eligible plan의 웹/모바일 GitHub PR activity 이벤트, 앱 사전 연결·repo 접근, Scheduled에서 관리. CLI/desktop/IDE에는 이벤트 trigger 없음
- [GitHub Codex review](https://developers.openai.com/codex/integrations/github/): Codex의 표준 GitHub code review 게시 및 자동 리뷰 설정. 예제는 connector bot의 commented review. **별도 ChatGPT 독립 승인 기능의 증거가 아님**
- [GitHub required reviews](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/reviewing-changes-in-pull-requests/approving-a-pull-request-with-required-reviews): PR author는 자기 PR 승인 불가

| 질문 | 공식 확인 범위 | 이번 계정/저장소 판정 |
|---|---|---|
| Plus에서 GitHub PR 이벤트 Work 가능? | Work/Codex Plus 포함과 eligible plan 웹/모바일 PR activity trigger는 문서화 | 실제 Plus 계정 eligibility/rollout/설정은 PENDING. 무조건 지원으로 단정하지 않음 |
| 계정 확인 절차? | 연결 앱 repo 접근, task 요청, Scheduled에서 trigger/run 관리 | 사용자 UI 확인 필요. 이번에는 연결/작업 생성/Run now를 하지 않음 |
| ChatGPT가 표준 Review 게시? | Codex GitHub 통합은 standard review 명시. Work의 같은 기능은 확인 못함 | ChatGPT Work 표준 Review writer PENDING; Codex가 대신 리뷰하지 않음 |
| APPROVED/CHANGES_REQUESTED 게시? | GitHub Review API는 이를 표현. Work가 두 state를 게시한다는 보장은 확보 못함 | PENDING/WAIT_REVIEW. COMMENTED/일반 댓글/이모지 제외 |
| 작성자와 다른 불변 ID인가? | 도구 이름/세션/역할은 GitHub 계정 독립성 증명이 아님 | PR #14의 관측 actor는 작성자와 동일. 독립성 BLOCKED |
| 같은 사용자 ChatGPT/Codex 연결로 독립 승인? | 같은 GitHub ID이면 자기 승인 불가 | 충족 불가. 다른 주체가 실제 관측되지 않으면 P4 통과 아님 |
| Plus 기본 사용량만으로 반복 가능? | 포함 allowance는 유한/공유, weekly limit 가능. 추가 credits로 확장 가능 | 무한 반복/항상 0원 보장 불가. PENDING/HALTED |
| 구매 크레딧 비사용 강제? | 검토한 문서에 포함량/credit 구분은 존재 | 검증된 포함량 전용 강제/소비 차단 증거 없음 → BLOCKED |
| Codex가 리뷰를 다시 읽고 재작업? | GitHub REST로 Review 조회 가능. Codex 통합은 후속 수정 요청도 문서화 | 이번 READ-ONLY/fixture 조회·Fake 재작업 검증. 실제 worker 연결 OFF |

사용자 UI 확인은 다음 순서입니다. 앱/연결에서 공식 GitHub 연결 제공 여부·대상 repository scope를 확인 → 웹/모바일 Scheduled 및 event-triggered task 기능/plan entitlement 표시 확인 → GitHub PR 이벤트와 조회/Review 작성 action 지원을 공식 UI 설명에서 확인 → **비용 차단과 별도 사용자 승인이 충족된 이후에만** 사용자가 실제 작업 생성/권한 승인/모델 시험을 검토합니다. 이후 시험에서도 REST Review의 user.id/state/commit_id로 판정해야 합니다. 현재 지원되지 않는 버튼/권한/기능을 만들어 가정하지 않습니다.

이번 계정 UI는 접근하지 않았습니다. 문서화된 “지원 플랜”과 실제 개인 Plus 계정의 활성 기능은 다릅니다. GitHub Review의 출처가 ChatGPT인지 단순히 본문만 보고 추정하지 않습니다. 공식 Codex bot이 별도 ID라고 해도 사용자가 요구한 별도 ChatGPT 리뷰를 자동으로 충족한다고 판단하지 않습니다. 비공식 API/브라우저 스크래핑/계정 공유/토큰 추출/유료 API로 보완하지 않습니다.

## Plus 포함량과 구매 크레딧

| 항목 | 공식 근거/확인된 범위 | 미확인/차단 |
|---|---|---|
| 포함량 잔량 | pricing의 usage dashboard 및 CLI `/status` 안내 | 실제 계정 최신 잔량 미조회. UI만으로 원자적 지출 제어 불가 |
| 구매 크레딧 | pricing은 포함량 소진 뒤 available credits로 작업 지속한다고 명시 | 개인 계정 구매 잔액/자동 소비 설정 미확인 |
| 자동 크레딧 소비 방지 | 검토한 공식 auth/pricing/app-server 문서만으로 강제 방법 입증 못함 | BLOCKED. 소비 비활성 boolean 수동 증명 금지 |
| 자동 충전 차단 | 개인 계정 billing UI/공식 정책 확인 필요 | USER_ACTION_REQUIRED. 직접 설정 변경 없음 |
| 포함량 소진 시 중단 | 정책상 HALTED. 공식 pricing은 active turn이 fair-use 내 계속될 수 있다고 명시 | quota 표본 polling만으로 정확한 경계 강제 불가 |
| machine-readable 증거 | [공식 App Server](https://developers.openai.com/codex/app-server/) `account/rateLimits/read`, updated 이벤트, usedPercent/windowDurationMins/resetsAt 및 선택적 planType/credits 문서화 | “공식 machine-readable가 전혀 없다”는 판단은 틀림. 다만 실제 호출/개인 Plus 필드 관측·출처/최신성은 PENDING |
| 구매 credit만 구별한 증거 | App Server는 서버가 제공할 때 remaining workspace credit details 포함이라고 설명 | 이 필드가 개인 Plus의 **구매분만** 정확히 식별한다는 보장/관측 없음 |
| 리셋/유료 확대 없이 재개 | usage dashboard reset 시점 이후 사용자가 다시 확인할 수 있음 | 자동 재개 없음. reset consume/purchase/API fallback 금지 |

App Server `account/read(refreshToken=false)`와 rateLimits/read는 공식 읽기 인터페이스 후보입니다. 이번에는 app-server 자체나 turn/thread/review/model을 시작하지 않았습니다. 문서상의 외부 토큰 로그인/캐시 복사 예제는 사용자 금지 조건 때문에 채택하지 않습니다. `account/rateLimitResetCredit/consume`은 earned reset 기능으로 문서화돼 있어도 이번에는 호출하지 않습니다. 구매 리셋과 같다고 혼동하지 않으며, 본 작업은 **어떤 reset 소비도 하지 않는** 보수적 범위입니다. `account/sendAddCreditsNudgeEmail`도 호출하지 않습니다.

Pricing의 [Plus/Pro credits 도움말](https://help.openai.com/en/articles/12642688) 링크는 이번 HTTP 오류로 본문 미확인입니다. 따라서 자동 충전/credit-only 차단 옵션의 존재·부재를 단정하지 않습니다. 확인하지 못한 것을 무료 보증으로 바꾸지 않습니다.

`cost_readiness`는 잔량 모양을 일부 해석하더라도 READY를 반환하지 않습니다. 잔량 없음/100%는 BLOCKED, 잔량 불명은 PENDING, 100% 미만도 freshness/provenance/기본량 대 구매분 강제 증거가 아니므로 PENDING입니다. 모든 경우 구매 크레딧 위험은 BLOCKED입니다. 기존 SubscriptionEvidence true fixture는 실제 계정의 검증 수단이 아니며 이번에도 실제 어댑터로 승격하지 않았습니다.

현재 Cloud 개발 세션에서는 사용자 요청 범위의 개발만 수행했고 별도 모델/API key/paid credit option을 선택하거나 자동 모델 작업을 시작하지 않았습니다. 계정 billing 전체를 관측하거나 이번 대화의 실제 결제 원장을 검증한 것은 아닙니다.

## A/B/C 운영 방식 비교와 추천

| 항목 | A: Mac 로컬 + CLI + Work | B: Cloud + GitHub + Work | C: Cloud 개발 + 수동 독립 리뷰 |
|---|---|---|---|
| Mac 필요 | 로컬 Controller 운영에 필요 | Cloud 진단/개발에는 불필요 | 불필요 |
| 공식 확인 기능 | ChatGPT CLI 인증, GitHub read, 조건부 Work 이벤트 | Cloud 개발, GitHub read, 조건부 Work 이벤트 | 사용자가 시작한 Cloud 개발·GitHub PR 조회, 수동 검토 |
| 무인 운영 | 현재 불가 | 현재 불가 | 수동 리뷰를 포함하므로 완전 무인 아님 |
| 사용자 개입 | 로컬 설치/인증/복구/비용·리뷰 지원 검증 | 앱 scope/이벤트/Cloud 내구 Controller·비용 검증 | 작업 시작, 비용 확인, 독립 검토·표준 Review·수동 병합 판단 |
| 독립 GitHub Review | 별도 actor 증거 필요, 현재 미충족 | 별도 actor 증거 필요, 현재 미충족 | 다른 권한 있는 실제 계정이 표준 승인해야 충족; 같은 계정의 ChatGPT 대화는 불충족 |
| 0원 보증 | 포함량 전용 강제 미확인 | 동일 위험 + 영속 trigger/controller 무료 조건 미확인 | 유료 옵션을 선택하지 않는 수동 진행만 검토 가능. 계정별 포함량/credit 소비 안전 확인 필요 |
| 미증명 | Mac 내구/공동 rollback/Work Review/비용 | 무인 세션 trigger·내구 원장·Work Review·비용 | 실제 독립 reviewer 및 사용자의 포함량 전용 확인 |
| 지금 운영 활성화 | 불가 | 불가 | 자동화 불가. 사용자 주도 개발/수동 검토 범위만 가능 |

추천은 **C의 사용자 주도 단계**입니다. 이미 작동하는 Cloud 개발·PR·읽기·오프라인 테스트를 활용하고, 독립 표준 승인을 확보하지 못하면 단순 검토 의견으로 남깁니다. 별도 리뷰어가 없는 현재 상태에서 “ChatGPT가 읽었으니 P4 승인”이라고 처리하지 않습니다. 수동 개발 세션을 유지한다는 추천이며 자동 모델 실행/재작업/게시/merge 활성화 추천이 아닙니다. 새 계정 공유나 유료 구독을 요구하지 않습니다. 무료 독립 reviewer를 확보하지 못하면 P4 자동화는 계속 WAIT_REVIEW입니다.

## 사용자 설정 순서: 개인 작업을 잠그지 않기

모든 설정은 사용자가 검토한 후 직접 적용합니다. 이번 PR은 GitHub 권한/설정을 수정하지 않습니다.

1. 먼저 비용/기능 UI와 읽기 가능한 권한을 확인합니다. 개인 Plus 잔량/구매분/자동 충전/포함량 전용 강제의 공식 근거를 확보하지 못하면 실제 모델 자동 실행은 중단합니다.
2. **필수 승인 1명 규칙을 먼저 켜지 않습니다.** 실제 PR 작성자와 다른 GitHub user.id, 그 계정의 repository 역할/표준 Review 가능성, exact head APPROVED/CHANGES_REQUESTED 지원을 먼저 확보합니다. 독립 리뷰어가 없으면 승인 규칙 활성화를 미루고 자동화도 OFF로 유지합니다.
3. 현재 standard CI 네 검사와 workflow/App 연결을 확인합니다. GitHub repository Settings → Branches의 master classic rule 또는 Rules → Rulesets에서 대상 master를 검토합니다. 초기 checks-only 보호를 검토할 수 있지만 이는 최종 P4 정책 충족이 아닙니다. `observe`는 일반 PR에서 정상 PENDING일 수 있어 필수 CI로 무작정 추가하지 않습니다.
4. 사용자가 적용할 최종 체크는 위 네 정확한 이름, source App GitHub Actions **15368**, branch up-to-date/strict입니다. force push/deletion 금지, conversation resolution도 검토합니다. 설정 UI가 App pinning/상속/bypass를 충분히 보여주지 않으면 API로 재검증할 때까지 PENDING입니다.
5. 다른 독립 리뷰어가 실제로 작동한 뒤에만 Require a pull request, 승인 최소 1, stale 승인 폐기, latest reviewable push 승인, admins 포함 적용/우회 없음 등 P4 정책을 단계적으로 적용합니다. 마지막 push 당사자 승인 제외도 실제 역할과 대조합니다. reviewer 없는 상태에서 모든 admin 우회까지 막아 개인 작업을 잠그지 않습니다. 최종 규칙을 만족하지 못하는 동안 자동 merge는 계속 차단합니다.
6. CODEOWNERS를 요구하려면 사용자가 실제 owner와 repository write 권한을 먼저 정하고 파일을 별도 변경으로 검토합니다. 현재 P4 지원은 단순 패턴/개별 user 부분집합입니다. 팀/복잡한 glob은 미지원 상태를 유지합니다.
7. 권한 부족은 사용자가 최소 scope를 별도로 검토합니다. 광범위 토큰/Secrets/App를 자동 등록하거나 admin=true만 믿고 우회하지 않습니다. 정책 builder/reviewer ID도 실제 API로 확인된 별도 계정만 검토 후 별도 변경합니다.
8. exact head 기준 읽기 진단을 다시 수행합니다. 모든 자동화 안전 조건 및 서버 측 base/head 경합 처리가 검증돼도 **별도 활성화 승인 전에는 DISABLED**입니다.

미래 Mac 연결의 최소 항목은 Python 3.12·Git/gh·공식 ChatGPT 로그인 CLI, private 로컬 원장/checkpoint/log 경로, 단일 writer, 수동 복구 및 P5 Preflight입니다. 이번 P6-A 진행에 Mac 실행을 요구하지 않습니다. launchd는 disabled/미등록/타이머 없음 상태를 유지합니다.

## P7의 정확한 기술적 장애물

- 실제 Plus 개인 계정에서 included-only enforcement, 구매 크레딧 미소비·자동 충전 차단의 공식 검증 및 신뢰 가능한 최신 machine-readable 증거. 단순 UI/잔량 polling은 불충분
- ChatGPT Work가 별도 불변 GitHub ID로 exact-head APPROVED/CHANGES_REQUESTED를 게시하는 공식 지원/실측. 없으면 수동 독립 reviewer 경계 유지
- master 보호/필수 checks/App pinning/bypass/독립 역할의 사용자 설정과 읽기 검증 권한 확보
- Cloud 무인 task trigger의 실제 Plus eligibility/비용 및 지속적인 조정 서비스의 무료·내구·역할 경계. GitHub-hosted runner 간 SQLite 공유 금지
- 프로세스/호스트/백업 공동 rollback에 대한 trusted checkpoint와 예약 단조성. 임의 새 원장/환급으로 우회하지 않음
- 중단 publish/merge의 receipt reconciliation, 서버 측 expected base/head/보호 변경 경합 처리. Fake CAS 보장을 GitHub 실동작으로 확대하지 않음
- merged PR CI의 현재 `pull_requests=[]`와 과거 증거 검증 범위 구분. 인증/스키마 실패를 완화하지 않고 정확한 관계를 증명하는 별도 설계 필요
- 관측 표본이 head/master 정책 변경과 경쟁하는 문제. 현재 double-read는 원자성 증명이 아니며 live adapter 없음

## 테스트 및 비활성 경계

신규 tests/test_cloud_readiness.py는 실제 네트워크를 막은 기존 P4 fixture와 mock subprocess만 사용합니다. 승인된 Issue/WorkInput 계획, 중복 dispatch 차단, Fake PR, WAIT_REVIEW, 표준 승인·수정 후 새 head/CI·Fake MERGE_ELIGIBLE, 자기 승인/댓글 거부, 미지원 이벤트/불명 quota/크레딧 위험, 보호 비활성, 승인 취소, observer 계약/관계 보류, Mac 미연결, HTTP 오류와 고정 read query를 검증합니다. 재시작 후 SQLite/checkpoint 대조·손상/롤백·sleep·응답 유실은 기존 P5/P4 테스트를 그대로 실행합니다.

```bash
python -m unittest tests.test_cloud_readiness -v
python -m unittest discover -v
python tools/check_safety.py
```

기존 442개와 신규 34개를 유지합니다. 전체 476개, OS별 실제 CI run/head/결과는 이 개발 PR에 기록합니다. Linux/macOS/Windows hosted CI 성공은 실제 MacBook이나 실제 계정의 무료 모델 E2E 성공이 아닙니다. policy.enabled=false, zero-cost의 모든 실운영 허용 false, REAL_TRADING=False, live worker/push/PR/merge/launchd/거래/배포 OFF를 유지합니다. 부분 READY와 전체 자동화 허가를 혼동하지 않습니다.

추가 회귀 수정: 전체 suite 반복 중 기존 P1 동시 open/claim 검사가 Reservation projection mismatch로 실패했습니다. 초기 validation이 독립 SELECT들을 autocommit 상태로 실행하면 writer commit 전의 issue와 commit 후 attempts/budgets가 섞일 수 있었습니다. `SQLiteLedger.__init__`에서 읽기 BEGIN으로 validation 전체를 동일 snapshot에 묶었습니다. 읽기 중 다른 WAL writer의 commit을 결정적으로 끼워 넣는 신규 테스트는 수정 전 실패하고 수정 후 통과합니다. 예약/한도/무결성 검사는 완화하지 않으며 초기화나 write 기능을 추가하지 않습니다.
