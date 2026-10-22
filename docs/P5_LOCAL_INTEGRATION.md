# P5: MacBook 로컬 통합 준비 (비활성)

기존 Plus 구독료 외 추가 지출 허용액은 **0원**입니다. 이 PR은 연결 준비만 합니다. 실제 Codex 모델 호출, 자동 개발, 자동 push/PR/merge, 거래, 운영 배포는 활성화하지 않습니다. `enabled=false`, 모든 live 플래그 false, `REAL_TRADING=False`를 유지합니다. 사람이 요청한 이번 개발 PR 제출은 자동 게시 기능 활성화와 별개입니다.

## 구현 경계와 실행 순서

MacBook에 접근하지 않았습니다. Linux Python 3.12 오프라인 검증만 수행했습니다. Mac 도구 설치·로그인·Keychain 접근·launchctl·시스템 설정·GitHub 권한/Secrets/보호 규칙 변경을 수행하지 않습니다.

1. 사용자가 Mac에서 Python 3.12, Git, gh, 공식 Codex CLI 설치 상태를 직접 확인합니다. 자동 설치하지 않습니다. 기존 checkout을 사용하며 임의 worktree를 만들지 않습니다.
2. 저장소 루트에서 다음 명령을 실행합니다. 보고서는 고정된 코드와 숫자 버전만 출력하며 인증정보/환경변수 값/명령 원문 출력을 전달하지 않습니다.

```bash
python3.12 -m automation.local_preflight --dry-run
python3.12 -m automation.local_github_diagnostics
python3.12 -m automation.launchd_plan
python3.12 -m unittest discover -v
python3.12 tools/check_safety.py
```

Preflight와 연결 진단의 exit **2는 의도적인 비활성/미검증 결과**입니다. 성공으로 바꾸지 않습니다. PASS는 해당 좁은 검사만 통과했다는 뜻입니다. FAIL은 안전 조건 불충족, PENDING은 증거 미확인, USER_ACTION_REQUIRED는 도구/로컬 경로 등 사용자 준비가 필요한 상태입니다. 전체 HALTED는 실행 권한이 아닙니다.

Preflight는 macOS/arm64, 현재 Python, 도구의 `--version`, 공식 `codex login status`, Git 저장소/브랜치/추적 파일 변경, `gh auth status`, launchctl **존재 여부**, 명시된 SQLite 경로의 메타데이터를 읽습니다. `codex login status` 원문과 stderr는 출력하지 않습니다. 버전 검사는 정규식 숫자만 전달합니다. M3 모델 자체, macOS 최소 지원 버전, 실제 quota/Plus 등급은 이 검사로 입증하지 못합니다. master 여부는 fetch/reset 없이 현재 브랜치만 검사하며 최신 원격과 같다는 증명이 아닙니다. gh 인증 상태는 실제 repository API 권한 증명이 아닙니다.

```bash
python3.12 -m automation.local_preflight --dry-run --database "$HOME/Library/Application Support/auto_trade/ledger/tasks.sqlite"
```

권장 원장 경로는 위 경로, checkpoint는 별도 `~/Library/Application Support/auto_trade/checkpoints/tasks.json`, 로그는 `~/Library/Logs/auto_trade/`입니다. 사용자가 소유한 디렉터리 0700/파일 0600, 로컬 APFS를 사용합니다. iCloud Drive/Dropbox/네트워크 볼륨/공유 runner에서 원장을 사용하지 않습니다. 경로 생성이나 chmod도 진단기가 수행하지 않습니다. 경로 검사는 무결성 검사가 아니며 symlink·ACL·상위 디렉터리·볼륨 내구성은 Mac에서 추가 확인해야 합니다.

## 구독 및 0원 증명 계약

공식 인증 참조: [Codex auth](https://developers.openai.com/codex/auth/), [Plus Codex](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan), [pricing](https://developers.openai.com/codex/pricing/), [config](https://developers.openai.com/codex/config-reference/). P3 예제는 `forced_login_method="chatgpt"`입니다. 사용자는 설치된 버전의 공식 문서와 `codex login --help`를 확인한 후 직접 ChatGPT 로그인을 진행해야 합니다. 이번 작업에서는 로그인/모델 시험을 하지 않았습니다.

**공식 문서와 실제 계정 검증을 구분합니다.** 기존 P3/P4가 공식 ChatGPT 인증 경로를 참조하지만, 이번 환경은 공식 문서 HTTPS 조회가 네트워크에서 거부되어 최신 문서 본문을 재검증하지 못했습니다. 위 링크는 확인할 공식 출처이며 이번에 읽었다는 주장이 아닙니다. ChatGPT Plus 로그인만으로 포함량 전용 실행을 증명하지 않습니다.

`SubscriptionContract`는 chatgpt/plus, 인증 유효, 최신 포함 quota, 구매 크레딧 비사용, 0원 증명을 각각 요구합니다. API 키/대체 endpoint 이름은 값 없이 감지하여 차단합니다. 테스트의 `SubscriptionEvidence` boolean은 **Fake fixture**일 뿐 공식 계정 증빙 어댑터가 아닙니다. 사람이 true로 채운 JSON/로그인 성공/잔액 화면만으로 실제 모델 실행을 허가하지 않습니다. 실제 preflight는 항상 미검증 증거로 HALTED하며 execute는 항상 거부합니다.

사용자가 포함량 전용 실행을 강제하고 구매 크레딧 소비·자동 충전·리셋 구매를 막을 공식적인 제어/최신 증거를 확인하지 못하면 **실제 자동 실행은 계속 차단**합니다. quota 부족/만료/불명은 HALTED, API fallback 없음. 인증 파일/Keychain/세션 토큰 추출이나 계정 스크래핑은 구현하지 않습니다. 이번 PR은 구매 크레딧 미사용을 증명하는 공식 방법을 확보하지 못했습니다. 실제 모델 시험도 별도 명시적 사용자 승인 전 금지입니다.

## 로컬 Controller와 복구

`local_controller`는 기존 SQLite 원장 + checkpoint만 열어 상태를 검사하며 생성/초기화/재실행 기능이 없습니다. P1/P3의 SQLite 영속 예약/감사/event dedup/CAS/lease와 P4의 intent/receipt를 그대로 사용합니다. P2 관측을 Fake 승인으로 변환하지 않습니다. 실제 GitHub 진단은 별도 읽기 경로이며 Controller에 실행 신호를 제공하지 않습니다.

```bash
python3.12 -m automation.local_controller --database /ABSOLUTE/ledger/tasks.sqlite --checkpoint /ABSOLUTE/checkpoints/tasks.json
```

한 Mac에서 `single_writer`의 커널 flock을 **원장 전체 작업 수명 동안** 유지해야 합니다. 관리 도구 inspect는 이 lock을 잡습니다. 향후 writer도 동일 lock을 사용해야 하며 SQLite BEGIN IMMEDIATE만으로 checkpoint 변경까지 원자적이지 않습니다. 현재 실제 writer가 없습니다. lock 파일은 삭제하지 않으며 프로세스 종료 시 OS가 lock만 해제합니다. Windows에서는 Controller lock을 지원하지 않아 차단하고, Fake pipeline 테스트는 계속 지원합니다.

`LocalCheckpoint`는 별도 0600 파일에 epoch/revision/전체 상태 hash를 저장하고 exclusive temporary file → fsync → replace → POSIX directory fsync로 교체합니다. 새 프로세스에서도 high-water mark를 읽습니다. DB commit 후 checkpoint 전 crash, 손상, 한쪽만 복원, 잔여 .next, checkpoint 분실은 중단합니다. mismatch를 DB에서 재생성하거나 DB를 삭제해 해결하지 않습니다. 파일 기반 checkpoint는 무료이며 관리자 권한·서비스·Keychain을 요구하지 않습니다. 지원 API는 Fake-only 원장에만 한정합니다.

**한계:** 동일 사용자/관리자 공격, DB+checkpoint의 공동 백업 롤백, 디스크 전체 복원/분실은 독립 신뢰 앵커 없이 감지할 수 없습니다. fsync는 APFS 하드웨어 전원 손실 보증이나 macOS F_FULLFSYNC 검증을 대체하지 않습니다. 이 한계는 실제 실행 차단 사유이며 비용/예약의 안전한 단조성을 증명했다고 주장하지 않습니다.

무료 내구 대안 조사/설계: 별도 로컬 APFS 경로는 재시작 지속성에 유용하지만 공동 복원 방지가 아닙니다. 사용자가 이미 보유한 별도 디스크의 append-only checkpoint와 분리 보관한 수동 서명/검토 기록을 비교하는 수동 복구는 추가 서비스 비용 없이 가능하지만, 디스크가 없으면 0원 전제에서 새 구매하지 않습니다. macOS Keychain에 비밀 아닌 high-water mark를 저장하는 안도 계정 권한/백업 복원/접근 정책의 실측이 필요하며 trusted monotonic counter로 단정하지 않습니다. 이번에는 Keychain을 읽거나 쓰지 않았습니다. Mac 내구 checkpoint의 강한 anti-rollback은 **미완료/실행 차단**입니다.

sleep/reboot/네트워크 오류 후 RUNNING/PATCH_READY/PUBLISHING/UNKNOWN/중단된 MERGE_INTENT가 있으면 HALTED. 짧은 중단이어도 자동 재개하지 않습니다. lease 만료·시간 역행·현재 Issue 승인/PR head·CI·독립 리뷰·receipt를 다시 확인하고 사람이 원인을 검토해야 합니다. 조회 실패는 재전송 사유가 아닙니다. publish 응답 유실은 receipt 확인만 하며 HALTED를 해제하지 않습니다. 예약/시도 수를 환급하거나 초기화하지 않습니다. 감사 로그는 원장 안의 revision/event/hash이며 Issue/리뷰 원문을 저장하지 않습니다.

## launchd 준비

`ops/local/com.auto-trade.dry-run.plist.example`은 등록되지 않은 예제입니다. Disabled=true, RunAtLoad=false, KeepAlive=false, **StartInterval/StartCalendarInterval 없음**. 작업은 preflight 한 번뿐이며 매매 프로그램을 실행하지 않습니다. 기본 자동 시작·반복 실행은 없습니다. stdout/stderr는 사용자 검토 후 private 로그 경로를 채웁니다. 사용자에게 검토되지 않은 전역 PATH나 상대 Python 경로를 사용하지 않습니다.

`python -m automation.launchd_plan`은 다음 후속 명령을 **문자열로만** 출력합니다. 이번에는 실행하지 않습니다. 사용자가 이후 별도로 승인하고 실제 절대 경로/로그 권한을 검토한 경우에만 수동 수행합니다.

```bash
plutil -lint /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist
launchctl bootstrap gui/$(id -u) /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist
launchctl print gui/$(id -u)/com.auto-trade.dry-run
launchctl bootout gui/$(id -u) /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist
```

이 명령 목록에는 enable/kickstart가 없습니다. 예제 등록 자체가 실행 활성화가 아니며 이후 자동 실행/주기 추가는 별도 변경입니다. Mac 잠자기 중 실행 보장이나 종료 중 계속 실행을 약속하지 않습니다.

## 독립 ChatGPT 리뷰 연결 가능성

공식 확인 대상: [ChatGPT Automations](https://learn.chatgpt.com/docs/automations), [GitHub 연결](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt), GitHub [Reviews](https://docs.github.com/en/rest/pulls/reviews). 이번 네트워크에서는 최신 본문과 실제 Plus 계정 UI를 확인하지 못했습니다.

| 항목 | 이번 확인 상태 | 차단 조건 |
|---|---|---|
| GitHub PR 생성 이벤트 → ChatGPT Work 작업 | 실제 Plus UI/제품 지원 미확인 | 작업 생성 안 함 |
| Plus 기본 포함량 및 추가 크레딧 비사용 | 미확인 | 0원 증거 전 HALTED |
| PR diff + 최신 head SHA 읽기 | P4 공식 GitHub API fixture 검증; ChatGPT 연결은 미확인 | 오래된/누락 head 거부 |
| 표준 APPROVED/CHANGES_REQUESTED 게시 | 실제 Work 기능 미확인 | 댓글을 Review로 승격하지 않음 |
| 별도 GitHub user.id로 독립성 | 실제 계정/역할 미설정 | builder와 같거나 unknown이면 차단 |
| 로컬 Controller의 리뷰 결과 조회 | P4 REST read-only 검증 계약 존재; 실환경 권한 미확인 | PENDING/WAIT_REVIEW |

사용자 설정 순서: ChatGPT 설정의 Apps/연결 기능에서 공식 GitHub 연결 제공 여부와 대상 저장소 scope를 확인 → Automations/Work UI가 실제 계정에 존재하는지 확인 → 문서에 명시된 PR 이벤트 trigger/action 지원과 포함 사용량 정책 확인 → 표준 Review 게시와 별도 GitHub ID 지원을 확인합니다. UI 명칭/지원이 없으면 그 지점에서 중단합니다. 확인 전 작업 생성이나 권한 승인하지 않습니다. 정확한 현재 UI를 보지 못했으므로 존재하지 않는 버튼 경로를 지어내지 않습니다. 지원된다면 사용자만 최소 scope로 연결/이벤트 작업 생성/권한 승인을 수행합니다. 비공식 API, 브라우저 자동화, 세션 스크래핑, 유료 API는 대안으로 사용하지 않습니다. 미지원이면 WAIT_REVIEW와 사람의 독립 표준 리뷰 절차를 유지하며 Codex가 대리 승인하지 않습니다.

## GitHub 실환경 점검

2026-10-22: 기존 HTTPS Git 인증으로 `git ls-remote origin HEAD` 성공, master=8754f614c83b849bf16f800b941fcab9deaf7296 (PR #13). 그러나 gh API repository/master/actions-permissions 호출은 프록시 CONNECT 403으로 실패했습니다. **현재 branch protection/App/권한/독립 계정이 재확인됐다고 주장하지 않습니다.** 사용자 보고 및 P4 관측의 master 보호 비활성을 해제할 새 증거가 없으므로 실제 merge 허가는 항상 false입니다.

`local_github_diagnostics`는 고정 GET 6종만 수행합니다. 접근 실패는 API_ACCESS_UNVERIFIED이며 Issue 계약 누락과 구분합니다. 성공은 좁은 응답 조회일 뿐 보호 정책 전체 검증/설치 App 전체 목록/실제 token scope 증명이 아닙니다. `/installation`은 repository에 접근 가능한 설치 정보이며 모든 App/개별 권한 열거가 아닙니다. threads/CODEOWNERS/독립 ID/과금은 PENDING으로 남깁니다. 상세 검증은 P4의 정확한 TaskReference/head 기반 reader를 사용하며 권한 부족을 성공 처리하지 않습니다.

저장소 workflow는 표준 ubuntu-latest/macos-latest/windows-latest Python 3.12이며 Safety gate가 세 플랫폼 성공을 요구합니다. 공개 저장소 표준 hosted runner 무료 조건은 [GitHub 공식 billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)을 사용자가 현재 플랜/정책과 대조해야 합니다. public 여부의 실 API 응답, larger/self-hosted 유료 인프라 사용 여부, 계정 billing까지 이번에 검증하지 못했습니다. 새 runner/서버/DB/artifact 저장 과금 경로를 추가하지 않았습니다.

사용자만 master 보호/ruleset에 P4의 정확한 필수 CI, strict 최신 브랜치, 독립 리뷰, stale 승인 폐기/last-push 승인, thread resolution, admins 포함 적용, force/delete 금지와 bypass 제한을 검토/설정해야 합니다. CODEOWNERS 요구 시 실제 owner 권한/승인을 검증합니다. 현재 builder/reviewer 목록을 임의 채우지 않습니다. base/head 경합의 서버 원자성도 P4 Fake를 실제 보증으로 대체하지 않습니다.

P2의 일반 PR은 `/autodev issue N spec SHA256` 승인 Issue 계약이 없으면 ISSUE_CONTRACT_MISSING으로 정상 보류합니다. 실제 API 오류/인증 만료/권한 부족/스키마 오류는 별도 진단이며 계약 누락으로 단정하지 않습니다. 이번 API 실패 때문에 실제 observer run 최초 실패 원인을 추가로 확정하지 못했습니다. Python CI 성공과 observer PENDING은 서로 다른 결과입니다.

## 테스트와 남은 승인

기존 413개를 유지하고 P5 27개를 추가했습니다. Fake 정상 후보/수정 후 새 head, quota/크레딧/0원 증빙/인증 실패, 리뷰 미지원, 보호 없음/승인 취소, sleep lease 만료, 중복 claim, publish 응답 유실, 내구 재시작/손상/롤백/commit gap, 비활성 launchd/안전 진단을 검사합니다. 기존 P1~P4가 동시 CAS·중단 intent·실제 자식 프로세스 종료·Review/CI 경합을 함께 검사합니다. Mac 전원 손실/실제 OS 동작, 실제 Plus UI, 실제 무료 model E2E를 Fake 테스트 성공으로 대체하지 않습니다.

| 결과 구분 | 상태 |
|---|---|
| 구현 및 테스트 | Linux Python 3.12 전체 결과는 PR에 기록. 타 OS CI는 동일 head 실제 결과 확인 필요 |
| 실제 MacBook 검증 | 없음. 사용자 실행 및 결과 검토 필요 |
| 사용자 설정 필요 | 도구/공식 로그인, private 로컬 저장소/로그, GitHub 최소 권한/보호/독립 리뷰어, UI 기능 지원 확인 |
| 무료/기능 미확인 차단 | 포함량 전용 강제, 구매 크레딧 비사용, Work 표준 독립 리뷰, 강한 anti-rollback, GitHub 실 API 보호 증거 |

이 항목들을 충족해도 이번 PR은 활성화 권한을 반환하지 않습니다. 실제 실행은 별도 사용자 승인과 후속 구현/검증이 필요합니다.
