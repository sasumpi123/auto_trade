# P28 미청산 종가 평가와 가상 청산 후 평가 분리 (오프라인 감사)

이슈 #59. 상위 연구 이슈 #7의 "종료 미청산 종가 평가와 가상 청산 비용 차감 평가 분리" 항목만 다룹니다.

## 문제

P21 결과의 보유 포지션은 마지막 종가로 평가됩니다
(`open_position.valuation = LAST_CANDLE_CLOSE_NO_HYPOTHETICAL_EXIT_COST`).
그 수량을 실제로 매도할 때 추가로 들 수수료·슬리피지·반스프레드는 차감하지 않습니다.
`compact()`는 가상 청산 후 값을 제공하지 않습니다.

## 동작 (변경 없음 + 추가)

- **유지:** P19/P20/P21/P22 실행기, 전략, `trading/costs.py`, 체결 경로, 프로토콜, 등록 해시, pin, 격리 실행기는 수정하지 않았습니다. P22에는 종료 강제 매도가 없으며, 이 계약도 바뀌지 않습니다.
  원래 종가 평가와 realized/unrealized PnL, fills, fees, MDD는 그대로 복사해 보존합니다.
- **추가:** `backtesting/terminal_exit_audit.py`. 저장된 P21 `results.json` 하나만 읽는 사후 계산 도구입니다. 같은 비용 모델로 보유분 전량을 매도한다고 가정한 평가를 종가 평가 옆에 나란히 기록합니다.

## 실행 명령 (저장소 루트에서)

```bash
P21_RESULTS=~/research/p21-output/results.json P28_OUT=~/research/p28-$(date +%Y%m%d-%H%M%S) PYTHON_DOTENV_DISABLED=1 python -m backtesting.terminal_exit_audit --input "$P21_RESULTS" --output "$P28_OUT"
```

- 고정 명령: `python -m backtesting.terminal_exit_audit --input "$P21_RESULTS" --output "$P28_OUT"`.
- 입력은 checkout 밖 기존 P21 출력의 `results.json`이고, 출력은 아직 존재하지 않는 새 비공개 디렉터리입니다.
- 출력 파일은 `results.json`(Decimal 전체 값·입력 SHA·환경), `terminal_exit.csv`, `REPORT.md` 세 개입니다.
- 종료코드는 `AUDITED`/`AUDITED_PARTIAL`이면 0, 그 밖에는 2입니다.
- 표준출력은 최대 8줄이며 경로·오류 원문·인증정보를 출력하지 않습니다.
  - 1행: 상태/짝 수/제외 수/종료코드(실패 시 고정 reason).
  - 다음 최대 6행: 짝마다 Legacy→44bps의 종가평가 수익률, 가상청산 후 수익률, 추가 비용.
  - 마지막 행: 해석 한계.

## 계산 (Decimal, 정책별)

기호: C=`cash`, Q=`open_position.quantity`, P=`open_position.mark_price`(마지막 종가), I=1,000,000(P21 고정), f=fee_bps/10000.

- `marked_equity = C + Q*P` (기존 평가)
- `exit_price = CostModel.price(P,'SELL') = P*(1-(slippage_bps + full_spread_bps/2)/10000)`: 전체 스프레드의 **절반**만 적용합니다.
- `exit_funds = Q*exit_price`, `exit_fee = exit_funds*f` (수수료는 mark가 아니라 체결대금 기준)
- `exit_proceeds = exit_funds - exit_fee`
- `hypothetical_exit_equity = C + exit_proceeds`
- `hypothetical_exit_pnl = hypothetical_exit_equity - I`; 수익률도 이 평가자산 기준입니다.
- `extra_exit_cost = marked_equity - hypothetical_exit_equity`
  - 다음 세 항목의 합과 대조합니다: `extra_slippage = Q*P*slip/10000`, `extra_half_spread = Q*P*spread/20000`, `extra_sell_fee`.
  - 과거 매수·매도 비용은 이미 C와 remaining_cost에 반영돼 있으므로 다시 빼지 않습니다.
  - `hypothetical_exit_pnl == realized + exit_proceeds - remaining_cost`도 검사합니다.
- Q=0이면 추가 비용은 정확히 0이고(`exit_price`=N/A), 가상 청산 후 평가자산은 기존 평가자산과 같습니다.
- NO_TRADES 정책은 수익률과 비교 차이를 기존과 같이 N/A로 둡니다. 무거래는 성공으로 세지 않습니다.
- 종가 평가는 양수인데 가상 청산 후 0 이하가 되면 `marked_positive_exit_nonpositive=true`로 표시하고, CLI에 `MARKED_POSITIVE_EXIT_NONPOSITIVE`를 출력합니다.
- 비용은 각 결과에 저장된 `costs`만 사용합니다. 그 값은 P21 계약값 `{'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}`과 같아야 하며, CLI로 덮어쓸 수 없습니다.

## 검증 (fail-closed, 고정 reason)

- **입력:** `P21_FIXED_44BPS_V1`의 `VERIFIED`/`VERIFIED_PARTIAL` 보고서만 받습니다. 다음 경우는 거절합니다:
  - `failure_reason`가 있거나, `unfinished`/`unfinished_policies`가 비어 있지 않은 경우
  - 같은 디렉터리에 `INCOMPLETE` 표지가 있는 경우
  - 16 MiB를 넘거나, JSON이 손상됐거나, NaN/Infinity·중복 키가 있는 경우
- **짝/정책 대조:**
  - 6짝/12정책 이하만 받습니다.
  - `cases ∪ excluded`가 P19의 3일×2마켓과 정확히 일치해야 합니다.
  - 중복·겹침·누락을 거절합니다.
  - `completed_policies`는 짝마다 legacy/entry_bandwidth_44bps 둘이어야 하고, `cases[].baseline/candidate`와 내용이 동일해야 합니다.
  - 짝의 status/delta/interpretation은 `p21_entry_filter.comparison()`으로 다시 계산해 대조합니다.
  - `VERIFIED_PARTIAL`이면 제외 목록이 있어야 하고 `VERIFIED`이면 없어야 합니다.
  - 부분 결과는 `AUDITED_PARTIAL`로만 표시하고, 제외 목록은 출력에 그대로 보존합니다.
- **숫자:**
  - 숫자는 producer의 문자열 Decimal만 받습니다.
  - bool/float/NaN/Infinity/80자 초과 값은 거절합니다.
  - 수량·현금·원가·비용의 음수는 거절합니다.
  - mark는 양수여야 합니다.
  - Q>0이면 remaining_cost>0이어야 하고 진입 시각이 필요합니다. Q=0이면 remaining_cost=0이어야 합니다.
  - mark/시각/수량/원가/비용이 없으면 추정 가격·현재 시세·역산 가격으로 채우지 않고 차단합니다.
- **회계 (`p9_reconciled=true`를 맹신하지 않음):**
  - `cash + Q*P == final_equity`
  - `realized + unrealized == final_equity - I == total_pnl`
  - `unrealized == Q*P - remaining_cost`
  - `net_return_pct == (final_equity-I)/I*100`
  - NO_TRADES는 Q=0, return=None, equity=I여야 합니다.
- **허용오차:** `services.notification_metrics.close`를 사용합니다. 이는 P19/P21 대사에 이미 쓰는 기준으로, 상대 1e-24 또는 절대 1e-20입니다. 28자리 Decimal 연산 순서 차이만 흡수하기 위함입니다. 출력 반올림 값으로는 대사하지 않습니다.
- **입력 불변:** 입력 SHA-256을 계산 전후로 대조합니다(`P28_INPUT_CHANGED`). 입력은 쓰지 않습니다.
- **경로:** 기존 `p19_baseline.safe_path`/`fresh`(`minute_study.new_output`)를 재사용합니다.
  - symlink, checkout 내부, 계정/holdout/runtime 경로, 기존 출력은 거절합니다.
  - 실패 시 `results.json`에 `INCOMPLETE` 상태와 reason을 남깁니다.
  - `INCOMPLETE` 표지는 남기고, 성공용 CSV/MD는 남기지 않습니다.
- **시간:** 최대 30초입니다. 단계 사이(읽기 후, 짝마다, 출력 후)에 deadline을 검사합니다(`P28_TIME_LIMIT`).
  - subprocess/감시 프로세스는 없습니다.
  - 한 단계 내부(예: 대형 JSON 파싱)는 중간에 끊지 못하며, 16 MiB 입력 한도로 이를 제한합니다.
- **기록:** 결과에 다음을 기록합니다.
  - 입력 SHA·크기·schema·status·P19 결과 SHA·protocol SHA
  - Python/플랫폼
  - 도구 버전, 모듈 SHA, 그리고 `.git`을 직접 읽은 commit(읽을 수 없으면 `UNKNOWN`)
  - 파일 SHA와 자기 신고 플래그는 독립적인 거래소 출처 증명이 아닙니다.

## 해석 한계

- P21 입력은 이미 공개된 개발 기간의 결과이며, independent/forward/holdout 평가가 아닙니다.
- 가상 청산 값은 마지막 종가와 저장된 비용으로 계산한 **모델값**입니다. 다음 어느 것도 아닙니다:
  - 실제 체결이나 확정손익
  - 유동성 보장
  - 청산 가능 금액의 하한 보장
  - 수익성 입증
- 가상 매도는 fills, SELL 수, 승률에 넣지 않습니다.
- 끝 시점 한 점만으로 MDD를 재계산하지 않습니다(`mdd_recomputed=false`).
- 재수집, 시세 조회, 재백테스트, 새 전략, 파라미터 탐색은 하지 않습니다. P19/P20/P22/raw CSV/PAPER 파일은 입력으로 받지 않습니다. P22는 이번 범위가 아닙니다.
- 실제 주문, PAPER 재실행, 네트워크 호출, 자동 실행은 없습니다. `REAL_TRADING=False`이며 추가 지출은 0원입니다.
