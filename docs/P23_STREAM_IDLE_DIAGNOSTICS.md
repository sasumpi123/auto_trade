# P23 PAPER_STREAM_TIMEOUT 순간 진단 보강

## 문제

신규 독립 PAPER 계좌(비용 5/5/2bps, KRW-BTC, 최대 55분) 실행이 `HALTED_NO_RESTART`,
`PAPER_STREAM_TIMEOUT`(`STREAM_IDLE idle_ms=15093.055`, `market=ALL_OR_UNKNOWN`)으로 끝났다.
미확정 주문 0, 포지션 없음, 매수/매도 0/0이었다. 이후 계좌 없는 공개 스트림 5분 관측에서는
15초 공백이 재현되지 않았다. 원인(네트워크 단절, 일시 시세 공백, 동일시각 반복 등)은 미확정이다.

기존 기록에는 다음이 없었다.

- 실패가 `recv` 타임아웃 경로인지, 수신된 메시지 검사 경로인지
- 마지막 메시지 수신 이후 시간과 마지막 전진 타임스탬프 이후 시간의 구분
- 실패 순간의 연결 상태와 Pong 응답 여부

## 변경 (진단 전용, 정책 불변)

| 파일 | 역할 |
| --- | --- |
| `services/stream_diagnostics.py` | 신규. 고정 allowlist 진단 레코드 생성·검증, 최대 2초 Pong 확인 |
| `services/market_stream.py` | `connection_state()`(OPEN/CLOSING/CLOSED/UNKNOWN), `send_ping()`(`ack_on_close=False`) 추가 |
| `ops/paper_session.py` | 런타임 루프에서 검사 경로/시각 기록, HALT 뒤 진단, 종료 결과에 `stream_idle_diagnostics` 추가 |
| `ops/paper_bot.py` | `view()` allowlist에 `stream_idle_diagnostics` 추가(run.log 종료 이벤트) |
| `tools/p23_stream_idle_summary.py` | run.log/report.json 오프라인 8줄 요약(원문 줄·가격·경로 미출력) |
| `tests/test_p23_stream_idle_diagnostics.py` | 합성/Fake 및 loopback 전용 검증 |
| `tests/fixtures/p14_before.patch.json`, `p14_upbit_minutes.json` | P14 기준선 역패치 픽스처를 현재 `paper_session.py`에 맞게 재생성(`git diff 69e790e -- 5개 파일`, 기존 PR과 동일 방식) |

변경하지 않은 것: `services/tick_timing.py`의 15초 `STREAM_IDLE`/60초 `MARKET_IDLE` 기준, 감독
프로세스 `grace=15`, 재접속·재시작·HALT 해제 없음, 전략·주문·체결·평가가격 경로, 계좌·세션·원장
형식과 복구 권한, P22 고정 소스 20개와 `p22_protocol.json`/`p22_registration.json`.

## 기록 항목 (`stream_idle_diagnostics`)

`status`: `TRIGGERED`(STREAM_IDLE 발생) / `NOT_TRIGGERED`(정상 종료·다른 사유) / `UNAVAILABLE`
(PAPER_STREAM_TIMEOUT이지만 진단 실패. 원래 사유는 그대로).

TRIGGERED 필드:

- `trigger_path`: `RECV_TIMEOUT` / `RECEIVED_MESSAGE_CHECK` / `OTHER`, `message_kind`: `STRATEGY_TICKER` / `NON_STRATEGY_OR_SNAPSHOT` / null
- `idle_ms`: TickTiming STREAM_IDLE 샘플의 실패 순간 값
- `since_last_message_ms`: 런타임 루프 마지막 메시지 수신 이후(수신 없으면 null)
- `since_last_advance_ms`: TickTiming `last_progress` 기준(전진 전이면 guard 시작 시점, `advance_reference` 명시)
- `counters`: 스트림 수신, timing 수신/통과/동일시각/폐기, 비전략 무시(guard 누계, `counter_scope` 명시)
- `connection_before`/`connection_after`: OPEN/CLOSING/CLOSED/UNKNOWN
- `pong`: CONFIRMED/UNCONFIRMED/UNSUPPORTED/CANCELLED/ERROR, `ping_sent`, `probe_reason`, `probe_elapsed_ms`, `probe_budget_ms=2000`
- `order`: `ENGINE_HALT_CONFIRMED_BEFORE_PROBE`(HALT 미확인이면 Probe 생략)

해석 예: `RECV_TIMEOUT` + `since_last_message_ms≈idle_ms`는 무수신, `RECEIVED_MESSAGE_CHECK` +
`since_last_message_ms=0` + 높은 `equal_timestamp`는 동일시각 반복이다. 어느 경우도 원인 확정이 아니다.

## 순서와 제한

1. 기존 예외 처리에서 `engine.halt('PAPER_SESSION_FAILURE')`를 먼저 수행·저장한다.
2. `engine.halted is True and ready is False`일 때만 Probe한다.
3. Probe는 데몬 스레드에서 ping을 보내고 호출 측은 총 2초까지만 기다린다. 송신이 끝나지 않으면
   `PING_SEND_NOT_COMPLETED_WITHIN_BUDGET`로 포기한다. 연결이 OPEN이 아니면 ping을 보내지 않는다.
4. `ack_on_close=False`이며, 이벤트가 설정돼도 연결이 OPEN이 아니면 CONFIRMED가 아니다.
5. 사용자/감독 stop 요청 시 `CANCELLED`. 진단 예외는 `UNAVAILABLE`로 바뀌고 원래 사유는 유지된다.
6. 이후 기존 `stream.close()`와 사후 검증이 그대로 진행된다. 진단 값은 전략·주문·체결·평가에 전달되지 않는다.

레코드는 고정 키·enum·숫자만 허용하고 2048바이트를 넘거나 형식이 다르면 `UNAVAILABLE`이다. 원문
패킷, 가격, 인증정보, 예외 문자열은 기록하지 않는다. 계좌 원장/세션 마커에는 저장하지 않으며
run.log 종료 이벤트에만 나타난다.

## 검증과 한계

합성 15.093초 무수신, 동일시각 반복, Pong 응답/미응답/연결 종료(Fake 및 websockets 17 loopback 서버),
송신 차단 2초 포기, 진단 예외, HALT 선행·HALT 뒤 전략/주문 없음, 14.999초 미중단/15초 중단, P22
`frozen()`을 검사한다. 이것은 진단 누락 개선의 증거이며 실제 네트워크 장애 원인을 재현하거나
해결했다는 증거가 아니다. Pong 미확인만으로 네트워크 장애라고 단정하지 않는다. 실제 Upbit 서버의
Pong 동작, Mac 절전/네트워크 전환, 장시간 실행은 실기기에서만 확인된다.

## Mac 실행 (병합 후, 신규 독립 계좌, 최대 55분)

기존 HALTED 계좌는 그대로 두고 재시작/초기화하지 않는다. 출력은 최대 8줄이며 전체 로그는
`~/paper/p23-*` 최신 디렉터리(`ls -dt ~/paper/p23-* | head -1`)에 비공개로 남는다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  git switch -q master && git pull -q --ff-only
  mkdir -p "$HOME/paper"
  d="$(mktemp -d "$HOME/paper/p23-idle.XXXXXX")"
  .venv/bin/python tools/check_safety.py >"$d/safety.txt" 2>&1 || { echo 'BLOCKED safety check'; exit 2; }
  .venv/bin/python -m unittest tests.test_p23_stream_idle_diagnostics >"$d/unit.txt" 2>&1 || { echo 'BLOCKED P23 tests'; exit 2; }
  .venv/bin/python -m ops.paper_bot init --directory "$d/account" \
    --fee-bps 5 --slippage-bps 5 --full-spread-bps 2 >"$d/init.json" 2>&1
  .venv/bin/python -m ops.paper_bot check --directory "$d/account" >"$d/check.json" 2>&1
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$d/account" --connect-public \
    --seconds 3300 --tickers KRW-BTC --observe-realtime-seconds 10 \
    --discard-small-timestamp-regressions --discard-small-timestamp-regressions-during-observation \
    --discard-repeated-small-timestamp-regressions >"$d/run.log" 2>&1
  run_code=$?
  .venv/bin/python -m ops.paper_bot report --directory "$d/account" --limit 20 >"$d/report.json" 2>"$d/report.stderr"
  report_code=$?
  set -e
  .venv/bin/python tools/p23_stream_idle_summary.py "$d/run.log" "$d/report.json" "$run_code" "$report_code"
)
```

REAL_TRADING=False, 추가 지출 0원, Slack/launchd/무인 실행/자동 재시작/자동 병합/개발 자동화 OFF를 유지한다.
