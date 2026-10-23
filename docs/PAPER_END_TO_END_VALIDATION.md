# PAPER → 원장 → 성과 정산 → Slack Mock 통합 검증

실제 AutoTrade, ExecutionEngine PAPER, StateStore, P9-A 정산, NotificationService, MessageQueue SQLite를 연결했습니다. 시장 데이터/분석 신호는 scripted 입력, Slack은 Mock입니다. 실제 거래소·Slack API, 모델, 운영 스케줄을 호출하지 않습니다. 새 실행 프레임워크/원장을 만들지 않습니다.

## 검증한 실제 경로

- BUY/SELL 신호 → `process_market_data` → PAPER 주문/확정 체결 → 영속 JSON과 메모리 동일 → 성과 정산 → 실제 포맷 → SQLite QUEUED → Mock `chat_postMessage` 응답 → SENT.
- 기존 +5% 익절, -5% 1회 물타기 후 추가 손절 경로를 실제 봇 코드로 통과했습니다. RSI/MACD/BB 산출 자체는 기존 테스트 범위이며 이 테스트에서 외부 OHLCV를 받지 않습니다.
- 기본 PAPER 체결 수수료는 엔진에서0입니다. 수수료 통합 시나리오는 **테스트에서만 `_apply_fill` 경계의 receipt를 복사해 Fake 수수료를 주입한 뒤 실제 체결 적용 함수로 전달**합니다. 엔진의 PAPER 수수료 정책/코드는 바꾸지 않았습니다. 400000 매수/수수료4000, 408000 매도/수수료4080에서 순손익−80·승률0·총수수료8080, 매수 직후 평가손익−4000을 원장/보고서/Mock 전달까지 확인했습니다. 테스트의 중간/최종 보고서는 별도 Fake 이벤트 ID로 큐에 넣으며 생산 스케줄/일일 중복 정책은 바꾸지 않았습니다.
- native PAPER 수수료0에서 10000 매수/10200 신호 매도는 순손익8000, 기존 +5% 익절은20000입니다. 물타기·손절 경로는 확정 주문3개와 음수 순손익을 확인했습니다. 수익성을 예측하는 결과가 아닙니다.
- 시작/상태/체결/오류/리포트/종료 및 FakeStream 시작·정지·close를 확인했습니다. 복구 보류는 RECOVERY_REQUIRED로 안내합니다. STOP의 성공 표시는 자원 종료이며 이전 HALTED/미확정 주문이 해결됐다는 뜻이 아닙니다.
- 확정 전 receipt 처리가 실패하면 원장은 intent로 남고 FILL은 없음. 재시작은 HALTED, 같은 주문 재제출 없음. 원장 삭제로 복구하지 않습니다.
- 재시작 후 원장 주문/확정 횟수와 outbox 중복 기록 유지. 가격은 새 관측 전까지 평가 N/A, 원장/알림 복원으로 새 체결을 만들지 않습니다.
- 일간 순실현과 누적 순실현을 별도 확인하고 일간 총수익률은 스냅샷 부재로 N/A입니다. 자세한 회계 정의는 P9_PERFORMANCE_ACCOUNTING.md를 따릅니다.
- enqueue 뒤 Mock client 호출0임을 먼저 확인합니다. 명시적 Mock drain 후에만 SENT이며 timeout/응답 유실은 UNKNOWN, 명확한 Slack 실패는 FAILED입니다. 중복 report/FILL을 다시 보내지 않습니다. Slack 실패/재시작/진단 전후 거래 원장 bytes/주문 수는 그대로입니다.

## 발견한 결함과 최소 수정

1. ExecutionEngine.halt가 원문 reason 및 저장 실패 traceback을 로그에 출력했습니다. 고정 문구로 변경했습니다. private 원장의 halt_reason과 중단/복구 상태·저장 정책은 유지합니다. 원장은 민감한 원문을 포함할 수 있으므로 공개 업로드 금지입니다.
2. SlackApiError 처리 중 outbox.finish까지 실패하면 예외가 handler 밖으로 전파됐습니다. UNKNOWN으로 반환하고 이미 영속화한 SENDING을 유지합니다. 다시 열면 기존 정책대로 UNKNOWN이며 재전송하지 않습니다. 주문에는 영향을 주지 않습니다.

그 외 주문/체결/전략/복구 로직과 거래 원장 스키마는 그대로입니다. 새 스케줄·writer·실제 Slack 소비자를 main에 연결하지 않았습니다.

## 준비 상태

| 항목 | 판정 |
| --- | --- |
| Linux Cloud의 실제 PAPER 코드 + Mock 전 구간 | 테스트 완료 |
| Linux/macOS/Windows Python3.12 | 동일 head CI 결과를 PR에서 확인 |
| 실제 시장 데이터/장시간 PAPER 운영 | 미검증, 사용자 승인 필요 |
| MacBook 파일 권한·sleep/wake·전원 종료·디스크 장애 | MacBook 접근 없음, 미검증 |
| Slack Bot/채널/chat:write/계정 무료 조건 | 미검증, 실제 발송 OFF |
| outbox 단일 소비자 | P9-B 시점에는 운영 필수 조건. P10에서 MessageQueue 수명 OS 잠금으로 보강 |
| Cloud 영수증 전역 롤백 방지 | 미구현, UNKNOWN_NOT_RESET / HALTED |

MessageQueue의 SQLite 트랜잭션은 개별 작업 선점/시도 한도를 보호하지만 운영 프로세스 독점 소유 증명이 아닙니다. P10에서는 별도의 OS 수명 잠금이 DB 연결/복구 전 협력 소비자의 동시 접근을 차단합니다. 두 소비자를 동시에 열거나 활성 소비자 옆에서 재시작 복구용 인스턴스를 열지 않습니다. constructor는 SENDING→UNKNOWN을 수행하므로 실제 소비자 정지 후에만 열어야 합니다. 읽기 전용 점검에는 utils.slack_preflight를 사용합니다. 금융 원장과 outbox는 서로의 백업/권한/이벤트 원장을 대체하지 않습니다.

## 사용자용 MacBook 운영 전 체크리스트

다음은 **향후 사용자 실행용**이며 이번 작업에서 MacBook에 실행하지 않았습니다. 도구를 자동 설치/로그인/launchd 등록하지 않습니다.

```text
python --version
python -m automation.local_preflight --dry-run
python tools/check_safety.py
python -m unittest tests.test_p9b_cloud_recovery tests.test_p9b_paper_slack_e2e -v
python -m unittest discover -v
python -m utils.slack_preflight --format json
python -m automation.cloud_resume --format markdown
```

- Python3.12/macOS arm64/git/gh 및 읽기 전용 인증 상태 확인. local_preflight는 추가 비용 증명이 없으면 HALTED/exit2가 정상이며 PAPER 봇 자체의 성공/실패 판정과 다릅니다. 실제 Codex 모델 호출은 하지 않습니다.
- 저장 경로는 개인 전용 디렉터리, POSIX 소유자/0700 디렉터리·0600 DB/checkpoint 확인. 토큰/세션/환경값은 터미널·대화에 출력하지 않습니다. 원장/checkpoint를 이미 쓰는 다른 프로세스가 없어야 합니다.
- 기존 outbox가 있을 때만 아래를 실행합니다. 파일이 없다고 진단 과정에서 초기화하지 않습니다.

```text
python -m utils.slack_preflight --outbox runtime/notifications.sqlite --format json
```

- Bot User OAuth token(xoxb) / app-level(xapp) 구분, chat:write, 채널 ID와 멤버십/초대를 사용자가 별도 확인합니다. 추가 App/권한 확대·새 구독은 이 단계에서 하지 않습니다. 토큰값을 진단에 입력하지 않습니다.
- 무료 Slack 플랜/앱 및 API 기능 허용 범위, 워크스페이스 기존 과금 상태, 메시지 보관/외부 전송 정책을 사용자가 확인해야 합니다. UI/설정 확인 없이 0원이라고 단정하지 않습니다.
- 실환경 PAPER를 실행하면 공개 시장 데이터 API/WebSocket과 로컬 파일을 사용합니다. 이번 테스트는 이를 검증하지 않았으며 main 실행·장시간 운영·재시작·sleep/wake 시험은 별도 사용자 승인 후 진행합니다. REAL_TRADING=False/Slack OFF/자동 시작 OFF를 유지합니다.
- 일관된 cold backup/복원 절차, outbox UNKNOWN 수동 조사, 단일 소비자 배선·종료·디스크 오류를 실제 MacBook에서 검증해야 합니다. `launchctl` 실행/자동 등록/운영 배포는 하지 않습니다.

## MacBook 없이 계속할 수 있는 일

기존 CLI 출력/복구 비교/Mock 장애 주입의 추가 검토, 사용자 보관 증빙 절차 검토, 안전한 cold backup 운영 절차 설계, 단일 소비자 독점 소유 방식 제안 및 검토는 가능합니다. 단, 새로운 원장/스케줄/유료 저장소를 도입하거나 무료 내구성/계정 권한을 추정하지 않습니다. 다음 단계는 사용자 실환경 검증 준비와 무료·독립 롤백 기준의 타당성 검토이며 실제 활성화는 별도 승인입니다.

P10 오프라인 사전 점검과 현재 단일 소유권/백업 경계는 [P10_MAC_PREFLIGHT.md](P10_MAC_PREFLIGHT.md) 및 [P10_BACKUP_RECOVERY.md](P10_BACKUP_RECOVERY.md)를 따릅니다.
