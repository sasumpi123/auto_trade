# P26 정상 종료 PAPER 계좌의 수동 재개와 비용 반영 왕복 정산

이슈 #55. 새 PAPER 프로필의 구현·오프라인 검증이다. 실제 시세 실행·실거래·배포·자동 재시작·자동 병합 승인이 아니다.
기존 strict/P15/P18/P25의 옵션·기본값·최초 시도 계약은 그대로다.

## 프로필 `P26_MANUAL_CONTINUATION_V1`

| 항목 | 값 |
| --- | --- |
| 선택 | 새 계좌 생성 시에만: `paper_bot init --directory D --profile p26` |
| 고정 | 초기자금 1,000,000원, 비용 5/5/2bps(다른 값은 생성 거부), KRW-BTC만, PAPER 전용 |
| 실행 | 최초 `--p26-first-run`, 재개 `--p26-resume`. 항상 `--tickers KRW-BTC --observe-realtime-seconds N` 명시, 다른 역행 플래그와 혼합 불가 |
| 회차 | 최대 2회(최초 + 수동 재개 1회) |
| 시간 | 회당 최대 3600초, 계좌 누적 예약 최대 7200초 |
| REST | 회당 64회(기존 `ops/paper_watch.py` 클라이언트 한도 그대로), 계좌 최대 128회 = 2회 × 64회를 회차마다 예약 |
| 역행 입력 | P25와 같은 격리·같은 종목 전진 후속 확인(`P26_ACCOUNT_QUARANTINE_V1`). 폐기 상한은 **계좌 전체** 종목 10회/전체 30회, 세션·시간창 리필 없음 |

프로필은 생성 영수증과 계좌 identity에 정확히 결속된다(`profile` 필드, 비용 정책과 함께 검증). 스키마 번호는 2로 유지했다.
P22 고정 소스인 `trading/costs.py`가 계좌 메타 스키마 1/2만 허용하기 때문이며, 그 파일은 수정하지 않았다.
기존 계좌를 P26으로 바꾸거나 복사하거나 재초기화할 수 없고, P26 계좌는 P15/P18/P25나 일반 세션을 시작할 수 없다.

## 상태 전이

```
생성(NEVER_STARTED) --begin(first)--> IN_PROGRESS(run 1 예약) --finish--> STOPPED_VERIFIED | PREPARATION_FAILED | HALTED
STOPPED_VERIFIED(run 1, 체크포인트 有) --begin(resume)--> IN_PROGRESS(run 2 예약) --finish--> STOPPED_VERIFIED(재개 불가) | ...
```

- `begin(p26=..., seconds=S)`가 원장 소유권(락)을 잡은 뒤 identity·프로필·세션 상태를 검증한다.
  - 인자·소유권·identity 오류는 아무것도 쓰지 않고 차단한다.
  - 통과하면 회차·요청 시간·REST 몫을 `session.json`의 `p26` 블록에 기록한다. 최초 실행이면 first-use 영수증도 함께 쓴다. 모두 **네트워크 전**이다.
  - 조기 stop, 준비 실패, 프로세스 중단이 있어도 예약은 환급되지 않는다.
- **재개 허용 조건**
  - 이전 세션이 `STOPPED_VERIFIED`이고 HALT/blocked/IN_PROGRESS/UNKNOWN/PREPARATION_FAILED가 아니다.
  - 미확정 주문이 0이고 활성 owner가 없다.
  - 다음 값이 서로 일치한다: 이전 session ID = 계좌 `last_session_id`, 종료 원장 SHA = 현재 원장 SHA, 정책·비용·체크포인트·예약.
  - 체크포인트에 미해소 격리가 없고, 잔여 시간·폐기 예산이 남아 있다.
  - 하나라도 어긋나면 네트워크·엔진 전에 차단한다.
- **체크포인트:** 정상 종료(`finish`) 때 `p26_checkpoint`를 기록한다(세션 ID, 종료 원장 SHA, high-water, 계좌 누적 폐기 수, 미해소 격리 없음).
  - 재개 시 같은 세션의 `regression_checkpoint` 증거와 대조한다. 누적 수 = 이전 누적 + 이 세션 폐기 수이며, 단일 필드를 낮춘 변조는 거부된다.
  - 내용 digest가 체크포인트 전체(high-water 포함)를 marker의 세션 ID·종료 원장 SHA·같은 세션 회귀 증거와 결속한다.
    high-water 한 값만 낮추거나 종목 키를 지우면 거부된다. 이 세션에 승인된 틱이 있으면 high-water 키가 있어야 하고,
    이전 이월값보다 낮을 수 없다(리뷰 5761233853).
  - 오래된 체크포인트 재사용은 세션 ID·SHA 불일치로 막힌다.
  - 동시 재개는 락과 IN_PROGRESS로 막힌다.
  - 여러 파일을 일관되게 함께 되돌리는 롤백까지 탐지한다고 주장하지 않는다.
- **이어받는 것과 아닌 것**
  - 원장(현금·수량·취득원가·수수료·확정손익·주문/체결·신호/쿨다운/물타기)은 엔진이 기존 파일을 그대로 연다. 같은 체결은 다시 반영되지 않는다.
  - high-water와 누적 폐기 수만 체크포인트에서 TickTiming으로 이어받는다. 실행 옵션이 아니라 예약된 marker 블록에서만 읽는다.
  - 이전 가격·평가 mark·캐시·버퍼는 쓰지 않는다. 재개도 시작 전 공개시세 관찰과 완료 봉 준비를 다시 통과해야 한다.
- **새 세션의 시계 원점:** 새 세션의 wall/monotonic 원점과 15초/60초 watchdog 기준은 그 세션 시작 시점이다. 이는 예산·high-water 초기화가 아니다(테스트로 확인).
  - 15초/60초, 격리 해제 전 만료 검사, supervisor grace 15, HALT 사유는 그대로다.
- **중지 중:** 프로세스가 없으므로 위험관리·매매가 실행되지 않는다. 재개 전 시세 변화는 반영되지 않는다.
- **출력:** run/status/report는 `p26_account`로 세션 값(회차·세션 시간·세션 폐기)과 계좌 누적(예약 시간·REST·누적 폐기·잔여·재개 가능)을 나눠 보여준다.
  - 사후 보고서는 신선한 가격이 없으면 보유 시 `equity=N/A`다.

## 검증 (합성)

- 실제 PAPER 엔진 왕복: Fake 공개 스트림 → BUY 10000 → 보유한 채 정상 종료 → 같은 계좌 명시 재개 → SELL 10010.
  - P17 5/5/2bps와 P9 실현손익·현금 기대식이 일치한다.
  - 주문 2건, 매수/매도 1/1, 체결 중복이 없다.
  - 재개 시 완료 봉과 공개 관찰을 다시 통과하고, 이전 mark를 재사용하지 않는다.
- 이월 폐기 예산:
  - 이전 9회 상태에서 1회만 더 허용하고 그다음은 HALT한다.
  - carried high-water보다 작은 값은 격리하고, 같은 값은 보류하며, 전진하면 해제한다.
  - 재개 후에도 15초 watchdog 검사가 유지된다.
- 한도 경계:
  - 시간 3600/7200 정확 경계, 0초와 3601초는 거부한다.
  - 세 번째 실행은 차단한다.
  - REST 64×2를 확인한다.
- 차단:
  - 재개 차단: HALT, 관측·런타임 미해소 격리, 준비 실패, 조기 stop 뒤(무환급)
  - 체크포인트 거부: stale, 다른 세션, 값 낮춤, 미해소, 누락, 예산 소진, 증거 누락
  - 예약 전 차단: 활성 owner, 잘못된 인자
  - 네트워크 전 차단: 실행 옵션·초·ticker 불일치
  - 동시 재개, 이전 marker 복원
- 승격 금지: 기존 계좌와 비용 지정 계좌는 P26으로 시작할 수 없다. P26 계좌는 다른 모드를 시작할 수 없다.
- CLI: 프로필 생성, 비용 불일치 생성 거부, profile은 init 전용, check의 계좌 카운터.
- 보존: 기존/HALTED 계좌·백업 바이트 불변, P25/P18 등 기존 테스트 통과, P22 격리 실행 11개 통과, P14 역패치 재생성.

공개시세 SELL은 실제 Mac 실행에서 SELL 신호가 발생해 정산된 경우에만 검증된 것이다. 신호가 없으면 미검증으로 남긴다.
강제 SELL, 전략 변경, 시험 연장은 하지 않는다.

## Mac 실행 (병합 후)

### 1) 새 P26 계좌 생성과 최초 실행 (최대 55분, 출력 최대 8줄)

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  test -z "$(git status --porcelain)" || { echo 'BLOCKED local changes present; nothing switched'; exit 2; }
  git switch -q master && git pull -q --ff-only
  mkdir -p "$HOME/paper"
  d="$(mktemp -d "$HOME/paper/p26-continuation.XXXXXX")"
  printf '%s\n' "$d" >"$HOME/paper/p26-latest-path"
  .venv/bin/python tools/check_safety.py >"$d/safety.txt" 2>&1 || { echo 'BLOCKED safety check'; exit 2; }
  .venv/bin/python -m unittest tests.test_p26_manual_continuation >"$d/unit.txt" 2>&1 || { echo 'BLOCKED P26 tests'; exit 2; }
  .venv/bin/python -m ops.paper_bot init --directory "$d/account" --profile p26 >"$d/init.json" 2>&1 || { echo 'BLOCKED init'; exit 2; }
  .venv/bin/python -m ops.paper_bot check --directory "$d/account" >"$d/check-1.json" 2>&1 || { echo 'BLOCKED check'; exit 2; }
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$d/account" --connect-public --seconds 3300 \
    --tickers KRW-BTC --observe-realtime-seconds 10 --p26-first-run >"$d/run-1.log" 2>&1
  run_code=$?
  .venv/bin/python -m ops.paper_bot report --directory "$d/account" --limit 20 >"$d/report-1.json" 2>"$d/report-1.stderr"
  report_code=$?
  set -e
  .venv/bin/python -m tools.p26_paper_summary "$d/run-1.log" "$d/report-1.json" "$run_code" "$report_code"
  [ "$run_code" -eq 0 ] && [ "$report_code" -eq 0 ] || exit 2
)
```

### 2) 같은 계좌 검증과 명시적 재개 (1)의 결과가 `resume_allowed=True`일 때만, 최대 55분

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  test -z "$(git status --porcelain)" || { echo 'BLOCKED local changes present'; exit 2; }
  d="$(cat "$HOME/paper/p26-latest-path")"
  test -d "$d/account" || { echo 'BLOCKED P26 account not found'; exit 2; }
  .venv/bin/python tools/check_safety.py >"$d/safety-2.txt" 2>&1 || { echo 'BLOCKED safety check'; exit 2; }
  .venv/bin/python -m ops.paper_bot check --directory "$d/account" >"$d/check-2.json" 2>&1 || { echo 'BLOCKED check'; exit 2; }
  .venv/bin/python -c 'import json,sys;sys.exit(0 if json.load(open(sys.argv[1])).get("p26_account",{}).get("resume_allowed") is True else 2)' \
    "$d/check-2.json" || { echo 'BLOCKED resume not allowed for this account'; exit 2; }
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$d/account" --connect-public --seconds 3300 \
    --tickers KRW-BTC --observe-realtime-seconds 10 --p26-resume >"$d/run-2.log" 2>&1
  run_code=$?
  .venv/bin/python -m ops.paper_bot report --directory "$d/account" --limit 20 >"$d/report-2.json" 2>"$d/report-2.stderr"
  report_code=$?
  set -e
  .venv/bin/python -m tools.p26_paper_summary "$d/run-2.log" "$d/report-2.json" "$run_code" "$report_code"
  [ "$run_code" -eq 0 ] && [ "$report_code" -eq 0 ] || exit 2
)
```

기존·HALTED 계좌는 재시작·초기화·이관하지 않는다. REAL_TRADING=False, 추가 지출 0원,
Slack/launchd/무인 실행/자동 재접속·재시작/자동 병합 OFF. 결과는 수익성이나 장시간 안정성의 증거가 아니다.
