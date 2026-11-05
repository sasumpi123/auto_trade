# P30 P22 결과 보조 감사 (사후 추가 기술 통계)

이슈 #63. P28(가상 청산 비용)과 P29(현금 100%·매수보유 40%) 계산을, 이미 사전 고정된 **P22 평가의 저장된 결과**에 적용하는 별도 입력 어댑터입니다.

## 성격: POST_REGISTRATION_SUPPLEMENT_DESCRIPTIVE_ONLY

- 이 지표들은 P22 기간이 시작된 뒤에 추가한 사후 기술 통계입니다.
- 원래 사전 등록 지표나 완료 판정과는 별개입니다.
  - P22 `status`, 완료·제외·미완료 정보는 `original_p22`에 그대로 옮겨 적고 바꾸지 않습니다.
  - `retroactive_success_criterion=false`입니다. 소급해서 사전 등록한 것처럼 주장하지 않습니다.
- 실제 P22 수집과 분석은 기존 계획 그대로 진행합니다. 2026-11-06 09:00 KST 이후, 격리 실행기(`tools/p22_isolated.py`)를 사용자가 수동으로 실행합니다. 이 도구는 조기 수집, 예약, 자동 실행을 승인하지 않습니다.

## 동작과 경계

새 비고정 CLI는 `backtesting/forward_result_audit.py`입니다.

**실행하지 않는 것**
- P22 `frozen()`, collect/analyze, P22 엔진, 전략
  - 현재 작업 트리에서는 P25의 등록된 drift 때문에 `frozen()`이 의도대로 차단됩니다. 이 검사는 우회하거나 완화하지 않습니다.
- monkeypatch 같은 우회 수단

**대신 확인하는 것**
- 고정 상수(`PROTOCOL_SHA`, `REGISTRATION_SHA`, `FREEZE_COMMIT`)를 정적 내용 pin으로 대조합니다.
  - 현재 트리의 protocol/registration 파일 SHA
  - P22 러너 파일 SHA(`runner_sha()`)
- 입력 파일에 기록된 SHA를 위 pin과 비교합니다.

**재사용하는 것**
- `p22_forward.inputs()`: 수정 없이 비공개 스냅샷에 대해 실행합니다.
  - 대상: collection/protocol/registration, 레지스트리, 시각, 호출 수
- `p22.work()`와 같은 일별 검증
- P28 `exit_audit`/`exit_values`
- P29 `_copy`/`unchanged`(경로 전체 검증과 bounded 읽기), `compare`, `flags`, `buy_hold_40_core`
  - P29는 `buy_hold_40`의 계산 본체만 `buy_hold_40_core`로 분리했습니다. P29 출력과 검증은 동일하고 테스트로 보존했습니다.

**고정 파일**
- P22 protocol/registration/runner/등록 소스/pin/격리 실행기와 P19/P20/P21 고정 소스는 변경하지 않았습니다.
- P19 날짜 상수도 바꾸지 않았습니다.

## 실행 명령 (저장소 루트에서)

경로 세 줄을 실제 위치로 바꾼 뒤, 블록 전체를 그대로 복사해 실행합니다.

<!-- P30_COMMAND_BEGIN -->
```bash
P22_ROOT="$HOME/research/p22-collection"
P22_RESULTS="$HOME/research/p22-analysis/results.json"
P30_OUT="$HOME/research/p30-$(date +%Y%m%d-%H%M%S)"
if test -z "$(git status --porcelain)"; then PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.forward_result_audit --p22-root "$P22_ROOT" --results "$P22_RESULTS" --output "$P30_OUT"; else echo "STOP: local changes in checkout"; false; fi
```
<!-- P30_COMMAND_END -->

**명령 작성 규칙**
- 변수 대입과 실행 줄은 분리합니다.
- 체크아웃에 로컬 변경이 있으면 STOP하고 종료코드가 0이 아닙니다.
- 검토한 소스인지는 PR 인계 명령에서 blob SHA로 확인합니다.

**실행 시각과 종료코드**
- `2026-11-06T00:00:00Z` 이전에는 즉시 `NOT_READY`로 끝나며 종료코드는 2입니다.
  - 경로 검사, 입력 읽기, 출력 생성, 네트워크, 대기를 모두 하지 않습니다.
  - 시간을 주입하거나 합성 입력을 허용하는 CLI 옵션은 없습니다.
- 성공하면 `SUPPLEMENT_AUDITED` 또는 `SUPPLEMENT_AUDITED_PARTIAL`이고 종료코드는 0입니다. 그 밖의 결과는 모두 2입니다.

**출력**
- `results.json`, `supplement.csv`, `REPORT.md`
- 표준출력 요약은 최대 8줄이며 경로와 오류 원문은 출력하지 않습니다.

**테스트(`CommandTests`)**
- 문서 블록 전체를 일회용 git 사본에서 bash로 실행합니다.
- 실제 시각에는 `NOT_READY`, 출력 없음을 확인합니다.
- 테스트 전용 shim에서만 시각과 합성 출처를 주입해 다음을 검사합니다.
  - 변수 unset/stale 상태의 경로 전달
  - 8줄 요약과 종료코드
  - 로컬 변경 시 STOP

## 입력 검증 (fail-closed)

**P22 결과**
- schema `P22_ANALYSIS_V1`, status `VERIFIED`/`VERIFIED_PARTIAL`이어야 하며 `failure_reason`이 없어야 합니다.
- protocol/registration/runner SHA와 `freeze_commit`이 고정 값과 일치해야 합니다.
- `collection_sha256`이 스냅샷의 collection.json SHA와 같아야 합니다.
- `input_issues`와 `unfinished_policies`는 비어 있어야 합니다.
- 최대 6짝, 12정책입니다.
- `cases ∪ excluded`가 정확히 UTC [2026-11-03,2026-11-06) × BTC/XRP여야 합니다.
- `completed_policies`는 다음을 만족해야 합니다.
  - 키 집합 일치, `evaluation_context` 7개 키
  - 두 정책의 context와 `data_sha256` 동일
  - result가 case의 baseline/candidate와 동일
- 짝의 status/delta/interpretation은 `p21.comparison`과 P22의 `TIME_SEPARATED_COMPARISON`/해석 문구로 재계산해 대조합니다.
- 정책 결과마다 P28과 같은 Decimal 회계 항등식과 5/5/2 비용을 검사합니다. `p9_reconciled` 플래그는 그대로 믿지 않습니다.

**수집본**
- 각 원본 파일을 열기 전에 `safe_path` 전체 검사를 거칩니다(P29 반영). 루트뿐 아니라 내부 data/CSV의 symlink와 계좌·holdout·runtime 표시도 확인합니다.
- bounded 스냅샷을 만든 뒤 다음을 검사합니다.
  - `p22.inputs`(collection status는 COLLECTED/PARTIAL_REAL_DATA만 허용)
  - INCOMPLETE 표지
  - 일별 manifest SHA = 레지스트리 값
  - plan 기간
  - `load_partitioned_sample(quarantine_gaps=True)`와 시작 시각 ≥ END, 호출 수
  - `input_hashes[day]` 일치
- 격리된 시장은 같은 `quality`로 제외되어 있어야 합니다. 제외 목록은 보존합니다.

**짝 단위**
- `dataset_hash(validate_candles(frame))`와 행 수가 `evaluation_context`와 같아야 합니다.
- 두 정책의 `mark_price`/`mark_at_utc`가 마지막 종가, 마지막 봉+60초와 같아야 합니다.

**마무리 검사**
- 원본 수집본과 결과 파일을 다시 bounded 해시해 스냅샷과 비교합니다(`P30_INPUT_CHANGED`).

## 계산

**Legacy/44bps**
- P28 `exit_audit`로 원래 종가 평가와 가상 청산 후 평가를 나란히 계산합니다.
  - 매도에 드는 추가 비용만 반영하고, 과거 비용을 다시 빼지 않습니다.
  - NO_TRADES는 N/A로 둡니다.
- 원래 손익·fills·fees·MDD는 바꾸지 않습니다.

**현금 100%**
- 0%로 정의한 기준선입니다.

**매수보유 40% (BH40)**
- P22는 BH40을 저장하지 않으므로 **새로 계산한 보조값**입니다(`source=NEW_SUPPLEMENTAL_CALCULATION_P22_STORES_NO_BH40`). 저장된 기준선과 일치하는지 검증한 값처럼 주장하지 않습니다.
- 진입 시각은 P22 `evaluation_context.execution_first_open`(공통 최초 체결 가능 시가)입니다. 후보 신호와 무관합니다.
- 일회용 P17 연구 엔진으로 수수료 포함 400,000원을 매수합니다.
  - 엔진 수량이 독립 공식 `q=400000/(1+f)/BUY(open)`와 다르면 `P30_BH40_FORMULA_MISMATCH`로 차단합니다.
- 마지막 종가로 평가하고, 별도로 가상 청산 후 값도 계산합니다.

**비교**
- 같은 평가 기준끼리만 vs cash, vs BH40 pp를 계산합니다.
- 합산, 복리, 순위, 통계적·위험조정 우위, 자동 채택은 하지 않습니다.

## 상한과 한계

- 최대 60초이며, 단계 사이마다 deadline을 검사합니다(`P30_TIME_LIMIT`). 중단 시 `P30_INTERRUPTED`로 끝납니다.
- 네트워크, 수집, replay, 감시기, DB를 사용하지 않습니다.
- BH40 계산은 현재 트리의 P17 연구 엔진으로 합니다. 고정된 P22 엔진 실행이 아니며, 공식 대조로만 검증합니다.
- 경로 검사와 실제 open 사이에는 TOCTOU 구간이 남습니다. 마지막 재해시로 보완합니다.
- 단계 내부의 파싱은 deadline으로 중단할 수 없습니다. 크기 상한으로 제한합니다.
- 과거 합성 fixture의 성공을 실자료 성과로 해석하지 않습니다.
- `REAL_TRADING=False`, 추가 지출 0원입니다.
