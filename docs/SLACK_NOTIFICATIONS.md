# Slack 알림: 기본 OFF

실제 Slack 발송·App 설치·권한 승인·토큰 조회는 이번 개발에서 수행하지 않습니다. NotificationService 기존 send_* 인터페이스는 유지하지만 자유 형식 문자열은 비공개 안내로 대체합니다. 신뢰되지 않은 예외·로그·계정정보를 직접 전송하지 않습니다. 서비스는 구조화 포맷터의 결과만 outbox에 기록합니다. 토큰 prefix 검사는 형식 검사이며 실제 권한 검증이 아닙니다.

## 사용자 설정 순서

1. 기존 무료 Slack 워크스페이스/앱의 이용 조건을 사용자가 확인합니다. 유료 기능·구독은 사용하지 않습니다. 새 앱 설치나 권한 승인은 이 작업에서 하지 않습니다.
2. 사용자가 Slack 앱의 OAuth & Permissions에서 **Bot Token Scopes: chat:write**만 확인합니다. 채널에 봇을 직접 초대하고 채널 ID를 확인합니다. chat:write.public 등 추가 권한은 요청하지 않습니다. chat:write는 초대된 채널 게시용입니다.
3. Bot User OAuth Token은 `xoxb-` 형식입니다. `xapp-` app-level 토큰은 chat.postMessage에 사용하지 않습니다. `SLACK_BOT_TOKEN` 설정을 우선하며 기존 `APP_TOKEN`은 Bot 토큰인 경우에만 호환됩니다. `SLACK_APP_TOKEN`은 기존 코드 호환용 alias이며 app-level 토큰을 뜻하지 않습니다. 값은 코드·PR·대화·로그에 넣지 않습니다.
4. 별도 사용자 승인 후에만 비활성 설정과 실행 배선을 검토합니다. 현재 `SLACK_NOTIFICATIONS_ENABLED=False`, 생성자의 `approved=False` 두 조건으로 차단됩니다. 현재 main에는 outbox/전송 worker를 연결하지 않았습니다. 토큰 존재만으로 실행되지 않습니다.
5. 향후 승인된 실행에서는 소유자 전용 runtime 디렉터리에 `MessageQueue(path, initialize=True)`를 최초 **1회 명시적으로** 생성합니다. 기존 DB는 `MessageQueue(path)`로 엽니다. Windows는 OS ACL도 별도 확인해야 합니다. POSIX는 파일 소유자·파일/직접 부모의 private 권한을 검사합니다. 상위 경로 전체·Windows ACL 검증은 구현하지 않았습니다.
6. `NotificationService(enabled=True, approved=True, queue=queue, channels=..., ...)`의 enqueue와 별도 `flush_one()` 소비자를 명시적으로 연결해야 합니다. 주문 처리 스레드에서 flush하지 않습니다. 서비스 기본 WebClient는 timeout=5, SDK 자동 retry 비활성입니다. 현재 작업에서 이 실환경 배선·호출은 하지 않았습니다.

공식 참고: https://docs.slack.dev/reference/methods/chat.postMessage/ , https://docs.slack.dev/authentication/tokens/ , https://docs.slack.dev/apis/web-api/rate-limits/ . 문서 링크는 권한/형식 안내이며 사용자 계정의 무료 지원·실제 발송 확인을 의미하지 않습니다.

## 구현 범위

- 모든 구조화 알림에 UTC timezone 포함 시각, PAPER/LIVE, 유형, 종목/작업, 상태, 해시 이벤트 ID. 매수/매도, Decimal 수량·평균가·금액·수수료·손익·물타기/사유. settled + 양수 체결량만 FILL. 미확정은 ORDER_UNKNOWN. 원장 uuid/계정번호 원문을 게시하지 않습니다.
- 시작/종료·복구 보류·중단·주기 상태 및 확정 거래는 기존 AutoTrade에 연결했습니다. 위험 유형과 개발 PR/CI/리뷰/병합 유형은 포맷터로 제공합니다. 개발 이벤트는 Fake 포맷 검증만 하며 실제 observer/Work에 연결하지 않습니다. 연속 손실 등의 새 전략 임계값/감지기는 만들지 않았습니다.
- 보고서는 재시작 포함 누적 확정 원장 기준입니다. 일간 손익으로 해석하지 않습니다. 현재 start_cash와 현금, 확인된 가격 기반 평가자산·누적수익률, 확정 주문 수·수수료, 매도 손익·승률을 계산합니다. 보유 수량·평균가는 원장에서 표시합니다. 평가손익/전략별/오류·중단 집계는 미집계입니다. 없는 값은 N/A이며 0을 추정하지 않습니다. 기존 PerformanceAnalyzer 원문은 외부 전송하지 않습니다.
- legacy send_* 반환 bool은 **로컬 enqueue 수락**이며 Slack 전달 성공이 아닙니다. 전달 상태는 SENT / FAILED / UNKNOWN / RATE_LIMITED / DEFERRED / DISABLED 등으로 별도 확인합니다. Slack 전송 장애를 Slack으로 재귀 통보하지 않습니다.

## Outbox와 중복/복구

SQLite 단일 연결 lock 및 BEGIN IMMEDIATE 선점, 채널별 최소 2초·UTC 일일 최대 900 **시도**(재시도 포함), 오류/체결 우선. 상태는 유형별 5분 bucket 중복 억제, 이벤트 ID별 영속 중복 억제. 반복 이벤트 수는 duplicates 테이블에서 집계하며 전송 직전까지의 반복 수를 첨부합니다. 전송 이후 반복만으로 별도 요약을 자동 발송하지 않습니다. 거래 중단은 실행 세션 ID로 구분하고 같은 세션의 중복 중단을 억제합니다. 최대 50,000 ID 후 새 enqueue 차단: 삭제·초기화로 중복 검사를 우회하지 말고 별도 보관/조정 설계가 필요합니다.

429는 Retry-After 2~3600초 범위로 최대 총 2회 시도합니다. timeout/예외/불완전 응답은 UNKNOWN으로 종료하며 무한 재시도하지 않습니다. 전송 직전 SENDING을 commit하므로 프로세스 종료 후 SENDING은 UNKNOWN으로 바뀌고 재전송하지 않습니다. 대기 QUEUED는 보존되지만 별도 수동 소비자 없이는 발송되지 않습니다. P10은 MessageQueue가 DB를 열기 전부터 close까지 OS 소유권 잠금으로 협력 소비자의 동시 사용을 차단합니다. 백업 롤백·기기 간 복사 방지는 보장하지 않습니다. 단일 소유 소비자만 사용하고 외부 sidecar/운영 복구 검증 전 무인 실행은 금지합니다.

Slack은 원장을 대체하지 않습니다. 전송 실패는 주문 제출을 호출하지 않습니다. 기존 사용자 정의 notification 객체가 예외를 내면 기존 거래 fail-closed 동작은 유지되며, 이미 확정된 주문을 다시 제출하지 않습니다. SQLite 손상/권한/초기화 오류는 차단하고 원장이나 outbox를 임의 초기화하지 않습니다.

## P8: 수치 검증 및 오프라인 진단

기존 원장/주문/전략은 변경하지 않고 `services/notification_metrics.py`에서 읽기 전용 검증을 합니다. 보고서는 실제 영속 원장과 메모리 상태가 같고, PAPER 엔진이 ready이며 halt 상태가 아닐 때만 계산합니다. 시작 자산은 생성자 값이므로 현금 흐름과 교차 검증합니다. 재시작 후 시작 자산이 다르면 관련 재무 수치를 모두 N/A로 표시합니다. 일관된 전체 원장 위조·백업 롤백을 탐지하는 새 보안 원장은 아닙니다.

- 확정 체결: settled + exchange_state done/cancel + 양수 체결량/금액, 매수·매도 방향, 비음수 수수료, 평균가×수량과 금액의 Decimal 일치 검증. 부분 체결 후 cancel은 실제 양수 체결량만 FILL이며, 0체결 취소·진행 중·NaN·불일치는 ORDER_UNKNOWN입니다. 가격·수량·금액·수수료는 저장된 확정 값 그대로 표시합니다. 일반 send_event/format_event 경로로 만든 성공 메시지는 FILL로 enqueue할 수 없으며 검증된 format_fill 결과만 사용합니다. 이 로컬 타입 구분은 암호학적 원장 인증이나 외부 데이터의 신뢰 증명이 아닙니다.
- 개별 체결 알림의 손익/손익률은 원가배분 근거가 없으므로 N/A입니다. 기존 engine.profit_rate는 매수/매도 수수료를 완전히 반영하지 않아 알림 성과값으로 사용하지 않습니다. 레거시 format_status_message의 total_profit 인자도 근거 미검증으로 N/A 처리합니다.
- 누적 수량은 매수량−매도량과 보유 수량을 대조합니다. 현금은 시작 자산−매수금액+매도금액−모든 수수료와 대조합니다. 잔여 원가도 기존 원장 평균가 및 기록 손익과 대조합니다. 음수 실현 손익은 signed Decimal로 유지합니다. 불일치·미확정 주문·필수 데이터 누락 시 모든 재무 지표는 N/A입니다.
- **누적 실현 손익**은 검증된 수수료 0인 PAPER 이력의 매도 확정 손익 합계입니다. **평가 손익**은 신선한 가격에 따른 보유 평가금액−남은 원가입니다. 수수료가 하나라도 있으면 원가배분을 추가 검증하기 전 실현/평가 손익과 승률은 N/A입니다. 수수료 합계 자체는 정확한 확정 합계입니다.
- 누적 수익률=(현금+보유 평가금액)/검증된 시작 자산−1. 현금 흐름에 모든 수수료가 반영되어야 합니다. 가격이 없거나 미래/300초 초과/0/비유한이면 평가자산·평가손익·수익률은 N/A입니다. 보유가 없으면 현금만으로 평가합니다.
- 승률=손익 양수인 매도 체결 주문 수/전체 매도 체결 주문 수. 손익0은 승리가 아니고, 매도 없음은 N/A입니다. 포지션 단위 승률이나 거래소 개별 fill 승률이 아닙니다. 거래 횟수는 양수 체결량이 있는 settled 주문 수이며 물타기·부분 체결 주문도 각각 1회, 0체결 취소는 제외합니다.
- 가격 근거는 유효 시장 데이터 처리 시 받은 Decimal 및 monotonic 시각입니다. 기존 float price_cache는 보고 평가에 쓰지 않습니다. 재시작 시 원장 횟수·수수료·손익 이력은 유지하지만 가격은 새로 확인할 때까지 N/A입니다. 복원된 이력을 새 거래로 재알림하지 않습니다.
- **일간 손익은 N/A**입니다. 누적 수치를 일간 손익으로 발송하지 않습니다. 평가가 불가능해도 원장 검증이 성공한 확정 횟수·수수료·실현 손익은 따로 표시할 수 있습니다. 전략별·오류/중단 집계는 여전히 미집계입니다.

모든 이벤트/보고서에서 로컬 생성·enqueue가 Slack 전달 성공이 아님을 표시합니다. `python -m utils.slack_preflight`는 토큰 없이 AST OFF 검사만 하고, `--outbox ...sqlite`는 read-only 구조/무결성/상태/중복/시도 검사를 추가합니다. SENDING은 재시작 시 UNKNOWN으로 취급해야 한다고 안내하지만 진단 자체는 수정하지 않습니다. 실제 Slack API 호출·스케줄 등록은 0회입니다. 과거 전송0회는 로컬 DB만으로 증명하지 않습니다. 실제 Slack/MacBook·계정 무료 조건/권한은 미검증입니다.

## P9-A: 수수료 포함 정산 (P8의 수수료 원가배분 제한을 대체)

[계산식·순서 증거·일간 경계](P9_PERFORMANCE_ACCOUNTING.md)를 따릅니다. 기존 주문/전략/복구 코드는 그대로이며 읽기 전용 계산만 개선합니다. 삽입 순서와 엄격히 증가하는 settled_at, 현금/수량/엔진평균가·profit_amount를 검증한 PAPER 이력에서 매수 수수료를 원가에 포함하고 부분 매도 원가를 배분합니다. 예제의 순실현 손익은9, 잔여 원가는50.5이며 엔진 profit_amount9.5와 다릅니다.

P8에서 수수료가 있으면 N/A였던 누적 실현/평가손익·승률을 이제 증거가 완전한 경우 계산합니다. 누락/모순/미래 시각/순서 모호성은 N/A이며 정렬·보정으로 복구하지 않습니다. UTC/KST(기본 KST) 일간 확정 손익·매수/매도 횟수·대금·수수료를 누적과 구분합니다. 일간 평가손익/총수익률은 과거 스냅샷 부재로 여전히 N/A입니다. 개별 체결 포맷터의 손익은 전체 원가 이력이 없으므로 N/A를 유지합니다.

보고서는 24줄 제한으로 회계 날짜, PAPER, 현금/평가자산, 누적 순손익/평가손익/수익률, 일간 확정 통계, 승률/수수료, 종목·전략·사유 요약을 표시합니다. 전략 ID 미기록은 N/A이며 사유로 전략을 추정하지 않습니다. 원문/비밀값은 보내지 않습니다. 보고 event ID만 회계 날짜+calendar로 구분하고 자동 실행/스케줄은 변경하지 않습니다. 실제 발송은 OFF, enqueue는 전달 성공이 아닙니다. 실제 Slack·MacBook·계정 권한/무료 조건은 미검증입니다.

P9-B 통합 검증: [PAPER_END_TO_END_VALIDATION.md](PAPER_END_TO_END_VALIDATION.md)에 실제 PAPER→원장→P9-A 정산→포맷→SQLite→Mock 전달과 MacBook 운영 전 체크리스트를 정리했습니다. SlackApiError와 outbox 기록 장애가 겹치면 UNKNOWN/SENDING으로 차단하고 재전송하지 않습니다. 운영은 반드시 단일 소비자이며 SQLite 선점 자체를 프로세스 독점 소유 증명으로 해석하지 않습니다. 실제 발송/설정/스케줄은 계속 OFF입니다.

P10 사전 준비: [MacBook 점검](P10_MAC_PREFLIGHT.md), [cold backup/복구](P10_BACKUP_RECOVERY.md). 소유권 확인 전 SENDING을 바꾸지 않으며, 전송 중 close는 요청 종료를 기다립니다. `python -m ops.local_preflight`는 MessageQueue를 열지 않고 기존 잠금/원장/metadata만 검사합니다. enqueue는 Slack 전달 성공이 아니며 실제 발송은 OFF입니다.

P16 사유 분류: 검증된 `side=bid/ask`가 주문 방향의 근거이며, `과매수/과매도` 안의
단어로 방향을 뒤집지 않습니다. `물타기`는 bid, `손절`·`이익 실현`은 ask에만 허용하고,
서로 모순된 위험관리 사유는 `기타/미기록`으로 표시합니다. 미기록·미확인 사유에서
전략 ID를 추정하거나 원문을 출력하지 않습니다. `paper_bot report`와 사유별 집계에
같은 분류를 사용하며, 현금·포지션·손익·수수료 계산과 저장된 원장은 변경하지 않습니다.
