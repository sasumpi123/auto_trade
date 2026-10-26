# P10-H: 신규 격리 PAPER의 실시간 활동성 사전 관찰

이 기능은 P10-F의 독립 실험만 대상으로 한다. 기본 동작은 여전히 offline/read-only이며, 기존 `--top`과 `--eligible-top`의 의미를 변경하지 않는다. 기존 BTC HALTED 원장과 HALTED 실험 원장을 복구·변경·재시작하지 않는다. 원장 생성은 기존 별도 명시 명령만 사용하고, 이 문서의 실행 예시는 현재 HALTED 계좌에 적용하지 않는다.

사용자가 보고한 P10-G 독립 진단에서는 7종목 중 BLAST만 0건, 다른 6종목은 수신했고 MARKET_IDLE로 중단됐다. 이것은 해당 진단의 관측 결과이며, BLAST의 거래 부재나 API 장애를 증명하지 않는다. 과거 PAPER_STALE_TICK의 구체적 원인도 여전히 미확인이다. Cloud에서는 실제 시장/원장/Mac에 접근하지 않았고 Fake만 실행했다.

## 서로 다른 세 가지 조건

- `--top N`: rolling 24h 거래대금 순위 상위 N개. 분봉이나 활동성 때문에 다른 순위 종목으로 대체하지 않는다.
- `--eligible-top N --candidate-limit M`: 거래대금 상위 M 후보 중 기존 연속 분봉 조건에 적합한 N개. 무체결 분봉 공백을 채우거나 지표를 변경하지 않는다.
- 신규 `--observe-realtime-seconds S`: 위에서 선택한 종목과 모든 보유 종목을 1~60초의 고정 창 동안 별도 관찰한다. 권장 창은 60초다. 조기에 몇 건을 받았다는 이유로 통과하지 않는다. 한 종목이라도 미충족이면 준비 실패다.

대체 후보 선정은 추가로 `--select-realtime-active`를 지정한 경우에만 허용한다. 이 옵션은 `--eligible-top`, `--candidate-limit`, `--observe-realtime-seconds`를 모두 요구한다. 상위 M에서 연속 분봉에 적합한 후보 전체를 관찰한 뒤, 실시간 조건도 충족한 후보 N개를 원래 순위대로 선정한다. 보유 종목은 별도로 반드시 검증하고 추가하며 제외할 수 없다. 후보 부족이면 상한을 확대하지 않고 실패한다. `ranking_deviation_declared`, 후보/제외 이유, `final_strategy_markets`를 보고하므로 실제 거래대금 상위 N과 다른 선정을 숨기지 않는다.

## 준비·시작·운영 경계

기존 원장 소유권/무결성/복구/실험 identity 검사를 통과하고 같은 프로세스 락을 유지한 상태에서 순위·분봉·분석기 준비 → WebSocket 관찰 → 캐시 분봉 유효성 재검사 → 최초 엔진 저장 직전 검사 순으로 진행한다. REST 예산 64회, 후보/보유 합집합 30개 상한은 그대로다. 활동성 검사 자체는 REST를 추가 호출하지 않는다. 분봉은 재조회/보간하지 않으므로 관찰 중 캐시가 만료되면 실패한다.

활동성 증거는 서버 응답의 `stream_type=REALTIME`이며 timestamp/가격/벽시계-단조시계 검사를 통과해야 한다. `SNAPSHOT`은 수신 횟수만 집계하고 활동성/전략 입력으로 사용하지 않는다. 타입 누락/미지원은 실패한다. 동일 timestamp를 반복 수신해도 timestamp 진행 시각을 갱신하지 않는다. 창 종료 시 실시간 메시지 1건 이상 및 마지막 timestamp 진행 10초 이내가 필요하다. 창 종료→최초 저장/실행 시작은 최대 5초, 선택 종목의 진행 나이는 15초 미만이어야 한다. 검증은 최초 저장 직전에도 수행한다.

이 제한은 사전 관찰이 미래의 활동성을 보장한다는 뜻이 아니다. 실행에는 관찰 소켓을 그대로 재사용하고 timestamp/진행 시각도 이어받는다. 타이머를 초기화하거나 사전 가격으로 매매하지 않는다. 운영 중 기존 전체 무갱신 15초/종목별 60초 제한과 timestamp/시계 검사는 유지한다. 보유 종목 무수신이면 HALTED이며 자동 매도/삭제/교체하지 않는다.

명시 대체 모드에서는 관찰한 후보 전체가 실제 `wire_subscription`/`final_subscription`에 남는다. 최종 전략 종목은 그 부분집합(`fixed_strategy_markets`/`final_strategy_markets`)이며 둘을 별도 보고한다. 새 연결/재구독으로 관찰 증거를 무효화하지 않기 위한 설계다. 제외/비선정 후보의 메시지 및 스냅샷은 전략에 전달하지 않고 무수신 검사만 수행한다. 전략 시작 후 종목 교체·자동 재구독·재연결은 없다.

준비 실패(무수신/후보 부족/연결 실패/시간 초과/Ctrl+C/SIGHUP/증거 만료)는 첫 엔진 저장 전이면 원장 SHA 불변이고 정상 기존 원장에 새 HALT를 기록하지 않는다. 기존 session/일회성 사용 영수증은 준비 시도를 보존하므로 원장 무변경을 모든 companion 파일 무변경으로 해석하지 않는다. 저장 시작 후 I/O 실패나 실행 장애는 기존 HALTED/미확인 정책을 따르며 롤백하거나 재시작을 허가하지 않는다. 원장·checkpoint 공동 롤백 탐지 보장은 추가하지 않았다.

## 유한한 진단과 원인의 한계

종목별 첫 수신/첫 실시간 수신까지 ms, 실시간/스냅샷/미확인 건수, 마지막 실시간 수신 후 ms, timestamp 무진행 ms를 출력한다. 관찰 통계는 종료 시점으로 고정하고, 보고 시 증거 나이와 마지막 시작 검사 지연은 별도 표시한다. 기존 독립 `ops.public_tick_diagnostic`에도 수신 종류별 건수·첫 수신·무수신 ms를 추가했다. 공개 ticker 프레임·주문 ID·원장 원문·토큰·환경변수·서버 close reason은 출력/저장하지 않는다. 관찰 메시지 100,000건에서 차단하며 종목 수 제한/수치 요약만 유지하고 자동 로그 파일은 만들지 않는다.

0건은 `NO_REALTIME_TICKS_DURING_OBSERVATION`이다. 스냅샷만 수신하면 실시간 구독 지원/전달은 미검증이다. 아무 메시지도 없으면 연결/활동성 원인은 UNKNOWN이다. 다른 종목 실시간 수신은 개별 무수신 종목의 건강성을 증명하지 않는다. 실제 WebSocket close를 관찰한 경우에만 숫자 close code를 기록하며, 연결/read 오류와 검증 중단을 구분한다. 원인이나 실제 강제 종료 여부를 추정하지 않는다.

공식 확인 대상:

- <https://docs.upbit.com/kr/reference/websocket-ticker>
- <https://global-docs.upbit.com/reference/websocket-ticker>

이번 Cloud에서 두 문서 조회는 프록시 연결 오류로 실패했다. 기존 요청의 `isOnlyRealtime: true`를 유지하며, 요청에 쓰인 camelCase와 사용자가 지칭한 `is_only_realtime`의 공식 관계/지원 범위 및 snapshot 규격은 **PENDING_EXTERNAL_DOC_VERIFICATION**이다. 임의 별칭을 추가하거나 구독 ACK/순서 보장을 가정하지 않았다. 응답의 명시적인 REALTIME 분류와 실제 timestamp 진행만 보수적으로 검증한다. 사용자 계정/Mac에서 공식 문서와 실응답의 지원 여부 확인이 필요하다. 서버 라벨도 실제 체결 발생의 독립 증거가 아니다.

## Mac SSH에서 추후 실행할 순서

PR 독립 리뷰와 별도 사용자 실행 판단 후, **HALTED가 아닌 새 독립 실험 계좌**에서만 아래를 택일한다. 이 PR은 생성/실행을 대신 승인하지 않는다. 생성·잠금·cold backup·복구 경계는 [P10-F](P10F_ISOLATED_PAPER_EXPERIMENT.md), [P10 백업](P10_BACKUP_RECOVERY.md)을 재사용한다.

```sh
cd "$HOME/projects/auto_trade"
# 사용자가 확인한 새 독립 실험 private 디렉터리. 기존 두 HALTED 경로 사용 금지.
experiment_dir="$HOME/Library/Application Support/auto_trade/paper-experiments/USER_SELECTED_NEW_EXPERIMENT"
# 기본 offline/read-only (비승인 보고 exit 2 가능)
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir"

# 추후 별도 결정 시: 기존 연속 분봉 선정 결과 전부의 활동성 요구, 대체 없음
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 180 --eligible-top 7 --candidate-limit 10 \
  --observe-realtime-seconds 60

# 위와 택일: 상위 10 후보 중 분봉+활동성 적합 7개를 명시적으로 선정
.venv/bin/python -m ops.paper_experiment --directory "$experiment_dir" \
  --connect-public --seconds 180 --eligible-top 7 --candidate-limit 10 \
  --observe-realtime-seconds 60 --select-realtime-active
```

180초에는 연결·준비·관찰·운영이 포함되며 정리/감독자 회수는 별도다. 충분한 시간 여유가 없으면 시작하지 않는다. Ctrl+C 후 종료 상태를 확인하며 SSH 단절/SIGHUP/미확인 종료 후 자동 재실행하지 않는다. 읽기 전용 대사, PID/락/실제 종료 확인, cold backup으로 진행하고 파일 삭제/수동 halt 해제는 하지 않는다. 공유할 요약은 최종 전략/구독 종목·제외 이유·활동성/시각 통계·engine_started·종료/대사 상태만 사용한다.

## 검증 범위와 남은 제한

Fake 테스트는 7개 중 1개 60초 무수신, 전부 수신, snapshot만 수신, 미분류 프레임, 명시 대체/대체 금지, 후보 부족, 보유 종목 사전/운영 무수신, 연결/close/읽기 오류, 고정 전략 종목, 증거 만료, 중복 소유권, 중단 이벤트를 검증한다. 기존 P10-F/G의 실제 자식 프로세스 SIGINT/SIGHUP/강제 종료 회귀 테스트도 유지한다. Fake 기존 BTC 및 두 번째 HALTED 실험의 원장·lock·백업·session/receipt 불변을 매 테스트 확인한다. 기존 855개에 25개를 추가하여 총 880개다. 정확한 OS별 결과/head/run/attempt는 PR에 기록한다.

실제 Mac의 새 모드/공식 응답 규격/60초 관찰·시작 지연/선정 결과/장시간 활동성/절전·SSH 종료/저장 내구성은 미검증이다. 실거래·Slack·launchd·자동 시작·유료 API/모델/인프라·자동 PR 게시/병합은 계속 OFF이며 전략/holdout/원장 복구 정책도 바꾸지 않았다.

P10-I 후속: [소폭 timestamp 역행의 실험적 폐기](P10I_SMALL_TIMESTAMP_REGRESSION.md)는 새 계좌 최초 실행의 별도 명시 옵션만 다룬다. 기본/사전 관찰은 엄격 HALT이며, 버린 가격과 timestamp는 전략·평가·활동성 타이머를 갱신하지 않는다. 기존 세 HALTED 계좌의 복구/재시작은 허용하지 않는다.
