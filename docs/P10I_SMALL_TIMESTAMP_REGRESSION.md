# P10-I: 소폭 ticker timestamp 역행의 실험적 폐기

기본 정책은 여전히 작은 역행도 `TIMESTAMP_REGRESSION / PAPER_STALE_TICK`으로 HALT다. 변경은 **새 격리 PAPER 계좌의 최초 실제 실행**에서 사용자가 별도 옵션을 지정한 경우에만 동작한다. 기존 세 HALTED 계좌를 복구·재시작·삭제·초기화하는 기능이 아니다. Cloud에서는 Fake만 실행했고 실제 Mac/공개 시장/사용자 원장에는 접근하지 않았다.

사용자 Mac 증거는 KRW-USDT previous=1793001534036, received=1793001534034, 차이 -2ms, 로컬 수신 대비85.085ms 지연이다. 해당 역행은 기존 코드에서 HALT하는 이유를 설명한다. 이것만으로 정상적인 재정렬이라는 원인이나 5ms까지의 안전성, 과거 다른 PAPER_STALE_TICK 원인을 증명하지 않는다.

## 공식 근거의 확인 범위

확인 대상은 Upbit 공식 [한국 ticker 문서](https://docs.upbit.com/kr/reference/websocket-ticker)와 [Global ticker 문서](https://global-docs.upbit.com/reference/websocket-ticker)다. 두 정적 문서 조회가 이번 Cloud에서도 ProxyError로 실패했다. 따라서 timestamp/trade_timestamp 규격과 순서 보장 여부는 **PENDING_EXTERNAL_DOC_VERIFICATION**이다. 공식적으로 역행이 정상/허용된다고 주장하지 않는다. 기존 ticker timestamp를 변경하거나 trade_timestamp로 대체하지 않는다. wire 요청/stream_type 분류는 [P10-H](P10H_REALTIME_ACTIVITY.md)를 유지한다. 비공식 API나 직접 네트워크 우회는 사용하지 않았다.

이 구현은 입증된 프로토콜 허용오차가 아니라 사용자가 요청한 제한적 실험이다. 기본 엄격 HALT, 독립 진단, 시작 전 관찰은 계속 엄격하다. 운영의 선택된 전략 종목 REALTIME 메시지에만 적용한다.

## 정확한 고정 제한

`--discard-small-timestamp-regressions`는 다음 조건을 모두 요구한다.

- `ops.paper_experiment --connect-public` 및 `--observe-realtime-seconds`를 명시한다. 기존 paper_watch/독립 진단에는 이 옵션이 없다. 기본 offline/read-only/생성과 함께 사용하면 오류다.
- 검증된 신규 격리 실험 identity, 기존 소유권 락·회계 대사·session CAS를 통과한다. `last_session_id is None`인 최초 실행만 허용한다. 정상 종료 후에도 이 계좌에서 옵션 재활성화는 금지한다. 준비 실패는 아직 runtime 폐기 경로를 실행하지 않았으므로 기존 준비 실패 정책을 유지한다.
- 세션 전체(준비 포함)는 1~3600초다. 역행 폭은 **1~5ms**, 종목별 **1회**, 세션 전체 **3회**다. 1시간 내 재충전/rolling window 해제는 없다. 최대1시간 최초 세션에 이 더 엄격한 누적 cap을 적용한다. 프로세스 재시작으로 같은 계좌의 한도를 새로 부여하지 않는다. 별도 독립 계좌 전체를 합산하는 사용자 단위 시간당 원장은 구현하지 않았으며 새 계좌를 연속 계좌/한도 초기화 수단으로 취급하지 않는다.
- 폐기 후 **그 종목에서 이전에 승인한 timestamp보다 큰** 정상 메시지를 받을 때까지 어느 종목에서도 두 번째 역행 폐기를 허용하지 않는다. 다른 종목의 정상 수신이나 같은 timestamp는 이 불확실성을 해소하지 않는다. 정상 후속을 받아도 종목별/전체 예산은 돌아오지 않는다.
- 정상 후속 수신 전에 세션을 종료하면 HALTED로 보고하며 정상 종료 성공으로 표시하지 않는다. HALTED 원장은 기존 읽기 전용 대사만 가능하며 재시작 권한이 생기지 않는다.

5ms/1회/3회는 단일2ms 관측으로 도출한 통계적 안전 구간이 아니라 고정된 실험 경계다. 더 큰 폭·반복/연속·예산 초과는 기존 HALT다. 옵션으로 숫자를 늘리는 인터페이스는 없다. 예산은 실행 메모리에서만 집계하며 같은 계좌 재활성화 금지는 기존 영속 last_session_id와 session/first-use 증거를 재사용한다. 새 원장을 추가하지 않았다. 파일 소실/공동 롤백/수동 조작에 대한 독립 witness나 암호학적 롤백 방지는 추가하지 않았고 기존 UNKNOWN/복구 금지를 유지한다.

## 가격과 watchdog 계약

timestamp 타입/누락, 로컬 wall/monotonic 불일치, 60초 초과 지연, 5초 초과 미래는 폐기 후보 판정보다 먼저 기존 검사를 통과해야 한다. 허용 폭 이내라도 이미 만료된 전체15초/종목60초 watchdog은 HALT한다. 폐기 메시지의 가격을 파싱하거나 전략/체결/price cache/평가 mark/종목 last_price에 전달하지 않는다. `previous`, 종목 progress, 전체 last_progress 및 accepted/session tick 수를 갱신하지 않는다. 받은 건수와 별도 폐기 수만 늘린다.

오래된 가격을 보간/수정하지 않고, 마지막 가격으로 매도 판단하지 않는다. 이후 입력은 정상 시각 검사를 다시 통과해야 한다. 가격/수수료/원가배분/RSI·MACD·볼린저 조건/기존 보유 종목 위험관리 코드는 바꾸지 않았다. 새로운 계좌에서 생성된 보유 포지션도 동일한 liveness 보호를 받는다. 다른 종목 수신이 계속돼도 보유 종목60초 무진행은 HALTED다.

## 유한한 보고

기존 tick_timing 요약에 `regression_policy`, `regression_discard`를 추가한다. 전체 폐기 수, 종목별 count/max/last 역행ms, `followup_newer_received`(폐기 없음이면 null), 기다리는 종목, 고정 상한을 보고한다. 샘플은 기존 최대16개, 종목 최대100개(격리 실험30개), 포화 카운터/수치 제한을 재사용한다. 샘플에는 수신/이전 timestamp와 시각 차이 등 고정된 숫자만 들어가며 가격/raw message/주문ID/close reason/토큰/환경변수는 출력하지 않는다. 로그 파일은 자동 생성하지 않는다. `SMALL_REGRESSION_DISCARDED`는 승인된 시세가 아니며 `timing_accepted_total`에 포함하지 않는다.

## 추후 Mac 검증 명령

아래는 독립 코드 리뷰와 별도 사용자 실행 결정 후에만 적용하는 예시다. 현재 세 HALTED 경로에는 절대 실행하지 않는다. 계좌 생성·private 권한·보관·cold backup은 [P10-F](P10F_ISOLATED_PAPER_EXPERIMENT.md), 종료/활동성/구독 경계는 [P10-H](P10H_REALTIME_ACTIVITY.md)를 재사용한다. 이 PR에서 계좌를 만들거나 실제 실행하지 않았다.

```sh
cd "$HOME/projects/auto_trade"
experiment_dir="$HOME/Library/Application Support/auto_trade/paper-experiments/USER_SELECTED_FRESH_ACCOUNT"
# 기본 읽기 전용; 비승인 보고 exit2 가능
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir"

# 추후 별도 결정: 이미 명시적으로 생성한 새 계좌의 최초 실행에만 적용
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 180 --eligible-top 7 --candidate-limit 15 \
  --observe-realtime-seconds 60 --select-realtime-active \
  --discard-small-timestamp-regressions
```

옵션을 생략하면 기존 엄격 HALT다. 공개 연결은 자동 재시도하지 않는다. 종료 후 자식/감독자 원인, 대사, 미확정 주문, 폐기/후속 시세 요약을 확인한다. HALTED면 파일 편집/다른 백업 덮어쓰기/재실행하지 않고 비공개 증거와 cold backup을 보존한다. 정상 종료한 계좌도 이 옵션을 다시 켜지 못하며 기존 원장 재개는 매번 별도 명시적 판단과 엄격 정책을 따른다.

## 검증 및 남은 위험

Fake로 -1/-2/-5ms 폐기와 기본HALT, -6ms HALT, 연속/반복/종목별/전체 한도, 늦음/미래/시계/무수신, 다른 종목 수신 중 보유 종목 무진행, 정상 후속·동일timestamp 구분, poisoned 문자열/이익실현을 유발할 가격이 전략·체결·평가에 도달하지 않음, 가상 매수/매도·영속 원장 대사, 미해소 종료HALT, 동일 계좌 재활성화 차단을 검증한다. 세 HALTED Fake 계좌와 백업/락/증거 불변도 확인한다. 전체 OS별 테스트 수/skip, exact head/run/attempt는 PR에 기록한다.

실제 Mac 실행, 시장의 역행 분포/공식 순서 보장, 5ms cap의 통계적 타당성, 장시간/절전/SSH/파일시스템 내구성은 미검증이다. 실거래/Slack/launchd/자동 시작·재시작/유료 모델·API·인프라/자동 게시·병합은 계속 OFF다. 실험 성공을 운영 승인이나 수익성 증거로 해석하지 않는다.
