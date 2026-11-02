# P18: 명시적 반복 소폭 timestamp 역행 폐기

[Issue #43](https://github.com/sasumpi123/auto_trade/issues/43) 구현이다. 기본 OFF인 별도
실험 정책이며 strict/P15를 변경하거나 과거 HALTED 계좌를 복구하지 않는다. 기존 전략,
P14 완료 봉, P17 비용·최소 주문대금·회계 정책은 그대로다. Cloud에서는 합성 Fake만
사용했으며 Mac/실제 공개 시장/사용자 계좌·backup에는 접근하지 않았다.

## 선택과 고정 한도

| 선택 | 폭 | 종목별 | 전체 | 시간 |
|---|---:|---:|---:|---:|
| 기본 strict | 역행 HALT | 폐기 없음 | 폐기 없음 | 기존 실행 제한 |
| 기존 P15 폐기 옵션 | 최대5ms | 1회 | 3회 | 준비 포함 최대3600초 |
| 새 P18_BOUNDED_REPEAT_V1 | 최대5ms | 10회 | 30회 | 준비 포함 최대3600초 |

새 `--discard-repeated-small-timestamp-regressions`는 기존
`--discard-small-timestamp-regressions`와
`--discard-small-timestamp-regressions-during-observation` 모두, 관측 옵션,
공개 연결 명시, 고정 `--tickers`, 신규 독립 계좌의 최초 시도를 요구한다.
옵션 누락/모순/후보 교체/기존 계좌 재활성화는 거부한다. 독립 공개 진단에는 새 모드가 없다.
상한을 CLI에서 숫자로 늘리거나 모듈 전역값을 변경하지 않는다. 고정 정책은 frozen 객체다.
5ms·10회·30회는 구현을 제한하는 실험 상한이지 통계적으로 입증한 안전/최적값이 아니다.

관측과 실행은 같은 guard/정책/시각 기준/카운터를 공유한다. 폐기 후 그 종목에서 이전
승인 timestamp보다 **큰** 정상 후속 틱이 있어야 미해소 상태가 풀린다. 후속 수신은 예산을
돌려주지 않는다. 미해소 상태에서 어느 종목이든 추가 역행은 HALT다. 동일 timestamp나
다른 종목의 정상 틱은 미해소 상태를 해제하지 않는다. 관측 창 종료 때 미해소면 엔진은
시작하지 않고, 실행 종료 때 미해소면 정상 종료로 보고하지 않는다.

폐기 메시지의 가격은 파싱/전략/위험관리/체결/평가에 전달하지 않는다. high-water,
종목/전체 progress, accepted 수신 수와 watchdog도 갱신하지 않는다. 수신/폐기 수만
올라간다. 지연/미래/벽시계-monotonic/무수신 보호와 15초 전체·60초 종목 watchdog을
유지한다. 이미 만료된 watchdog이 폐기보다 우선한다. timestamp를 재작성하거나 정렬,
trade_timestamp fallback, 재접속/재시작, 자동 청산을 추가하지 않는다.

## 최초 시도 증거와 복구 경계

새 원장/lock을 만들지 않고 기존 생성 영수증·원장 소유권·first-use·session·SHA를 사용한다.
선택 정책 ID를 first-use와 session 양쪽에 네트워크 전에 원자 저장하고, 관측 시작 증거도
기존 session에 남긴다. ID 불일치/한쪽 소실/다운그레이드는 차단한다. 같은 토큰의 자식
재실행도 기존 observation-start 증거로 막는다.

준비 실패·중지·시간 초과·정상 종료·프로세스 사망 뒤에는 같은 계좌에서 새 예산을 주지
않는다. 다른 폐기 플래그나 strict로 바꿔 예약을 덮어쓰지도 않는다. 경로를 대체/삭제해서
새 계좌를 만드는 것은 부모 생성 예약과 기존 경로 identity 검사로 차단한다. 실패를 성공으로
승격하거나 HALT/UNKNOWN을 초기화하지 않는다. 전체 파일의 공동 롤백/공동 조작을 탐지할
독립 witness는 여전히 없으며, 그 한계를 복구 성공으로 주장하지 않는다.

`regression_evidence`의 observation/combined에는 새 모드의 policy ID와 유효 한도가
명시된다. 기존 evidence에 이 필드가 없으면 기존 P15 상한만 허용한다. 정책/한도 누락이나
변조는 report 검증에서 거절한다. 종목 최대30, 샘플 최대16, 기존 보고 크기 제한과 고정
숫자/키만 사용한다. 가격/raw payload/인증정보/사용자 계좌 결과는 증거에 넣지 않는다.
보고와 저장 증거는 재시작 허가가 아니다.

## 재현과 검증

기준 master9257736에서 테스트만 추가한 7f6078e를 먼저 실행했다. 동일 합성 입력은
정상100 → 역행98 → 정상101 → 진행200 → 역행198 → 정상201이다. P15는 두 번째
역행에서 PAPER_STALE_TICK이며 그 동작을 유지한다. 새 옵션 성공 계약은 수정 전 같은
HALT로 RED였고, 구현 후 두 번 폐기/4번 승인/미해소 없음으로 GREEN이다. 합성 주변
틱이 과거 실제 세션을 재현한다고 주장하지 않는다.

필수 회귀는 1/2/5ms·6ms, 종목10/11회·전체30/31회, 후속/미해소/시계·watchdog·1시간,
관측/실행 합산, poison 가격 파싱 차단과 위험관리/체결/평가 미전달, 최초 증거·중단·프로세스
사망·정책 손실·경로 대체, 비용 계좌의 실제 PAPER 엔진 가상 매수/매도/P9 정산을 포함한다.
가상 체결 성공은 전체 시각·후속·원장 대사를 통과한 경우만 정상 종료다. 기존 계좌/backup
바이트 불변과 P14/P15/P17 회귀도 유지한다. 정확한 전체/OS별 CI 결과는 PR에 기록한다.

```sh
.venv/bin/python -m unittest -v tests.test_p18_repeat_discard
.venv/bin/python -m unittest discover -v
.venv/bin/python tools/check_safety.py
```

## 리뷰·병합 후에만 사용할 Mac 명령 한 블록

현재 실행 요청이 아니다. 사용자가 리뷰·병합 뒤 새 private 경로를 선택할 때만 실행한다.
기존 계좌/HALTED 경로를 사용하지 않는다. full logs는 로컬 private 경로에 저장하며
GitHub로 업로드하지 않는다. 실패·HALTED·불확실 상태는 보존하고 재실행하지 않는다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  .venv/bin/python tools/check_safety.py
  .venv/bin/python -m unittest tests.test_p18_repeat_discard
  mkdir -p "$HOME/paper"
  paper_dir="$HOME/paper/paper-p18-01"
  if [ -e "$paper_dir" ]; then printf '%s\n' 'BLOCKED: choose a fresh account path'; exit 2; fi
  log_dir="$(mktemp -d "$HOME/paper/p18-private-logs.XXXXXX")"
  printf 'Full private logs: %s\n' "$log_dir"
  .venv/bin/python -m ops.paper_bot init --directory "$paper_dir" \
    --fee-bps 5 --slippage-bps 5 --full-spread-bps 2 >"$log_dir/init.json" 2>"$log_dir/init.stderr"
  .venv/bin/python -m ops.paper_bot check --directory "$paper_dir" >"$log_dir/check.json" 2>"$log_dir/check.stderr"
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$paper_dir" --connect-public \
    --seconds 180 --tickers KRW-BTC --observe-realtime-seconds 10 \
    --discard-small-timestamp-regressions --discard-small-timestamp-regressions-during-observation \
    --discard-repeated-small-timestamp-regressions >"$log_dir/run.log" 2>&1
  run_code=$?
  .venv/bin/python -m ops.paper_bot report --directory "$paper_dir" --limit 20 >"$log_dir/report.json" 2>"$log_dir/report.stderr"
  report_code=$?
  set -e
  .venv/bin/python - "$log_dir/report.json" "$run_code" "$report_code" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]));s=r.get('summary',{})
print(json.dumps({'run_exit':int(sys.argv[2]),'report_exit':int(sys.argv[3]),
    'session_state':r.get('session_state'),'cost_policy':r.get('cost_policy'),
    'buys':s.get('cumulative_buys'),'sells':s.get('cumulative_sells'),
    'fees':s.get('fees'),'net_realized_pnl':s.get('net_realized_pnl')},ensure_ascii=False))
PY
  test "$run_code" -eq 0 && test "$report_code" -eq 0
)
```

성공 기준은 정책 ID/합산 한도/같은 종목 후속 확인, STOPPED_VERIFIED/미확정0/회계 대사다.
역행이나 체결이 발생하지 않았다면 실제 해당 동작은 미검증이다. 합성 결과는 현재 거래소
타임스탬프 순서/실제 비용·유동성/수익성을 증명하지 않는다. Mac·절전·SSH와 실제 시장은
추가 검증 대상이다. REAL_TRADING=False, 추가 지출0, Slack/launchd/무인 실행·자동
재시작/유료 모델·API·인프라/개발 자동화는 OFF다. 구현 작업에서 병합/실행하지 않는다.
