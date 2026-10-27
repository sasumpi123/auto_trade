# P12: MacBook PAPER 봇 실행

기존 AutoTrade/ExecutionEngine/paper_experiment/paper_session/paper_watch를 연결한 CLI입니다. 새 엔진·원장 없이 독립 모의 계좌를 만듭니다. 기존 HALTED 계좌·백업을 복구하거나 사용하지 않습니다. Slack/launchd/무인 시작·재시작/실거래 OFF, 추가 지출 0원입니다. Cloud에서 실제 시장 연결이나 Mac 배포는 실행하지 않았습니다.

## MacBook에서 사용할 5단계

Python 3.12 가상 환경과 의존성을 갖춘 `~/projects/auto_trade`에서 실행합니다. 아래 `paper-mvp01`은 **새 실험 이름**이며 기존 계좌 경로로 바꾸면 안 됩니다. 명령은 사용자가 직접 실행하며 PR 병합이 실행 승인은 아닙니다.

```sh
cd "$HOME/projects/auto_trade"

# 1. 한 명령으로 새 private 부모 디렉터리와 독립 계좌 준비. 초기 1,000,000 KRW, 보유 0.
.venv/bin/python -m ops.paper_bot init --directory "$HOME/paper/paper-mvp01"

# 실행 전 점검 (네트워크 없음, 원장 변경 없음)
.venv/bin/python -m ops.paper_bot check --directory "$HOME/paper/paper-mvp01"

# 2. 사용자 명시 공개 시세 연결 + 기존 전략 판단 + PAPER 체결. 최대 600초 후 종료.
.venv/bin/python -m ops.paper_bot run --directory "$HOME/paper/paper-mvp01" \
  --connect-public --seconds 600 --tickers KRW-BTC
```

`init`은 기존 경로를 재사용·덮어쓰기·chmod하지 않습니다. 새 부모 디렉터리 한 단계만 만들 수 있습니다. 기존 부모가 공개 권한이면 중단합니다. 새 경로도 Git checkout/운영 runtime/인증 디렉터리·symlink에는 만들 수 없습니다. 기존 세 HALTED 계좌/락/백업을 이동·삭제·초기화하지 마세요.

다른 SSH 창에서 같은 저장소/계좌 경로로:

```sh
cd "$HOME/projects/auto_trade"

# 3. 실행 중 종목별 틱/가상 체결 수/현금/포지션/평가손익 확인
.venv/bin/python -m ops.paper_bot status --directory "$HOME/paper/paper-mvp01"

# 4. 협력적 중지 요청 (또는 실행 창에서 Ctrl+C)
.venv/bin/python -m ops.paper_bot stop --directory "$HOME/paper/paper-mvp01"

# 5. 종료 후 확정 거래와 P9 수수료 포함 정산 재조회 (외부 API 없음)
.venv/bin/python -m ops.paper_bot report --directory "$HOME/paper/paper-mvp01" --limit 20
```

`STOP_REQUESTED_NOT_YET_STOPPED`는 **요청 수락**이며 종료 확인이 아닙니다. 실행 창의 `STOPPED` + `post_validation=VERIFIED_UNDER_OWNER_LOCK`와 최종 `STOPPED_VERIFIED`를 기다린 뒤 report의 `session_state=STOPPED_VERIFIED`, `pending_orders=0`을 확인합니다. 종료 전 report가 잠금 충돌로 BLOCKED인 것은 정상입니다. `status`는 실행 프로세스의 메모리 관측을 받으므로 원장을 다시 열지 않습니다.

600초 동안 신호가 없으면 주문 0이 정상일 수 있습니다. 억지 주문·전략 완화·신호 주입 옵션은 없습니다. 공개시장 매수/매도가 실제로 발생했는지는 실기기 보고서로만 확인합니다.

## 출력과 기록

- 실행 중 5초 간격 및 체결 직후 JSON 요약: 종목별 수신 수/마지막 관측 가격, 누적 가상 매수·매도 수, 현금, 보유 수량·평균단가, 실현·평가손익/수익률. status에는 관측 나이와 `snapshot_fresh`가 표시됩니다. 오래된 관측을 현재 시세로 간주하지 않습니다.
- 기존 `experiment-state.json`에 확정 체결/잔고/평균단가/신호가 영속됩니다. 기존 `.lock`, 생성 영수증, 최초 사용 영수증 및 `session.json` 계약을 유지합니다.
- 정상 종료 때 기존 `session.json`에 마지막 세션의 제한된 수신 요약을 추가합니다. 새 원장이나 영수증 체계가 아닙니다. 다음 세션 시작 시 이전 세션 요약이 교체될 수 있지만 누적 거래 원장은 남습니다.
- report는 잠금 아래 원장·identity·P9 회계를 다시 검증하고, 최근 거래의 시각/종목/방향/가격/수량/거래금액/수수료/순실현손익을 출력합니다. 기본 최근 20개, `--limit 1~1000`. 전체 체결 수와 표시 수를 별도 표시합니다. 주문 ID·원문 reason·비밀값은 출력하지 않습니다.
- **현재 기존 PAPER 생성기는 수수료 0원**인 확정 영수증을 만듭니다. report는 실제 기록된 수수료를 합산하며 매수 수수료 포함 이동평균 원가를 P9로 대사합니다. 수수료를 임의로 추가하지 않았으며 이 수익률이 실제 Upbit 수수료·슬리피지를 반영했다고 주장하지 않습니다.
- 보유가 없으면 종료 현금으로 순수익률을 재검증합니다. 보유가 남으면 오프라인 report의 평가손익/총수익률은 **N/A: OFFLINE_NO_FRESH_MARKS**입니다. 마지막 관측 가격은 역사적 참고값이며 현재 평가에 재사용하지 않습니다. 확정 실현손익·잔여 원가·수량은 계속 조회합니다.
- HALTED 원장도 확정 회계 대사가 가능하면 `HALTED_NO_RESTART`로 읽을 수 있지만 check/run 허가나 복구가 아닙니다. UNKNOWN/미확정 주문·불일치면 보고서도 BLOCKED입니다.

## 시작·중지·장애 경계

`run`은 기존 실험 CLI의 인자 검증과 공개 시세 제한, 분봉 검증, 틱 freshness, 보유 종목 보호, 중복 프로세스 방지, 시간 제한(1~3600초)과 종료 검증을 재사용합니다. `--connect-public` 없이는 실행하지 않습니다. 기존 `--top`/`--eligible-top`/활동성 사전 관찰 옵션도 그대로 전달할 수 있으며 의미·실험 제한은 바꾸지 않습니다. 처음에는 위 BTC 단일 종목 명령으로 확인하는 것을 권합니다.

진행 조회/중지는 private 계좌 안의 `control.sock` Unix 소켓만 사용합니다. 외부 TCP/API/계좌 인증이 없고 PID를 추측해 다른 프로세스에 신호를 보내지 않습니다. 정상 종료 때 소유한 임시 소켓만 제거합니다. 원장/락/영수증은 삭제하지 않습니다. 기존 소켓·동시 실행·권한 불명확이면 중단하며 stale 소켓을 자동 삭제하지 않습니다. Mac/Linux만 지원하며 Windows 계좌 소유권은 기존처럼 BLOCKED입니다.

Ctrl+C/SIGTERM/SSH SIGHUP은 기존 감독자에 중지를 요청합니다. 네트워크/시간 오류, 비정상 데이터, 저장 실패, 종료 확인 실패 시 기존 HALT/재시작 차단 정책을 유지합니다. 시간 제한 뒤 협력 종료 여유 15초와 프로세스 회수 시간이 추가될 수 있습니다. 강제 종료는 정상 종료로 처리하지 않습니다. 자동 재연결·재시작·계좌 리셋은 없습니다.

SSH 단절 후에는 `status`와 `report`로 상태를 확인하세요. BLOCKED/IN_PROGRESS/UNKNOWN/HALTED, 소켓 접속 실패 또는 원장 불일치면 재실행하지 말고 결과를 검토합니다. 파일 삭제나 이전 백업 덮어쓰기로 해제하지 않습니다. 정상 종료 후 백업이 필요하면 [기존 cold backup 절차](P10_BACKUP_RECOVERY.md)를 사용합니다. 실기기 sleep/네트워크/SSH 종료·APFS 권한은 Mac에서 추가 확인해야 합니다.

## 이번 검증의 완료 범위

Fake 200개 캔들을 기존 DataAnalyzer/Legacy 조건으로 분석해 BUY를 만들고, Fake ticker 3건으로 **매수 → 쿨다운 보유 → 기존 +5% 익절 매도 → 실제 PAPER 원장 정산 → 정상 종료 → 읽기 전용 기록 재조회**를 한 통합 시험에서 검증합니다. 초기 1백만원, 매수 40만원/잔여 현금 60만원, 최종 현금 102만원, 확정 순실현손익 2만원, 기록 수수료 0원입니다. 이 숫자는 **합성 시험 결과**이며 실시간 공개시장 성과가 아닙니다.

별도 시험에서 Unix IPC 상태/중지와 실제 자식 프로세스의 협력 종료, 중복 계좌/소켓·권한·원장 손상·HALTED 읽기 및 보호 파일 불변을 검증합니다. 연구 CLI·실제 과거 데이터 전략 비교는 실행하지 않습니다. 실제 MacBook 공개시장 체결·장시간 운영·실제 수익성은 미검증입니다.
