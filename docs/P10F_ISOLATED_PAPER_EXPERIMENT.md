# P10-F 격리 PAPER 실험과 연속 분봉 적합성

## 범위와 현재 승인

새 계좌는 **새로운 독립 모의 실험**이다. 기존 `runtime/paper-state.json`의 복구/연속 운영/백업 복원이 아니다. 기존 600,000원 현금과 BTC 400,000원 원가·체결을 복사하거나 합산하지 않는다. 신규 가상 초기자금은 정확히 **1,000,000 KRW**, 포지션/주문/신호는 빈 상태다. 출력에는 항상 `NEW_INDEPENDENT_PAPER_EXPERIMENT_NOT_RECOVERY` 범위를 표시한다. 이후 수익률·주문 수는 이 새 계좌 안에서만 누적된다.

현재 Cloud 작업은 코드와 격리 Fake 테스트뿐이다. 실제 Mac 신규 계좌 생성/공개 시세/매매 시험은 수행하지 않았고 아직 승인되지 않았다. 기존 HALTED 원장과 `.lock`/백업을 수정·삭제·복원·초기화하지 않는다. [P10-E 복구 차단](P10E_MANUAL_PAPER_RECOVERY.md)은 그대로다. REAL_TRADING=False, Slack/실거래/launchd/자동 시작·재시작/유료 호출 OFF이며 전략/체결/holdout은 변경하지 않는다.

## 도구와 경로

`python -m ops.paper_experiment`의 기본은 offline/read-only다. 디렉터리를 생략해도 기존 원장으로 fallback하지 않고 BLOCKED다. 생성은 `--create` 한 번에만 허용하며 실행과 분리되어 있다. --reset/--force/복구/가져오기/자금 변경 옵션은 없다.

- 대상은 기존 프로젝트/다른 Git checkout 밖의 **새 leaf 디렉터리**다. 부모는 미리 존재하는 사용자 소유 private 디렉터리여야 한다. 기존 빈 디렉터리도 재사용하지 않는다.
- POSIX owner/mode0700, 파일0600, 심볼릭 링크·hardlink/alias·인증 디렉터리를 검사한다. Mac은 `/bin/ls -lde` 메타데이터만 읽어 추가 ACL이 있으면 보수적으로 차단한다. ACL/권한을 자동 변경하지 않는다. Windows private ACL/cold-lock 운영은 미인증으로 BLOCKED다.
- 생성 시 부모에 `.paper-experiment-<경로해시>.json` 예약 파일을 O_EXCL로 먼저 보존한다. 경로의 대소문자/Unicode 표기 차이도 보수적으로 같은 예약으로 취급한다. 생성 중 실패/leaf 소실에도 같은 경로를 다시 만들지 않는다. 새 계좌 디렉터리에는 `experiment-state.json`, 기존 방식의 `.lock`, `initial-state.json`, `experiment.json`이 생긴다. 처음에는 engine.open/가짜 매수 없이 원래 PAPER JSON version1의 빈 상태를 만든다.
- 공개 실행 시 부모의 `.paper-experiment-used-<경로해시>.json`과 leaf의 `session.json`에 시작 증거를 남긴다. 별도 DB/Controller/Slack outbox를 도입하지 않고 기존 StateStore의 atomic/fsync 저장과 원장 소유권을 재사용한다. 원장 메타데이터에는 실험 ID/초기자금/경로 해시/마지막 세션 ID만 추가한다. 첫 엔진 저장은 데이터 준비 후다.
- 원장/manifest/초기 스냅샷/부모 예약은 서로 연결되어야 한다. 다른 경로로 복제하거나 초기 파일을 덮어써서 시작하지 않는다. 부모 예약·첫 사용 증거와 원장이 함께 소실/롤백되면 로컬 파일만으로 탐지하지 못한다. 이를 독립 witness라고 주장하지 않는다.

## 명시적 종목 선정

사용자가 Mac에서 보고한 상위10 중 KRW-BLAST/BABY/IRYS/T/ORDER/XRP/CAP 7개 통과와 KRW-AXIS/SOPH/USDT의 PUBLIC_CANDLE_GAP을 Fake로 재현한다. 보고값을 현재도 같은 시장 순위/통과 결과라고 주장하지 않는다. 사용자 확인 공식 문서의 무체결 분봉 미생성 조건을 반영한다: **공백은 API 장애의 독립 증거가 아니다.** 반면 이 전략의 연속 1분 200개 입력 조건은 유지하므로 공백을 채우거나 무시하지 않는다.

| 옵션 | 뜻 | 공백 처리 |
|---|---|---|
| `--top 10` | 실제 rolling24h 거래대금 상위10 그대로 | 하나라도 공백이면 준비 실패. 7개만 실행/차순위 교체하지 않음 |
| `--tickers KRW-BTC ...` | 사용자가 명시한 종목 | 전 종목과 보유 종목의 분봉 검증 필요 |
| `--eligible-top 7 --candidate-limit 10` | 실제 상위10을 순위대로 검사해 연속 분봉이 적합한 최대7개 선정 | PUBLIC_CANDLE_GAP만 명시 제외. 7개 부족하면 실패 |
| `--eligible-top 10 --candidate-limit 20` | 실제 상위20 후보 중 적합한10개 | 원래 거래대금 상위10과 다른 집합일 수 있음을 명시 |

마지막 두 옵션은 **새 실험 CLI에만** 있고 기존 --top의 의미를 변경하지 않는다. 후보/명시 종목은 최대30, 선택+보유의 합집합도 최대30이다. 공개 REST HTTP GET은 세션 전체(등록 종목/배치 ticker/캔들/후속 갱신 포함) **최대64회**로 제한하며 기존 요청 간격·5초 응답 제한·시계 검사·전체 실행 기한을 유지한다. 후보 제한과 HTTP 제한은 다르며 후보 검사 중 예산이 먼저 끝나도 추가 요청/유료 fallback/자동 재시도를 하지 않는다. WebSocket은 별도의 논리 연결 1회이며 자동 재연결하지 않는다. WebSocket 틱 수는 REST 요청 수/실제 체결 수와 다르다.

429/시각 이상/누락·중복/응답 오류/200개 미달/후보 부족은 fail-closed다. 적합성 모드에서도 이들을 공백으로 간주해 넘어가지 않는다. 실제 순위 전체 검증을 위해 후보 목록 안의 오래된 상위 시각도 차단하며 자동으로 후보 범위를 넓히지 않는다.

`selection` 출력은 mode/requested_count/candidate_limit/inspected_candidates/ranked_candidates/excluded/held_required/held_added/selected/final_subscription을 담는다. 실패 시 selected는 검증을 통과했던 후보일 뿐 실제 구독이 아니며 **final_subscription은 빈 목록**이다. 성공 DATA_PREPARED도 아직 engine_started=false이므로 실제 WebSocket 연결·틱 수신 성공으로 해석하지 않는다. 이후 최종 상태의 종목별 틱과 WAIT_TICK/RECEIVED를 따로 확인한다.

보유 종목이 순위 밖이면 별도 추가·검증한다. 보유 종목에 공백이 있거나 위험관리를 위한 입력을 확보하지 못하면 해당 종목을 버리지 않고 전체 시작을 중단한다. 적합성 제외 종목이 보유 종목이라도 마찬가지다. 현금·수량·체결은 그대로 보존한다.

## 준비 실패와 운영 중 실패

기존 원장 잠금을 획득한 뒤 실험 ID·세션 CAS를 확인하고, 순위·최초 캔들·동일 지표 함수를 모두 준비한다. **그 후에만** 엔진이 첫 저장한다. 검증한 캔들을 최초 지표 준비에 재사용하고 중복 조회하지 않는다. 준비 종료 시 원래 캔들 시각의 180초 freshness와 wall/monotonic 불연속을 다시 확인하며, 그 사이 오래됐으면 새 시각으로 덮어쓰거나 자동 재조회하지 않고 실패한다. 기존 원장 신호/보유의 복구와 동일 RSI/MACD/BB 계산은 바꾸지 않는다. 운영 중 주기적 갱신은 새 캔들 검증을 다시 한다.

- 준비 실패: 세션 HALTED/exit2 + engine_started=false. 원장 파일 bytes/보유/현금/HALT를 바꾸지 않는다. 원장 밖 session.json에는 `PREPARATION_FAILED`를 기록하고 before/end SHA 동일성을 확인한다. 실패를 STOPPED 성공으로 꾸미지 않는다. 원장이 정합하고 변하지 않은 사실을 다시 확인한 뒤에만 사용자가 **새 명시 명령**으로 재시험할 수 있다.
- 운영 시작 후 429/분봉 공백/WebSocket·저장 오류: 새 실험 원장에 기존 영속 HALT 정책을 적용한다. 원본 계좌는 그대로다. 미확정/UNKNOWN/원장 불일치는 자동 정리하지 않는다.
- Ctrl+C/SIGTERM/SSH SIGHUP: 기존 감독자가 중지 요청, 최대15초 정리 대기 후 강제 종료한다. 이후 worker 종료·잠금 해제·디스크 대사를 확인한다. 검증된 STOPPED만 STOPPED_VERIFIED를 기록한다. 강제 종료/통신 유실/미확인 시작은 IN_PROGRESS 또는 blocked 증거가 남고 재시작을 차단한다.
- session.json 단독 소실/이전 세션으로 롤백·원장 단독 롤백/첫 사용 증거 소실도 차단한다. 파일을 지워 NEVER_STARTED로 돌리지 않는다. 저장 오류 후 원장/표시가 깨끗해 보인다는 이유만으로 다시 실행하지 않는다.
- 모든 local 파일·부모 증거가 함께 롤백되면 탐지할 수 없다. 전체 저장 실패로 오류 표식조차 저장할 수 없는 경우도 미래의 정상 실행을 증명하지 못한다. 그런 오류를 관찰한 사용자는 도구가 다음에 읽힌다는 이유로 재실행하지 말고 기존 P8/P9B/P10 절차대로 HALTED/PENDING 수동 검토를 유지한다.

수익률은 P9-A 수수료 포함 순수익률을 재사용한다. 매수/매도는 이 계좌의 누적 확정 거래, 틱은 이번 세션 수신 메시지다. 가격 근거가 없는 오프라인 보유 평가나 HALTED/불일치 지표는 N/A다. 현금만 남은 정합한 계좌는 오프라인에서도 평가할 수 있다. raw 주문/사유/키/토큰/환경변수 값을 출력하지 않는다. enqueue/Slack 전송 자체를 하지 않는다.

## Mac 사용자 실행 순서 — 지금 Cloud에서 실행하지 않음

PR의 독립 검토·병합 후, **사용자가 별도로 새 실험 생성을 명시 결정한 경우에만** Mac SSH에서 수행한다. 공개 연결 결정은 생성과 별개다. 기존 봇/다른 checkout writer가 있다면 정상 종료·경로/소유권부터 확인한다. 실행 중 git pull/백업/두 번째 프로세스를 시작하지 않는다.

```sh
cd "$HOME/projects/auto_trade"
git status --short --branch
git fetch origin master
git pull --ff-only origin master
git rev-parse HEAD
.venv/bin/python --version
PYTHON_DOTENV_DISABLED=1 .venv/bin/python tools/check_safety.py
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m unittest discover
# 기존 HALTED 원장은 읽기만 한다. 기록한 SHA와 비교하고 수정하지 않는다.
/usr/bin/shasum -a 256 runtime/paper-state.json
```

아래 부모 경로가 이미 있다면 권한/ACL/기존 내용부터 읽기 전용 확인한다. 자동 chmod/ACL 제거/기존 디렉터리 삭제는 하지 않는다. 경로 문자열은 인증정보가 아니며 .env/토큰은 명령이나 보고서에 넣지 않는다.

```sh
private_parent="$HOME/Library/Application Support/auto_trade-paper-experiments"
umask 077
if [ ! -e "$private_parent" ]; then /bin/mkdir -m 700 "$private_parent"; fi
/bin/ls -lde "$private_parent"
experiment_dir="$private_parent/run-20261026-01"

# 별도 새 실험 생성 결정 후 한 번만 실행. 아직 연결/주문하지 않는다.
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" --create
# 생성과 분리된 읽기 전용 확인: exit2가 정상적인 비승인 보고 동작이다.
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir"
```

생성 실패/기존 경로/불완전 파일이면 삭제 후 재생성하지 않는다. 결과와 private 증거를 보존해 검토한다. 새 이름으로 새 계좌를 만드는 것은 매번 별도 독립 실험이며 이전 손익/장애를 초기화한 연속 계좌로 보고하지 않는다.

아래는 **공개 데이터 PAPER 시험을 별도로 결정한 뒤** 택일한다. 동시 실행하지 않는다. 600초는 감독 프로세스 시작 뒤 자식의 데이터 준비·연결·실행을 포함한다. 앞선 로컬 경로·원장 검사와 뒤의 정리 대기·프로세스 회수 시간은 별도다. 생성 시 기존 BTC 포지션을 이전하지 않았다.

```sh
# 엄격한 실제 거래대금 상위10: 분봉 공백이면 준비 실패가 정상이다.
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 600 --top 10

# 별도 명시한 적합성 모드: 상위10 후보에서 적합한7개. 기존 top10과 다르다.
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 600 --eligible-top 7 --candidate-limit 10

# 또는 상위20 후보에서 적합한10개: 후보 상한을 자동 확대하지 않는다.
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 600 --eligible-top 10 --candidate-limit 20
```

종료 후 같은 읽기 전용 명령과 기존 원장 SHA를 다시 확인한다. 원본 백업은 사용자에게 알려진 실제 private 파일의 SHA를 별도로 비교하며 공개 업로드하지 않는다. 선택/제외 이유, engine_started, STOPPED_VERIFIED 또는 PREPARATION_FAILED/HALTED, 틱·현금·보유·누적 거래·수익률/N/A 사유만 비밀 없는 요약으로 ChatGPT에 전달한다. PREPARATION_FAILED를 운영 성공으로 보고하지 않는다.

SSH 재접속/절전·재부팅 후에는 실행부터 하지 말고 PID/실제 cwd/launchd 미등록/해당 원장의 읽기 전용 진단·미확정 주문·session 상태를 먼저 확인한다. 락 파일 존재는 실행 중이라는 증명도 삭제 허가도 아니다. 강제 종료/미확인이면 자동 시작하지 않는다. launchctl/nohup/스케줄 등록 도구를 추가하지 않았다. 정상 종료 재개도 매번 명시 옵션이 필요하다.

## 보존·백업·미검증

cold backup은 모든 writer 종료·잠금 확인 후 leaf 전체(원장/lock/초기 snapshot/manifest/session/blocked 증거)와 부모의 해당 creation/used receipt를 동일 세대로 비공개 보존한다. [기존 백업 경계](P10_BACKUP_RECOVERY.md)를 재사용한다. 이 CLI에는 restore/import/reset가 없고 다른 경로 복제를 새 연속 계좌로 인정하지 않는다. 복원/원장 소실/공동 롤백 의심은 실제 독립 증거 없이 READY로 승격하지 않는다.

Fake 테스트에서 기존 HALTED BTC 원장/백업/lock bytes 불변, 새 빈 계좌, 실제 PAPER Fake 체결·영속성·수익률, 상위10 중3개 공백·명시 적합성·후보 부족·429/시각/응답 오류, 캔들 재사용·보유 종목·준비 전/후 장애, 중복 소유권·강제 종료·SIGHUP·저장 실패·기록 소실을 검증한다. 테스트 데이터/원장 삭제는 TemporaryDirectory에서만 수행한다. 3 OS 정확한 head/run/attempt 및 skip은 PR에 기록한다.

Mac M3의 실제 생성/ACL/권한/공개 API·순위/7개 적합성/64회 예산/HTTP Date·시계/종목별 틱/실제 전략 신호·Ctrl+C·SSH·절전/전원 내구성은 **아직 미검증**이다. 사용자 과거 보고를 새 도구의 성공으로 옮기지 않는다. 실제 Slack/실거래/launchd/모델/유료 인프라는 계속 OFF다.
