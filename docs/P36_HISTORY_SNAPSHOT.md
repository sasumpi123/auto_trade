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
  - `P36 <상태> requests=<n>/120000 series_full=<k>/12`
  - 고정 `end`와 holdout 경계
  - 주기별 완전 확보 수, 최초 월, 개발 구간 누락 봉
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
- 짧은 페이지(빈 페이지 포함)가 오면 그 계열의 상장 시점에 도달한 것으로 보고 `EARLIEST_REACHED`로 표시합니다.
  - 근거: 업비트는 거래가 없는 분을 건너뛰고, `to` 이전의 최근 봉을 `count`만큼 채워 반환합니다.
  - 이 동작은 저장소의 기존 수집 경로와 같은 가정입니다. 공식 문서로 재확인하지는 못했습니다(아래 한계).

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
- 요청 간격 0.25초 이상(초당 4회)입니다. 저장소의 기존 공개 시세 클라이언트 간격(0.2초, 0.26초)보다 느립니다.
- 업비트 공식 문서의 시세 캔들 요청 제한은 개발 환경에서 문서 호스트에 접근할 수 없어 재확인하지 못했습니다. 실행 전 사용자가 공식 값이 초당 4회 이상인지 확인해야 합니다.
- 스냅샷 전체 요청 상한은 **120,000회**입니다. 모든 재개 실행을 합친 값이며, 다시 채워지지 않습니다.
- 상한에 도달하면 `REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED`이며, **전체 기간 확보로 보고하지 않습니다**.

**자동 재시도 없음**
- 429·5xx·검증 오류·네트워크 오류가 나면 즉시 `STOPPED_RESUME_MANUALLY`로 멈춥니다.
- Ctrl-C로 중단하면 `INTERRUPTED_RESUME_MANUALLY`입니다.
- 자동 재시도, 재접속, 예약은 없습니다.

**수동 재개(`resume`)는 먼저 다음을 검증합니다**
- 기존 월 파일 전부의 SHA-256(`P36_RESUME_HASH_MISMATCH`)
- 경로·구분(`P36_RESUME_LAYOUT_MISMATCH`)
- 고정 계획(`P36_PLAN_CHANGED`: end, holdout, 상한, 간격)

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

**월 완료 판정**
- 그 달보다 이른 봉을 받았거나 상장 시점에 도달해야 완료입니다.
- 페이지가 정확히 월 시작에서 끝나면, 다음 페이지로 확인한 뒤 저장합니다. 그래서 최초 월 표시가 정확합니다.

**manifest 내용**
- 계획, 상태, 누적 요청 수, 계열별 상태·요청 수
- 월별: 파일 경로, `file_sha256`, `rows`
- 개발 월만 추가로 기록합니다: `expected_rows`, `missing_bars`, `longest_gap_bars`, 첫/마지막 봉, `dataset_sha256`, 최초 월 여부
  - 최초 월의 기대 봉 수는 첫 관측 봉부터 셉니다.
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

## 합성 검증 (`tests/test_p36_history.py`, 15개, 실제 API 호출 0회)

모든 응답은 Fake HTTP 세션이 만들고, 소켓 연결은 테스트를 실패시킵니다.

- **전체 수집 round-trip:** 12개 계열의 모든 봉이 월 파일 합집합과 정확히 같습니다(누락·중복 없음). 상장 시점 도달, 인증 제거, 리다이렉트 금지, `count=200`, 요청 간 0.25초 대기를 확인했습니다.
- **holdout:** 파일이 `holdout/`에 있고, 기록은 `{partition, file, file_sha256, rows}`뿐입니다. 통계 단어가 manifest와 요약 출력에 없습니다.
- **누락 기록:** 주입한 누락 2봉 → `missing_bars=2`, `longest_gap_bars=2`. 보간하지 않습니다. 최초 월의 기대 봉 수는 상장 시점부터 셉니다.
- **월 경계:** 정확히 200봉이 월 시작에서 끝나는 경우와, 월을 가로지르는 계열을 분리하는 경우를 확인했습니다.
- **거절:** 중복, 역순, 격자 밖, 다른 종목, OHLC 불일치, 음수, 200행 초과 → `STOPPED`. 완료 월만 남고 `.pending` 파일은 없습니다.
- **오류 처리:** 429/503/연결 실패는 정확히 1회 요청으로 멈춥니다(재시도 없음). 서버 Date 30초 차이는 `P36_CLOCK_UNVERIFIED`입니다.
- **요청 상한:** 상한(테스트에서 7)에서 `REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED`이고, 재개해도 리필되지 않습니다.
- **재개:** 중단(Ctrl-C) 뒤 오프라인 검증 → 수동 재개 결과가 중단 없는 수집과 월별 SHA-256이 같습니다. 해시·계획 변조는 차단됩니다.
- **기존 자료 보존:** P22 drift(`ops/paper_experiment.py`만)와 P33 고정 상태가 그대로입니다. 기존 스냅샷을 읽는 코드가 없습니다.

## 한계

- **실자료 미수집:** 실제 수집은 하지 않았습니다. 조회 가능 기간, 상장일, 누락 비율, 실제 소요 시간은 사용자 실행 뒤에야 알 수 있습니다.
- **공식 문서 미확인:** 업비트의 요청 제한과 "짧은 페이지 = 이전 기록 없음" 동작은 공식 문서로 재확인하지 못했습니다(개발 환경에서 문서 호스트 접근 불가). 실행 전에 사용자가 확인해야 합니다.
- **조기 종료 오판 가능성:** 거래소가 중간에 짧은 페이지를 반환하면 그 계열은 조기 종료된 것으로 기록됩니다. 최초 월과 행 수를 상장일과 대조해 판단해야 합니다.
- **빈 달:** 거래가 전혀 없는 달은 파일이 생기지 않고 공백으로 남습니다.
- **출처 증명:** SHA-256은 무결성 증거일 뿐, 거래소 출처의 독립 증명이 아닙니다.
- **분석기 미지원:** P35 러너와 기존 loader는 이 월별 형식과 다년치 크기를 아직 읽지 않습니다. Phase 1에서 검토된 loader를 별도로 추가해야 합니다.
- **결과 공개 금지:** 실자료 원본은 공개 이슈나 PR에 올리지 않고 요약만 보고합니다. `REAL_TRADING=False`, 추가 지출 0원입니다.
