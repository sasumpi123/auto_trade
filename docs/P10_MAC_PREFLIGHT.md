# P10-A — MacBook PAPER 오프라인 사전 준비

이 단계는 Cloud에서 코드와 격리된 테스트를 검증한다. MacBook M3, 실제 Slack 계정, 실제 시장 연결, 장시간 운영은 미검증이다. 진단 결과는 실행 허가가 아니다. 추가 지출 0원, REAL_TRADING=False, Slack OFF, 자동 개발/게시/병합 OFF, launchd 미등록 정책을 유지한다.

## 현재 실제 실행 경로

- `main.py` → `TradingBot.run` → 공개 시장 종목 조회 → `AutoTrade.start`. **main.py는 오프라인 리허설 명령이 아니다.** 현재 실행하지 않는다. 시작 시 `ExecutionEngine.open`이 `runtime/paper-state.json.lock`을 비차단 독점 소유하고 상태를 검증한다. PAPER 미확정 intent/submitted는 재제출하지 않고 중단한다.
- SIGINT/Ctrl-C 및 SIGTERM은 `request_stop`을 요청한다. 루프 종료의 finally에서 스트림과 엔진을 닫고 잠금을 해제한다. 현재 초기 종목 조회가 signal handler 설치보다 먼저이므로 이 시점의 종료와 실제 네트워크 timeout은 실기기 검증 대상이다. 종료 메시지/프로세스 종료만으로 원장 정상·주문 확정을 판단하지 않는다.
- `main.py`의 `NotificationService()`는 OFF이며 durable outbox나 Slack 소비자를 연결하지 않는다. P10에서 지정한 `runtime/notifications/outbox.sqlite3`은 **향후 명시적 설정용 권장 경로**이고 현재 기본 프로그램이 생성/소비하지 않는다. 토큰을 설정해도 자동 활성화되지 않는다.
- `automation.local_controller`는 기존 Fake-only DB/checkpoint 검사기다. 실제 거래 봇이나 실제 Codex worker가 아니다. 기존 controller.lock, CAS/lease/receipt 규칙은 유지한다. P5 launchd 파일은 disabled 예제로서 실제 등록 상태의 증거가 아니다.
- P7/P8 HandoffJournal, checkpoint, recovery summary는 기존대로 사용한다. 파일 소실/공동 롤백에는 UNKNOWN_NOT_RESET/HALTED를 유지한다.

## outbox의 단일 소유권

`MessageQueue`는 canonical outbox 경로의 `.lock`을 **DB 생성/연결과 SENDING 복구 전** 획득하고 close까지 보유한다. 상태 원장과 같은 커널 잠금 방식(POSIX flock, Windows msvcrt byte lock)을 재사용한다. 두 번째 인스턴스/프로세스는 즉시 실패하며 살아 있는 소비자의 SENDING을 변경하지 않는다. 초기화는 여전히 명시적이고 기존 DB 재초기화는 실패한다.

소유자 사망 시 커널 잠금은 해제되지만 lock 파일은 남긴다. 새 소유자만 SENDING을 UNKNOWN으로 전환한다. UNKNOWN은 자동 재시도/리셋하지 않는다. 기존 명시적 HTTP429의 최대 2회 제한 외에 timeout/모호한 전달을 재시도하지 않는다. reserve의 DB intent commit 후에만 Mock/향후 승인된 전송을 시도한다. close는 전송 전체와 직렬화되므로 네트워크 요청 도중 소유권을 다른 소비자에 넘기지 않는다. 전송 결과 저장 실패는 UNKNOWN 또는 durable SENDING으로 남는다. **enqueue 성공은 Slack 전달 성공이 아니다.**

이 보장은 협력하는 MessageQueue 프로세스가 동일한 로컬 파일을 사용하는 경우다. 직접 sqlite3로 쓰기, lock/DB 삭제·교체, hardlink 복사, 관리자 변조, 네트워크/동기화 볼륨을 허용하지 않는다. DB/lock identity 변경과 fork로 상속한 연결은 사용을 차단한다. 프로세스 간 전달에는 열린 DB를 넘기지 말고 종료 후 새 프로세스에서 새로 연다. 잠금은 DB+백업 공동 롤백, 메시지의 exactly-once 전달, 하드웨어 전원 손실 내구성을 증명하지 않는다.

## 읽기 전용 점검

```sh
python -m ops.local_preflight --format json
python -m ops.local_preflight --state runtime/paper-state.json --outbox runtime/notifications/outbox.sqlite3 --format markdown
```

config를 실행하지 않고 AST와 정책 JSON을 검사한다. 토큰/환경변수 값/인증파일/개인 오류·거래·메시지 원문을 읽거나 출력하지 않는다. 외부 API, 설치, launchctl, 프로세스 종료, 파일 생성/복구/SQL UPDATE를 하지 않는다. 명시한 원장·DB의 무결성 검사에 필요한 내용만 로컬에서 읽고 집계 상태만 출력한다.

기존 lock을 읽기 전용으로 열어 잠금을 보유한 동안만 snapshot을 검사한다. 누락된 lock을 생성하지 않는다. outbox sidecar가 있거나 소유자가 실행 중이면 SQLite를 열지 않는다. cold snapshot은 SQLite immutable 읽기를 사용해 WAL/SHM 생성도 방지한다. 상태 원장은 기존 엔진 validator와 P9-A replay를 실행하되 엔진 open/save/halt는 호출하지 않는다. START_CASH와 실제 과거 원장이 불일치하면 임의 보정 없이 차단한다. 평가 가격·일간 자산 snapshot은 새로 만들지 않는다.

- READY_*: 해당 시점의 제한된 로컬 검사만 통과.
- PENDING_*: 파일/실기기/증거 미확인. 새 Mac의 빈 runtime도 여기에 해당하며 초기화하지 않는다.
- BLOCKED_*: 잠금 충돌·불확실 주문/전송·무결성 실패·정책 불일치·검사 불가.
- USER_ACTION_REQUIRED: 실제 launchd 등록 및 사용자 설정 확인 필요.

전체 결과는 PENDING/BLOCKED, **항상 exit 2**다. 정상 로컬 검사도 실제 실행 승인·복구 한도·공동 롤백 신뢰를 부여하지 않는다. Windows의 소비자 잠금은 구현/CI 대상이지만 읽기 전용 cold-lock 진단은 미인증으로 BLOCKED다. 실제 ACL·남은 디스크 공간·동기화 경로·다른 이름의 launchd 서비스·다른 프로세스가 쓰는 파일까지 이 도구가 증명하지 않는다. 사용자 확인 항목을 생략하지 않는다.

## Android → MacBook M3 사용자 체크리스트

아래는 Mac에서 **사용자가 실행할 안내**다. Cloud에서는 Mac에 접속하거나 이를 실행하지 않았다. 저장소 경로는 사용자가 `~/auto_trade`에 checkout한 경우다. 다른 경로면 첫 cd만 실제 checkout 경로로 바꾼다. 변경 사항이 있으면 먼저 보존하고 강제 reset/clean은 하지 않는다.

```sh
cd "$HOME/auto_trade"
git status --short --branch
git fetch origin master
git switch master
git pull --ff-only origin master
git rev-parse HEAD
python3.12 --version
uname -sm
```

Python 3.12가 없으면 여기서 멈춘다. 이 도구는 설치하지 않는다. 기존 가상환경이 있으면 새로 만들지 말고 Python 버전을 먼저 확인한다. 아래 venv 생성/의존성 설치는 사용자가 검토한 로컬 준비 작업이며 유료 서비스가 없다.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python --version
python -m pip install -r requirement.txt
export PYTHON_DOTENV_DISABLED=1
python tools/check_safety.py
python -m unittest discover -v
python -m ops.local_preflight --format json
python -m ops.paper_rehearsal
```

`paper_rehearsal`은 기존 P9-B 통합 테스트와 P10 회귀 테스트를 호출한다. 임시 디렉터리의 PAPER 원장/Mock Slack만 사용하고 socket 연결을 차단한다. 운영 원장을 만들거나 바꾸지 않는다. 출력은 통과/실패/skip 수와 비밀 없는 요약이며 실패의 원문 로그는 전달하지 않는다. 리허설 성공도 실제 장치 운영 성공이 아니다.

런타임이 없다는 PENDING을 없애기 위해 빈 원장/outbox/영수증을 만들지 않는다. 기존 기록이 있으면 [백업·복구](P10_BACKUP_RECOVERY.md)를 먼저 따른다. 사용자는 로컬 APFS, 본인 소유 디렉터리 0700/파일 0600, ACL, 디스크 여유 공간, 기존 터미널/launchd 자동 시작이 없는지 확인한다. 권한을 무조건 chmod해서 원래 상태를 숨기지 않는다. 실패 원인을 조사한다.

검토에 전달할 요약은 새 private 디렉터리에 저장한다. 아래 redirection은 사용자가 명시적으로 수행하는 출력 저장이며 진단 도구 내부는 파일을 쓰지 않는다.

```sh
umask 077
mkdir -p "$HOME/auto-trade-private-reports"
python -m ops.local_preflight --format json > "$HOME/auto-trade-private-reports/p10-preflight.json"
python -m ops.paper_rehearsal > "$HOME/auto-trade-private-reports/p10-rehearsal.json"
```

전체 runtime/SQLite/로그/.env/토큰을 업로드하지 않는다. 위 요약, `git rev-parse HEAD`, 테스트 합계와 실패 검사 이름만 별도 ChatGPT 검토에 전달한다. P8/P9B Cloud 영수증 요약은 기존 cloud_resume 절차로 개인 보관하고 이 진단 결과로 대체하지 않는다.

## P10-B 별도 승인 전 남은 조건

1. Mac에서 위 명령·권한/프로세스/launchd/기존 원장 진단을 수행하고 요약 검토.
2. 보유 상태·원가·현금·미확정 주문/UNKNOWN 전달을 사용자 대조. 소실된 Cloud 영수증은 UNKNOWN_NOT_RESET/HALTED 유지.
3. cold backup 및 격리 복원 리허설 검토. 동시 롤백은 독립 증거 없으면 검증 불가.
4. 실제 네트워크 시작/종료·잠자기·재부팅·디스크/WAL/ACL 조건 검증 계획을 별도 승인.
5. 실제 시장 데이터 PAPER 연결과 Slack Bot/채널/무료 조건·한 명의 소비자 설정은 각각 명시적 승인 후 별도 구현·검토. 본 PR에는 운영 시작/전송 스케줄 명령이 없다.

현재 판정은 **오프라인 준비 도구 제공, 실제 PAPER 운영 승인 불가**다. 기존 전략·주문·체결·복구 정책, 0원 및 자동화 OFF 설정은 변경하지 않는다.
