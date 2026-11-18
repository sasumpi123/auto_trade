# P36 BTC·ETH·XRP·SOL 1/15/60분봉 다년치 과거 스냅샷 (holdout 분리)

이슈 #76. ROADMAP Phase 0의 "과거 데이터 먼저" 항목입니다.

**작업 질문:** 업비트 공개 분봉으로 KRW-BTC/ETH/XRP/SOL의 1·15·60분봉을 조회 가능한 전체 기간 동안 확보할 수 있는가? 자료의 누락·중복·정렬 품질이 연 단위 walk-forward에 쓸 수준인가?

**이 작업이 낳을 판단:** Phase 1 walk-forward 분할(어느 연도·주기를 개발/검증/holdout으로 쓸지)의 범위 설계.

**범위 밖:** 이 PR은 수집 도구만 제공합니다. 전략 평가, 분할 확정, 새 지표, 종목 추가는 하지 않습니다.

## 실행 (사용자 Mac, 수동 1회)

수집 명령:

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p36_history collect --output "$HOME/research/p36-history-$(date +%Y%m%d)" --connect-public
```

- 표준출력은 최대 8줄 요약입니다. 예시:
  - `P36 <상태> requests=<n>/120000 unconfirmed=<u> bounded=<k>/12 no_rows=<z>`
  - 고정 `end`와 holdout 경계
  - 주기별 조회 경계 도달 수, 최초 관측 월, 개발 구간 누락 봉, 빈 개발 월 수
  - 개발·holdout 월 수와 행 수
- 출력은 저장소 밖의 새 private 디렉터리입니다. 기존 경로는 덮어쓰지 않습니다.

나중에 요약만 다시 보는 명령(네트워크 0, 해시 재검증 포함):

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p36_history verify --output "$HOME/research/p36-history-YYYYMMDD"
```

중단됐을 때 사용자가 명시적으로 재개하는 명령:

```bash
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p36_history resume --output "$HOME/research/p36-history-YYYYMMDD" --connect-public
```

`plan` 명령은 고정 `end`, holdout 경계, 요청 상한만 출력합니다. 네트워크 호출과 파일 생성은 없습니다.

**예상 소요:** 1분봉이 대부분을 차지합니다.
- 상장 이후 전 기간을 가정하면 약 9만 회 요청이고, 초당 4회 기준 약 6~7시간이 걸립니다.
- 실제 상장일과 조회 가능 기간은 수집 전에는 알 수 없습니다. 위 수치는 추정입니다.

## 수집 범위와 방식

**대상과 기간**
- 종목: KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL
- 주기: 60 → 15 → 1분 순서(요청이 적은 계열부터)
- `end`: 첫 실행 시점이 속한 달의 1일 00:00 UTC(미포함)입니다. 진행 중인 달은 수집하지 않으므로 완료 봉만 저장합니다. 재개해도 `end`는 바뀌지 않습니다.

**수집 방식**
- `end`에서 과거 방향으로 `count=200`씩 페이지를 요청합니다(`to`는 미포함).
- **비어 있지 않은 응답은 길이와 무관하게** 가장 오래된 봉을 다음 `to`(미포함)로 삼아 계속 요청합니다. 짧은 페이지를 이력의 끝으로 보지 않습니다(리뷰 5949862643).
- **빈 응답만** 계열을 끝냅니다. 상태는 다음 둘 중 하나입니다.
  - `API_BOUNDARY_REACHED`: 봉을 받은 뒤 빈 응답이 옴. `boundary_before`에 마지막 cursor를 기록합니다.
  - `NO_ROWS_RETURNED`: 첫 응답부터 비어 있음. 확보한 계열로 세지 않습니다.
- 빈 응답은 **API 조회 경계**일 뿐입니다. 상장 시각이나 거래소 전 기간 확보의 증거로 표시하지 않습니다.

**재사용한 기존 코드**
- `collection.FIELDS`, `collection.quality_report`(월별 검증), `collection.utc`, `data.dataset_hash`
- `services.public_market`의 `PublicOnlyAuth`와 `positive`(인증 헤더·쿠키 제거, 유한 양수 검사)

**새로 만든 부분**
- 기존 `collection.py`, `historical.py`, `minute_study.py`는 P22/P33 고정 소스(code_sha256)에 포함되므로 **수정하지 않았습니다**.
- 기존 두 클라이언트는 이 작업에 쓸 수 없었습니다.
  - `PublicCandles`: 15/60분만 지원하고, 자동 재시도를 합니다.
  - `PublicMarketClient`: 1분 엔드포인트만 지원합니다.
- 그래서 같은 검증 규칙을 쓰는 단일 GET 클라이언트 `p36_history.Transport`를 새로 만들었습니다.

## 응답 검증 (복구 없이 거절)

**거절 코드**
- `P36_MARKET_OR_UNIT_MISMATCH`: 응답의 종목이나 주기가 요청과 다름
- `P36_INVALID_TIME`, `P36_OUT_OF_WINDOW_OR_GRID`: `candle_date_time_utc`가 naive UTC가 아니거나, 주기 격자를 벗어나거나, cursor 이전 범위를 벗어남
- `P36_DUPLICATE_CANDLE`: 중복 봉
- `P36_UNORDERED_PAGE`: 엄격한 내림차순이 아님
- `P36_INVALID_PAGE`: 200행 초과 또는 목록이 아님
- `P36_INVALID_NUMBER`: 비유한 값이나 음수 가격·거래량
- `P36_INVALID_RANGE`: OHLC 범위 불일치

**HTTP 수준 검사**
- 응답 크기 2MB 상한
- 중복 JSON 키, NaN/Infinity 거부
- 서버 `Date`와 로컬 시계 차이 5초 이내, `to` ≤ 서버 시각(`P36_CLOCK_UNVERIFIED`)
- 429는 `P36_RATE_LIMITED`, 그 밖의 비200 응답은 `P36_HTTP_ERROR`, 연결 실패는 `P36_REQUEST_FAILED`

정렬, 중복 제거, 보간, 대체, 리샘플링은 하지 않습니다.

## 요청 상한과 수동 재개 (P22/P33과의 구분)

이 작업은 **사전등록 전방 평가가 아닙니다.** 그래서 P22/P33의 "재수집 금지·일 40회 상한"은 적용 대상이 아닙니다. 대신 아래 제한을 둡니다.

**요청 제한**
- 요청 간격 0.25초 이상(초당 4회)입니다. 리뷰어가 [업비트 공식 분 캔들 문서](https://docs.upbit.com/kr/reference/list-candles-minutes)에서 확인한 IP당 캔들 그룹 공유 한도(초당 10회)보다 낮습니다. 같은 IP에서 다른 캔들 요청을 함께 보내면 공유 한도에 합산되므로, 수집 중에는 다른 시세 프로그램을 돌리지 않습니다.
- 스냅샷 전체 요청 상한은 **120,000회**입니다. 모든 재개 실행을 합친 값이며, 다시 채워지지 않습니다.
- **선차감:** 요청을 보내기 **전에** `budget.json`의 카운터를 1 올리고 fsync합니다. 프로세스가 강제 종료돼도 그 요청은 소비된 것으로 남습니다. 재개할 때 manifest에 기록되지 않은 소비분은 `unconfirmed_requests`로 보고하고 환급하지 않습니다.
- **단일 writer:** 스냅샷마다 `.lock`을 배타 잠금(`flock`, 비차단)으로 잡습니다. 이미 잠겨 있으면 `collect`, `resume`, `verify`가 네트워크 호출이나 파일 쓰기 전에 `P36_SNAPSHOT_LOCKED`로 거절됩니다. 잠금은 프로세스가 끝나면 OS가 풉니다.
- 상한에 도달하면 `REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED`이며, **전체 기간 확보로 보고하지 않습니다**.

**자동 재시도 없음**
- 429·5xx·검증 오류·네트워크 오류가 나면 즉시 `STOPPED_RESUME_MANUALLY`로 멈춥니다.
- Ctrl-C로 중단하면 `INTERRUPTED_RESUME_MANUALLY`입니다.
- 자동 재시도, 재접속, 예약은 없습니다.

**수동 재개(`resume`)와 `verify`는 먼저 다음을 검증합니다** (리뷰 5949862643)
- 고정 계획이 같은지(`P36_PLAN_CHANGED`: end, holdout, 상한, 간격)
- `budget.json`이 유효한지(`P36_BUDGET_INVALID`)
- 계열이 허용된 12개 안에 있고 상태 값이 올바른지(`P36_SERIES_INVALID`)
- 요청 회계가 맞는지: 계열별 합 + 미확인분 = manifest 합 ≤ budget(`P36_REQUEST_COUNT_MISMATCH`)
- 월 레코드의 키와 경로, 구분(`P36_RESUME_LAYOUT_MISMATCH`), 파일 SHA-256(`P36_RESUME_HASH_MISMATCH`)
- 실제 `development/`·`holdout/` 파일 집합이 manifest와 **정확히 같은지**(`P36_UNREGISTERED_OR_MISSING_FILE`)
- 완료 상태라면 12개 계열이 모두 종료 상태인지(`P36_COMPLETION_UNVERIFIED`). 빈 `series`로 완료를 주장하면 실패합니다.
- 모든 경로 구성요소에 symlink가 없는지(`P36_SYMLINK_FORBIDDEN`)

월 파일을 쓴 직후 manifest를 갱신하기 전에 강제 종료되면, 등록되지 않은 파일이 남아 재개가 차단됩니다(fail-closed). 이때는 파일을 지워 우회하지 않고 새 스냅샷으로 다시 수집합니다.

검증을 통과하면 완료된 월은 건너뛰고, 미완료 계열의 가장 오래된 완료 월 시작부터 이어 받습니다. 중단 시점에 미완료였던 월은 저장되지 않으므로 처음부터 다시 받습니다. `--connect-public` 없이 실행하면 검증만 하고 끝냅니다(`VERIFIED_RESUMABLE_OFFLINE`).

## 출력 구조와 품질 지표

```
<OUTPUT>/manifest.json
<OUTPUT>/development/<MARKET>/<unit>m/YYYY-MM.csv.gz
<OUTPUT>/holdout/<MARKET>/<unit>m/YYYY-MM.csv.gz
```

**파일 형식**
- 월별 gzip CSV입니다. 열은 `timestamp`, `open`, `high`, `low`, `close`, `volume`입니다.
- gzip mtime이 0이라 같은 자료면 바이트가 같습니다.

**경로 보호** (리뷰 5949862643)
- 스냅샷 루트는 기존 `p19_baseline.safe_path` 규칙을 그대로 따릅니다.
  - 루트와 모든 상위 경로에 symlink가 없어야 합니다.
  - 저장소 밖이어야 합니다.
  - 상위에 계좌·holdout 표지(`paper-state.json`, `experiment.json` 등)가 없어야 합니다.
  - 다른 스냅샷 표지(`manifest.json` 등)가 있는 디렉터리 아래에는 만들지 않습니다.
- 루트 아래의 모든 경로 구성요소도 symlink가 아닌 실제 디렉터리여야 합니다.
- 월 파일은 같은 디렉터리의 배타 임시 파일에 쓰고 fsync한 뒤, 기존 파일을 덮어쓰지 않는 방식(`link`)으로 게시합니다.

**월 완료 판정**
- 그 달보다 이른 봉을 받았거나 빈 응답(조회 경계)을 받아야 완료입니다.
- 페이지가 정확히 월 시작에서 끝나면, 다음 페이지로 확인한 뒤 저장합니다. 그래서 최초 관측 월 표시가 정확합니다.

**manifest 내용**
- 계획, 상태, 누적 요청 수, 계열별 상태·요청 수
- 월별: 파일 경로, `file_sha256`, `rows`
- 개발 월만 추가로 기록합니다: `expected_rows`, `missing_bars`, `longest_gap_bars`, 첫/마지막 봉, `dataset_sha256`, 최초 월 여부
  - 최초 월의 기대 봉 수는 첫 관측 봉부터 셉니다.
- **개발 구간 coverage** (`development_coverage`): 계열별로 [첫 관측 봉, holdout 시작) 구간을 계산합니다.
  - 통째로 빈 개발 월 목록(`empty_development_months`)
  - 월 경계를 넘는 공백까지 포함한 `longest_gap_bars`
  - 구간 전체의 `missing_bars`
  - 요약 출력에 `empty_dev_months`로 표시합니다. 봉을 보간하지 않고, holdout 값은 들어가지 않습니다.
- 거래가 없어 봉이 없는 분과 API 누락은 **구분할 수 없습니다**. 누락 봉은 그대로 세어 보고합니다.

## holdout 오염 방지

**경계**
- `end` 직전 6개월(`holdout_start` = `end`의 6개월 전 1일 00:00 UTC)이 holdout입니다.
- 예: 2026-11 실행 → holdout은 [2026-05-01, 2026-11-01)

**저장**
- holdout 행은 `holdout/` 디렉터리에 따로 저장합니다.
- manifest에는 **파일 SHA-256과 행 수만** 남깁니다.
- 첫/마지막 봉, 누락, gap, 가격·수익률 통계는 기록하거나 출력하지 않습니다. 저장 전 형식 검증은 하지만 그 값은 버립니다.
- 디렉터리 이름 `holdout`은 기존 연구 입력 검사(`p19_baseline.safe_path`)가 경로를 거부하는 표지입니다. 그래서 기존 분석기가 이 자료를 읽지 않습니다.

**읽지 않는 자료**
- 2026-10-21 스냅샷의 봉인 구간을 포함해, 기존 연구 스냅샷은 읽지 않습니다. 새로 공개 API에서 받은 자료만 씁니다.

## 합성 검증 (`tests/test_p36_history.py`, 22개, 실제 API 호출 0회)

모든 응답은 Fake HTTP 세션이 만들고, 소켓 연결은 테스트를 실패시킵니다.

- **전체 수집 round-trip:** 12개 계열의 모든 봉이 월 파일 합집합과 정확히 같습니다(누락·중복 없음). API 조회 경계 도달, 인증 제거, 리다이렉트 금지, `count=200`, 요청 간 0.25초 대기를 확인했습니다.
- **holdout:** 파일이 `holdout/`에 있고, 기록은 `{partition, file, file_sha256, rows}`뿐입니다. 통계 단어가 manifest와 요약 출력에 없습니다.
- **누락 기록:** 주입한 누락 2봉 → `missing_bars=2`, `longest_gap_bars=2`. 보간하지 않습니다. 최초 관측 월의 기대 봉 수는 첫 관측 봉부터 셉니다.
- **월 경계:** 정확히 200봉이 월 시작에서 끝나는 경우와, 월을 가로지르는 계열을 분리하는 경우를 확인했습니다.
- **거절:** 중복, 역순, 격자 밖, 다른 종목, OHLC 불일치, 음수, 200행 초과 → `STOPPED`. 완료 월만 남고 `.pending` 파일은 없습니다.
- **오류 처리:** 429/503/연결 실패는 정확히 1회 요청으로 멈춥니다(재시도 없음). 서버 Date 30초 차이는 `P36_CLOCK_UNVERIFIED`입니다.
- **요청 상한:** 상한(테스트에서 7)에서 `REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED`이고, 재개해도 리필되지 않습니다.
- **재개:** 중단(Ctrl-C) 뒤 오프라인 검증 → 수동 재개 결과가 중단 없는 수집과 월별 SHA-256이 같습니다. 해시·계획 변조는 차단됩니다.
- **리뷰 회귀 (5949862643):**
  - 첫 페이지가 4월 봉 1개뿐이어도 계속 요청해 2월까지 받습니다. 3월은 빈 개발 월로 보고됩니다.
  - 첫 응답부터 빈 12개 계열은 `bounded=0/12 no_rows=12`입니다.
  - `os._exit` 강제 종료 뒤에도 budget=1이 남고, 재개 시 `unconfirmed_requests=1`이며 상한이 유지됩니다.
  - 잠금을 쥔 동안 두 번째 resume은 네트워크 0, 파일 바이트 불변으로 거절됩니다.
  - 빈 series 완료 주장, 월 레코드 누락, 계열 누락, 요청 카운터 변조, 미지 계열, 잘못된 상태, 미등록 파일은 모두 차단됩니다.
  - 파티션 디렉터리 symlink, 스냅샷 안을 가리키는 `budget.json` symlink, 계좌 표지·상위 symlink·기존 스냅샷 하위 경로를 거절하며, 외부 파일 바이트는 바뀌지 않습니다.
- **기존 자료 보존:** P22 drift(`ops/paper_experiment.py`만)와 P33 고정 상태가 그대로입니다. 기존 스냅샷을 읽는 코드가 없습니다.

## 한계

- **실자료 미수집:** 실제 수집은 하지 않았습니다. 조회 가능 기간, 상장일, 누락 비율, 실제 소요 시간은 사용자 실행 뒤에야 알 수 있습니다.
- **API 조회 경계:** 빈 응답은 조회 경계일 뿐, 상장 시각이나 거래소 전 기간 확보를 증명하지 않습니다. 최초 관측 월과 행 수는 별도 근거(상장 공지 등)와 대조해야 합니다.
- **빈 달:** 거래가 전혀 없는 개발 월은 파일이 생기지 않습니다. coverage 보고의 `empty_development_months`에 표시됩니다.
- **잠금 범위:** 잠금은 같은 머신의 `flock`입니다. 네트워크 파일시스템이나 다른 머신 사이의 동시 쓰기는 막지 못합니다.
- **출처 증명:** SHA-256은 무결성 증거일 뿐, 거래소 출처의 독립 증명이 아닙니다.
- **분석기 미지원:** P35 러너와 기존 loader는 이 월별 형식과 다년치 크기를 아직 읽지 않습니다. Phase 1에서 검토된 loader를 별도로 추가해야 합니다.
- **결과 공개 금지:** 실자료 원본은 공개 이슈나 PR에 올리지 않고 요약만 보고합니다. `REAL_TRADING=False`, 추가 지출 0원입니다.
