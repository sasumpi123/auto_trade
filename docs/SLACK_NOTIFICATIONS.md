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

429는 Retry-After 2~3600초 범위로 최대 총 2회 시도합니다. timeout/예외/불완전 응답은 UNKNOWN으로 종료하며 무한 재시도하지 않습니다. 전송 직전 SENDING을 commit하므로 프로세스 종료 후 SENDING은 UNKNOWN으로 바뀌고 재전송하지 않습니다. 대기 QUEUED는 보존되지만 별도 수동 소비자 없이는 발송되지 않습니다. 다른 프로세스의 outbox 동시 사용·백업 롤백·기기 간 복사 방지는 보장하지 않습니다. 단일 소유 소비자만 사용하고 외부 sidecar/운영 복구 검증 전 무인 실행은 금지합니다.

Slack은 원장을 대체하지 않습니다. 전송 실패는 주문 제출을 호출하지 않습니다. 기존 사용자 정의 notification 객체가 예외를 내면 기존 거래 fail-closed 동작은 유지되며, 이미 확정된 주문을 다시 제출하지 않습니다. SQLite 손상/권한/초기화 오류는 차단하고 원장이나 outbox를 임의 초기화하지 않습니다.
