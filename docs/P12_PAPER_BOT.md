# P12: MacBook PAPER 봇 실행

기존 AutoTrade/ExecutionEngine/paper_experiment/paper_session/paper_watch를 연결한 CLI입니다. 새 엔진·원장 없이 독립 모의 계좌를 만듭니다. 기존 HALTED 계좌·백업을 복구하거나 사용하지 않습니다. Slack/launchd/무인 시작·재시작/실거래 OFF, 추가 지출 0원입니다. Cloud에서 실제 시장 연결이나 Mac 배포는 실행하지 않았습니다.

## MacBook에서 사용할 5단계

Python 3.12 가상 환경과 의존성을 갖춘 `~/projects/auto_trade`에서 실행합니다. 아래 `paper-mvp01`은 **새 실험 이름**이며 기존 계좌 경로로 바꾸면 안 됩니다. 명령은 사용자가 직접 실행하며 PR 병합이 실행 승인은 아닙니다.

```sh
cd "$HOME/projects/auto_trade"

# 1. 한 명령으로 새 private 부모 디렉터리와 독립 계좌 준비. 초기 1,000,000 KRW, 보유 0.
.venv/bin/python -m ops.paper_bot init --directory "$HOME/paper/paper-mvp01"

# 실행 전 점검 (네트워크 없음, 원장 변경 없음)
.venv/bin/python -m ops.paper_bot check --directory "$HOME/paper/paper-mvp01"

# 2. 사용자 명시 공개 시세 연결 + 기존 전략 판단 + PAPER 체결. 최대 600초 후 종료.
.venv/bin/python -m ops.paper_bot run --directory "$HOME/paper/paper-mvp01" \
  --connect-public --seconds 600 --tickers KRW-BTC
```

`init`은 기존 경로를 재사용·덮어쓰기·chmod하지 않습니다. 새 부모 디렉터리 한 단계만 만들 수 있습니다. 기존 부모가 공개 권한이면 중단합니다. 새 경로도 Git checkout/운영 runtime/인증 디렉터리·symlink에는 만들 수 없습니다. 기존 세 HALTED 계좌/락/백업을 이동·삭제·초기화하지 마세요.

다른 SSH 창에서 같은 저장소/계좌 경로로:

```sh
cd "$HOME/projects/auto_trade"

# 3. 실행 중 종목별 틱/가상 체결 수/현금/포지션/평가손익 확인
.venv/bin/python -m ops.paper_bot status --directory "$HOME/paper/paper-mvp01"

# 4. 협력적 중지 요청 (또는 실행 창에서 Ctrl+C)
.venv/bin/python -m ops.paper_bot stop --directory "$HOME/paper/paper-mvp01"

# 5. 종료 후 확정 거래와 P9 수수료 포함 정산 재조회 (외부 API 없음)
.venv/bin/python -m ops.paper_bot report --directory "$HOME/paper/paper-mvp01" --limit 20
```

`STOP_REQUESTED_NOT_YET_STOPPED`는 **요청 수락**이며 종료 확인이 아닙니다. 실행 창의 `STOPPED` + `post_validation=VERIFIED_UNDER_OWNER_LOCK`와 최종 `STOPPED_VERIFIED`를 기다린 뒤 report의 `session_state=STOPPED_VERIFIED`, `pending_orders=0`을 확인합니다. 종료 전 report가 잠금 충돌로 BLOCKED인 것은 정상입니다. `status`는 실행 프로세스의 메모리 관측을 받으므로 원장을 다시 열지 않습니다.

600초 동안 신호가 없으면 주문 0이 정상일 수 있습니다. 억지 주문·전략 완화·신호 주입 옵션은 없습니다. 공개시장 매수/매도가 실제로 발생했는지는 실기기 보고서로만 확인합니다.

## 출력과 기록

- 실행 중 5초 간격 및 체결 직후 JSON 요약: 종목별 수신 수/마지막 관측 가격, 누적 가상 매수·매도 수, 현금, 보유 수량·평균단가, 실현·평가손익/수익률. status에는 관측 나이와 `snapshot_fresh`가 표시됩니다. 오래된 관측을 현재 시세로 간주하지 않습니다.
- 기존 `experiment-state.json`에 확정 체결/잔고/평균단가/신호가 영속됩니다. 기존 `.lock`, 생성 영수증, 최초 사용 영수증 및 `session.json` 계약을 유지합니다.
- 정상 종료 때 기존 `session.json`에 마지막 세션의 제한된 수신 요약을 추가합니다. 새 원장이나 영수증 체계가 아닙니다. 다음 세션 시작 시 이전 세션 요약이 교체될 수 있지만 누적 거래 원장은 남습니다.
- report는 잠금 아래 원장·identity·P9 회계를 다시 검증하고, 최근 거래의 시각/종목/방향/가격/수량/거래금액/수수료/순실현손익을 출력합니다. 기본 최근 20개, `--limit 1~1000`. 전체 체결 수와 표시 수를 별도 표시합니다. 주문 ID·원문 reason·비밀값은 출력하지 않습니다.
- **현재 기존 PAPER 생성기는 수수료 0원**인 확정 영수증을 만듭니다. report는 실제 기록된 수수료를 합산하며 매수 수수료 포함 이동평균 원가를 P9로 대사합니다. 수수료를 임의로 추가하지 않았으며 이 수익률이 실제 Upbit 수수료·슬리피지를 반영했다고 주장하지 않습니다.
- 보유가 없으면 종료 현금으로 순수익률을 재검증합니다. 보유가 남으면 오프라인 report의 평가손익/총수익률은 **N/A: OFFLINE_NO_FRESH_MARKS**입니다. 마지막 관측 가격은 역사적 참고값이며 현재 평가에 재사용하지 않습니다. 확정 실현손익·잔여 원가·수량은 계속 조회합니다.
- HALTED 원장도 확정 회계 대사가 가능하면 `HALTED_NO_RESTART`로 읽을 수 있지만 check/run 허가나 복구가 아닙니다. UNKNOWN/미확정 주문·불일치면 보고서도 BLOCKED입니다.

## 시작·중지·장애 경계

`run`은 기존 실험 CLI의 인자 검증과 공개 시세 제한, 분봉 검증, 틱 freshness, 보유 종목 보호, 중복 프로세스 방지, 시간 제한(1~3600초)과 종료 검증을 재사용합니다. `--connect-public` 없이는 실행하지 않습니다. 기존 `--top`/`--eligible-top`/활동성 사전 관찰 옵션도 그대로 전달할 수 있으며 의미·실험 제한은 바꾸지 않습니다. 처음에는 위 BTC 단일 종목 명령으로 확인하는 것을 권합니다.

진행 조회/중지는 private 계좌 안의 `control.sock` Unix 소켓만 사용합니다. 외부 TCP/API/계좌 인증이 없고 PID를 추측해 다른 프로세스에 신호를 보내지 않습니다. 정상 종료 때 소유한 임시 소켓만 제거합니다. 원장/락/영수증은 삭제하지 않습니다. 기존 소켓·동시 실행·권한 불명확이면 중단하며 stale 소켓을 자동 삭제하지 않습니다. Mac/Linux만 지원하며 Windows 계좌 소유권은 기존처럼 BLOCKED입니다.

Ctrl+C/SIGTERM/SSH SIGHUP은 기존 감독자에 중지를 요청합니다. 네트워크/시간 오류, 비정상 데이터, 저장 실패, 종료 확인 실패 시 기존 HALT/재시작 차단 정책을 유지합니다. 시간 제한 뒤 협력 종료 여유 15초와 프로세스 회수 시간이 추가될 수 있습니다. 강제 종료는 정상 종료로 처리하지 않습니다. 자동 재연결·재시작·계좌 리셋은 없습니다.

SSH 단절 후에는 `status`와 `report`로 상태를 확인하세요. BLOCKED/IN_PROGRESS/UNKNOWN/HALTED, 소켓 접속 실패 또는 원장 불일치면 재실행하지 말고 결과를 검토합니다. 파일 삭제나 이전 백업 덮어쓰기로 해제하지 않습니다. 정상 종료 후 백업이 필요하면 [기존 cold backup 절차](P10_BACKUP_RECOVERY.md)를 사용합니다. 실기기 sleep/네트워크/SSH 종료·APFS 권한은 Mac에서 추가 확인해야 합니다.

## 이번 검증의 완료 범위

Fake 200개 캔들을 기존 DataAnalyzer/Legacy 조건으로 분석해 BUY를 만들고, Fake ticker 3건으로 **매수 → 보유 → 기존 +5% 익절 매도 → 실제 PAPER 원장 정산 → 정상 종료 → 읽기 전용 기록 재조회**를 한 통합 시험에서 검증합니다. 초기 1백만원, 매수 40만원/잔여 현금 60만원, 최종 현금 102만원, 확정 순실현손익 2만원, 기록 수수료 0원입니다. 이 숫자는 **합성 시험 결과**이며 실시간 공개시장 성과가 아닙니다.

별도 시험에서 Unix IPC 상태/중지와 실제 자식 프로세스의 협력 종료, 중복 계좌/소켓·권한·원장 손상·HALTED 읽기 및 보호 파일 불변을 검증합니다. 연구 CLI·실제 과거 데이터 전략 비교는 실행하지 않습니다. 실제 MacBook 공개시장 체결·장시간 운영·실제 수익성은 미검증입니다.

## P13: 0건 매매의 실제 판단 근거 확인

기존 status/report에 `decision_trace`가 추가됩니다. 과거 328/685/702틱 세션은 이 통계를 기록하지 않았으므로 원인을 소급 추정하지 않으며 `N/A_NOT_RECORDED`입니다. 새 PR 코드로 실행한 이후의 세션만 관측합니다. 전략/지표/위험관리/체결/회계는 바꾸지 않았습니다.

|필드|실제 집계 지점/분모|
|---|---|
|`counts.stream_messages_received`|운영 stream.get이 반환한 메시지. 미처리 중지/검증 실패/스냅샷·제외 후보 메시지를 포함할 수 있음. 사전 관찰은 제외|
|`counts.timing_accepted`|기존 TickTiming 통과, 폐기 메시지는 제외. 가격 검사 실패는 별도 data_failures|
|`counts.analyzer_calls` / `analyzer_returns`|AutoTrade가 실제 분석기를 호출/판단을 반환받은 횟수|
|`counts.strategy_attempts` / `strategy_evaluations`|거래량·쿨다운을 통과해 기존 LegacyCompositeStrategy 평가를 시도/완료한 횟수|
|`strategy_BUY/SELL/HOLD`|그 **실제 지표 전략 평가 결과**만 집계. 평가하지 않은 BUY를 가정해서 세지 않음|
|`decision_BUY/SELL/HOLD`|분석기가 실제 반환한 최종 판단. 거래량·쿨다운의 조기 HOLD도 포함|
|`strategy_not_evaluated`|거래량, 쿨다운, 위험관리 우선, 분석기 데이터 오류, 시장 데이터 검증 실패 사유별 실제 이벤트|
|`buy/sell_attempts`|AutoTrade.buy_coin/sell_coin 진입. 사전 주문 한도에 막힌 시도도 포함|
|`buy/sell_engine_attempts`|실제 ExecutionEngine.buy/sell 호출 직전|
|`buy/sell_fills`|기존 엔진이 확정 체결 성공을 반환한 횟수. 현재 세션 통계이며 누적 원장 체결 수와 다름|

거래량이 먼저 차단하면 쿨다운이 동시에 참이어도 최종 사유는 VOLUME_GATE 하나입니다. `gate_conditions_overlapping`은 관측 시점의 두 조건과 BOTH를 별도 집계합니다. BUY 조건이 맞는 캔들이더라도 거래량/쿨다운에서 반환됐다면 실제 지표 전략은 평가하지 않았으므로 `strategy_BUY`를 만들어 넣지 않습니다. `decision_HOLD`와 gate 사유로 확인합니다. 순수 지표 전략이 HOLD를 반환하면 `NO_ACTIONABLE_SIGNAL`이며, 어느 개별 지표를 바꾸면 매수했을 것이라는 반사실은 계산하지 않습니다.

매수 시도에서 `candidate_constraints_overlapping`은 최대 보유 종목·현금이 최소금액 미만·요청금액이 최소금액 미만·요청금액이 현금 초과 조건을 중복 집계합니다. `attempts_with_multiple_constraints`와 쌍별 집계를 함께 제공합니다. `final_blocks`는 **실제로 반환/예외가 발생한 분기**이며 중복 후보 조건을 더한 값이 아닙니다. 정상 매수는 현금에 맞춰 금액을 축소하는 기존 정책이므로 “원래 40% 예산보다 현금이 적다”만으로 현금 부족이라 부르지 않습니다. 물타기 금액이 현금을 초과한 경우 후보 조건은 기록하지만 엔진의 False 반환 사유는 ENGINE_REJECTED로 표시합니다. 매도에는 기존 코드에 없는 최소 주문금액 차단을 만들지 않습니다.

BUY_ALREADY_HELD/SELL_WITHOUT_POSITION은 주문 메서드 이전 차단이라 주문 시도는 0입니다. 익절·손절·물타기는 분석기보다 먼저 실행되므로 risk_priority와 평가 미수행에 기록됩니다. 한 위험관리 틱에서 물타기 시도 실패 후 손절 매도가 이어질 수 있어 주문 시도 수가 틱 수와 같지는 않습니다. 검증 실패는 타이밍 전/후에도 발생할 수 있고 오류 이벤트가 겹칠 수 있으므로 수신/미평가/거절 수를 임의로 합쳐 틱 수라고 해석하지 않습니다. P13까지는 같은 캔들의 반복 평가도 포함했습니다. P14의 공개 PAPER 경로는 아래처럼 같은 완료 봉의 중복 평가를 생략합니다.

`last_strategy_result`는 마지막 실제 지표 평가, `last_decision`은 마지막 분석기 반환 판단, `last_order`는 마지막 주문 경로의 단계/결과입니다. UTC 시각/epoch, 종목과 고정 사유 코드만 남깁니다. 미발생 항목은 null이며 가상의 마지막 판단을 만들지 않습니다. 실행 중 `price_age_seconds`, 기존 `tick_timing`, `timestamp_stats.issues`도 확인할 수 있습니다. `latest_guard_timestamp_ms`는 guard가 기억한 시각으로, 모든 경우 전략 전달까지 보장하는 값이 아닙니다. snapshot_age/snapshot_fresh는 프로세스 관측의 나이이며 시장 가격의 나이와 별개입니다.

통계는 메모리의 고정 카운터/종목 최대 30개/마지막 이벤트만 보관합니다. 카운터 상한 2,147,483,647, trace 상한 65,536 bytes입니다. `saturated=true`는 정확한 횟수 아님, `availability=UNVERIFIED`는 계측 불완전임을 뜻합니다. 원문 reason/틱/주문 ID/토큰/무제한 로그는 저장하지 않습니다. 매 틱 새 원장 저장은 없고 기존 5초/체결 관측 주기를 재사용합니다.

정상 종료는 기존 session.json의 한 번의 종료 저장에 통계를 넣고 원장 SHA/세션 identity와 연결합니다. `decision_trace_scope=STOPPED_VERIFIED_LAST_SESSION_ONLY`는 마지막 정상 세션 범위입니다. HALTED 종료는 기존 원장 잠금 아래 상태와 일치하는 경우에만 session.json에 **종료 확정이 아닌 관측 checkpoint**를 한 번 남깁니다. report는 SHA/세션 ID가 맞을 때 `HALTED_WORKER_OBSERVATION_ONLY_NOT_SUCCESS_OR_RESTART_AUTHORITY`로 구분합니다. 원장 HALT와 IN_PROGRESS는 그대로입니다. 강제 종료·저장/종료 확인 실패·checkpoint 불일치에서 통계가 없으면 N/A/BLOCKED로 남습니다. 과거 상태/백업이나 원본 원장을 통계 보강을 위해 수정하지 않습니다. 이 요약과 SHA는 독립 롤백 방지 증거가 아닙니다.

Mac에서 별도 사용자 실행을 결정한 뒤 새 독립 계좌 예시:

```sh
cd "$HOME/projects/auto_trade"
.venv/bin/python -m ops.paper_bot init --directory "$HOME/paper/paper-p13-01"
.venv/bin/python -m ops.paper_bot check --directory "$HOME/paper/paper-p13-01"
.venv/bin/python -m ops.paper_bot run --directory "$HOME/paper/paper-p13-01" --connect-public --seconds 600 --tickers KRW-BTC
# 다른 SSH 창에서
.venv/bin/python -m ops.paper_bot status --directory "$HOME/paper/paper-p13-01"
.venv/bin/python -m ops.paper_bot stop --directory "$HOME/paper/paper-p13-01"
# 실행 창의 종료 결과를 확인한 후
.venv/bin/python -m ops.paper_bot report --directory "$HOME/paper/paper-p13-01" --limit 20
```

기존 HALTED 계좌는 지정/재시작하지 마세요. 이 PR의 Fake 시험은 실제 702틱 원인을 확인하지 않았고 Mac 공개시장 시험도 실행하지 않았습니다. 기존 -4ms 역행 HALT 정책은 유지합니다. 연구/백테스트 CLI나 새로운 전략은 추가하지 않습니다.


## P14: 완료 1분봉 기준 평가 수정

확인된 기존 코드 결함은 `PublicMarketClient.candles()`의 **to 없는 최신 봉 조회**, 5분간 분석 자료 재사용, 매 틱 같은 자료의 분석기 호출입니다. 따라서 진행 중 봉의 낮은 누적 거래량을 기준으로 같은 VOLUME_GATE를 반복할 수 있었습니다. Mac이 보고한 1,085건은 실제 차단 증거이나 당시 원본 봉 시각/거래량이 없어 **모든 차단이 미완성 봉 때문이었다고 확정하지 않습니다**. 완료 봉 자체가 실제로 평균 대비 작았을 가능성도 남습니다.

- 공개 조회는 UTC 현재 분 시작을 명시적 `to`로 보냅니다. 예: 12:34:42 UTC → `to=12:34:00+00:00` exclusive → 가장 최신 허용 봉의 open은 12:33:00 UTC입니다. UTC 필드 계약대로 읽고 기존 KST-naive DataFrame 계약으로 변환하며 로컬 시간대를 추측하지 않습니다.
- 정확히 200개 연속 완료 봉과 직전 분 봉까지 검증합니다. 진행 중 봉/잘못된 시간대/정렬·중복/누락은 차단하고 보간·대체하지 않습니다. 최신 분에 체결이 없어 봉이 없을 수도 있으나 API 장애라고 단정하지 않고 기존 PUBLIC_CANDLE_GAP 정책을 적용합니다.
- 거래량은 완료 봉의 값, 평균은 **해당 봉을 포함한 완료 20봉 평균**입니다. 기존 `< 평균 × 0.5` 기준을 그대로 사용합니다. 지표 조건·쿨다운·익절·손절·물타기는 바꾸지 않습니다.
- 공개 PAPER 분석기는 실행 프로세스 안에서 완료 봉당 한 번 분석기를 호출합니다. 거래량/쿨다운 HOLD도 해당 봉을 소비합니다. 다음 UTC 분의 유효 틱에서 새 완료 자료가 필요할 때만 해당 종목을 갱신합니다. 과거 봉을 순회해 추격 주문하지 않습니다. 세션을 넘어선 새 원장 키/봉 영수증은 만들지 않았으며 재시작 시에는 기존 신호 쿨다운·포지션·복구 계약을 유지합니다.
- 기존 위험관리는 **모든 유효 틱에서 봉 확인/REST보다 먼저** 실행합니다. 같은 봉이라도 익절/물타기/손절은 즉시 확인합니다. 동기 REST 조회 자체의 기존 최대 5초 지연 가능성까지 제거했다고 주장하지 않습니다.
- 기존 64회 예산/요청 속도/타임아웃/세션 제한은 유지합니다. 10종목을 매분 갱신하는 긴 실행은 예산을 소진할 수 있으며, 상한을 늘리거나 오래된 봉으로 계속하지 않고 HALTED로 남습니다. 우선 BTC 한 종목 600초로 확인하세요. 주입된 구형 분석기의 기존 갱신 계약은 별도로 유지하며 공개 분석기에는 5분 중복 갱신을 적용하지 않습니다.

기존 status 출력과 정상 종료 report의 `candle_evaluation[종목]`에서 `last_completed_open_utc`(마지막 로드한 완료 봉), `last_evaluated_open_utc`(분석기를 호출하도록 소비한 봉), `volume`, `mean_volume_20_completed`, `threshold_fraction=0.5`, `mean_includes_evaluated_candle=true`, `skipped_same_candle`, `refreshes`를 확인합니다. `refreshes`는 캐시를 포함한 분석기 자료 로드 수이며 HTTP 호출 수는 기존 `public_rest_requests`입니다. 같은 봉 생략은 HOLD나 COOLDOWN으로 꾸미지 않고 별도 집계합니다. 기존 P13 trace schema/과거 보고서는 유지합니다. 위험관리로 분석기를 우회한 봉이나 검증 실패는 실제 전략 성공 평가를 뜻하지 않습니다. 없는 거래량 근거는 null입니다. HALTED 보고서에서 이 추가 봉 요약의 영속성을 보장하지 않으며 기존 부분 관측 범위를 유지합니다.

[업비트 공식 분봉 문서](https://docs.upbit.com/kr/reference/list-candles-minutes)의 `to` exclusive/UTC 계약을 사용합니다. 이번 Cloud의 공식 페이지 재조회는 프록시 오류로 미확인이며 우회하지 않았습니다. 실제 시세 API는 호출하지 않았습니다. Fake 응답에서 to 전송/반환 범위/완료 봉을 검증했고, 실제 Mac 재검증은 별도입니다.

검토·병합 후, 기존 계좌/백업은 그대로 두고 새 독립 계좌 예시로 확인합니다.

```sh
cd "$HOME/projects/auto_trade"
.venv/bin/python -m ops.paper_bot init --directory "$HOME/paper/paper-p14-01"
.venv/bin/python -m ops.paper_bot check --directory "$HOME/paper/paper-p14-01"
.venv/bin/python -m ops.paper_bot run --directory "$HOME/paper/paper-p14-01" --connect-public --seconds 600 --tickers KRW-BTC
# 다른 SSH 창에서
.venv/bin/python -m ops.paper_bot status --directory "$HOME/paper/paper-p14-01"
# 실행 창의 STOPPED_VERIFIED 확인 후
.venv/bin/python -m ops.paper_bot report --directory "$HOME/paper/paper-p14-01" --limit 20
```

실기기 완료 기준은 완료 봉 시각·거래량/평균의 일치, 동일 봉 생략 수, 새 봉에서 실제 평가 증가, 원장/P9 정산 및 종료 검증입니다. 정상 조건 Fake 시험은 strategy_evaluations > 0을 확인했지만 BUY를 보장하지 않습니다. 실제 완료 봉이 계속 50% 미만이면 적은 횟수의 VOLUME_GATE가 정상일 수 있습니다. 기존 HALTED 계좌를 재시작/초기화하지 않습니다. 실거래/Slack/launchd/유료 호출/자동 병합은 OFF입니다.

### P14 추가: 기준 master RED → 동일 fixture GREEN

앞선 수정이 이미 커밋된 뒤 추가 지시를 받았으므로, 최초 수정 전에 이 재현을 했다고 소급 주장하지 않습니다. 추가 작업에서는 애플리케이션을 더 바꾸기 전에 기준 master `69e790ed314090c2dc4c81df6251d4ca7f2d83cf`의 실패를 먼저 실행했습니다. 비교 대상 5개 모듈은 Git 원본 SHA-256과 일치해야 하며, 기준 소스를 임시 **비-checkout** 디렉터리에 재구성합니다. 현재 checkout을 분기 전환/reset하거나 원장/연구 자료를 복사하지 않습니다. 필요한 추적된 Python 소스만 임시 경로에 복사합니다. 테스트는 기준 Git 객체가 없는 shallow CI에서도 고정 reverse patch와 해시로 같은 기준 코드를 검증하며, 재구성 실패를 RED로 취급하지 않습니다.

Fixture `tests/fixtures/p14_upbit_minutes.json`은 **실제 Mac/API 캡처가 아닌 합성 모델**입니다. 공식 응답 필드인 UTC/KST 봉 open, OHLC, 마지막 거래 시각(timestamp), 누적 거래량·거래대금, unit을 사용합니다. 분당 240개의 양수 체결이 0.25초 간격으로 누적돼 첫 1초에는 volume=1.04, 완료 시에는 volume=100이 됩니다. 시간·거래량은 같은 거래 일정에서 계산하고, 없는 봉을 만들어 채우거나 전략 판단을 바꾸지 않습니다.

|동일 fixture / 실제 운영 코드|기준 master RED|수정본 GREEN|
|---|---|---|
|1틱, 진행 중/직전 완료 봉 분리|현재 봉 volume 1.04, 평균 95.052, 기준 47.526 → gate 1 / 전략 평가 0|직전 완료 봉 volume 100, 평균 100 → gate 0 / 전략 평가 1 (HOLD)|
|1,085틱, 약 10분|분석기 1,085 / VOLUME_GATE 1,085 / 전략 평가 0 / REST 2|분석기·전략 평가 11 / VOLUME_GATE 0 / REST 11|
|완료 봉 조회만 통제한 캐시 분리 실험, 17틱|gate 16 / 평가 1. 새 완료 봉 volume=100인데 기존 volume=1로 5분 갱신 전 차단 12회|진짜 첫 저거래량 gate 1 / 새 봉 평가 5 / 잘못된 지연 gate 0|
|실제 완료 봉도 volume=1인 대조군|차단|평균 95.05 대비 50% 미만 → 계속 차단|
|REST→기존 분석·전략→PAPER 왕복|매수/매도 0/0|매수/매도 1/1, 현금 1,020,000, 실현 20,000, 기존 0수수료, 미확정 0|

캐시 분리 실험은 **테스트 HTTP 경계에서만**, 빠진 `to`를 완료 분 경계로 추가하는 대조 조작입니다. 기준 코드가 원래 완료 봉을 요청했다거나 실제 API의 기본값이 그렇다고 주장하지 않습니다. 이 조작으로 미완성 봉 문제를 제거한 뒤에도 기준 코드의 오래된 캐시 차단이 남음을 분리합니다. 12회는 elapsed 60~300초 미만 구간으로 명시해 집계합니다.

같은 성공 조건을 검사하는 replay는 기준 코드에서 exit 1(예상한 계약 실패 6개), 수정본에서 exit 0입니다. 예외/준비 실패/다른 원인으로 종료된 실행은 RED 증거로 인정하지 않습니다. 분석기 wrapper는 실제 반환 후 입력 시각·volume·평균만 관측하고 결과나 신호를 대체하지 않습니다. 운영 회귀는 다음 한 명령으로 재현합니다.

```sh
.venv/bin/python -m unittest -v tests.test_p14_red_green
```

새 fixture 검증으로 **기존 수정안에도** 로컬 시계 +4초가 분 경계를 넘으면 아직 거래소에서 완료되지 않은 봉을 허용하는 반례가 확인됐습니다. 해당 시험의 `PublicDataError not raised` 실패를 먼저 확인한 뒤, candle GET의 explicit `to`가 HTTP 서버 Date보다 미래이면 기존 `PUBLIC_RESPONSE_CLOCK_UNVERIFIED`로 차단하도록 5줄을 보완했습니다. 허용 오차 5초를 늘리지 않았고, 이 응답 시각 검사는 독립적인 거래소 데이터 attestation이라고 주장하지 않습니다.

Mac 판정 기준:

- **성공:** 새 세션에서 사용 봉 시각이 현재 진행 분보다 이전이고, 정상 완료 거래량 조건의 새 봉에서 strategy_evaluations가 증가합니다. 같은 봉 틱은 skipped_same_candle로 쌓이고 5분을 기다리지 않습니다. 종료 후 정산·보고와 STOPPED_VERIFIED도 확인합니다. BUY 없이 실제 전략 HOLD여도 성공입니다.
- **정상 필터:** 완료 봉 자체의 volume < mean×0.5 또는 기존 쿨다운이면 평가 0일 수 있습니다. 완료 봉 시각과 수치를 확인하며 무조건 실패·API 장애로 단정하지 않습니다.
- **실패/미검증:** 미완성 봉을 사용하거나, 정상 거래량·쿨다운 없음인데 새 봉에서 계속 평가되지 않거나, 같은 봉을 계속 평가하거나, 예산/시각/누락으로 HALTED면 정상 운영 성공으로 표시하지 않습니다. HALT는 해제하지 않습니다.

이 증거는 조건부 결함과 수정 경로를 입증합니다. 실제 Mac 1,085틱의 원본 봉/체결 일정이 없으므로 당시 원인을 확정하거나 실기기 성공으로 표시하지 않습니다.
