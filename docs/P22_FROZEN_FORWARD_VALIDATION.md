# P22 — 고정 44bps 후보의 시간 분리 검증 준비

현재는 코드·프로토콜·합성 시험만 준비한다. 실제 미래 시세 수집, 예약·대기 실행,
launchd, 봇 운영·전략 채택은 하지 않는다.

## 먼저 고정하고 push한 계약

- 프로토콜: `backtesting/p22_protocol.json`
- 사전 커밋: `31c24a717d6b26337e1306a3d1b4545ec0c05985`
- 프로토콜 SHA-256: `4aa290d9077f0a134a376b01b8560f2eddaf65854f1bd329d6e73b9373266af4`
- 원격 ref 관측: UTC `2026-11-02T07:15:27.361850+00:00`에 같은 SHA 확인
- 후보/엔진 기준: P21 병합 `4cdfa9cb0ea732c0d5707db0a39a7f08c7be1d0f`와 프로토콜의 코드 20개 SHA

`p22_registration.json`에는 실제 Git push 뒤 확인한 원격 ref와 실행기 UTC를 기록했다.
이는 독립적인 GitHub 서버 push 시각 증명·전자서명·rollback witness가 아니다.
Git commit의 자기 신고 시각만으로 사전 등록을 증명하지 않는다. 코드가 영수증/프로토콜
hash와 기간 시작 전 관측 조건을 확인하며, 재발급/소급 등록/날짜 이동 옵션은 없다.
검증 불가 또는 기한을 놓친 관측은 BLOCKED다. 새 날짜로 자동 대체하지 않는다.
이 관측은 squash 뒤에도 보존되므로 원래 커밋이 master의 ancestor라고 가정하지 않는다.

UTC `[2026-11-03 00:00, 2026-11-06 00:00)`를 11/03·04·05 하루씩 BTC/XRP로 나눈다.
수집은 **2026-11-06 09:00 KST(00:00 UTC) 이후**에만 가능하다. 로컬 UTC가 잘못되었으면
실행하지 않는다. 실제 수집 때는 기존 공개 클라이언트의 서버 Date/로컬 시계 검사도 유지한다.

## 재사용 범위

Legacy와 기존 `P21 EntryFilterBot` 두 정책만 사용한다. 44bps 미만 최초 진입 차단,
정확히 44bps 허용, 신호/last_signal/쿨다운 저장 뒤 검사라는 연결을 변경하지 않는다.
거래량 필터·보유 BUY·SELL·익절·손절·물타기도 그대로다. 기본 비용 5/5/2bps,
초기자금 1,000,000원, 주문 한도 40%, 완료 봉 200개 워밍업과 다음 봉 시가 체결을 유지한다.
일·종목·정책마다 독립 임시 계좌를 사용한다. 전날 포지션이나 워밍업을 이어 붙이지 않으며
연속 3일 운영 또는 다종목 포트폴리오 재현이 아니다. 실제 유효 평가 구간은 결과 시각을 따른다.

P19의 **공통 prepare/case/P17ResearchEngine**과 P21 compact/회계를 재사용한다.
P19 protocol/고정 날짜 registry 또는 P20의 P19 결과 매칭에는 연결하지 않는다.
P22에 과거 P19 결과를 대입하거나 기간·캔들·평가 구간을 섞지 않는다. 새로운 기간에는
기존 P19 집계와 같아야 한다는 검사를 하지 않으며, 같은 P22 입력/지표/구간을 두 정책이 공유한다.
고정 코드 hash가 바뀌면 차단한다. 숫자/코드를 바꿔 이전 프로토콜에 맞추지 않는다.

## 수집과 오프라인 분석

`python -m backtesting.p22_forward collect --output NEW_PRIVATE_ROOT`는 기본 네트워크 OFF다.
종료 전에는 `NOT_READY`, 종료 뒤에는 `OFFLINE_PLAN_ONLY`이며 파일도 만들지 않는다.
실제 수집은 별도 `--connect-public`이 있어야 한다. 일별 기존 minute collector를
한 번씩만 호출한다. 날짜당 최대 40요청/120초, 합계 최대 120요청이다. 조건 탐색/재시도/
재수집/자동 재개/대기/날짜 이동 옵션은 없다. 기존 collector의 너무 오래된 기간 거부도 유지한다.

출력 root에는 frozen protocol/registration 복사본, `collection.json`, `data/날짜/`의
plan·manifest·CSV를 저장한다. 새 private 경로만 만들며 기존 계좌/backup/holdout/
checkout 안 경로/symlink/기존 출력은 거부한다. 프로토콜은 연구 기록이며 운영 원장이 아니다.

`analyze --input P22_ROOT --output NEW_PRIVATE_ANALYSIS`는 네트워크 없는 별도 명령이다.
조기 분석도 `NOT_READY`이고 입력·출력을 열지 않는다. 이후 P22 protocol/registration/
runner SHA, 수집 registry·시각, 고정 UTC 기간, 기존 loader의 plan/manifest/CSV·SHA·
정렬·OHLCV·누락 품질을 검증한다. P19/P21 입력은 거부한다. 누락 봉만 격리하고 채우지 않는다.
API 오류·불완전 수집·hash 불일치는 정상 누락과 구분하고 전체 `INCOMPLETE`를 유지한다.

입력당 지표 한 번, 최대 6입력 × 2정책 = 12사례, 단일 spawn 자식/600초다.
종료 정리는 최대 4초 추가될 수 있다. 각 완료 정책을 fsync/원자 저장하며, 두 정책이 모두
끝난 뒤 짝 비교를 만든다. timeout/중단/실패 시 완료 정책과 미완료 목록을 남긴다.
분석 전후 입력·고정 코드 hash를 대조한다. 의존성까지 모두 동일함을 증명하는 장치는
아니므로 결과에 Python/pandas 버전과 runner SHA도 기록한다.

## 출력과 판정

private `results.json`, `comparison.csv`, `REPORT.md`, `analysis.log`에 일별·종목별
기준→후보 순수익률/차이(pp), 거래 수·차단 수·비용, MDD·보유/평가 노출 비중,
실현/미실현손익·현금·잔여 수량/원가와 P9 대사를 남긴다. 마지막 강제 매도는 없다.
평가손익은 마지막 종가 기준이며 가상 청산 비용을 더 빼지 않는다.

- `VERIFIED`: 6입력의 두 정책이 기술적으로 검증됨. 수익성 입증 의미가 아니다.
- `VERIFIED_PARTIAL`: 검증된 누락 봉 입력을 격리하고 나머지만 완료. 전체 6입력 성공이 아니다.
- `INSUFFICIENT_REAL_DATA`: 수집 품질상 분석할 입력이 없음.
- `INCOMPLETE`: 불완전 수집/오류/중단/timeout. 완료분이 있어도 전체 성공이 아니다.
- `NO_TRADES`: 해당 정책의 확정 체결 0건. 수익률/차이는 N/A, 개선 성공으로 세지 않는다.

일별/종목별 수익률을 합산해 포트폴리오처럼 표시하지 않는다. 시간 분리 검증이지만,
짧은 표본의 손실 감소를 수익성·통계적 우위·글로벌 미관측 데이터 증명으로 해석하지 않는다.
결과에 따른 임계값 변경이나 운영 자동 채택도 없다. 실제 수집·Mac 실행·성과는 현재 미검증이다.

## 리뷰·병합 뒤 11월 6일 09:00 KST 이후 Mac 명령 한 블록

**P25 이후 변경:** P25가 P22 고정 소스인 `ops/paper_experiment.py`를 수정했으므로, 작업 트리에서
`python -m backtesting.p22_forward`를 직접 실행하면 변경되지 않은 `frozen()`이 `P22_FROZEN_CODE_CHANGED`로
fail-closed 한다(의도된 동작). P22는 `tools/p22_isolated.py`로만 실행한다. 이 도구는 추적 파일을 임시
디렉터리로 복사하고, 고정 파일 중 작업 트리와 다른 것(현재 `ops/paper_experiment.py` 1개)만
`backtesting/p22_pinned/`의 **등록된 정확한 바이트**로 교체한 뒤, 프로토콜·등록·러너(`p22_forward.py`)·고정
소스 20개 해시를 모두 검증하고, 그 안에서 변경되지 않은 `python -m backtesting.p22_forward ...`를 실행한다.
그 실행의 `frozen()`이 다시 전부 검증한다. 해시 재등록·검사 완화·monkeypatch·환경변수 정책 전환은 없다.
P25 입력 정책은 P22 실행 경로에 들어가지 않는다(P22는 P25 옵션을 쓰지 않고, 등록된 `paper_experiment.py`로 실행).

검토된 P22 코드가 있는 checkout에서 실행한다. 아래 명령은 자동 예약이 아니다.
첫 기본 OFF 점검에서 조기 `NOT_READY`/오류이면 즉시 끝나며 기다리지 않는다.
그 뒤 명시적 수집 한 번, 오프라인 분석 한 번만 수행한다. 수집 오류 뒤에도 분석은
완료된 자료의 한계를 보고하기 위해 한 번 시도하며, 수집 실패 exit code를 숨기지 않는다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  .venv/bin/python tools/check_safety.py >/dev/null
  .venv/bin/python tools/p22_isolated.py collect \
    --output "$HOME/research/p22-offline-plan-unused"
  mkdir -p "$HOME/research"
  p22_dir="$(mktemp -d "$HOME/research/p22-private.XXXXXX")"
  set +e
  .venv/bin/python tools/p22_isolated.py collect --connect-public \
    --output "$p22_dir/sample" >"$p22_dir/collection-command.log" 2>&1
  p22_collection_code=$?
  .venv/bin/python tools/p22_isolated.py analyze \
    --input "$p22_dir/sample" --output "$p22_dir/analysis"
  p22_analysis_code=$?
  set -e
  printf 'Private outputs: %s\n' "$p22_dir"
  test "$p22_collection_code" -eq 0 && test "$p22_analysis_code" -eq 0
)
```

실제 자료·거래내역·로그·개인 경로는 공개 GitHub에 올리지 않는다.
REAL_TRADING=False, 추가 지출 0원, Slack/launchd/무인 실행/자동 재시작/자동 병합 OFF.
