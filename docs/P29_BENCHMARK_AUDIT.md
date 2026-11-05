# P29 현금·40% 매수보유 기준선 비교 (오프라인)

이슈 #61. 상위 이슈 #7 가운데 "동일 기간·비용 기준선 비교" 항목만 다룹니다.

## 문제

- `p19_baseline.buy_hold()`는 매수보유 40% 수익률을 계산해 P19 `metrics.buy_hold_40_net_pct`에 저장합니다.
- 그러나 P21 비교 보고서(`compact()`)와 P28 감사 결과는 이 기준선과 현금 보유를 함께 보여주지 않습니다.
- 그래서 후보가 Legacy보다 나아도 현금이나 같은 기간 고정 노출보다 나은지는 별도로 확인해야 했습니다.

## 동작

새 비고정 도구 `backtesting/benchmark_audit.py`가 다음 네 가지를 같은 조건에서 비교합니다.

- 비교 대상: Legacy, 고정 44bps, 현금 100%, 매수보유 40%
- 같은 조건: 같은 일·종목, 같은 P19 공통 평가 구간, 같은 5/5/2bps 비용

P19/P20/P21/P22 실행기·전략·회계·프로토콜·pin·격리 실행기는 수정하지 않았습니다. P28 모듈은 순수 계산 `exit_values()`를 분리해 재사용합니다. P28의 동작은 바뀌지 않았고 기존 테스트가 그대로 통과합니다.

## 실행 명령 (저장소 루트에서)

경로 세 줄을 실제 위치로 바꾼 뒤, 아래 블록 전체를 그대로 복사해 실행합니다.

<!-- P29_COMMAND_BEGIN -->
```bash
P19_ROOT="$HOME/research/p19"
P21_RESULTS="$HOME/research/p21-output/results.json"
P29_OUT="$HOME/research/p29-$(date +%Y%m%d-%H%M%S)"
if test -z "$(git status --porcelain)"; then PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.benchmark_audit --p19-root "$P19_ROOT" --p21-results "$P21_RESULTS" --output "$P29_OUT"; else echo "STOP: local changes in checkout"; false; fi
```
<!-- P29_COMMAND_END -->

- **변수 대입 분리:** 변수 대입은 실행 줄과 분리합니다(P28 리뷰 반영). 한 줄로 쓰면 bash가 실행 줄의 인자를 대입 전 값으로 확장합니다.
- **로컬 변경 차단:** 추적 파일이나 추적되지 않은 파일에 변경이 있으면 실행하지 않고 STOP을 출력하며, 종료코드는 0이 아닙니다.
- **검토 소스 확인:** PR 인계 명령에서 정확한 blob SHA로 확인합니다. squash 병합에서는 검토한 Head가 master의 조상이 된다고 가정하지 않습니다.
- **테스트:** `CommandTests`는 이 블록 전체를 bash로 실행하며, 인터프리터 경로만 치환합니다. 실행 환경은 checkout의 일회용 git 사본이고, 다음 세 경우를 확인합니다.
  - 두 변수가 없는 경우
  - 이전 값이 남아 있는 경우
  - 로컬 변경이 있는 경우
- **입력:**
  - 기존 P19 루트: `analysis/results.json`, `data/<day>/plan.json`, `manifest.json`, CSV
  - 그 P19에서 만든 P21 `results.json`
- **출력:** 새 비공개 디렉터리에 다음 파일을 씁니다.
  - `results.json`: 전체 Decimal 값, 입력 해시, 구간, 환경
  - `benchmark.csv`
  - `REPORT.md`
- **종료코드:** `BENCHMARKED` 또는 `BENCHMARKED_PARTIAL`이면 0, 그 밖에는 2입니다.
- **표준출력:** 최대 8줄입니다(상태 1줄, 짝당 1줄씩 최대 6줄, 해석 한계 1줄). 경로·오류 원문은 출력하지 않습니다.

## 입력 검증과 공통 구간

1. **bounded 스냅샷.** P19 원본 파일은 `terminal_exit_audit.read_bounded`로 읽어 비공개 임시 스냅샷에 복사합니다.
   - 대상: `analysis/results.json`, 필요한 날짜의 plan/manifest/CSV
   - 읽기 상한: results 16 MiB, 그 밖의 파일은 `minute.MAX_FILE_BYTES`. 파일이 읽는 중에 커져도 이 상한을 넘겨 읽지 않습니다.
   - 기존 helper는 이 스냅샷만 읽습니다: `p20.inputs`, `p20.metadata`, `minute.load_partitioned_sample(quarantine_gaps=True)`.
   - 끝나면 원본을 다시 bounded로 해시해 스냅샷과 같은지 확인합니다(`P29_INPUT_CHANGED`).
2. **P21 결과 검증.** P21 결과는 P28 `load`와 `check_report`로 검증합니다.
   - 형식/중복/충돌/비용 5/5/2/P9 숫자 대사/짝 상태를 확인합니다.
3. **P19–P21 연결.** 다음이 서로 정확히 일치해야 합니다.
   - P21의 `p19_results_sha256`와 스냅샷 P19 results SHA
   - P21의 `protocol_sha256`와 현재 protocol
   - P21의 `input_hashes[day]`(manifest/plan/선택 CSV SHA)와 이번에 계산한 값
   - P21 짝 집합과 P19 5/5/2 primary 집합, P21 제외 목록과 P19 제외 목록
   - 같은 날짜라는 이유만으로 다른 수집본을 섞지 않습니다.
4. **짝별 구간 검증.**
   - P19 `input_quality`와 manifest를 대조합니다.
   - `validate_candles(frame,60)`의 `dataset_hash`가 P19 `data_sha256`과 같아야 하고, 행 수도 같아야 합니다.
   - P21 두 정책의 `mark_price`는 프레임의 마지막 종가와 같아야 하고, `mark_at_utc`는 마지막 봉 시작+60초와 같아야 합니다.
5. **공통 진입 시점.** 매수보유 40%는 P19의 공통 `execution_first_open`에 진입합니다.
   - 이 값은 첫 평가 창 다음 시가입니다(`legacy_diagnostic.simulate`의 `frame.index[min(seen)+1]`). 정책·후보 신호·첫 BUY와 무관합니다.
   - 값이 없으면 기준선은 `NA_NO_COMMON_EXECUTION_OPEN`입니다. 이때 P19 저장값도 None이어야 합니다.
   - 날짜 끝이나 다음 봉을 임의로 추가하지 않습니다.

## 기준선과 계산

- **현금 100%:** 초기자금 1,000,000원, 이자 0, 수수료 0, 거래 0으로 정의한 0% 기준선입니다. 전략 NO_TRADES의 N/A와는 다른 값입니다.
- **매수보유 40%:** `p19_baseline.buy_hold`와 같은 경로로 계산합니다.
  - 일회용 연구용 `P17ResearchEngine`(임시 0700 디렉터리)로 공통 시가에 400,000원 **수수료 포함 예산**으로 `buy`를 1회 실행합니다. 나머지 600,000원은 현금으로 둡니다.
  - 매수 수량은 `q = 400000/(1+f)/BUY(open)`이고, `BUY(open) = open*(1+(slip+spread/2)/10000)`입니다. 매수 수수료는 `400000/(1+f)*f`입니다.
  - 종가 평가는 `600000 + q*close_last`입니다.
  - 이 종가 평가 수익률은 P19 `buy_hold_40_net_pct`와 일치해야 합니다(`P29_BH40_MISMATCH`). 다른 공식으로 기준선이 조용히 달라지지 않게 하기 위함입니다.
  - 가상 청산 후 값은 P28 `exit_values`를 같은 CostModel로 적용합니다. 슬리피지와 반스프레드를 반영하고, 매도 수수료는 체결대금 기준입니다.
  - 가상 청산은 fills·SELL·확정손익에 넣지 않습니다(`hypothetical_sell_counted_as_fill=false`).
- **Legacy/44bps:** P28 `exit_audit` 결과를 씁니다. 종가 평가와 가상 청산 후 평가를 함께 제시하고, 과거 비용은 다시 빼지 않습니다.
- **비교 원칙:**
  - 정책별 `*_vs_cash_pp`와 `*_vs_bh40_pp`는 같은 평가 기준끼리만 계산합니다(종가끼리, 청산 후끼리).
  - 전략 NO_TRADES이거나 기준선이 N/A이면 비교 차이도 N/A입니다.
- **flags (`exit`/`marked`):**
  - `candidate_better_than_legacy`
  - `candidate_not_above_cash`
  - `candidate_not_above_bh40`
  - `candidate_negative_return`
  - 음수 전략이 더 크게 하락한 기준선보다 나았다고 해서 흑자나 수익성으로 표시하지 않습니다.
- **노출·거래 횟수:** 다르다는 점을 명시합니다.
  - 전략: `policies[].fills`, `time_held_pct`, `mean_capital_exposure_pct`
  - 매수보유 40%: BUY 1회, 400,000원을 마지막 종가까지 고정 보유
- **허용오차:** `services.notification_metrics.close`를 씁니다. 기존 P19/P21 대사 기준과 같습니다.

## 실패 처리

- 고정 reason을 사용하고 종료코드 2로 끝납니다.
- 실패 시 `results.json`에 `INCOMPLETE`를 기록하고 `INCOMPLETE` 표지를 유지합니다. 성공용 CSV/MD는 남기지 않습니다.
- 다음을 모두 같은 방식으로 실패 처리합니다.
  - P21 미완료/손상/중복/비용·회계 불일치(P28 reason)
  - 연결 해시 불일치, 구간·mark 불일치, 매수보유 40% 불일치
  - 원본 변경, 크기 초과, 기존 출력, symlink, 계정/백업 경로
  - 60초 초과(`P29_TIME_LIMIT`, 단계 사이 검사), 중단(`P29_INTERRUPTED`)
- 부분 결과는 `BENCHMARKED_PARTIAL`로만 표시하고 제외 목록을 보존합니다.

## 상한과 한계

- 최대 6짝, 전체 60초 이내입니다.
- 네트워크, 재수집, 원전략 replay, 파라미터 탐색, subprocess, 감시기는 없습니다.
- 계산 중 생성하는 계좌는 짝마다 일회용 P17 연구 엔진 1개뿐이며 임시 디렉터리에서 삭제됩니다. 실제·기존·HALTED·P26 계좌는 열지 않습니다.
- 한 번의 JSON/CSV 파싱 안에서는 deadline으로 중단할 수 없습니다. 파일 크기 상한으로 이를 제한합니다.
- P19/P21은 이미 공개된 개발 기간의 결과입니다. independent/forward/holdout 평가가 아닙니다.
- 짝별 차이는 통계적 우위나 위험조정 우위가 아닙니다. 순위, 종목·기간 합산, 연속 3일 포트폴리오, 복리 수익률로 표시하지 않습니다.
- 매수보유 40%와 전략은 노출과 거래 횟수가 다릅니다.
- 가상 청산 값은 실제 체결이나 청산 금액의 하한 보장이 아닙니다. 수익성을 입증하지 않습니다.
- `REAL_TRADING=False`, 추가 지출 0원입니다. P22는 이번 범위가 아닙니다.
