# P11-C 실제 1분봉 확보 상태

**INSUFFICIENT_REAL_DATA — 실제 1분봉은 확보하지 못했다.** 운영 1분봉 전략의 실제 신호·체결·순손익을 보고하지 않는다. 합성 회귀 결과를 실제 수익성 자료로 섞지 않았다.

Cloud checkout의 등록 연구 자료는 BTC/ETH/XRP/SOL의 15·60분 snapshot 8개다. 1분봉 원본은 찾지 못했으며 Mac이나 개인 파일 전체를 조사했다고 주장하지 않는다.

BTC `[2026-10-26T00:00Z, 2026-10-26T06:00Z)` 공개 REST 수집을 1회 시도했으나 `PUBLIC_REQUEST_FAILED`로 중단했다. 검증된 HTTP 응답과 CSV는 없다. 요청 1회는 시도 횟수이며 Upbit 서버가 응답했다는 증거가 아니다. 연결 실패의 세부 원인은 UNKNOWN이고 프록시 우회나 재시도는 하지 않았다. 실제 확보 기간은 없다.

| 전략 | 실제 신호·가상 체결·성과 |
|---|---|
| Legacy Composite | N/A — INSUFFICIENT_REAL_DATA |
| RSI | N/A — INSUFFICIENT_REAL_DATA |
| MACD | N/A — INSUFFICIENT_REAL_DATA |
| Bollinger | N/A — INSUFFICIENT_REAL_DATA |
| Consensus 2/3 | N/A — INSUFFICIENT_REAL_DATA |
| Consensus 3/3 | N/A — INSUFFICIENT_REAL_DATA |

[readiness.json](readiness.json)과 [metrics.csv](metrics.csv)에 실제 확보 false, 확보 기간 null, 실패 근거와 전략별 N/A를 기록했다. 이는 수익률 0%나 거래 0건을 관측했다는 뜻이 아니다. [수집·분석 도구와 Mac 명령](../../docs/P11C_MINUTE_DATA.md)을 사용해 나중에 유효 표본을 확보해야 한다.

CLI는 기본 network OFF이며 공개 REST 연결에 명시 옵션을 요구한다. 최대 4종목·24시간·40요청·120초 deadline·요청 5초 상한, 인증 차단, UTC·누락·중복·정렬·가격·거래량·SHA·출처 audit 검증을 제공한다. 봉을 보간하거나 종목을 대체하지 않는다.

검증을 통과한 표본만 기존 P11-A/B의 여섯 전략, 300초 cooldown, 거래량 필터, 익절·손절·물타기, PAPER 원가·수수료 정산으로 분석한다. JSON·CSV·Markdown을 생성하며, 자료나 지표 부족은 실패하고 정상 평가에서 거래가 없으면 성과를 N/A로 표시한다.

Fake 테스트로 수집 pagination, 예산·timeout·429·시계, 미래·holdout 차단, 중복·누락·수치, SHA·audit, 기존 출력 보존을 검증했다. 합성 1분봉에서는 신호·쿨다운·가상 매수·매도·비용·보고서 연결도 검증했다. 이 합성 결과를 실제 1분봉 성과로 게시하지 않았다. 실제 Mac 수집과 과거 Mac의 0건 원인은 여전히 미확인이다.

계획은 기존 개발·holdout 기간과 겹치지 않지만 신규 수집을 독립 검증으로 부르지 않는다. 다른 신규 수집과의 중첩은 UNKNOWN이다. 기존 HALTED 계좌·락·백업·원본 연구·운영 전략은 변경하지 않았다. 실거래·Slack·launchd·자동 운영·유료 호출·직접/자동 병합은 OFF이며 추가 지출 0원 정책을 유지한다. 정확한 테스트·CI·head는 PR에 기록한다.
