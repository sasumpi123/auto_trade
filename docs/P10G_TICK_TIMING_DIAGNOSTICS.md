# P10-G 실시간 ticker 시각 진단과 HALTED 보고

## 확인한 사실 / 미확인인 실제 원인

사용자가 Mac에서 보고한 DATA_PREPARED, 7종목/943건, REST12/64, 주문0, PAPER_STALE_TICK/HALTED를 출발점으로 삼았다. Cloud에서 Mac/기존 BTC 원장/신규 HALTED 실험 원장이나 공개 시장에 접근하지 않았다. 원장을 복구·재시작·초기화하지 않는다.

확인된 **코드의 문제**는 다음과 같다.

- 이전 PAPER_STALE_TICK 조건이 timestamp 누락/타입/60초 초과 지연/+5초 초과 미래/동일 종목 역행을 하나로 묶었다. 해당 메시지/숫자가 없어 어느 분기인지 실제 사고 보고만으로 확정할 수 없다. 절대 시계 오차도 배제할 수 없다.
- 이전 session_ticks는 검증을 통과한 메시지만 셌다. 보고된943을 거절된 틱까지 포함한 총 네트워크 수신 수라고 단정하지 않는다.
- 감독자가 자식의 명시적 HALTED도 FORCED_STOP_OR_UNCONFIRMED_SESSION으로 감쌌다. **이 문구만으로 실제 강제 종료가 있었다고 판단할 수 없다.**
- 원장의 일반 PAPER_SESSION_FAILURE만으로는 원인을 독립 입증할 수 없다. 새 진단도 과거 사건의 사라진 메시지를 재구성하지 않는다.

실제 사고 종목, 수신/이전 timestamp, 지연·미래·역행 여부와 크기, Mac 절대 시계 정확성, 거래소/전송/SDK 순서 원인은 **미확인**이다. 관찰된 HALTED 원장은 계속 보존한다.

## 공식 규격 조사와 정책

공식 참고:

- https://docs.upbit.com/kr/reference/websocket-ticker
- https://docs.upbit.com/kr/reference/websocket-usage-and-error-guide

이번 Cloud의 공식 문서 HTTP 요청은 ProxyError로 실패했다. 따라서 timestamp/trade_timestamp의 정확한 정의·갱신 단위·동일 종목 수신 순서 보장·소폭 역행이 정상이라는 명제를 원문으로 새로 확인했다고 주장하지 않는다. 해당 사항은 **공식 근거 확인 PENDING**이다. 다른 비공식 API/스크래핑/토큰 추출/유료 서비스로 대체하지 않았다.

현재 구현은 기존과 같이 `timestamp`를 epoch millisecond로 검증한다. `trade_timestamp`는 보조 숫자 차이만 관찰하며 timestamp를 대체하거나 최신 시각으로 고치지 않는다. 서버 제공 두 필드의 차이도 원문 확인 없이 네트워크 지연/체결 순서 증명으로 해석하지 않는다.

**소폭 역행도 HALTED 유지**다. 허용 임계값/ignore 옵션은 없다. 향후 완화에는 공식 의미·순서 근거, 오래된 가격을 전략에 절대 전달하지 않는 검증된 폐기 정책, 중복/연속 역행·미갱신·보유 위험관리 실패 감시 및 별도 리뷰가 필요하다. 이번에는 근거 부족으로 완화하지 않는다.

## 시각 분류와 수치

`services.tick_timing.TickTiming`은 네트워크/원장 I/O가 없는 공통 검사다. 기존 PAPER_STALE_TICK 코드는 호환성을 위해 유지하고 `tick_timing.samples[].issue`로 세분한다.

| issue | 조건 / 기존 root reason |
|---|---|
| TIMESTAMP_MISSING | timestamp 키 없음 / PAPER_STALE_TICK |
| TIMESTAMP_TYPE_INVALID | int 이외(bool/None/문자열/float 포함) / PAPER_STALE_TICK |
| TIMESTAMP_DELAYED | 로컬 수신 시각보다60초 초과 지연 / PAPER_STALE_TICK |
| TIMESTAMP_FUTURE | 로컬 수신 시각보다5초 초과 미래 / PAPER_STALE_TICK |
| TIMESTAMP_REGRESSION | 같은 종목의 마지막 통과 timestamp보다 작음 / PAPER_STALE_TICK |
| CLOCK_DISCONTINUITY | wall/monotonic 경과 차이5초 초과, 역행/비정상 시계 / PAPER_CLOCK_DISCONTINUITY |
| STREAM_IDLE | 전체 유효 timestamp 진전15초 중단 / PAPER_STREAM_TIMEOUT |
| MARKET_IDLE | 선택/보유 포함 특정 종목의 진전60초 중단 / PAPER_MARKET_STALE |

동일 timestamp는 기존처럼 허용하되 equal_timestamp로 센다. 동등 시각이 가격/거래의 중복 여부를 입증한다고 주장하지 않고 전략 조건도 바꾸지 않는다. 동일 시각 반복은 liveness 타이머를 갱신하지 않아 새 메시지가 계속 도착해도 장기 미갱신을 숨기지 못한다. 거절된 시각/가격은 전략으로 전달하지 않는다. 보유 종목도 전체 구독 목록의 동일 감시 대상이다.

종목별 received/timing_accepted/equal_timestamp/분류별 횟수, 전체 received_total/timing_accepted_total, 오류 샘플을 제공한다. timing_accepted는 **시각 검사를 통과했다는 뜻**이며 전략 호출/매수·매도/체결 수가 아니다. 공개 진단의 valid_public_ticks는 시각과 가격 모두 검사한 건수다. PAPER session_ticks/주문 수와 구분한다.

숫자 샘플은 수신 timestamp(ms), 마지막 통과 timestamp, 두 값의 차이, 로컬 수신 wall ms와 timestamp 차이, 두 서버 제공 필드 timestamp−trade_timestamp 차이를 표시한다. CLOCK에는 wall/monotonic 경과·차이를, 미갱신에는 idle_ms를 표시한다.

**server_relative_delay_ms는 null(N/A)**이다. 검증된 독립 서버 수신 시계/offset이 없으므로 로컬 수신−timestamp를 서버 기준 지연이라고 꾸미지 않는다. 이 로컬 차이는 시계 오차·서버 생성/전송/큐잉 영향을 포함할 수 있다. timestamp−trade_timestamp도 서로 다른 필드의 단순 산술 차이이며 one-way latency가 아니다.

## 개인정보와 로그 상한

새 timing payload는 최대100종목, 고정 분류, 최근16개 샘플, 카운터2,147,483,647까지다. 숫자는 ±10^16 범위로 제한하고 제한값을 출력한다. 잘못된 문자열 timestamp의 값은 버리고 타입만 표시한다. 원시 WebSocket/HTTP body·가격·주문 ID·토큰·환경변수·원장 원문은 이 진단에 저장하지 않는다.

새 timing payload는 최대65,536 bytes 범위로 테스트하며 PAPER의 최종 요약에만 추가한다(기존 5초 상태 출력에는 반복 첨부하지 않음). 공개 전용 CLI는 최대131,072 bytes의 최종 JSON1건, 최대100,000메시지/600초다. 출력 제한 초과도 HALTED/exit2이며 성공으로 처리하지 않는다. **자동 생성·보관 로그 파일 수는0**이고 log-dir/무제한 dump/자동 회전 파일 기능은 없다. 사용자가 shell로 리다이렉트한 파일의 보관은 도구가 관리하지 않는다. 필요한 요약만 private 장소에 보관하고 파일 누적을 직접 관리하며 원문/인증정보를 공개 업로드하지 않는다.

## 감독자와 HALTED 회계 대사

자식 HALTED의 reason/post_validation/tick_timing을 그대로 보존하고 별도 child_status/child_reason/supervisor_reason/process_exit_code/termination_requested/forced_termination으로 감독 결과를 표현한다. 자식이 PAPER_STALE_TICK을 반환했다면 root reason을 모호한 FORCED_STOP으로 바꾸지 않는다. 종료 정리 오류는 cleanup_error로 분리해 원래 오류를 숨기지 않는다.

- CHILD_REPORTED_HALTED: 자식이 명시적으로 중단. 실제 kill을 의미하지 않는다.
- FORCED_TERMINATION_CONFIRMED: 이 감독자가 kill을 요청했고 POSIX exitcode가 -SIGKILL인 경우에만 확인한다.
- TERMINATION_REQUESTED_OUTCOME_UNCONFIRMED: kill 요청은 있으나 결과를 확정하지 못함. Windows exitcode1만으로 자연 오류와 TerminateProcess를 구분하지 않아 보수적으로 이 상태를 유지한다. forced_termination=false만으로 "아무 kill도 없었다"고 주장하지 않는다.
- CHILD_RESULT_UNCONFIRMED/CHILD_EXIT_UNCONFIRMED: 결과 부재/비정상 종료. 성공·자동 재시작 권한 없음.

자식 STOPPED라도 종료 후 상태 검증 실패는 POST_CLOSE_STATE_UNVERIFIED로 HALTED다. 자식이 이미 HALTED였다면 원인 코드를 유지한다. 실험 `finish`도 알려진 자식 시각 오류를 최종 reason으로 보존하되 session_finalization=HALTED_NO_RESTART다.

`offline_report`는 HALTED 원장을 정상 summary로 바꾸지 않는다. 별도 `halted_reconciliation`에서 기존 P10-D `diagnose`/순수 P9 replay를 재사용해 잠금 아래 회계만 대사한다. 0주문·현금100만원도 회계 대사는 가능하다. 그 결과 RECONCILED_READ_ONLY는 복구/원인 증명/정상 종료/시작 권한이 아니다. restart_authorized/recovery_authorized는 false이며 기존 HALT/실험 session/백업을 변경하지 않는다.

## 원장 없는 공개 WebSocket 진단

새 명령은 PAPER 실행/실험 생성/원장 경로/REST 순위 선택 옵션이 없다. 계좌·원장·전략·체결을 열지 않고 명시한 종목의 공개 ticker 시간만 관찰한다. 자동 재연결이 없고 첫 시각/수신/가격 오류에서 중단한다. 기본은 네트워크 OFF다.

아래는 PR 독립 검토·병합 후 사용자가 Mac에서 **진단 연결을 별도로 결정했을 때만** 실행할 안내다. Cloud에서 실제로 실행하지 않았다. 두 HALTED 계좌의 재실행이 아니다.

```sh
cd "$HOME/projects/auto_trade"
.venv/bin/python -m ops.public_tick_diagnostic
# 위 명령은 DISABLED/exit2, 네트워크·원장 접근 없음

# 명시적 공개 ticker 관찰만, 최대180초. 실제 현재 상위7을 뜻하지 않는다.
.venv/bin/python -m ops.public_tick_diagnostic --connect-public --seconds 180 \
  --tickers KRW-BLAST KRW-BABY KRW-IRYS KRW-T KRW-ORDER KRW-XRP KRW-CAP
```

감독은 자식 시작/연결/관찰 기한을 관리하고 Ctrl+C/SIGTERM/SIGHUP에 정리 요청을 전달한다. 정리 대기15초와 회수 대기가 추가될 수 있다. SIGKILL/전원 손실로 최종 JSON이 없으면 결과 미확인으로 남긴다. STOPPED라도 0건/diagnostic_has_valid_ticks=false는 유효 시세 수신 성공이 아니다. 어떠한 결과도 PAPER 시작/복구 승인으로 전환하지 않는다.

기존 HALTED 원장은 writer가 완전히 종료되고 기존 private 권한/lock을 확인한 뒤에만 기존 읽기 전용 명령으로 대사한다. actual 경로는 사용자가 알고 있는 파일을 직접 지정한다. 이 단계에서 backup 복원/삭제/새 실험 생성/연결은 하지 않는다.

```sh
# 기존 BTC 계좌: 읽기만, HALT 유지
.venv/bin/python -m ops.paper_recovery_diagnostic --state runtime/paper-state.json
# 신규 HALTED 실험: <실제 실험 경로>를 알고 있을 때만 지정
.venv/bin/python -m ops.paper_recovery_diagnostic --state '<실제 실험 경로>/experiment-state.json'
```

## 검증과 미완료 항목

Fake로 정상/동일/소폭·큰 역행/지연/미래/누락·타입/수신 중단·반복/다중 종목/시계 변화, 로그 수치·샘플·출력 상한/비밀 원문 차단, stale 가격 전략 입력 차단, 0주문 HALTED 대사 bytes 불변, 자식 이유/실제 kill 구분, 원장 없는 Ctrl+C/SIGHUP을 검사한다. 기존832개와 신규 회귀 테스트의 exact head/3 OS Python3.12/run/attempt/skip/Safety는 PR에 기록한다.

실제 장애 원인과 공식 정의·순서 근거, Mac 실시간 재현/서버 기준 지연/절전·네트워크·전원 결과는 미확인이다. 새 관찰에서 시장/issue/수치와 프로세스 종료 정보를 얻은 뒤 다시 판단한다. 그 전에는 소폭 역행 허용·자동 재연결/복구/재초기화를 하지 않는다. REAL_TRADING=False, Slack/launchd/모델·유료 API/자동 시작 OFF, 전략/연구 holdout 불변을 유지한다.

P10-H 후속: [실시간 활동성](P10H_REALTIME_ACTIVITY.md)에서 신규 격리 실험의 시작 전 관찰과 제한된 대체 후보 선정을 설명한다. 독립 공개 진단에도 종목별 첫 수신·수신 종류별 건수·무수신 ms를 추가했으며, 서버 REALTIME 라벨을 실제 거래나 API 건강성 증명으로 해석하지 않는다. 이전 PAPER_STALE_TICK의 구체적 원인은 계속 미확인이다.
