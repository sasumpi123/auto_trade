# P10-D 공개 시세 freshness와 기존 HALTED PAPER 원장

실제 Mac 장애 보고를 바탕으로 만든 수정이다. Cloud에서 사용자 Mac/운영 원장/백업에 접근하지 않았다. 실제 Upbit 시세, 실제 주문/계좌 API, Slack, launchd를 실행하지 않았다. REAL_TRADING=False, Slack/자동화/유료 실행 OFF와 기존 전략·체결·원장 계약을 유지한다.

## 확인된 코드 원인과 아직 확인하지 못한 사실

P10-C는 KRW 전체 ticker를 조회하고 모든 행의 `timestamp`가 5분 이내일 것을 요구했다. 따라서 상위 10개가 정상이더라도 순위 밖 종목 하나가 오래되면 PUBLIC_STALE_TICKER였다. 사용자 응답 원문을 수집하지 않았으므로 **실제 실패 종목/시각/사용자 시계 정확성은 미확인**이다. 이 코드 경로를 Fake로 재현했으며 실제 원인을 하위 종목으로 확정한 것은 아니다.

공식 참고 문서:

- [현재가 정보](https://docs.upbit.com/kr/reference/list-tickers): `timestamp`, `trade_timestamp`, `acc_trade_price_24h`.
- [분봉](https://docs.upbit.com/kr/reference/list-candles-minutes): 체결이 없는 구간의 캔들 생성 조건.

이번 Cloud의 공식 문서 HTTP 접근은 실패했다. `timestamp`의 정확한 생성/갱신 보장과 비유동 시장의 실제 갱신 동작은 **공식 원문 재확인 PENDING**이다. `trade_timestamp`는 최근 체결 시각을 나타내는 필드로 취급하되, 두 필드를 HTTP 응답 생성 시각이나 요청 시각으로 덮어쓰지 않는다. 모든 시장이 요청 시점에 새 체결/새 timestamp를 가져야 한다고 가정하지 않는다. 사용자에게 제공된 문서 조건(무체결 분봉 미생성)을 검토에 반영했지만 이 세션에서 원문을 확인했다고 주장하지 않는다. Mac에서 문서/공개 응답을 확인하기 전 지원 보장을 확대하지 않는다.

## 순위와 시간 검증

1. 등록된 KRW 전체 집합을 최대 100개씩 조회한다. 누락/중복/다른 종목, 비정상 가격/24h 거래대금, 누락/비정상/미래 timestamp·trade_timestamp는 하위 종목이라도 차단한다. 429/HTTP/JSON 오류에 재시도나 다른 소스 fallback을 하지 않는다.
2. HTTP `Date`가 있어야 하고, 로컬 시각과 5초 이내여야 한다. 응답 수신도 최대 5초, 벽시계/monotonic 불연속은 차단한다. 오래된 응답 전체를 최신 순위로 간주하지 않는다. Date 부재/시계 오차는 PUBLIC_RESPONSE_CLOCK_UNVERIFIED이며 자동 시계 보정은 없다. HTTPS 서버가 제공하는 HTTP Date는 가격 정확성/인증된 영속 증빙 자체가 아니다.
3. **전체 응답의 원래 `acc_trade_price_24h`로 정렬한 뒤** 상위 N을 선정한다. 상위 N의 두 시각 모두 5분 이내인지 재검사한다. 오래된 상위 종목을 버리고 차순위로 대체하지 않는다. 하위 오래된 종목도 순위 계산에서 제거하지 않는다. UTC 일봉 거래대금으로 바꾸지 않는다.
4. 전체 ticker 스캔은 최대 30초이며 시계 불연속/역행을 차단한다. 분할 API는 원자적인 동일 시점 스냅샷이 아니다. 경계 순위는 조회 사이 변할 수 있고 30초 제한은 이 한계를 없애지 않는다.

시각은 로컬 요청 시각으로 조작하지 않는다. 상위 종목의 최근 체결도 확인할 수 없으면 보류한다. 위 5초/5분/30초는 보수적인 이 도구의 정책이며 Upbit가 보장하는 SLA라고 주장하지 않는다. 실제 서버 Date 헤더/응답 스키마/전송 지연과 Mac 시계는 실기기 재검증 대상이다.

분봉 200개는 원본만 사용한다. 무체결로 실제 분봉 공백이 생길 수 있으므로 PUBLIC_CANDLE_GAP이 곧 API 손상을 의미하지는 않는다. 그러나 기존 전략의 연속 1분 입력 계약을 유지하기 위해 **여전히 차단**한다. 가상 봉/forward fill/지표 변경/비연속 봉을 200분으로 해석하지 않는다. 이번 수정으로 상위 순위 조회가 통과해도 해당 종목의 캔들 공백으로 운영이 중단될 수 있다.

## 조회 실패와 매매 원장 구분

`ExistingPaperStore.acquire()`는 원래 원장 `.lock`을 독점 확보하고 기존 원장·확정 거래를 검증한 **뒤**, 아직 `engine.open()`이 상태를 저장하기 **전**에 종목 선택을 수행한다. 잠금 전 공개 API 호출은 세션 경로에서 하지 않는다. 조회 실패 시 잠금을 해제하되 디스크를 저장하지 않는다. 기존 BTC 보유/현금/주문/신호/HALT는 바이트 단위로 그대로다.

조회 실패는 STOPPED 성공으로 바꾸지 않는다. 세션 status=HALTED, engine_started=false, post_validation=UNVERIFIED, 종료 코드2다. `ledger_snapshot=VERIFIED_BEFORE_DISCOVERY_ONLY`는 잠금 아래 조회 직전의 마지막 검증 요약이며 실패 후 현재 디스크 재검증이 아니다. 부모의 after_close 점검을 별도로 확인한다. 세션 HALTED와 저장 원장의 halted=false가 함께 나올 수 있다. 자동 재시도/재시작은 없다.

기존에 HALTED/미확정/UNKNOWN/불일치가 저장된 원장은 **조회 전에 차단**한다. engine.open의 기존 암묵적 halt 해제를 복구 수단으로 사용하지 않는다. 캔들 준비·스트림·매매·저장 등 엔진 시작 이후 장애는 기존 영속 HALT 경로를 유지한다. 모든 시작 실패를 복구 가능한 것으로 재분류하지 않는다. 다른 경로의 기존 main.py는 이 단계 분리의 보장을 받지 않는다.

## 현재 사용자 원장: 읽기 전용 점검

먼저 모든 기존 writer를 정상 종료한다. 이름만 보고 pkill/kill하거나 lock을 삭제하지 않는다. 현재 HALTED 원장을 재실행하지 않는다. [기존 Mac 점검](P10_MAC_PREFLIGHT.md)과 [cold backup](P10_BACKUP_RECOVERY.md)을 먼저 따른다. 실행 중 git pull/백업/다른 checkout 봇을 시작하지 않는다.

검토·병합 후 Mac SSH에서 실행할 명령이다. 이 Cloud에서 실행한 것이 아니다.

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
.venv/bin/python -m ops.paper_recovery_diagnostic --state runtime/paper-state.json
```

진단은 네트워크/engine.open/save/Slack/초기화 없이 기존 cold_lock을 잡아 읽는다. exit2는 의도된 것으로 실행 승인이 아니다. Windows read-only 잠금 검증은 BLOCKED다. 파일 부재·권한·손상·미확정 거래·잠금 충돌이면 대사를 성공으로 표시하지 않는다. 설정은 AST로 읽으며 dotenv/토큰을 읽지 않는다. 원장 원문/주문 ID/사유/신호 원문/예외는 출력하지 않고 PAPER 숫자와 상태/해시만 출력한다.

기존 절차로 안전하게 보관한 **비공개 cold backup**의 실제 경로를 알고 있을 때만 다음 옵션을 추가한다. 예제 변수를 실제 백업 파일로 지정하며 파일을 이동/덮어쓰지 않는다.

```sh
# 이 경로는 예시다. 존재하는 private .json cold backup을 직접 지정한다.
backup_json="$HOME/auto_trade-cold-backup/paper-state.json"
.venv/bin/python -m ops.paper_recovery_diagnostic \
  --state runtime/paper-state.json --backup "$backup_json"
```

두 파일과 각각의 부모는 사용자 소유·private이어야 한다. 원본은 기존 잠금 아래 검사하고, 백업은 모든 writer가 종료된 cold copy라는 사용자 확인이 필요하다. 읽기 전후 원본/백업 내용 동일성을 검사하지만 파일 잠금/해시는 공동 롤백이나 백업 최신성을 증명하지 않는다. DB/outbox/sidecar/Controller/checkpoint의 동일 세대 보존은 기존 backup 문서를 따른다.

대사 확인 기준:

- ownership=READY_IDLE_AT_SNAPSHOT와 ledger=RECONCILED_READ_ONLY. 잠금 획득은 모든 다른 경로의 봇이 없다는 증명이 아니다.
- 보고된 사고에서는 확정 매수 1건/매도 0건, pending_orders=0을 기대한다. 취소 0체결과 실제 체결을 구분하고, 추가 확정 거래가 있다면 원인을 확인하기 전 중단한다.
- 현금 600,000과 BTC 수량 약 0.003662601179357579753140680511은 **사용자 보고값**이다. 진단은 보고값으로 원장을 맞추지 않고 확정 주문의 수량/거래대금/수수료를 순서대로 재생하여 저장 현금·수량·엔진 평균단가와 대사한다. 수수료 포함 잔여 원가는 별도로 표시한다. 평균단가는 반올림된 보고 수량으로 역산해 덮어쓰지 않는다.
- source_sha256/backup_sha256, backup_bytes_equal과 backup_accounting_equal을 구분한다. 후자는 cash/positions/orders/signals 동일성으로 HALT 차이를 제외하지만 **HALT 삭제 허가가 아니다**. 백업이 매수 이전이라 다르더라도 원본을 백업으로 되돌리지 않는다.
- 기존 PAPER_SESSION_FAILURE는 일반 사유라 PUBLIC_STALE_TICKER를 원장만으로 입증할 수 없다. cause=PENDING_EXTERNAL_SESSION_EVIDENCE_REQUIRED를 유지한다. 당시 세션 요약의 reason/engine단계/신규 틱·주문 0, 사고 전 백업과 사고 후 주문 이력, 사용자 관찰을 함께 검토한다. 오래된 버전 요약은 신규 주문 수 대신 누적 주문 수를 표시하므로 혼동하지 않는다. 이 자료도 독립 rollback 방지 증빙이 아니다.

## 별도 수동 복구 계획 — 이번 PR은 변경 명령을 제공하지 않음

1. 현재 HALTED 원장과 관련 파일을 완전 종료 후 그대로 private cold backup하고 해시/코드 SHA/진단 요약을 보관한다. 과거 백업으로 덮어쓰지 않는다.
2. 원장 대사·pending=0·단일 소유권·사고 전후 체결 이력·실패 원인을 독립 ChatGPT 검토에 전달한다. 개인 원장 전체/토큰/로그는 GitHub에 업로드하지 않는다. 비밀 없는 요약만 전달한다.
3. 정확한 원장 해시와 원인을 특정해 사용자가 **별도로 수동 복구 변경을 명시 승인**해야 한다. 현재 일반 사유만으로 원인을 입증할 수 없거나 rollback/UNKNOWN이 남으면 HALTED를 유지한다.
4. 승인 이후에도 단순 halt_reason 삭제, engine.open 호출, 과거 백업 복원은 금지다. 과거 HALT 증거를 보존하고 동일 해시·잠금·정합성을 재확인하는 감사 가능한 전환 절차의 별도 구현/리뷰가 먼저 필요하다. 이번 PR에는 그런 mutator가 없으므로 **현재 원장의 HALT 해제는 미완료/차단**이다.
5. 복구가 별도 검토된 뒤에만 공개 데이터 PAPER 실행을 다시 명시 결정한다. Slack/launchd/실거래 승인으로 확장하지 않는다.

## 실기기 재검증과 한계

Cloud에서는 Fake HTTP Date/전체 순위/공백 분봉/스트림과 실제 격리 PAPER 원장·잠금만 사용했다. 기존 BTC 보유 원장의 조회 실패 전후 bytes 동일, 기존 HALTED 재시작·조회 차단, 정상/손상/미확정 원장 진단, 백업 비교와 비인가 복구 차단을 테스트한다. 같은 head의 3 OS 테스트/skip/Safety 결과는 PR에 기록한다.

Mac에서는 공식 문서 시각 의미, Date 헤더와 시계, 실제 실패 시장의 순위/시각(원문·비밀 없는 요약), 원장/백업 대사, APFS 권한/잠금, Ctrl+C/시간 제한/SSH 단절·절전/재부팅을 추가 확인해야 한다. 기존 HALTED 원장은 복구 승인 전 공개 연결 금지다. 실기기 성공, 실제 24h 순위 검증 완료, 기존 HALT 복구 완료라고 보고하지 않는다. 추가 지출/모델 호출/실제 주문/Slack/launchd/자동 병합은 모두 OFF다.

P10-E 후속: [감사형 수동 복구 프로토콜](P10E_MANUAL_PAPER_RECOVERY.md)이 추가되었다. 실제 원장 변경 CLI는 여전히 BLOCKED이며, 독립 증거·일회성 승인·롤백 기준이 없는 현재 사용자 원장은 그대로 HALTED다. Fake 전환 검증을 실제 복구 완료로 해석하지 않는다.
