# 자동 개발 아키텍처: 로컬·추가 지출 0원

## 최우선 정책과 현재 활성화 상태

기존 ChatGPT Plus 구독료 외 추가 지출 허용액은 **0원**입니다. `.github/automation/zero-cost.json`이 명시하는 API 과금·크레딧 구매·한도 확대·유료 runner·외부 유료 DB/서버/호스팅 금지가 우선합니다. 과거 API Codex Action/별도 유료 리뷰 API/클라우드 원장 제안은 채택하지 않습니다.

`enabled=false`, `REAL_TRADING=False`, 실제 builder/reviewer ID 빈 목록, Worker/Publisher/merge 비활성 상태를 유지합니다. P3는 실제 CLI·API 모델·Push·PR 자동 게시·merge·로그인·launchctl·운영 배포를 호출하지 않습니다. 사람이 승인한 개발 PR을 제출하는 작업은 Fake 자동화 실행과 별개입니다.

## 단계별 구현

| 단계 | 현재 구현 | 활성화 전 남은 경계 |
|---|---|---|
| P1 | [SQLite Fake-only 실행 원장](P1_LEDGER.md), 중복/예산/lease/복원 격리 | 실제 이벤트로 우회 연결 금지 |
| P2 | [인증된 GitHub GET 관측](P2_GITHUB_READONLY.md), ID/hash/head/리뷰/CI provenance | 보호 규칙 권한, 실제 HTTP E2E, rulesets/threads |
| P3 | [로컬 Worker·patch guard·Fake Publisher·CAS/SQLite](P3_LOCAL_WORKER.md), 0원 정책/launchd 예제 | 실제 Plus/M3/격리/포함량·추가 크레딧 차단 증명/내구 checkpoint |
| P4 | [독립 Review Gate·REST/GraphQL 최종 검증·Fake Merge/리뷰 재작업](P4_REVIEW_MERGE_GATE.md) | 실제 Plus 표준 Review 게시/사용량/권한 확인, 내구 checkpoint, 서버 base race 검증 및 별도 사용자 승인 |

## 목표 운영 방식

MacBook M3의 Python 3.12/로컬 SQLite를 단일 조정자로 사용하고 launchd가 Mac이 깨어 있는 동안 trusted controller를 깨웁니다. P2로 승인 Issue/PR/리뷰/CI를 재조회하고 작업별 CAS/lease를 예약합니다. 향후 공식 ChatGPT 로그인 Codex CLI가 별도 작업 디렉터리에서 실행되고, 독립 broker가 byte patch를 검증해 지정 PR에 게시하는 구조입니다. 현재는 이 모든 실행/게시가 Fake입니다. GitHub-hosted runner에 Mac 인증정보/원장/쓰기 키를 전달하지 않으며 여러 runner의 SQLite 공유를 가정하지 않습니다.

승인 Issue → 제한된 Worker → 패치 검사 → 별도 Publisher → ChatGPT 독립 리뷰 → 수정 요청 시 최대 2회 재작업 → 최신 CI → P4 최종 판정 → 다음 Issue 순서입니다. 독립 리뷰 기능이 없으면 WAIT_REVIEW로 유지합니다. 비공식 ChatGPT HTTP 호출이나 유료 리뷰 API로 대체하지 않습니다.

## 구독 인증과 0원 확인

[공식 인증](https://learn.chatgpt.com/docs/auth)과 [비대화형 실행](https://learn.chatgpt.com/docs/non-interactive-mode)에 따라 사용자는 Mac에서 직접 `codex login` 브라우저 흐름을 사용합니다. [Plus 포함 범위](https://learn.chatgpt.com/docs/pricing)는 Codex CLI를 포함하지만 추가 크레딧 기능도 있으므로 인증·포함량·크레딧 미사용을 별도 검증해야 합니다. auth.json/Keychain/브라우저 토큰을 추출·복사·공유하지 않습니다. 공식 `codex login status`와 사용자 usage 화면 이외 비공식 인증 우회 경로를 만들지 않습니다.

`SubscriptionContract`는 API/대체 인증 환경, 인증 만료, quota 부족·불명, 0원 증빙 부재를 차단합니다. 모든 증빙을 Fake로 true라 해도 실제 execute 메서드는 항상 비활성입니다. 신뢰 가능한 실시간 포함량 및 크레딧 미사용 adapter가 없으므로 사용자 확인만 받아 즉시 켤 수 있는 스위치가 아닙니다. API 키는 등록할 항목이 아니라 금지 항목입니다.

## 신뢰 주체와 최소 권한

Worker는 GitHub writer·Secrets·host 파일·주변 환경·네트워크를 받지 않습니다. Controller는 읽기/원장만, Publisher는 검증된 패치의 지정 branch 게시만, 독립 Reviewer는 별도 불변 ID의 현재 commit 표준 Review만 담당하도록 분리합니다. 현재 권한 확대/Secret 등록/App 설치는 없습니다. 실제 OS 계정/sandbox/자격 경계 검증은 P3 문서의 미해결 사항입니다.

기존 P2 observer는 master 코드와 읽기 전용 GitHub token을 유지합니다. PR head/artifact를 권한 있는 job에서 실행하지 않습니다. P3를 Actions Worker로 등록하지 않으며 기존 Action 예제는 외부 workflows 위치에서 if:false인 폐기 안내로 남깁니다. 실제 구독 인증은 GitHub runner로 옮기지 않습니다.

## 실패·재시작과 비용

회당 $5/Issue $15/UTC 일일 $50은 기존 **모의 예약값**이며 실제 지출 허용액은 0원입니다. 총 3회, 연속 실패 2회, lease 최대 300초/절대 1,200초, 영속 dispatch/fencing을 유지합니다. 승인/명세/feedback/head 변경은 기존 결과를 재사용하지 못합니다. 게시 응답 유실은 HALTED 후 read-only receipt 대조이며 재전송하지 않습니다.

Mac sleep/offline/프로세스 중단 후 원장·lease·현재 GitHub 상태·독립 checkpoint를 먼저 대조합니다. 증빙 없이는 자동 resume하지 않습니다. P3 FakeWitness는 내구 anti-rollback 구현이 아니며 실제 프로세스/디스크 rollback 검증은 추가 작업입니다. 원장 삭제·복원·환급으로 한도를 초기화하지 않습니다. 공유 서비스를 필수로 두지 않으며 선택적 CoordinationStore/FakeSharedStore는 설계/경합 시험용입니다.

## CI 및 P5 진입 체크리스트

기존 Python 3.12 Linux/macOS/Windows 전체 테스트와 safety gate를 유지합니다. 현재 공개 저장소의 standard runner는 [GitHub 공식 문서](https://docs.github.com/en/billing/concepts/product-billing/github-actions)의 무료 범위입니다. 캐시 업로드를 제거하고 artifact/유료 runner를 추가하지 않았습니다. 공개 여부/무료 조건이 바뀌면 중단해야 합니다. skipped/neutral/누락/다른 SHA/동명 위조 Check는 통과가 아닙니다.

- [ ] 사용자 Plus의 공식 CLI 로그인, 포함 quota/추가 크레딧 미사용 및 0원 증빙 경로 검증
- [ ] M3에서 CLI pin·자격 격리·취소/시간/출력/도구/환경 제한의 실제 테스트
- [ ] 사용자만 launchd 경로/계정 확인. 예제는 Disabled 상태이며 설치하지 않음
- [ ] 로컬 내구 checkpoint·잠자기/프로세스 복구·P2 API 재조회 및 실제 outbox 부분 성공 조정
- [ ] 사용자 Plus의 GitHub PR 이벤트/예약 Work 가용성과 정확한 commit_id의 독립 표준 리뷰 게시 확인. 미지원이면 대기 유지
- [ ] 사용자만 별도 builder/reviewer 불변 ID와 최소 GitHub 게시 권한을 검토. 이번 PR은 빈 목록/기존 권한 유지
- [ ] P4의 rulesets/CODEOWNERS/review threads/최신 head-base/CI 검증을 실제 계정의 읽기 권한으로 확인. Fake CAS는 실제 GitHub expected-base 원자성을 증명하지 않음. 실제 merge API 없음
- [ ] 0원 및 독립 검증 통과 후에도 사용자 명시적 활성화 승인이 별도로 필요

실제 업비트 주문, 실거래 모드, 운영 배포, 검증되지 않은 전략의 실거래 채택, 인증정보 출력, 검증 우회 병합은 계속 금지합니다. PR은 Codex가 직접 병합하지 않고 별도 ChatGPT가 리뷰합니다.

## P4 최종 검증 경계

MergeReader의 실제 REST/고정 GraphQL query 결과는 읽기 전용 READ_ONLY_VERIFIED이며 실행/예약/게시 권한이 아닙니다. MERGE_ELIGIBLE은 exact Fake 서비스와 P3 patch/receipt/명세/head가 일치할 때만 생성되는 15초 모의 후보입니다. 최종 재조회·Fake 서버 CAS·intent/receipt를 기록하고 중단/응답 유실은 HALTED로 격리합니다. 자동 재전송·재부팅 후 자동 재개는 없습니다.

PR #12 observer는 승인 Issue 계약이 없는 일반 PR에서 발생하는 보류를 Fake HTTP로 재현했습니다. 과거 generic 로그만으로 실제 최초 실패를 확정할 수 없어 권한/스키마 원인은 미확인으로 남깁니다. 새 고정 진단 코드는 PENDING/exit 2를 유지하며 master 병합 후에만 trusted observer에 반영됩니다. 상세 조사·한계·P5 설정은 P4 문서를 참조하세요.

## P5 로컬 통합 준비

[P5_LOCAL_INTEGRATION.md](P5_LOCAL_INTEGRATION.md)에 읽기 전용 Mac Preflight, GET 전용 GitHub 진단, 기존 Fake-only 원장 Controller 검사, 파일 내구 checkpoint와 복구 한계, 타이머 없는 disabled launchd 예제, 사용자 설정 순서를 정리했습니다. 실제 모델·자동 게시·병합·실거래는 비활성입니다. Plus 로그인만으로 구매 크레딧 미사용을 증명하지 않으며 신뢰할 수 있는 포함량 전용 제어가 없으면 HALTED, 독립 리뷰 미지원이면 WAIT_REVIEW입니다. MacBook/Plus 계정 실측은 수행하지 않았습니다.

## P6-A Cloud 준비 상태

[P6_CLOUD_INTEGRATION_READINESS.md](P6_CLOUD_INTEGRATION_READINESS.md)와 `python -m automation.cloud_readiness --pr N`은 Mac 없이 가능한 공식 GitHub 읽기/비용·독립성 준비 상태를 보고합니다. 전체 DISABLED/exit 2를 유지합니다. PR #14에서 관측된 COMMENTED actor는 작성자와 같은 ID여서 별도 ChatGPT 대화의 검토를 GitHub 독립 승인으로 승격하지 않습니다. 공식 App Server quota 읽기 기능은 문서화돼 있지만 구매 크레딧 미소비 강제 증거가 아닙니다. 추천은 사용자가 시작하는 Cloud 개발과 수동 검토 단계이며 실제 자동 실행/게시/병합은 허가하지 않습니다.

## P7 사용자 주도 Cloud 리뷰 핸드오프

[P7_CLOUD_REVIEW_WORKFLOW.md](P7_CLOUD_REVIEW_WORKFLOW.md)에 Android에서 복사해 쓸 리뷰 요청·재작업·수동 병합 전 점검 순서를 정리했습니다. `automation.review_handoff`, `automation.rework_handoff`, `automation.manual_merge_readiness`는 exact head/base와 최신 읽기 표본을 사용하며 모델·GitHub 쓰기를 호출하지 않습니다. 로컬 재작업 영수증은 중복 발급/PR당 최대 2회 요청을 관리하지만 P1/P3 실행 원장이나 trusted anti-rollback 증거가 아닙니다. 수동 검토 완료 UI와 P4 독립 GitHub 승인은 별개이며 자동 병합 상태는 항상 AUTOMATED_MERGE_BLOCKED입니다. Work 이벤트는 [별도 안내](P7_CHATGPT_WORK_SETUP.md)만 제공하고 생성하지 않습니다.

## 영구 불변 원칙과 Slack

매 작업 지침은 AGENTS.md, 상세 계약은 [PROJECT_INVARIANTS.md](PROJECT_INVARIANTS.md)를 따릅니다. Safety gate는 비활성·0원 정책을 정적으로 추가 검사하며 실제 과금/권한의 증명이 아닙니다. [SLACK_NOTIFICATIONS.md](SLACK_NOTIFICATIONS.md)의 기존 서비스/outbox는 기본 OFF입니다. 개발 이벤트는 Fake 포맷만 제공하며 observer/Work 이벤트를 Slack에 연결하지 않습니다. 현재 개발 PR 작성 권한을 운영 알림·무인 실행 승인으로 해석하지 않습니다.

## P8 Cloud 세션 복구 / Slack 수치 점검

[휴대폰 세션 복구 절차](P8_CLOUD_RESUME.md): `python -m automation.cloud_resume`가 P7 증거 수집을 재사용해 master/최근 PR/선택 PR exact head·base·CI·리뷰·병합을 재조회합니다. 오래된 검토는 무효이며 SQLite/영수증 소실 시 한도는 UNKNOWN_NOT_RESET입니다. 새 원장·초기화·재작업 실행은 제공하지 않습니다. 병합 PR의 PR-run 연결이 사라진 경우 과거 CI 메타데이터 관찰만 별도로 표시하며 P2/P4 gate를 통과시키지 않습니다.

[Slack 지표 계약](SLACK_NOTIFICATIONS.md)의 확정/부분/물타기/재시작·수수료·손익·평가·누적/일간 구분을 검증합니다. `python -m utils.slack_preflight`는 토큰/SDK/config 실행 없이 기본 OFF 및 선택 outbox를 읽기 전용 점검합니다. enqueue는 전달 성공이 아닙니다. 실제 Slack/모델/자동 개발·게시·병합/거래/배포/스케줄은 계속 비활성입니다.

## P9-A PAPER 성과 정산

[수수료 포함 읽기 전용 회계 계약](P9_PERFORMANCE_ACCOUNTING.md)을 추가했습니다. 기록 순서/settled_at와 원장 수치를 검증해 이동평균 순원가·부분 매도 배분·누적/일간 확정 순손익을 계산합니다. UTC/KST 일간 평가손익/총수익률은 스냅샷 부재로 N/A입니다. 원문 전략을 추정하지 않으며 기존 거래/복구 원장과 자동화 설정은 그대로입니다. 실제 Slack/모델/자동 게시·병합/실거래/운영 배포는 계속 비활성입니다.

## P9-B 복구와 PAPER 통합

[P9B_CLOUD_RECOVERY.md](P9B_CLOUD_RECOVERY.md)는 기존 HandoffJournal의 개별 손상/롤백과 공동 롤백 한계, 사용자 보관 메타데이터 비교를 설명합니다. 요약 일치도 신뢰 witness가 아니며 HALTED/UNKNOWN_NOT_RESET을 유지합니다. [PAPER_END_TO_END_VALIDATION.md](PAPER_END_TO_END_VALIDATION.md)는 기존 실제 PAPER 코드와 Mock Slack 전체 경로, 최소 결함 수정, 단일 소비자/백업/사용자 MacBook 체크리스트를 제공합니다. 실제 운영/Slack/모델/게시·병합 활성화는 없습니다.

## P10-A MacBook 오프라인 준비

[MacBook 체크리스트](P10_MAC_PREFLIGHT.md)와 [백업·복구](P10_BACKUP_RECOVERY.md)를 추가했습니다. 기존 PAPER 상태 잠금의 OS 방식을 재사용해 outbox 연결 전부터 close까지 단일 소유권을 유지하고 SENDING 복구와 전송 중 종료를 보호합니다. `python -m ops.local_preflight`는 외부 API/설정 import/상태 변경 없이 진단하며 실행 허가를 반환하지 않습니다. `python -m ops.paper_rehearsal`은 기존 P9-B 테스트와 최소 추가 회귀 테스트를 임시 데이터로 실행해 비밀 없는 요약을 출력합니다. MacBook·실제 Slack·네트워크 운영은 미검증, 자동 실행/게시/병합/Slack/launchd는 계속 비활성입니다. 공동 롤백·영수증 소실은 HALTED/UNKNOWN_NOT_RESET 유지입니다.

P10-C 공개 데이터 PAPER 관찰 도구의 offline 기본값, 기존 원장만 재사용하는 명시적 시간 제한 실행, 순위 조회 변경 및 SSH 종료/복구 절차는 [P10C_PAPER_OPERATIONS.md](P10C_PAPER_OPERATIONS.md)를 따릅니다. 실제 주문/Slack/launchd/자동화는 활성화하지 않습니다.
