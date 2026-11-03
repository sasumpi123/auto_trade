# P24 거래 없는 수신 공백·회복 관측 도구

## 배경

P23 실기기 결과(신규 독립 PAPER 계좌): `HALTED_NO_RESTART`/`PAPER_STREAM_TIMEOUT`, 진단 `TRIGGERED`
`RECV_TIMEOUT`, `idle_ms = since_last_message_ms = since_last_advance_ms = 15095.774`, 연결 `OPEN→OPEN`,
Pong `UNSUPPORTED`. 확인된 것은 **애플리케이션 메시지 수신 공백**뿐이며, 원래 장애 원인이나 정상적인
시장 공백으로 확정하지 않는다.

P24는 계좌·주문 없이 같은 조건의 공개 스트림을 관측해 15초 공백 뒤 수신이 회복되는지, 두 연결이 같이
멈추는지를 기록한다. 운영 봇 정책은 바꾸지 않는다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `ops/stream_gap_observer.py` | 신규 CLI·관측기·독립 감독자. 계좌 경로 인자 없음 |
| `tests/test_p24_stream_gap_observer.py` | 합성 시계/스트림, spawn 감독자, loopback websockets 검증 |

기존 파일은 수정하지 않았다(운영 `MarketStream`을 그대로 사용).

## 동작

- `--connect-public` 없이는 `DISABLED`만 출력하고 네트워크를 쓰지 않는다. `--seconds`는 60~3300(55분).
  `--output-dir`은 이미 존재하는 비어 있는 0700 사용자 소유 디렉터리여야 하며 저장소 밖이어야 한다(생성·chmod 안 함).
- KRW-BTC ticker를 **독립 연결 2개(C1, C2)**로 구독한다. 각 연결은 운영 `services.market_stream.MarketStream.connect()`를
  그대로 사용한다(같은 URL, `open_timeout=10`, `close_timeout=2`, `ping_interval=20`, `ping_timeout=10`,
  `isOnlyRealtime=True`). 결과에 연결 객체에서 읽은 실제 적용값과 websockets 버전을 기록한다.
  수신만 `recv(timeout=0)`로 비우고 50ms 쉰다(운영 루프의 recv 1초 대기와 다름, 수신 시각 해상도 약 50ms).
- 연결별 기록: 수신/전진/동일시각/역행/무효, REALTIME/SNAPSHOT, 마지막 수신·전진 이후 시간, 최대 공백,
  종료 사유, close 코드(수신/송신), keepalive Pong 시간 초과로 라이브러리가 닫았는지(1011 + 고정 사유 일치 여부).
  wall/monotonic 차이 최대값과 포착 시점 값을 기록한다.
- 공백 판정은 운영 STREAM_IDLE과 같은 15초 이상. 종류를 구분한다.
  - `NO_RECEIVE`: 15초 동안 어떤 메시지도 없음
  - `NO_ADVANCE_WITH_RECEIVE`: 메시지는 오지만 timestamp가 15초 동안 전진하지 않음(동일시각/역행)
- 첫 공백을 포착하면 그 순간의 두 연결 값을 **먼저 비공개 파일에 저장**(감독자가 즉시 fsync)한 뒤 최대
  30초 회복을 관측하고 종료한다. 연결별 회복 분류: `RECEIVE_AND_ADVANCE_RESUMED`, `RECEIVE_ONLY_NO_ADVANCE`,
  `NOT_RECOVERED_WITHIN_WINDOW`, `ENDED_DURING_RECOVERY`, `NOT_GAPPED_AT_CAPTURE`(+창 안 공백 여부).
- 공백 없이 기한이 끝나면 `NOT_REPRODUCED`. 재연결·자동 재시도 없음.
- Pong: `UNMEASURED`. 관측 연결에 진단 ping을 보내지 않는다(P23에서 제거한 송신 스레드를 다시 넣지 않음).
  라이브러리 `latency` 값이나 OPEN 상태를 최근 Pong 확인으로 간주하지 않는다. keepalive 실패는 연결 종료와
  close 코드로만 관측된다.

## 상태와 기한

- `REPRODUCED`: 공백 포착 + 30초 회복 창 완료 + 두 연결 유지 + 정상 정리 + 포착 저장 성공
- `NOT_REPRODUCED`: 기한까지 공백 없음 + 정상 정리
- `INCOMPLETE`: 연결 실패, 공백 전/회복 중 연결 종료, stop 요청, 회복 창이 기한에 잘림, 정리 실패,
  저장 실패, 결과 누락, 강제 종료. 정상 관측 성공으로 처리하지 않으며 종료 코드 2.

감독자는 spawn 자식 프로세스를 `seconds`에 stop 요청하고 `grace=15`초 뒤에도 끝나지 않으면 kill한다
(`FORCED_TERMINATION`). 자식의 관측 예산은 `seconds-5`로 감독 기한보다 먼저 끝난다. 정리는 연결별 라이브러리
`close_timeout=2` 범위다. 관측기는 trading/ledger/strategy/config/`ops.paper_*`를 import하지 않는다(테스트로 확인).

## 출력

- 비공개 `--output-dir`: `p24-events.jsonl`(포착·최종 기록, 0600, 기록마다 fsync), `p24-result.json`(0600).
- 터미널: 최대 8줄 요약. 경로·원문 패킷·가격·인증정보·예외 문자열 없음.

## 검증과 한계

합성 시계/스트림으로 공백 후 회복, 한 연결만 공백, 두 연결 공백, 동일시각 반복(무수신 아님), 14.95/15.05초
경계, 미재현, 연결 종료(1011 keepalive), 읽기 오류, 연결 실패, 정리 실패, stop, 회복 창 잘림, 저장 실패를
검사한다. 실제 spawn 감독자로 정상/무응답 kill/stop/결과 누락/쓰기 실패를, loopback websockets 서버와 운영
`MarketStream`으로 실제 연결·적용 설정·구독·공백·회복·정상 종료를 검사한다.

이것은 관측 도구가 의도대로 기록한다는 증거일 뿐이며, 업비트 공백 원인을 재현·규명한 것이 아니다. 두 연결이
같이 멈춰도 원인(서버, 네트워크, 시장 공백, Mac 절전 등)을 확정하지 않는다. 실제 업비트 관측은 Mac에서만 수행된다.

## Mac 실행 (병합 후, 계좌 없음, 최대 55분)

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  test -z "$(git status --porcelain)" || { echo 'BLOCKED local changes present; nothing switched'; exit 2; }
  git switch -q master && git pull -q --ff-only
  mkdir -p "$HOME/research"
  d="$(mktemp -d "$HOME/research/p24-gap.XXXXXX")"
  mkdir -m 700 "$d/obs"
  .venv/bin/python tools/check_safety.py >"$d/safety.txt" 2>&1 || { echo 'BLOCKED safety check'; exit 2; }
  .venv/bin/python -m unittest tests.test_p24_stream_gap_observer >"$d/unit.txt" 2>&1 || { echo 'BLOCKED P24 tests'; exit 2; }
  set +e
  .venv/bin/python -m ops.stream_gap_observer --connect-public --seconds 3300 --output-dir "$d/obs" 2>"$d/observer.stderr"
  code=$?
  set -e
  [ "$code" -eq 0 ] || exit 2
)
```

상세 기록은 `ls -dt ~/research/p24-gap.* | head -1`의 `obs/`에 남는다. REAL_TRADING=False, 추가 지출 0원,
Slack/launchd/무인 실행/자동 재시작/자동 병합/개발 자동화 OFF.
