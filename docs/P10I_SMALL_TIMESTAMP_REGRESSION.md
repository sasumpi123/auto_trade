# P10-I: 소폭 ticker timestamp 역행의 실험적 폐기

아래 P10-I 계약의 시작 전 엄격 관측은 기본값으로 유지한다. 별도 P15 옵션의 제한적 예외와 첫 시도 예약은 이 문서 하단에 명시한다.

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

## P15: 고정 명시 종목의 시작 전 관측까지 제한적 확장

P10-I의 기본 관측 엄격 검사는 의도된 정책이었다. P15는 그 버그 수정이나
P14 거래량/완료 봉 처리 변경이 아니라 **새 명시 옵션에 한한 정책 적용 범위 확장**이다.
기존 옵션만 지정한 관측, 기본 관측, 독립 공개 진단은 계속 엄격하다.

새 `--discard-small-timestamp-regressions-during-observation`은 다음을 모두 요구한다.

- 신규 독립 계좌의 **첫 시도**, `--connect-public`, 명시적 `--tickers`.
- `--observe-realtime-seconds 1..60`과 기존 `--discard-small-timestamp-regressions`.
- 준비를 포함한 `--seconds 1..3600`, 관측 종료 후 최소 5초의 시작 여유.
- `--top`, `--eligible-top`, 후보 교체와 함께 사용할 수 없다. 기존 계좌·HALTED 계좌에는 적용하지 않는다.

관측과 실행은 **동일한 TickTiming 인스턴스**를 공유한다. 시각 기준은 준비 시작이며,
최대 5ms·종목별 1회·전체 3회·최대 3600초가 합산 한도다. 후속 정상 틱도 예산을
충전하지 않는다. 관측의 BTC 1회 사용 후 실행에서 BTC가 다시 역행하면 HALT다.
큰/연속/반복 역행, 지연·미래·벽시계 불연속, 기존 watchdog 한도는 유지한다.
관측의 무수신 후보 측정을 위한 기존 liveness 계약과 실행의 15초/종목60초 보호를
완화하지 않는다. 폐기 직전 이미 만료된 watchdog도 기존처럼 실패한다.

폐기 메시지는 수신/폐기 통계에만 들어간다. 가격 파싱, REALTIME 인정 건수,
progress/last_progress, 전략·체결·평가에는 전달하지 않는다. 같은 종목에서 이전
승인 timestamp보다 **큰** 정상 틱을 관측 창 안에서 받아야 시작한다. 동일 timestamp,
다른 종목, 관측 창이 끝난 뒤 받은 틱은 해결 증거가 아니다. 미해소 종료/중단은
PREPARATION_FAILED이며 원장 무변경이다. 관측 성공은 이후 시세 안정성의 보장이 아니다.

### 첫 시도와 실패 증거

기존 원장 `.lock`, 기존 first-use 영수증, `session.json`만 사용한다.
새 옵션은 `begin`에서 first-use에 세션 토큰을 독점 예약하고 fsync한다. 이전 first-use가
있으면 거절하므로, 이전 엄격 실행의 준비 실패 계좌에도 새 옵션을 추가할 수 없다.
자식은 기존 원장 소유권 아래 session에 관측 시작을 원자 저장한 **후** 분봉/연결을
시작한다. 같은 세션 토큰 재실행도 거절한다. 준비 단계에서 원장 첫 저장은 하지 않는다.

예약 이후 데이터 준비 실패·중지·결과 유실·프로세스 사망·저장 실패라도 예약을
환급하지 않는다. 같은 계좌의 새 관측 옵션 및 기존 실행 폐기 옵션에 새 예산을 주지
않는다. 성공/실패와 무관하게 이 실험은 한 번뿐이다. 기존 엄격 실행/읽기 전용 복구
판정은 기존 계약을 따르며, 보고서는 실행 허가가 아니다. 파일 삭제·초기화·복구로
이 제한을 우회하지 않는다. 파일 소실은 UNKNOWN/BLOCKED; 원장과 영수증·session을
함께 과거로 되돌리는 공동 롤백을 탐지할 독립 witness는 여전히 없다.

`status`와 `report`의 `regression_evidence`는 다음을 구분한다.

- `observation`: 관측 종료/실패 때 고정한 enabled, received/accepted/discarded,
  종목별 폐기 폭/횟수와 `followup_newer_received`, 미해소 종목, 최대16개 시각 샘플.
- `combined`: 새 옵션에서는 동일 guard의 관측+실행 누계. 기존 실행 전용 옵션에서는
  실행 guard 통계이며 관측 통계는 별도다. 실행 전에는 관측 통계만 존재한다.
- `runtime_enabled`, `runtime_started`, `runtime_discarded`: 본 실행 정책/진입/추가 사용.

기존 session에 계좌 identity·세션 토큰·시작 전/종료 원장 SHA를 연결해 기록한다.
가격/raw payload/임의 예외 문자열은 저장하지 않으며, 고정 키/숫자/사유만 검증하여
보고한다. 준비 실패 사유도 보존한다. 실행 HALT는 소유권 아래 관측 checkpoint로만
저장하고 HALT를 해제하지 않는다. 프로세스 강제 종료 전 기록되지 않은 상세 결과는
N/A/BLOCKED이며 추정 복원하지 않는다. 저장 실패는 정상 종료 성공이 아니다.

### RED → GREEN과 재현 범위

기준 `301809ef3f403f4d00baab9f6e0a86bec2c252cc`에서 구현 변경 **전에** 실행했다.
재현 테스트만 추가한 커밋은 `3bcfad80cfd2c6a38732aca7549fca731b348bd0`이다.
사용자 보고의 두 timestamp 1793150503403 → 1793150503401을 사용했고, 주변24개와
후속 틱·가격은 합성이다. Mac 원본 전체 틱을 재생했다고 주장하지 않는다.

| 동일 합성 입력 | 변경 전 | 새 옵션 적용 후 |
|---|---|---|
| 기존 실행 옵션만, 9.694초의 -2ms | 수신25/accepted24, PAPER_STALE_TICK, 엔진 미시작 | 동일 엄격 실패 |
| 새 관측 옵션, -2ms, 같은 BTC의 더 큰 후속 틱 | PREPARATION_FAILED (RED) | STOPPED_VERIFIED (GREEN) |
| 폐기 poison 가격 | 엄격 시각 실패 | 파싱/전략/체결/평가 도달 없음 |
| 새 옵션, 후속 없음 | 지원되지 않음 | 원장 SHA 불변, PREPARATION_FAILED, 예산 재부여 불가 |

RED 재현은 위 테스트 전용 커밋을 별도 임시 checkout에서 실행하면 2개 중 성공 계약
1개가 실패해야 한다. 현재 브랜치는 아래로 실행한다. 실제 네트워크는 사용하지 않는다.

```sh
.venv/bin/python -m unittest -v tests.test_p15_observation_discard
.venv/bin/python -m unittest -v tests.test_p14_completed_candles tests.test_p14_red_green
.venv/bin/python -m unittest discover
.venv/bin/python tools/check_safety.py
```

P14의 기대 결과와 기준 5개 파일 SHA는 그대로 유지한다. P14 baseline 복원용 reverse
patch만 현재 소스에 맞게 갱신한다. 복원 실패를 RED로 인정하거나 기대값을 완화하지 않는다.
신호/회계/P14 완료 봉 코드 자체는 변경하지 않았다. 전체 OS별 결과와 exact head/run은 PR에 기록한다.

### 리뷰 후 Mac의 새 계좌에서만 실행할 명령

이 작업에서는 Mac/기존 계좌/백업/실제 공개 시장에 접근하지 않았다. 아래도 검토 후
사용자가 새 경로를 선택해 직접 실행할 명령이다. 기존 HALTED 경로에 실행하지 않는다.

```sh
cd "$HOME/projects/auto_trade"
# 검토·병합된 최신 master를 확인한 뒤 테스트/Safety를 먼저 수행한다.
.venv/bin/python -m unittest -v tests.test_p15_observation_discard
.venv/bin/python tools/check_safety.py
experiment_dir="$HOME/paper/paper-p15-01"
.venv/bin/python -m ops.paper_bot init --directory "$experiment_dir"
.venv/bin/python -m ops.paper_bot run --directory "$experiment_dir" \
  --connect-public --seconds 600 --tickers KRW-BTC \
  --observe-realtime-seconds 10 --discard-small-timestamp-regressions \
  --discard-small-timestamp-regressions-during-observation
# 별도 SSH 창에서 상태/중지를 요청한다. 원장을 직접 열지 않는다.
.venv/bin/python -m ops.paper_bot status --directory "$HOME/paper/paper-p15-01"
.venv/bin/python -m ops.paper_bot stop --directory "$HOME/paper/paper-p15-01"
# 종료 후 읽기 전용 결과
.venv/bin/python -m ops.paper_bot report --directory "$HOME/paper/paper-p15-01" --limit 20
```

성공 기준: 관측 enabled=true, 폐기가 있다면 같은 종목 후속=true, combined 한도 이내,
정상 종료 STOPPED_VERIFIED/미확정 주문0/회계 대사 성공. 주문0건도 가능하며 매수 성공을
뜻하지 않는다. 역행이 전혀 없다면 연결·종료만 검증한 것이며 실제 폐기 동작은 미검증이다.
후속 없음·추가 역행·시각/무수신 이상·저장/종료 불확실성은 실패로 보존하고 재시작하지 않는다.
같은 계좌에 옵션을 다시 주거나 파일을 지워 실험을 반복하지 않는다.

5ms의 시장 통계적 타당성, 공식 수신 순서 보장, 실제 SSH/절전/파일시스템 내구성은
여전히 미검증이다. REAL_TRADING=False, Slack/launchd/무인 실행·재시작/유료 호출 OFF를 유지한다.
