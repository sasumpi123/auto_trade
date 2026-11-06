# P32 기존 Consensus 2/3의 P17 비용 반영 개발 비교

이슈 #67. 새 전략이나 첫 검증이 아닙니다. P11 `strategy_comparison`에서 이미 연구한 기존 `strategies.signals.ConsensusStrategy(quorum=2)`을 **그대로** 가져와, P17 PAPER 체결 경로와 P9 회계 조건에서 Legacy·고정 44bps와 한 번 비교하도록 연결했습니다. 운영 채택은 별도로 판단합니다.

## 실행 (저장소 루트에서)

`P19_ROOT`에는 기존 P19 수집 루트(`analysis/results.json`과 `data/`가 있는 폴더)를 **정확히** 지정하세요. 후보가 여러 개면 임의로 고르지 말고 어느 수집본인지 먼저 확인한 뒤 지정합니다.

<!-- P32_COMMAND_BEGIN -->
```bash
P19_ROOT="$HOME/research/p19"
P32_OUT="$HOME/research/p32-$(date +%Y%m%d-%H%M%S)"
if test -z "$(git status --porcelain)"; then PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p32_consensus_cost_comparison --input "$P19_ROOT" --output "$P32_OUT"; else echo "STOP: local changes in checkout"; false; fi
```
<!-- P32_COMMAND_END -->

- 변수 대입과 실행 줄은 분리되어 있습니다. 로컬 변경이 있으면 STOP하고 nonzero로 끝납니다.
- 출력은 새 비공개 디렉터리에 `results.json`, `comparison.csv`, `REPORT.md`로 남습니다.
- 표준출력은 최대 8줄입니다. 상태 1줄, 짝별 최대 6줄, 한계 1줄로 구성됩니다.
- 종료코드는 `VERIFIED`/`VERIFIED_PARTIAL`이면 0이고, 그 밖에는 2입니다.
- 결과가 모두 무거래이면 `evidence=NO_TRADES_ONLY`로 표시합니다. 이를 전략 성공으로 보고하지 않습니다.

## 고정 범위

- **입력:** 기존 P19 개발 자료만 사용합니다.
  - 기간은 UTC 2026-10-30/31, 11-01이고 종목은 KRW-BTC/KRW-XRP입니다. 최대 6개 입력입니다.
  - 검증은 기존 `p20.inputs`와 P21의 일별 plan/manifest/CSV·품질 검증을 그대로 씁니다.
  - 정상 `PARTIAL_REAL_DATA`의 유효한 부분 입력은 허용합니다. 누락 분봉은 격리하며, 보간·재수집·대체는 하지 않습니다.
  - P22 자료는 입력으로 받지 않습니다.
- **정책:** 정확히 3개이며, 최대 18 정책 케이스입니다.
  1. `legacy`: `p21.baseline_case`. 저장된 P19 case와 전 항목이 정확히 일치하는지 다시 확인합니다.
  2. `entry_bandwidth_44bps`: `p21.candidate_case`.
  3. `consensus_2of3`: `ConsensusStrategy(quorum=2)`를 기존 `legacy_diagnostic.simulate(strategy=...)`에 주입합니다. 44bps 필터는 결합하지 않으며, quorum 3·신호 조합 탐색은 없습니다.
- **공통 조건:** P19 `case`와 동일합니다(`strategy_case`). 아래 값은 바꾸지 않습니다.
  - 비용 5/5/2bps, 시작자금 100만 원
  - 1분봉, 200봉 warmup, 완료 봉 신호 → 다음 분 open 체결
  - 300초 cooldown, 거래량 0.5, 40% sizing
  - 익절·손절·1회 물타기·최소주문
- **Consensus 신호 규칙:** 같은 방향 2표 이상이고 반대표가 0이면 BUY/SELL, 그 밖에는 HOLD입니다. target은 없습니다. Legacy target을 보충하지 않습니다.
- **검증:**
  - 세 정책 모두 data hash, 완료·체결 window, warmup, 평가 시점이 일치해야 합니다(`P32_COMPARISON_WINDOW_MISMATCH`).
  - P9 대사는 `p21.compact`가 수행합니다: 순실현 + 미실현 = equity − 초기자금, 수수료·잔여 원가·수량 일치.
  - 끝나면 입력 해시를 다시 검증합니다.
- **평가 값:**
  - 종가 평가와 가상 terminal-exit(P28 `exit_audit`)을 구분해 기록합니다. 실제 강제 매도는 하지 않습니다.
  - 현금 100%는 0%입니다.
  - BH40은 P29 `buy_hold_40`으로 계산하며, P19 저장값과 일치하는지 검증합니다.
  - 차이는 같은 평가 기준끼리만 계산합니다: vs legacy, vs cash, vs BH40. NO_TRADES는 N/A입니다.
  - 날짜·종목별 수익률을 합산하거나 포트폴리오·복리 수익으로 계산하지 않습니다.
- **실행 제약:**
  - 기존 `p20.analyze` worker를 그대로 씁니다. 600초 상한과 기존 자식 정리를 따르며, 새 runner나 process-group 구현은 없습니다.
  - 네트워크 호출은 0입니다.
  - 새 출력 경로만 허용하고, 입력·계좌·백업·checkout은 바꾸지 않습니다.
- **실패 처리:**
  - 실패, 대사 불일치, 예산 초과는 INCOMPLETE와 nonzero 종료로 처리합니다.
  - 완료된 정책 결과는 보존하되, 성공으로 승격하지 않습니다.

## 해석 한계

- P19 개발 표본입니다. 독립·전방·holdout 검증이 아니며, 새 사전등록 합격 기준도 아닙니다.
- 유리한 결과가 나와도 수익성 입증이나 실거래 전환의 근거가 아닙니다. 그렇게 쓰려면 새 사전 고정 평가가 먼저 필요합니다.
- P22 고정 protocol/registration/runner/20개 소스/pin/격리 실행기, 운영 전략, 기본 설정은 바꾸지 않았습니다.
- `REAL_TRADING=False`, 추가 지출 0원입니다.
