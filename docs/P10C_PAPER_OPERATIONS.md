# P10-C — MacBook PAPER 관찰 도구

현재 사용자가 전달한 실기기 결과는 ARM64/Python3.12.7, 기존 테스트 723개·오프라인 리허설 31개 통과, 최초 원장·백업, BTC 10분/700틱 수신 및 정상 종료(pending=0/halted=false)다. 10종목 시험도 사용자 보고다. **새 P10-C 코드의 MacBook 실기기 검증 결과는 아직 아니다.** Cloud에서는 Fake/Mock만 사용하며 실제 Upbit 시세/계좌/주문/Slack 호출이나 MacBook 접속을 하지 않는다.

## 안전 경계와 기존 코드 재사용

- 새 명령 `python -m ops.paper_watch`는 기본값이 읽기 전용/offline이며 원장·잠금·outbox를 생성하지 않는다. `--dry-run`은 같은 동작이다. 공개 시세 연결은 `--connect-public --seconds N` 두 옵션을 모두 명시해야 한다. 1~3,600초만 허용한다.
- 원래 PAPER 원장이 존재해야 한다. 같은 경로 `.lock`을 OS 비차단 독점 소유한 뒤에만 공개 시세를 조회한다. 원장/잠금 누락, 부적절한 권한, symlink/hardlink, 손상, pending/UNKNOWN, HALTED, 현금·체결 원가 불일치는 재초기화 없이 중단한다. 초기화·삭제·halt 해제 옵션이 없다.
- `ExistingPaperStore`는 기존 StateStore의 동일 lock 방식/파일 및 원자적 JSON save/load 계약을 재사용한다. 잠금 아래 검증을 먼저 하므로 `ExecutionEngine.open`이 이전 halt를 자동으로 지우기 전에 차단한다. 기존 trading/ 코드는 수정하지 않는다.
- AutoTrade에 `real_trading=False`, `broker=None`, `notification=None`을 강제한다. 공개 전용 I/O 어댑터는 기존 DataAnalyzer의 지표·RSI/MACD/BB·거래량·쿨다운 함수 본문을 그대로 호출한다. legacy raw-error/알림 decorator만 우회한다. 기존 bfill/ffill·진행 중 분봉 취급도 그대로이며 새로운 전략이나 연구의 인과적 지표로 교체하지 않는다.
- 실제 주문·인증 계좌·Slack 호출을 연결하지 않는다. `.env` 로딩을 child에서 비활성화하고 raw 로그/예외/응답/인증정보를 출력하지 않는다. outbox는 읽기 전용 사전 점검만 하며 소비자를 열지 않는다. Slack 미설정/파일 없음은 OFF 유지 상태이며 PAPER 원장 생성의 근거가 아니다. 기존 outbox에 UNKNOWN/SENDING/소유권 불확실성이 있으면 시작을 보류한다.
- 자동 모델·게시·병합, launchd 등록/자동 시작, 유료 서비스는 계속 OFF다. 기존 P1/P3/P8/P9B 원장·영수증·복구 한도를 변경하지 않는다. 새 실행 원장을 만들지 않는다. summary의 execution_authorized=false는 다음 실행/자동화/Slack에 대한 추가 권한을 부여하지 않는다는 뜻이다.

## 종목 선택 변경

기존 `get_top_tickers(10)`은 KRW 종목마다 일봉 1개 `value`를 읽었다. 진행 중 일봉의 거래대금은 rolling 최근 24시간과 다르다. 새 구현은 공식 공개 GET `/v1/market/all` 1회 → `/v1/ticker?markets=...`를 최대 100종목 단위로 조회하고 `acc_trade_price_24h`로 정렬한다. 동률은 시장 코드순이다. 전체 KRW가 N개면 discovery 요청 수는 `1 + ceil(N/100)`이다. 종목별 일봉 조회는 없다.

전 종목 목록과 ticker 집합이 정확히 일치해야 한다. 누락/중복/예상 밖 코드·부적절한 금액/가격·5분 초과 오래된 시각/5초 초과 미래 시각·HTTP429/기타 오류는 캐시·일봉 fallback이나 자동 재시도 없이 중단한다. GET 경로는 공개 market/ticker/minute1 candles만 허용한다. 리다이렉트·ambient netrc 인증·Authorization/Cookie 전달을 차단하고 기존 프록시 경로는 유지한다. 요청 간격 최소 0.2초, 요청 timeout 최대 5초, 응답 최대 2MB다. 공유 IP의 다른 작업까지 rate limit을 보장할 수는 없다.

선택 종목은 세션 시작 때 고정한다. 기존 원장에 수량이 남은 종목은 순위 밖이어도 구독/관리 대상에 추가한다. `--tickers`를 명시하면 순위 discovery를 생략한다. 선택 방식 변경은 요청된 개선이며 운영 신호 조건 변경은 아니다. `config.get_top_tickers`를 사용하는 기존 main.py도 새 순위를 사용한다.

각 구독 종목의 기존 전략용 minute1 캔들 200개는 별도 조회가 필요하다. 초기 및 기존 DATA_UPDATE_INTERVAL(300초) 갱신 시 종목별 요청이 남는다. 200개 미달/중복/누락/역순/비유한 값/OHLC 범위·시각 이상은 보류한다. 모든 시장을 한 번에 가져온다고 주장하지 않는다. 현재 응답 검증과 공개 API의 실제 가용성은 Mac에서 별도 확인한다.

## Mac SSH 실행 순서

경로는 실제 사용자 checkout `~/projects/auto_trade` 기준이다. 이미 시험 봇이 실행 중이면 **기존 터미널의 정상 종료부터** 수행하고 원장·잠금·미확정 주문을 확인한다. 실행 중 git pull/두 번째 봇 시작/lock 삭제/이름 기반 pkill은 하지 않는다. 이전 checkout이나 다른 파일을 쓰는 프로세스는 이 경로의 잠금으로 탐지할 수 없으므로 사용자 확인이 필요하다.

기존 [P10 점검](P10_MAC_PREFLIGHT.md)과 [cold backup](P10_BACKUP_RECOVERY.md)을 먼저 따른다. 새 CLI에는 별도 초기화·백업/복원 명령이 없고 기존 원장을 재사용한다. 원장 경로를 모르면 추측해서 새 파일을 만들지 않는다.

```sh
cd "$HOME/projects/auto_trade"
git status --short --branch
git fetch origin master
git pull --ff-only origin master
git rev-parse HEAD
.venv/bin/python --version
PYTHON_DOTENV_DISABLED=1 .venv/bin/python tools/check_safety.py
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m unittest discover
.venv/bin/python -m ops.paper_watch --dry-run --state runtime/paper-state.json
```

작업 트리 변경/다른 브랜치/원장 오류가 있으면 중단한다. dry-run은 검사 보고서이지 실행 승인이 아니므로 **exit2**다. Python3.12, 정책 OFF, PAPER 원장 대사, idle lock을 확인한다. launchd 등록/ACL/APFS/백업의 실제 운영 증거는 여전히 별도 사용자 점검이며 도구가 READY로 대체하지 않는다. Windows는 기본 offline 진단만 제공하며 공개 연결 모드는 read-only cold-lock 검증 미인증으로 BLOCKED다. macOS/Linux에서 명시 연결을 지원한다.

아래는 **PR 검토·병합 및 사용자 실행 결정 후 Mac에서만** 수행할 공개 데이터 PAPER 명령이다. Cloud에서는 실행하지 않는다. 최초 원장 생성이나 실거래/Slack/상시 운영 승인이 아니다.

```sh
# BTC 단일 종목, 최대 10분
.venv/bin/python -m ops.paper_watch --connect-public --seconds 600 \
  --tickers KRW-BTC --state runtime/paper-state.json

# 별도 사용자 판단 후: rolling 24h 거래대금 상위 10종목, 최대 10분
.venv/bin/python -m ops.paper_watch --connect-public --seconds 600 \
  --top 10 --state runtime/paper-state.json
```

같은 원장을 사용하는 두 명령을 동시에 실행하지 않는다. 공개 연결 여부는 매 실행 명시해야 하며 설정 파일을 enabled=true로 바꾸지 않는다. `main.py`는 기존 네트워크 시작 경로/무기한 루프이므로 이 도구의 시간 제한·offline 기본값을 대신하지 않는다.

## 출력과 종료 판정

JSON Lines로 시작/약 5초 주기 상태/종료 요약을 출력한다. 가상 현금, 종목별 보유수량·평균가격·물타기 사용 여부, **누적** 주문 수·pending·halted, **현재 세션** 수신 틱 수·종목별 마지막 가격/WAIT_TICK 또는 RECEIVED 상태를 포함한다. 틱 수는 수신 메시지 수이며 고유 거래/체결 수가 아니다. 이전 세션 틱은 저장되지 않았으므로 NOT_RECORDED이고 재시작 후 누적했다고 주장하지 않는다. dry-run의 0틱은 이번 명령이 수신하지 않았다는 뜻이다. 원장 원문·주문 ID/사유·개인 오류는 출력하지 않는다.

- STOPPED + post_validation=VERIFIED_UNDER_OWNER_LOCK + after_close.summary 존재: 소유권 아래 메모리와 디스크 일치, pending=0/halted=false, 종료 후 재조회까지 확인. 틱0/WAIT_TICK이면 해당 시장 수신 성공 증거는 없다.
- BLOCKED: 실행 전 정책/원장/잠금/플랫폼/외부 outbox 상태 검증 실패. 파일을 새로 만들지 않는다.
- HALTED: 세션 오류·저장 오류·미확정 종료·재검증 실패. 종료 코드2이며 자동 재시작/재제출/초기화하지 않는다. 실패한 초기화에서는 읽지 못한 원장의 현금/포지션을 빈 상태로 꾸며 출력하지 않는다. 실패 후 메모리 요약은 디스크 검증 성공으로 간주하지 않는다.

정상 연결 세션 종료는 exit0이고 미래 실행이나 수익성 보장은 아니다. enqueue는 Slack 전달 성공이 아니며 이 도구는 enqueue/flush 자체를 수행하지 않는다.

## Ctrl+C·재접속·장애

부모 프로세스가 전체 시간(자식 시작/종목 선택/캔들 준비 포함)을 관리한다. Ctrl+C/SIGTERM 및 POSIX SIGHUP는 중단 요청을 전달한다. 진행 중 작업을 정리하도록 15초 기다리고, 응답하지 않는 자식은 kill하여 잠금을 해제한다(프로세스 회수 대기 최대5초 추가). 강제 종료는 **HALTED/불확실**이며 디스크가 깨끗해 보여도 성공으로 승격하지 않는다. 기존 원자적 저장/intent 복구 규칙이 그대로 적용되며 타임아웃을 이유로 주문을 재제출하지 않는다.

WebSocket은 1초 polling timeout 후 즉시 실패하지 않고 전체 무수신 15초 또는 종목별 무수신 60초에서 중단한다. 오래된/미래/역행 틱은 매매 처리 전에 차단한다. 시계와 monotonic 경과의 5초 초과 차이를 검출하면 중단하며 벽시계 종료 시각도 검사한다. 절전 중 tick 처리나 자동 재연결을 보장하지 않는다. 호스트 종료/SIGKILL·전원 손실·SSH 구현의 신호 전달은 실제 장치 검증 대상이다.

SSH가 끊겼으면 새 SSH에서 먼저 PID/comm 및 해당 원장의 읽기 전용 점검으로 종료/잠금을 확인한다. 새 프로세스를 바로 시작하거나 nohup/launchd로 우회하지 않는다. 잠금이 바쁘면 원래 세션을 확인하고, HALTED/pending/UNKNOWN은 기존 복구 절차로 수동 대조한다. JSON 요약만 비밀 없이 보관하고 전체 원장/.env/토큰/로그를 공개 GitHub로 보내지 않는다. cold backup은 모든 writer가 완전히 종료된 뒤 동일 세대 JSON/outbox/sidecar/Controller/checkpoint/기존 요약을 보존하는 기존 절차를 따른다. lock은 삭제하지 않는다. 공동 롤백 탐지/백업 최신성/다른 경로의 중복 봇/Slack exactly-once를 이 도구가 증명하지 않는다.

## 검증과 남은 실기기 항목

오프라인 Fake GET/캔들/스트림, 실제 PAPER 엔진·JSON 원장·OS 잠금으로 일괄 순위, 누락/중복/429/잘못된 시각/수치, 정상 거래·종료·재시작, 기존 보유 구독, 중복 소유자·손상/미확정/UNKNOWN/HALTED/저장 오류, 기본 CLI 무네트워크/미초기화, Ctrl+C 협력 종료·시간 초과 강제 종료를 검증한다. 기존 전략 함수와 adapter의 지표·신호 동일성도 검사한다. 기존 전체 테스트/세 플랫폼 Python3.12/Safety gate 결과는 PR exact head/run에 기록한다. 연구 holdout/기존 정책·복구 한도는 변경하지 않는다.

Mac 실기기에서 새 버전의 API 응답/거래대금 순위, 인증 없는 공개 호출, 10종목 초기 요청 rate limit, Ctrl+C/시간 제한/SSH 단절/절전·재부팅 후 원장 대사 및 잠금 해제를 추가 확인해야 한다. 사용자가 보고한 이전 버전 성공을 새 CLI 검증 성공으로 옮기지 않는다. 실제 주문·인증 계좌·Slack·launchd·유료 서비스는 계속 미승인/OFF다.

P10-D 변경: 순위 밖 오래된 ticker 때문에 전체를 차단하던 검사를 수정했고, 종목 선택을 기존 원장 잠금 아래 엔진 시작 전으로 옮겼다. HTTP 응답/상위 N freshness와 분봉 공백의 보수적 차단, 기존 HALTED 원장의 읽기 전용 대사 및 별도 복구 승인 경계는 [P10D_FRESHNESS_RECOVERY](P10D_FRESHNESS_RECOVERY.md)를 따른다. 이전 HALTED 원장을 이 변경으로 자동 해제하지 않는다.

P10-F: 기존 HALTED 원장을 보존한 채 시험하려면 [격리 PAPER 실험](P10F_ISOLATED_PAPER_EXPERIMENT.md)을 따른다. 별도 private 신규 계좌의 생성과 실행을 분리하며, 명시적 연속 분봉 적합성 모드는 새 `ops.paper_experiment`에만 제공한다. 기존 `--top`의 실제 거래대금 순위 의미와 원래 계좌의 복구 차단은 바꾸지 않는다.
