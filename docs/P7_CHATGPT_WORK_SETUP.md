# 선택 사항: ChatGPT Work 이벤트 확인 절차

이 문서는 **설정 안내만** 제공합니다. P7에서 이벤트 작업·연결·권한 승인을 생성하지 않았으며 수동 핸드오프에는 필요하지 않습니다. P6에서 확인한 [공식 Scheduled/Automations 문서](https://learn.chatgpt.com/docs/automations)의 eligible plan 웹/모바일 GitHub PR activity 기능을 전제로, 실제 계정 가용성은 사용자가 확인해야 합니다. 동일 지원 조사를 반복하거나 기능이 있다고 계정별 지원/무료 실행을 단정하지 않습니다.

1. 사용자 ChatGPT 웹/모바일에서 Apps/연결에 공식 GitHub 연결과 대상 repository scope가 표시되는지 확인합니다. 현재 연결된 계정과 저장소 접근을 검토합니다. 없는 버튼이나 미지원 기능을 비공식 연결로 대신하지 않습니다.
2. Scheduled 및 event-triggered task 기능이 해당 Plus 계정에 실제 제공되는지 확인합니다. 이벤트 기능은 공식 문서상 CLI/desktop/IDE 관리 기능과 다릅니다.
3. GitHub PR 이벤트에 사용할 trigger/action, 허용 도구, 최신 diff/head 접근, 표준 APPROVED/CHANGES_REQUESTED 게시 지원, 실제 GitHub 작업 주체의 불변 user.id를 확인합니다. 댓글이나 Codex bot의 review 지원을 별도 ChatGPT 독립 승인으로 추정하지 않습니다.
4. Work와 Codex의 공유 사용량·추가 credits 정책, 개인 계정의 구매분/자동 충전/포함량 전용 강제를 검증합니다. usage UI 또는 machine-readable quota 존재만으로 구매 크레딧 미소비가 보장되지 않습니다. 추가 지출 가능성이나 지원 여부가 불명하면 PENDING/BLOCKED로 둡니다.
5. 실제 trigger 생성·권한 승인·Run now·모델 시험은 비용 조건과 **별도 사용자 승인 이후의 후속 작업**입니다. 이번에는 수행하지 않습니다. 지원이 확인된 뒤 공식 안내에 따라 웹/모바일 ChatGPT에 PR 이벤트와 수행할 작업을 설명해 생성을 요청하고 Scheduled에서 검토하는 방식입니다.

표준 독립 Review·비용 강제가 확인되지 않으면 WAIT_REVIEW/자동 실행 OFF를 유지하고 [P7 수동 흐름](P7_CLOUD_REVIEW_WORKFLOW.md)을 사용합니다. 비공식 API, 세션 스크래핑, 브라우저 인증 우회, 계정 공유, 토큰/Keychain 추출, API 종량제 대체, 유료 구독/서비스 추가는 금지합니다. 설정이 가능해져도 P4/P6 조건과 별도 활성화 승인 없이 무인 운영을 켜지 않습니다.
