# P17: 새 독립 PAPER 계좌의 명시적 체결 비용

[Issue #41](https://github.com/sasumpi123/auto_trade/issues/41)의 구현이다. 수익성 연구나
기존 계좌 마이그레이션이 아니다. Mac/실제 공개 시장/사용자 계좌·백업에는 접근하지 않았다.
REAL_TRADING=False, 초기 현금1000000, 추가 지출0, Slack/launchd/무인 실행은 OFF다.

## 생성·지속성

`paper_bot init`에 `--fee-bps`, `--slippage-bps`, `--full-spread-bps`를 **모두 명시**한
새 계좌만 비용을 사용한다. 숫자는 유한·비음수이고 기존 CostModel과 동일하게 fee<10000,
slippage+full_spread/2<10000이어야 한다. 부분 지정·실행 시 변경 옵션은 거부한다.
5/5/2는 연구 가정이지 실제 현재 거래소 요율/호가/시장 충격을 검증한 값이 아니다.

새 정책은 기존 creation identity/영수증의 schema2에 cost_policy schema1로 고정한다.
원장 자체의 version1 및 주문 형식은 유지한다. 원장·부모 영수증·experiment.json·초기 원장
SHA·기존 first-use/session/소유권 검사를 대조한 뒤 네트워크와 실행을 허용한다. 재개 때
비용 누락/변조/버전 내림/불일치는 차단하며 자동 대체하지 않는다. 실행 중 메모리 정책
변경도 주문 전에 거절한다. 파일 전체의 악의적 공동 변경/롤백을 막는 독립 witness는 아니다.

옵션 없는 기존 schema1 계좌와 과거 보고서는 기존 무비용 산술을 사용한다. 기존 파일을
수정하지 않는다. 명시적 0/0/0 계좌는 schema2로 구분하며 legacy 비용 미지정과 혼동하지
않는다. init/check/status/report에 정책을 보여주고, 로드 전에는 NOT_LOADED로 표시한다.
비용 계좌에 ZERO_FEE 라벨을 붙이지 않는다. report는 읽기 전용이다.

## 가격·수수료와 회계

순수 CostModel/decimal을 trading/costs.py로 옮기고 backtesting.engine에서 그대로 재노출한다.
계산식은 변경하지 않았다. 운영 실행은 pandas/백테스트/데이터 다운로드를 비용 모듈 때문에
추가로 import하지 않는다.

- 매수 체결가 = 기준가 × (1+(slippage+full_spread/2)/10000)
- 매도 체결가 = 기준가 × (1−(slippage+full_spread/2)/10000)
- 매수: 선택 금액 B는 **총지출 상한**. 대금=B/(1+fee/10000), 수량=대금/매수체결가,
  수수료=대금×fee/10000. 일반/물타기에 동일 적용한다. 기존 최소 주문 판정은 총 선택
  금액 기준이고 실행 대금은 수수료만큼 작다. 별도의 거래소 최소 체결대금 검증이 아니다.
- 매도: 기존 보유 수량×매도체결가를 대금으로 하고 수수료를 차감한다.

기존 Decimal 기본 28자리 산술, _apply_fill, P9 이동평균/수수료 원가배분을 재사용한다.
임의 원화/코인 단위 반올림은 추가하지 않는다. 나눗셈 반올림으로 총지출 상한이나 순차
현금 차감이 음수가 되는 경우에만 대금을 현재 Decimal 정밀도의 한 단계(next_minus)
줄여 다시 검사한다. 실패하면 HALT다. 3bps/전액 매수의 극미세 음수 경계도 회귀 검증한다.
P9의 기존 절대/상대 close 허용오차를 유지하며, 대금+수수료를 묶는 순서 차이로 극미세
잔여 현금의 마지막 자릿수가 달라질 수 있다. 이를 원장 재작성으로 보정하지 않는다.

엔진 평균가는 매수 수수료 제외, P9 잔여 원가는 매수 수수료 포함이다. engine profit_amount는
매도 수수료만 차감한 기존 정의다. P9 순실현 손익은 매수 수수료 배분까지 반영한다.
슬리피지/스프레드는 체결가에 이미 포함되므로 손익에서 재차 빼지 않는다.
전략/위험관리 조건은 그대로다. 바뀐 체결 평균가·현금 때문에 이후 물타기/익절/손절 시점과
주문 경로는 자연스럽게 달라질 수 있다. 순수 비용 차이를 전략 기여도로 단정하지 않는다.

## 합성 재현과 검증

구현 전에 기존 엔진의 fee=0/기준가 체결을 RED로 재현했다. 테스트만 추가한 커밋은
27d20d9이며 기준은92bd245다. 이후 같은 두 테스트는 GREEN이다.
가짜 기준가10000→10010/총 매수 예산100000에서 무비용 왕복은 +100이다.
5/5/2일 때 60자리 독립 Decimal 계산은 약 -119.977958252534736 KRW다. 실제 PAPER
영수증과 P9의 수수료 포함 정산을 이 결과에 대조한다. 사용자 거래나 수익성 증거가 아니다.

추가로 fee-only/slip-only/spread-only/명시적 zero, 전액·최소 주문 경계, 물타기, 부분
체결 원가배분, 정상 종료/재개, 정책 손상·누락·버전 내림의 네트워크 전 차단, HALTED
및 legacy 계좌/백업 바이트 불변, 읽기 전용 보고를 검증한다.

```sh
.venv/bin/python -m unittest -v tests.test_p17_paper_costs
.venv/bin/python -m unittest discover -v
.venv/bin/python tools/check_safety.py
```

## 리뷰·병합 후에만 사용할 Mac 명령 한 블록

현재 실행 요청이 아니다. 새 경로가 이미 있거나 실패하면 멈춘다. 기존/HALTED 계좌를
다시 열거나 초기화하지 않는다. 로그는 사용자 소유 private 경로에만 보관하고 GitHub에
업로드하지 않는다. 실제 신호/체결 여부는 이 실행 결과로만 판정한다.

```sh
(
  set -eu
  umask 077
  cd "$HOME/projects/auto_trade"
  .venv/bin/python tools/check_safety.py
  .venv/bin/python -m unittest tests.test_p17_paper_costs
  mkdir -p "$HOME/paper"
  paper_dir="$HOME/paper/paper-cost-p17-01"
  if [ -e "$paper_dir" ]; then printf '%s\n' 'BLOCKED: choose a fresh account path'; exit 2; fi
  log_dir="$(mktemp -d "$HOME/paper/p17-private-logs.XXXXXX")"
  printf 'Full private logs: %s\n' "$log_dir"
  .venv/bin/python -m ops.paper_bot init --directory "$paper_dir" \
    --fee-bps 5 --slippage-bps 5 --full-spread-bps 2 >"$log_dir/init.json" 2>"$log_dir/init.stderr"
  .venv/bin/python -m ops.paper_bot check --directory "$paper_dir" >"$log_dir/check.json" 2>"$log_dir/check.stderr"
  set +e
  .venv/bin/python -m ops.paper_bot run --directory "$paper_dir" --connect-public \
    --seconds 180 --tickers KRW-BTC --observe-realtime-seconds 10 >"$log_dir/run.log" 2>&1
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

STOPPED_VERIFIED·미확정0·정책 일치·회계 대사를 확인한다. 거래0건이면 실제 비용 체결은
미검증이다. HALTED/불확실 종료는 보존하고 재시작하지 않는다. 실제 요율·호가 충격·체결
정밀도/유동성·Mac 실기기/절전/SSH/실제 Slack는 이 Cloud Fake 검증의 범위 밖이다.
