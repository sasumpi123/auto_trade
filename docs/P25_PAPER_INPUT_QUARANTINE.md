# P25 PAPER 입력 정책: 역행 가격 격리와 정상 후속 확인

이슈 #53. 새 PAPER 전용 입력 정책의 구현·오프라인 검증이다. 기존 기본값 변경, 실제 공개시세 실행,
실거래·배포·자동 병합 승인이 아니다.

## 배경

- 약 55분 무거래 관측(P24): 두 연결 각 수신 3904, 무효 0, 최대 공백 약 10.93초, 15초 공백 미재현
  (이전 STREAM_IDLE 원인이 해결됐다는 증거는 아님).
- 이후 비용 반영 PAPER: 수신 1147/통과 1146, 매수·매도·미확정 0, `PAPER_STALE_TICK`으로 중단.
  실패는 `TIMESTAMP_REGRESSION`, 직전 승인 timestamp 대비 **-31ms**. P18(폭 5ms/종목 10/전체 30/최초 3600초)이
  폐기 0건에서 **폭 조건**으로 HALT했다. 이는 P18 계약대로의 동작이며 계산 버그가 아니다.
  로컬 수신 시각 차이는 독립된 서버 지연 측정이 아니다.

## 정책 `P25_QUARANTINE_UNTIL_ADVANCE_V1`

선택: `python -m ops.paper_bot run ... --quarantine-timestamp-regressions` (내부 `paper_experiment.main`).

| 항목 | 값 |
| --- | --- |
| 역행 폐기 판정 | 유효성 검증 통과 + 같은 종목 high-water보다 작은 timestamp → 폐기. **폭 한도 없음**(`max_width_ms=None`) |
| 해제 | 같은 종목의 high-water보다 **큰** 유효 timestamp(`SAME_MARKET_TIMESTAMP_ABOVE_HIGH_WATER`) |
| 예산 | 종목 10회, 세션 전체 30회, 준비 포함 최초 3600초(P18과 같은 상한. 통계적으로 입증된 안전값 아님) |
| 범위 | 명시적 공개 연결, 고정 `--tickers`, `--observe-realtime-seconds` 필수, 신규 독립 계좌 첫 시도, 최대 3600초 |
| 배타성 | P15/P18 플래그와 혼합 불가. 계좌의 first-use·세션 영수증에 정책 ID가 정확히 결속 |

처리 순서(`services/tick_timing.py`):
1. 시장/타입/정수 timestamp/절대 지연(60초)/미래(5초)/벽시계-monotonic(5초) 검증 → 실패는 기존대로 HALT.
   잘못된 메시지를 정상으로 재작성하지 않는다.
2. high-water보다 작으면: 이미 만료된 15초/60초 watchdog을 먼저 검사(만료 시 기존 사유로 HALT) →
   예산 안이면 폐기. 가격·high-water·마지막 진행·accepted·watchdog을 갱신하지 않고 `None`을 반환한다
   (가격 파싱/전략/위험관리/주문/체결/평가로 가지 않음). 예산 초과는 `TIMESTAMP_REGRESSION` → `PAPER_STALE_TICK`.
3. 격리 중 같은 종목의 동일 timestamp(비전진)는 거래 경로로 보내지 않고(`quarantine_held`) 해제 증거도 아니다.
   다른 종목 메시지는 정상 처리하되 해제 증거가 아니다.
4. high-water보다 큰 유효 후속 → 격리 해제(`followup_newer_received=true`), 정상 승인.
5. 관측 종료·런타임 시작 검사·세션 종료 시 미해제 격리가 남으면 `TIMESTAMP_REGRESSION`으로 실패(기존 P18 경로).
   자동 재접속·재시작·HALT 해제·자동 청산 없음.

timestamp 정렬·재작성·`trade_timestamp` 대체는 하지 않는다. 관측과 런타임은 같은 TickTiming 객체
(정책·예산·high-water·격리 상태·시계 기준)를 공유한다.

## 결속과 재사용 차단

- `exp.begin(observation_discard=True, quarantine=True)`가 네트워크·엔진 시작 전에 first-use 영수증과
  `session.json`에 `discard_policy=P25_QUARANTINE_UNTIL_ADVANCE_V1`과 관측 세션을 기록한다(기존 P18과 같은 메커니즘,
  별도 원장 없음). 영수증이 이미 있거나 `last_session_id`가 있으면 거부된다.
- 실행 준비에서 선택 정책과 두 영수증이 정확히 같아야 한다. 영수증 손실·P18/P15 값으로 교체·옵션 다운그레이드는
  엔진·후보 조회 전에 차단된다(테스트).
- 준비 실패/중지/중단/정상 종료 뒤 P25·P18·P15 어떤 정책도 다시 부여되지 않는다(예산 리필 없음).
- 보고서 검증(`read_report`)은 저장된 증거의 정책이 결속 ID의 evidence와 같아야 하며, 정책 제거나 P18로의
  다운그레이드는 거부된다.

## 미선택 시 동작

strict/P15/P18의 `discard_policy()` 결과, 5ms 한도, evidence 딕셔너리, report 형식, 동일 timestamp 승인,
-31ms에서의 HALT가 그대로임을 테스트로 확인한다. P25 전용 report 키(`quarantined_markets`,
`quarantine_held`, `release`)는 P25에서만 나타난다.

## P22 고정 실험과의 분리

`ops/paper_experiment.py`는 P22 고정 소스 20개 중 하나이고, P19 엔진이 P22 분석 중 `exp.create/begin`을 호출한다.
P25는 이 파일을 수정하므로(정책 결속·CLI), 작업 트리에서 P22를 직접 실행하면 변경하지 않은 `frozen()`이
`P22_FROZEN_CODE_CHANGED`로 막는다(의도된 fail-closed).

- `backtesting/p22_pinned/paper_experiment.py.pin`: 등록된 정확한 바이트(sha256 `630fcc6a…`, 프로토콜 값과 일치).
- `tools/p22_isolated.py`: 추적 파일을 임시 디렉터리로 복사 → 작업 트리와 다른 고정 파일은 위 사본으로만 교체
  (예상 밖 드리프트는 차단) → 프로토콜·등록·러너(`p22_forward.py`)·고정 20개 해시를 모두 검증 → 그 안에서
  변경 없는 `python -m backtesting.p22_forward collect|analyze ...` 실행. 실행 안의 `frozen()`이 다시 검증한다.
- 해시 재등록, `frozen()` 삭제/완화, monkeypatch·환경변수·전역값 전환이 없다. P22 프로토콜·등록·기간·44bps·비용·
  수집 시작 시각은 변경하지 않았고, 수집/분석을 실행하지 않았다.
- 기존 P22 테스트 11개는 삭제·skip 없이 격리 트리 안에서 전부 실행된다(`tests/test_p22_forward.py`의
  `load_tests`). 작업 트리에서는 fail-closed와 드리프트가 정확히 그 한 파일뿐임을 검사한다.
- 한계: P22는 원래도 고정 20개 밖의 모듈(`services/tick_timing.py`, `ops/paper_session.py` 등)을 작업 트리에서
  import한다. 이 모듈들의 기존 동작은 P25 미선택 시 동일하도록 유지·테스트했다. P22 문서의 Mac 명령은 격리 실행기로 바꿨다.

## 검증 (합성)

-31ms 단독 재현(P18 HALT 유지 / P25 폐기 후 정상 후속 진행), 폭 1~30000ms, 절대 지연·미래·정수·시계,
동일 timestamp·다른 종목·반복 역행, 종목 10/전체 30/3600초 경계, 만료 watchdog 우선, 다른 종목 60초 미전진,
관측→런타임 같은 guard, 증거 검증·변조 차단, Fake 스트림 → **실제 PAPER 엔진** 매수/매도 → P17 5/5/2bps →
P9 실현손익 대사, poison 가격(문자열·숫자) 미전달·미파싱, 미해제 격리(관측·런타임) 실패, 중단·준비 실패 무리필,
결속 손실·교체·다운그레이드 차단, 기존/HALTED 계좌·백업 바이트 불변, CLI 배타성. 주변 틱·가격·후속은 합성이다.

## Mac 실행 (병합 후, 신규 계좌, 비용 5/5/2bps, 최대 55분)

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  test -z "$(git status --porcelain)" || { echo 'BLOCKED local changes present; nothing switched'; exit 2; }
  git switch -q master && git pull -q --ff-only
  mkdir -p "$HOME/paper"
  d="$(mktemp -d "$HOME/paper/p25-quarantine.XXXXXX")"
  .venv/bin/python tools/check_safety.py >"$d/safety.txt" 2>&1 || { echo 'BLOCKED safety check'; exit 2; }
  .venv/bin/python -m unittest tests.test_p25_regression_quarantine >"$d/unit.txt" 2>&1 || { echo 'BLOCKED P25 tests'; exit 2; }
  .venv/bin/python -m ops.paper_bot init --directory "$d/account" \
    --fee-bps 5 --slippage-bps 5 --full-spread-bps 2 >"$d/init.json" 2>&1
  .venv/bin/python -m ops.paper_bot check --directory "$d/account" >"$d/check.json" 2>&1
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$d/account" --connect-public \
    --seconds 3300 --tickers KRW-BTC --observe-realtime-seconds 10 \
    --quarantine-timestamp-regressions >"$d/run.log" 2>&1
  run_code=$?
  .venv/bin/python -m ops.paper_bot report --directory "$d/account" --limit 20 >"$d/report.json" 2>"$d/report.stderr"
  report_code=$?
  set -e
  .venv/bin/python tools/p25_paper_summary.py "$d/run.log" "$d/report.json" "$run_code" "$report_code"
  [ "$run_code" -eq 0 ] && [ "$report_code" -eq 0 ] || exit 2
)
```

기존 HALTED 계좌는 재시작·초기화하지 않는다. REAL_TRADING=False, 추가 지출 0원, Slack/launchd/무인 실행/
자동 재시작/자동 병합/개발 자동화 OFF. 결과는 장시간 안정성이나 수익성의 증거가 아니다.
