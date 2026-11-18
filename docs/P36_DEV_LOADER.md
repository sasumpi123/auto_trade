# P36 개발 구간 로더 (Phase 1 준비)

이슈 #85. P36 스냅샷(`P36_HISTORY_SNAPSHOT_V2`)의 월별 gzip CSV에서 **개발 구간만** 읽는 오프라인 로더입니다.
- P35 러너는 P22/P33 일별 fold 구조를 읽습니다.
- `backtesting/development_data.py`는 단일 gzip의 접두부를 읽습니다.
- 그래서 둘 다 P36의 월 파일 형식에는 그대로 쓸 수 없습니다.

**이 작업이 낳을 판단:** 후속 Phase 1 작업에 넘길 수 있는 읽기 인터페이스가 준비됐는가.

**판정하지 않는 것:** 실데이터 품질, 분할 확정, P35 연결·등록, walk-forward 평가, holdout 평가. Mac 실수집(#76)은 별도로 보류 중입니다.

## 인터페이스

```python
from backtesting.p36_dev_loader import load_development
frame, report = load_development(SNAPSHOT_ROOT, 'KRW-BTC', 60, '2024-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')
```

**입력**
- 명시적 스냅샷 루트
- 종목: `KRW-BTC`/`ETH`/`XRP`/`SOL`
- 주기: 1/15/60분
- 구간: UTC 반열린 구간 `[start, end)`. 경계는 주기 격자에 맞아야 합니다.

**`frame`**
- 요청 구간의 저장된 캔들만, UTC 오름차순으로 반환합니다.
- 열은 `open`, `high`, `low`, `close`, `volume`입니다.
- 누락을 채우거나, 보간·리샘플하거나, 기간을 고르지 않습니다.

**`report`**
- `status`: `LOADED`, 또는 `NO_DEVELOPMENT_ROWS`(계열이 `NO_ROWS_RETURNED`이거나 개발 월이 없음)
- `rows`, `first_observed_bar`, `before_first_observed`
- `expected_rows_from_first_observed`, `missing_bars`
- `empty_months`: 첫 관측 이후 구간에서 파일이 없는 월
- `holdout_read`: 항상 `false`
- `note`

**해석 주의**
- 로드 성공이나 스냅샷의 `COMPLETE` 상태는 **데이터 품질 합격, 분할 결정, 전 기간 확보의 증거가 아닙니다**.
- 누락 봉은 무거래 분일 수도 API 누락일 수도 있습니다. 구분할 수 없으므로 개수만 보고합니다.
- `NO_DEVELOPMENT_ROWS`와 빈 월은 데이터 확보도, 정상 연속성도 아닙니다.

## 검증 순서 (fail-closed)

1. **루트**
   - `p36.root_path(new=False)` → 기존 `p19_baseline.safe_path`를 그대로 적용합니다. symlink, 저장소 내부, 계좌·holdout·운영 경로를 거절합니다.
   - 다른 스냅샷 하위 경로도 거절합니다.
   - 차단 코드: `P36D_ROOT_FORBIDDEN`
2. **메타데이터 (캔들 바이트를 읽지 않음)**
   - schema와 고정 계획: `P36D_PLAN_INVALID`
   - 상태가 `COMPLETE_API_BOUNDARY_REACHED`인지: `P36D_SNAPSHOT_NOT_COMPLETE`
   - budget 형식과 범위 `0 ≤ budget ≤ plan.max_requests`(P36 verifier와 같은 상한, 리뷰 5951924433): `P36D_BUDGET_INVALID`
   - 12개 계열, 요청·응답 수: `P36D_MANIFEST_INVALID`
   - P36 단일 성공 규칙 `completion()`(종료 증거, 요청 회계): `P36D_COMPLETION_UNVERIFIED`
3. **종목·주기와 구간**
   - 종목·주기: `P36D_SERIES_INVALID`
   - 구간 형식: `P36D_WINDOW_INVALID`
   - `end > holdout_start`이면 **캔들 파일을 하나도 열기 전에** `P36D_HOLDOUT_OVERLAP`으로 거절합니다.
4. **최초 관측 봉 (리뷰 5951924433)**
   - 최초 개발 월은 요청 구간 밖에 있어도 **그 파일을 열어 검증한 뒤** 실제 첫 봉을 `first_observed_bar`로 씁니다. holdout이 아닌 개발 월입니다.
   - 계열의 `boundary_to`도 그 봉과 같아야 합니다(`P36D_RECORD_INVALID`).
   - 검증되지 않은 기록값으로 누락 수를 계산하지 않습니다. 결과 행 수가 기대 행 수를 넘으면 `P36D_COUNT_INCONSISTENT`로 거절하므로, 음수 누락은 나오지 않습니다.
5. **개발 월 (요청 구간과 겹치는 월 + 최초 개발 월)**
   - 월 키가 `YYYY-MM`(01–12)인지 날짜 해석 전에 확인합니다: `P36D_RECORD_INVALID`
   - 레코드가 정확한 키 집합이고 경로가 정규 경로 `development/<MARKET>/<unit>m/YYYY-MM.csv.gz`인지, 그리고 `earliest_observed_month`가 최초 개발 월에만 참인지: `P36D_RECORD_INVALID`
   - `p36.inside()`로 경로 이탈·symlink 확인: `P36D_PATH_FORBIDDEN`
   - 파일 존재·크기 상한: `P36D_FILE_MISSING`
   - SHA-256: `P36D_HASH_MISMATCH`
   - 정확한 헤더: `P36D_HEADER_INVALID`
   - UTC 오프셋 0 시각: `P36D_TIMESTAMP_INVALID`
   - 행 수: `P36D_ROW_COUNT_MISMATCH`
   - 중복·역순: `P36D_DUPLICATE_OR_UNORDERED`
   - 주기 격자·월 경계: `P36D_GRID_OR_MONTH_INVALID`
   - 기존 `quality_report`로 OHLCV 검사(유한 양수, 범위): `P36D_OHLCV_INVALID`
   - **기록된 품질 전체를 P36의 `month_quality()`로 다시 계산**해 기록값과 비교합니다: `P36D_QUALITY_MISMATCH`
     - 대상: rows, expected_rows, missing_bars, longest_gap_bars, 첫/마지막 봉, dataset hash
     - 최초 월은 실제 첫 봉부터, 그 외 월은 1일 00:00 UTC부터 계산합니다(리뷰 5951924433).
     - 해시가 맞아도 품질 기록만 바뀌면 거절합니다.

**하지 않는 것**
- `holdout/`은 나열하거나 열거나 해시하지 않습니다.
- holdout 바이트까지 읽는 P36 전체 `verify()`는 호출하지 않습니다.
- 잠금, 임시 파일, manifest 갱신 등 쓰기가 없고, 네트워크 호출도 없습니다.

## 합성 검증 (`tests/test_p36_dev_loader.py`, 14개)

스냅샷은 P36 수집기와 Fake HTTP로 만듭니다. 소켓 연결은 테스트를 실패시킵니다.

**양성**
- 개발 구간 전체를 원본 봉과 정확히 같게, 오름차순으로 반환합니다(누락 2봉 보고).
- 월 경계를 넘는 구간과 부분 구간을 읽습니다. 1분 누락은 채우지 않습니다.
- 빈 3월은 `empty_months`로 보고하고 행이 없습니다.
- 빈 계열과 holdout만 있는 계열은 `NO_DEVELOPMENT_ROWS`입니다.

**holdout 격리** (`open`, `Path.read_bytes`, `gzip.open`, `os.open`, `os.scandir`, `os.listdir` 감시)
- 12개 계열을 개발 구간 끝까지 읽을 때와 경계를 넘는 요청 3가지에서 holdout 경로 접근이 0회입니다.
- 겹치는 요청은 `.csv.gz`를 하나도 열기 전에 거절됩니다.
- 스냅샷 파일의 바이트와 mtime이 바뀌지 않습니다(쓰기 없음).

**차단**
- 해시, 행 수, 헤더, OHLCV, 역순, 중복, 격자 이탈, tz 없는 시각, 품질 기록 불일치, 경로 이탈
- 미완료 스냅샷, 계획 변조, 요청 회계 불일치, 계열 누락, 응답 0회, schema 변조
- 잘못된 종목·주기·구간
- 계좌 표지 경로, symlink 루트, symlink 종목 디렉터리. 외부 파일 바이트는 바뀌지 않습니다.

**리뷰 회귀 (5951924433)와 같은 종류의 점검**
- 상한 초과: budget과 manifest를 함께 120,001로 맞추고 차이를 미확인으로 돌려도 `P36D_BUDGET_INVALID`입니다. 정확히 상한(120,000)은 허용합니다.
- 최초 관측 봉 위조: 1월·3월 봉이 있는 스냅샷에서 1월 기록의 `first_bar`와 `boundary_to`만 4월 1일로 바꾸고 3월을 읽으면 거절됩니다(CSV 불변). 정상일 때는 `missing_bars=0`이고, 최초 봉은 실제 1월 봉입니다.
- 품질 기록 위조: `expected_rows`, `missing_bars`, `longest_gap_bars`, dataset hash, rows를 하나씩 바꾸거나, 앞의 셋을 함께 바꿔도 거절됩니다. 해시는 정상인 상태입니다.
- 일관성: 최초 월이 아닌 월에 `earliest_observed_month=True`, `2026-13` 월 키, 첫 봉과 다른 `boundary_to`를 거절합니다.

**경계**
- 로더가 `verify`나 쓰기 함수(잠금, 수집, 재개, transport)를 호출하지 않습니다.
- P22/P33 고정 상태는 그대로입니다.

## 한계

- 합성 스냅샷으로만 검증했습니다. 실제 P36 수집본은 아직 없습니다(#76 Mac 실수집 보류).
- 파일 해시는 스냅샷 내부의 일관성만 확인합니다. 거래소 출처를 증명하지 않습니다.
- 계열 하나, 구간 하나를 읽는 인터페이스입니다. fold 구성, 다종목 정렬, P35 입력 결속은 후속 이슈에서 다룹니다.
- `REAL_TRADING=False`, 추가 지출 0원입니다. 실데이터 조회와 평가는 없습니다.
