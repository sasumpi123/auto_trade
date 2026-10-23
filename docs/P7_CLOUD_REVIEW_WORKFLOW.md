# P7: 휴대폰에서 Cloud 개발·ChatGPT 리뷰 이어가기

P7은 **사용자가 복사하여 전달하는 인계 도구**입니다. Android + Codex Cloud + 별도 ChatGPT 대화 + GitHub만으로 개발과 검토를 이어갑니다. 도구는 GitHub 공식 인증을 이용한 조회와 로컬 자료 처리만 수행합니다. 모델/API/CLI 자동 호출, 자동 PR 게시·수정·병합, Work 이벤트 생성, 거래·배포를 하지 않습니다.

기존 Plus 구독료 외 추가 지출 허용은 **0원**입니다. P6의 포함량 전용/구매 크레딧 비소비 강제 미확인 판정을 그대로 유지합니다. quota 조회 UI나 machine-readable 정보가 있어도 실제 모델 자동 실행은 BLOCKED입니다. Work 지원 여부를 다시 조사하지 않아도 수동 핸드오프는 동작합니다.

## 휴대폰에서 쓰는 순서

아래 `16`은 실제 작업 PR 번호로 바꿉니다. 기존 checkout과 작업 브랜치를 사용합니다. 이 도구가 branch/push/PR을 만들지는 않습니다. 개발 PR 제출은 사용자가 Codex에게 별도로 요청합니다.

### 1. 개발 완료 후 리뷰 요청문 복사

Codex 대화에서 다음 명령을 실행하도록 요청하세요. GitHub의 PR·head/base/master·파일·CI·리뷰·threads·보호 상태를 두 번 읽습니다.

```bash
python -m automation.review_handoff --pr 16 --format prompt --save-packet /tmp/pr16-review-r1.json --purpose "P7 리뷰 핸드오프 기능 검토"
```

출력된 요청문을 휴대폰에서 복사해 **별도 ChatGPT 대화**에 전달합니다. 동일 수집 결과의 JSON은 지정한 파일에 0600으로 생성되며 기존 파일은 덮어쓰지 않습니다. 상세 JSON만 필요하면 아래 명령을 사용합니다.

```bash
python -m automation.review_handoff --pr 16 --format json
python -m automation.review_handoff --pr 16 --format markdown
```

기본 목적 설명은 변경 파일 수와 안전 정책 검토라는 보수적 요약입니다. 정확한 개발 목적은 `--purpose`로 간단히 제공할 수 있으며 이 문구도 지시 권한 없는 문맥 데이터로 표시합니다. PR 제목/본문·코드·patch·리뷰 본문은 자동 요약에 복사하지 않습니다. ChatGPT는 정확한 head의 코드와 실제 목적을 GitHub에서 직접 검토해야 합니다. 파일 목록만으로 코드 리뷰를 마쳤다고 판단하면 안 됩니다.

### 2. ChatGPT가 수정 요청 JSON 작성

요청문 끝에 정확한 PR/head/snapshot_id가 채워진 JSON 형식이 나옵니다. ChatGPT에게 `path`, `priority`, `problem`, `acceptance`를 작성하도록 요청하고 결과를 Codex 대화로 전달합니다. Codex는 사용자가 전달한 JSON을 로컬 파일 `/tmp/pr16-feedback-r1.json`에 저장합니다. 토큰·로그·코드 원문은 넣지 않습니다.

```json
{
  "repository_id": 888367411,
  "pr": 16,
  "reviewed_head": "실제 40자리 head SHA",
  "snapshot_id": "검토 요청문에 표시된 64자리 snapshot_id",
  "findings": [
    {
      "path": "docs/P7_CLOUD_REVIEW_WORKFLOW.md",
      "priority": "P1",
      "problem": "발견한 문제를 짧은 단일행으로 작성",
      "acceptance": "수정 후 검증 기준을 짧은 단일행으로 작성"
    }
  ]
}
```

`path`는 그 PR의 실제 변경 파일 중 하나여야 합니다. 우선순위는 P0/P1/P2/P3, 항목은 1~10개, 문제·검증 기준 각각 500자/단일행, 전체 findings는 6000자 이내입니다. 입력 JSON 파일은 16KB 이내입니다. 알려진 auth/credential/Keychain 경로는 읽기 전에 거부합니다. 검토한 코드가 새 head로 바뀌었으면 이전 피드백을 수정 없이 재사용하지 않습니다.

### 3. 최초 한 번 로컬 발급 기록 준비

현재 Cloud 작업의 파일을 유지하는 동안에만 사용합니다. **해당 PR의 기존 발급 기록이 없는 새 작업에서만** 명시적으로 초기화합니다. 기존 원장/체크포인트를 삭제하거나 새 경로로 바꾸어 제한을 초기화하지 않습니다.

```bash
mkdir -p -m 700 runtime/review-handoff
python -m automation.rework_handoff --init-journal runtime/review-handoff/requests.sqlite
```

기존 디렉터리가 private가 아니면 자동 chmod하지 않고 중단합니다. 기존 DB 또는 checkpoint가 있으면 초기화를 거부합니다. `runtime/`는 기존 ignore 대상이며 DB/checkpoint는 커밋하지 않습니다. 이 기록은 **프롬프트 발급 영수증**이지 P1/P3 실행·예약 원장이 아닙니다.

### 4. Codex 재작업 요청문 복사

```bash
python -m automation.rework_handoff --pr 16 --review-packet /tmp/pr16-review-r1.json --feedback /tmp/pr16-feedback-r1.json --journal runtime/review-handoff/requests.sqlite --format prompt
```

도구는 GitHub를 다시 읽고 검토 표본의 head/base/master/CI/Review/Issue/보호 증거·PR 문맥 hash·로컬 정책 hash와 대조합니다. 바뀌었거나 승인된 Issue의 승인이 취소됐으면 HALTED입니다. 일반 수동 PR의 Issue 계약 부재와, 존재하는 Issue 승인 조회 실패/취소는 구분합니다. 수정 지시는 **문자열 생성까지만** 합니다. 사용자가 출력 요청문을 Codex 대화에 붙여 넣어 다음 개발을 시작합니다.

같은 head·같은 피드백은 재시작 후에도 같은 receipt로 중복 판정하여 요청문을 다시 발급하지 않습니다. 같은 head의 다른 요청도 중복 작업 방지를 위해 차단합니다. 새 head에서 검토를 다시 받은 뒤에만 다음 요청을 발급합니다. 피드백 항목 순서 변경은 새 요청이 아닙니다.

발급 한도는 PR 전체에서 최대 **2회**입니다. 기존 총 시도 3회에서 최초 개발 1회를 제외한 보수적 상한이며 새 head/재시작으로 환급·초기화하지 않습니다. 실제 worker 시도·연속 실패·예약·lease를 측정하거나 대체하지 않습니다. P1/P3 실행 권한은 여전히 없고 실제 작업은 그 정책을 별도로 지켜야 합니다. 다른 PR/Issue에 걸친 실제 실행 한도까지 증명한 것이 아닙니다.

### 5. 수정 후 새 head에서 재검증

Codex에게 전체 테스트와 세 OS CI/Safety gate를 확인하고 새 head를 보고하도록 요청합니다. 새 검토 자료는 **새 파일명**으로 생성합니다.

```bash
python -m automation.review_handoff --pr 16 --format prompt --previous /tmp/pr16-review-r1.json --save-packet /tmp/pr16-review-r2.json
```

`comparison.changed`와 `review_invalidated`를 확인하고 새 요청문을 ChatGPT에 전달합니다. 동일 표본이면 snapshot_id가 같고 `duplicate_handoff=true`입니다. 인계 자료 복사 자체는 작업 실행이나 새 재작업 발급이 아닙니다. `--previous`를 생략하면 전역 중복 여부를 알 수 없으므로 `NO_PREVIOUS_SAMPLE`을 표시합니다.

### 6. 수동 병합 판단 직전 재조회

ChatGPT가 **그 head의 검토를 마쳤다고 사용자가 명시한 경우에만** 해당 head를 전달합니다.

```bash
python -m automation.manual_merge_readiness --pr 16 --previous /tmp/pr16-review-r2.json --reviewed-head 실제_검토한_40자리_SHA --format markdown
```

검토 완료를 아직 표시하지 않을 때는 `--reviewed-head` 없이 실행합니다. head뿐 아니라 base/master/리뷰·CI·승인/보호 표본이 바뀌면 기존 완료 표시는 거부합니다. 이때 자료를 다시 생성하고 바뀐 내용을 검토해야 합니다. 사람이 표본을 편집해 해시를 다시 만들더라도 공식 승인 증거가 되지 않습니다.

`REVIEW_COMPLETE_MANUAL_DECISION_REQUIRED`는 **사용자 선언에 따른 UI 단계**입니다. GitHub 표준 APPROVED, 별도 reviewer ID, P4 task/원장 증거가 아닙니다. 같은 GitHub 계정의 COMMENTED나 별도 ChatGPT 대화의 검토를 독립 승인으로 승격하지 않습니다. 보호 미설정·권한 부족·P4 미충족은 자동 병합 차단 사유로 계속 출력합니다. 판단과 병합은 사용자가 시작한 별도 ChatGPT 대화의 명시적 작업으로만 진행하며 Codex는 병합하지 않습니다.

마지막 조회 직후에도 상태는 바뀔 수 있습니다. 보고서는 서버 측 원자적 merge 조건이나 수동 병합 허가 토큰이 아니며 **merge API 코드가 없습니다**. 자동 병합 상태는 항상 `AUTOMATED_MERGE_BLOCKED`입니다.

### 7. 병합 후 다음 작업

다시 인계 도구를 실행하면 `CLOSED_OR_MERGED`와 다음 작업 계획 안내가 나옵니다. 닫힌/병합된 PR에는 재작업 요청을 발급하지 않습니다. 다음 승인 작업은 사용자 대화에서 선택합니다. 다음 Issue를 자동 claim하거나 Codex를 호출하지 않습니다.

## 상태와 종료 코드

| 상태 | 다음 사용자 행동 |
|---|---|
| DEVELOPMENT_COMPLETE | Codex에게 개발 PR 제출을 요청한 뒤 인계 도구 실행 (이 상태 안내는 공통 어휘이며 PR 생성 이벤트를 수신하지 않음) |
| PR_READY_FOR_REVIEW | ChatGPT 리뷰 요청문 복사 |
| WAIT_CHATGPT_REVIEW | 별도 대화에서 검토 |
| CHANGES_REQUESTED | 현재 head 기준 수정 사항을 구조화 |
| WAIT_CODEX_REWORK | 재작업 요청문을 Codex에 전달. duplicate이면 기존 작업 확인 |
| WAIT_CI | 실패/진행 중인 최신 job 확인 후 재조회 |
| REVIEW_COMPLETE_MANUAL_DECISION_REQUIRED | 최신 상태를 확인하고 별도 ChatGPT 대화에서 수동 판단 |
| AUTOMATED_MERGE_BLOCKED | 자동 게이트 상태. 수동 UX 단계와 독립적이며 이번에는 항상 유지 |
| CLOSED_OR_MERGED | 병합 여부 확인 후 다음 작업 계획 |
| HALTED | 오래된 표본/오류/승인 취소/기록 불일치를 조사. 초기화로 우회 금지 |

review_handoff/rework_handoff의 정상 자료 생성은 exit 0, 검증 실패는 exit 2입니다. 이는 **자료 생성 성공**만 의미합니다. manual_merge_readiness는 수동 판단 준비가 돼도 항상 exit 2이며 자동 merge 승인으로 해석하면 안 됩니다. `automation_status=DISABLED`, 실행/게시/병합 false는 모든 결과에서 유지합니다.

## 수집 범위와 보안 경계

- Repository ID **888367411**, 동일 repo PR head/base, PR 번호/state/draft/mergeable, 현재 master를 검증합니다. 공개 저장소가 아니면 중단합니다.
- 변경 파일 전체 페이지 수/총개수/중복을 확인합니다. 파일 **내용·patch·로그**는 출력하지 않습니다. rename 이전 경로도 민감도로 분류합니다. credential 경로/POLICY_PROTECTED/테스트/문서/기타 코드로 구분하며 보호 경로를 승인으로 바꾸지 않습니다.
- CI는 P2의 latest trusted PR run/attempt/workflow/PR/head/job/check/App 검증을 재사용합니다. 실패/진행/누락/실제 skipped job을 성공 처리하지 않습니다. 네 정확한 필수 검사와 `.github/workflows/tests.yml`을 유지합니다.
- Windows의 기존 POSIX unit test 5개 skip과 **CI job 전체 skipped**는 다릅니다. 도구는 원문 로그를 읽지 않아 개별 test 수/skip 수는 `NOT_COLLECTED`입니다. job success만으로 테스트 개수를 지어내지 않습니다. 개발자는 전체 suite 및 실제 CI 결과를 별도로 기록합니다.
- Review는 불변 ID/state/commit_id/content hash만 출력합니다. P4 allowlist와 보호 규칙의 승인 수를 적용한 좁은 결과이며 전체 P4 통과가 아닙니다. 리뷰 본문은 출력하지 않되 본문 변경은 opaque hash로 감지합니다.
- GraphQL threads는 정확한 head/base와 전체 pagination/불변 ID를 확인하며 outdated라도 unresolved면 차단합니다.
- 보호/ruleset은 P4의 strict source/bypass 검증을 재사용합니다. 권한 부족은 PENDING, branch protected=false는 BLOCKED로 표시합니다. CODEOWNERS는 정확한 PR base의 존재/blob metadata만 출력합니다. 개별 owner 권한/승인까지 검증했다고 주장하지 않습니다.
- Issue 기계 계약이 없는 일반 수동 PR도 유용한 인계 자료를 생성합니다. 계약이 있는 Issue의 현재 승인 실패/취소는 재작업과 수동 완료 표시를 차단합니다. 수동 PR을 승인 Issue/FakeAuthority로 승격하지 않습니다.
- HTTP 401/403/404/429는 고정 코드로 구분하고 stderr/response body/환경변수/credential을 출력하지 않습니다. PR/리뷰 본문·주석은 명령으로 해석하지 않습니다.
- 목적·피드백은 길이/단일행/control/bidi/credential-like 패턴 검사를 거칩니다. 경로는 제한된 문자 집합 밖이거나 비밀 패턴이면 `WITHHELD_PATH_<hash>`로 대체합니다. 원문을 잘라 비밀 일부를 보여주지 않습니다. 탐지 패턴은 모든 임의 비밀이나 모든 prompt injection을 탐지하는 보장이 아니므로 민감 원문을 입력하지 않습니다.
- 요청문에는 고정 안전 지시와 신뢰하지 않는 JSON findings를 분리합니다. 이 도구는 findings를 코드/셸/모델 입력으로 실행하지 않습니다. 사용자가 전달한 뒤의 LLM 동작까지 강제로 보증하는 sandbox는 아닙니다.
- 두 번의 수집 사이 변화는 HALTED입니다. 한 pass 내부 캐시는 다음 pass에 재사용하지 않습니다. 이 검사는 관측 시점의 일관성을 높일 뿐 GitHub 원자적 상태/변경 불가능성을 증명하지 않습니다.

## 로컬 영수증 복구와 Cloud 한계

`handoff_journal.py`는 SQLite BEGIN IMMEDIATE로 단일 writer를 관리하고 state/정책 hash/누적 receipt를 확인합니다. checkpoint를 fsync/atomic replace한 뒤 SQLite를 commit합니다. 다른 issuer는 트랜잭션이 끝날 때까지 대기합니다. checkpoint와 DB 사이 crash/한쪽만 복원/분실/손상은 HALTED이며 자동 복구/초기화하지 않습니다. checkpoint/DB 파일과 parent의 POSIX 소유자/0700·0600을 확인합니다. Windows는 POSIX 권한 증명을 제공하지 않습니다.

DB와 checkpoint를 **함께** 과거로 되돌리는 경우, 같은 사용자/관리자의 변조, 전체 Cloud workspace 복원은 독립 신뢰 앵커 없이 탐지하지 못합니다. SQLite와 별도 파일의 hash는 trusted monotonic counter가 아닙니다. Cloud 새 작업에 ignored 파일이 자동 복원되거나 실행 프로세스가 계속된다고 가정하지 않습니다. 기존 작업 기록이 없어졌으면 재초기화해서 활성 PR의 횟수를 우회하지 말고 보류합니다. 이 영수증은 P1/P3 예산/시도 원장과 별개이며 실제 실행 안전성의 증거로 사용할 수 없습니다.

## 현재 실측과 미검증

개발 중 실제 병합된 PR #15에서 조회를 검증했습니다. head `376db357ddb5153fb1f065863693575aeb34886e`, PR base `c106207eb1152c97c8169b4a96f3b8f2c70f66d1`, 현재 master `3ccddc5edc237bd5989b353d2795a986491b372f`를 구분하고 `CLOSED_OR_MERGED`로 표시했습니다. 과거 PR CI의 현재 관계 미확인과 보호 endpoint 접근 제한은 경고로 남기며 성공으로 꾸미지 않습니다. 열린 이번 개발 PR의 최종 head/CI 실측은 PR 설명에 기록합니다.

Work UI·구매 크레딧 미사용 강제·독립 reviewer identity·MacBook·실제 모델/게시/병합 E2E는 검증하지 않았습니다. Work 설정은 [별도 안내](P7_CHATGPT_WORK_SETUP.md)에 두며 설정하지 않아도 본 흐름이 동작합니다. policy.enabled=false, zero-cost 실운영 허용=false, REAL_TRADING=False, launchd/Secrets/권한/보호 설정은 그대로입니다.

## 검증과 P8

```bash
python -m unittest tests.test_review_handoff -v
python -m unittest discover -v
python tools/check_safety.py
```

기존 476개와 신규 64개를 함께 실행합니다. Fake HTTP/Mock으로 정상/실패/진행/skip, 표준·자기·오래된 리뷰, 새 head, 중복 요청/동시 발급, 승인 취소, 악성/비밀 본문, API 오류, closed PR, snapshot/영수증 손상·rollback·crash gap을 검증합니다. Linux/macOS/Windows Python 3.12 및 Safety gate의 실제 run/head/결과는 개발 PR에 기록합니다.

P8에서는 다음을 해결해야 합니다. 이번에 정책을 완화하거나 지원을 가정하지 않습니다.

1. Cloud 작업 사이 영수증의 무료 영속 전달·공동 rollback에 대한 신뢰 가능한 이력. 현재 동일 작업 내 local dedup 범위를 넘어선 보증은 없음
2. 구조화된 피드백의 사용자 확인/우선순위·민감 경로 검토 UX. 모델 실행은 계속 별도 수동 작업
3. 개인 Plus 포함량 전용 실행/구매 크레딧 미소비의 공식 강제 증거 및 최신 계정 검증
4. 별도 실제 GitHub reviewer ID의 표준 승인, 사용자 보호 설정과 필요한 read 권한. 같은 계정의 대화 검토는 대체 불가
5. 실제 실행 adapter를 검토한다면 P1/P3 원장·예약·CAS·lease·receipt와 서버 base/head 경합을 별도 검증. P7 packet/receipt/수동 완료 상태를 권한으로 전용하지 않음

P8 새 세션 주의: 이 문서의 `--init-journal`은 최초 생성 절차이며 세션 변경·파일 소실 후 한도 초기화 방법이 아닙니다. 기존 영수증을 찾을 수 없으면 UNKNOWN_NOT_RESET으로 보류하고 [P8 복구 절차](P8_CLOUD_RESUME.md)를 따릅니다. 로컬 DB+checkpoint 복사본만으로 Cloud 영속/롤백 부재를 증명하지 않습니다.
