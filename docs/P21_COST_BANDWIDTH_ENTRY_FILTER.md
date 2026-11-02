# P21 — 비용 대비 BB 폭 신규 진입 제한 후보 하나

운영 전략을 바꾸는 기능이 아니다. P19 개발 자료의 Legacy 기준과 **연구 후보 하나**를
동일한 P17 체결·P9 회계로 비교한다. `ops.paper_bot`, 실제 전략/신호 계산, 기존 계좌,
HALTED/backup, holdout에는 연결하지 않는다. 추가 수집·네트워크 호출도 없다.

사용자가 결과 확인 전에 지정한 가설을 고정한다.

```
bandwidth_bps = (현재 완료 봉 bb_upper - bb_lower) / 현재 완료 봉 close × 10000
bandwidth_bps < 44 → 최초 신규 BUY 차단
bandwidth_bps = 44 → 허용
```

44bps는 기본 비용 fee/slippage/full spread = 5/5/2bps의 **대략적인 왕복 22bps의 두 배**를
근거로 지정한 미검증 가설이다. 최적 임계값, 손익분기 보장, 예상 미래 변동폭이 아니다.
임계값/전략/비용을 바꾸는 CLI 옵션이나 탐색 루프는 없다. 다른 후보를 추가하지 않는다.

## 실제 연결과 유지되는 계약

기존 `simulate`에 선택적 연구 bot factory를 추가했다. 기본은 이전 `ProbeBot` 그대로다.
`EntryFilterBot.buy_coin`은 원래 `AutoTrade`가 분석 결과를 받고 last_signal/시각을
원장에 저장한 **뒤**, 포지션이 없고 `amount is None`인 매수 진입에서만 완료된 analyzer
행의 폭을 확인한다. 차단해도 BUY 신호와 300초 쿨다운을 취소하거나 되돌리지 않는다.

거래량 필터가 먼저 적용되며, 후보가 허용한 주문에는 기존 포지션 한도·주문 크기·현금·
순대금 최소 주문 검사가 그대로 적용된다. 후보 차단은 기존 `buy_attempts`/엔진 거절 이전
별도 `entry_filter_checks/blocked/allowed`로 기록한다. 따라서 차단 건수를 엔진 거절에
합산하거나 필터 이후 주문 시도와 혼동하지 않는다. 보유 중 BUY 처리, SELL, 익절·손절,
`amount`가 지정된 물타기는 원래 경로 그대로다. 신호·가격·수량·수수료는 수정하지 않는다.

기존 `prepare`가 각 완료 봉까지의 rolling-200 지표만 만든다. 후보가 실행 시가나
미래 봉의 close/BB를 폭 계산에 사용하지 않는다. 신호 다음 봉 시가의 P17 실제 PAPER
주문/영수증/체결/저장과 P9 수수료 포함 이동평균 원가를 재사용한다. 마지막 강제 매도는 없다.
봉 내부 체결/실시간 틱 실행을 재현하는 연구가 아니라는 기존 P19 한계도 그대로다.

## 입력 검증과 12사례/600초 제한

```sh
PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p21_entry_filter \
  --input EXISTING_PRIVATE_P19_ROOT --output NEW_PRIVATE_DIRECTORY
```

P20 `inputs/metadata`와 기존 minute loader가 P19 protocol/SHA, UTC 고정 날짜
2026-10-30/31/11-01, BTC/XRP, 비용 registry, plan/manifest/CSV hash·기간·품질 및
제외 기록을 검증한다. `P19_ROOT/analysis` 입력도 허용한다. 기존 누락 BTC 두 입력은
원래 P19 제외 기록대로 유지한다. 임의로 제외 날짜를 추측하거나 보간·재수집하지 않는다.

기본 5/5/2bps만 재실행한다. 기준의 **전체 case**가 기존 P19 canonical JSON과 같아야
후보를 비교한다. 수익률만 반올림해 맞추지 않는다. P20의 `matched_case`를 그대로 사용한다.
후보도 data SHA·평가/실행 구간·원시 조건 집계가 같은지 확인한다. 준비 지표는 입력당
한 번만 계산해 두 정책이 공유한다. 최대 6입력 × 2정책 = 12사례다. 다른 비용 case는
입력 registry 검증에만 쓰며 재실행하지 않는다. P19 내부 B&H 비교 계산은 그대로 유지한다.

P20의 부모 감독/단일 spawn 자식/600초 제한을 재사용한다. 종료 정리는 최대 4초 추가된다.
기준 정책 하나만 끝났어도 `completed_policies`를 fsync/원자 저장한다. 짝 비교는 두 정책이
완료된 뒤에만 만든다. 입력/결과 불일치, timeout, Ctrl+C는 `INCOMPLETE`와 완료 정책,
미완료 정책 목록을 남긴다. 자동 재개/재시작은 없다. 기존 출력 덮어쓰기도 거부한다.

입력 해시는 마지막에 다시 확인한다. hash/출처 선언은 독립 attestation, rollback witness
또는 과거 상세 영수증 증명이 아니다. 원래 P19 집계와의 일치 및 재실행 P17/P9 회계 대사다.
운영 원장·민감 경로·symlink·holdout은 거부하며 임시 신규 연구 계좌만 만들고 폐기한다.

## 결과 해석

private `results.json`, `comparison.csv`, `REPORT.md`, `analysis.log`에 입력별 기준/후보를
나란히 남긴다. 순수익률과 차이(percentage points), 매수/매도 수·필터 차단 수·수수료,
MDD, 보유 분봉 시가 표본 비율, 시간 보유 비중과 초기자금 대비 평균 평가 노출,
실현/미실현손익·현금·잔여 수량/수수료 포함 원가와 P9 대사 결과를 구분한다.
미실현손익은 마지막 종가로 평가하며 가상의 청산 비용은 빼지 않는다.

어느 정책이든 0체결이면 `NO_TRADES`, 수익률/차이는 N/A다. 이를 개선 성공으로 세지 않는다.
평가 가능한 입력의 수익률을 합산하거나 하나의 포트폴리오 결과로 만들지 않는다.
개발 표본에서 손실이 작아졌다는 관찰과 수익성 입증을 구분한다. 기존에 공개된 자료이므로
독립 검증이 아니며 결과에 따라 임계값 변경/운영 자동 채택을 하지 않는다.

## 리뷰·병합 뒤 Mac에서 한 번 복사할 명령

현재 실행 승인이 아니다. 기존 P19 루트를 로컬에서 입력한다. 새 private 출력은 그 아래
`p21-entry-filter`에 생성한다. 이미 있으면 멈추며 기존 출력이나 계좌를 초기화하지 않는다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  printf 'Existing private P19 root: ' >&2
  IFS= read -r p21_source
  p21_source="${p21_source%/}"
  test -n "$p21_source"
  .venv/bin/python tools/check_safety.py >/dev/null
  .venv/bin/python -m backtesting.p21_entry_filter \
    --input "$p21_source" --output "$p21_source/p21-entry-filter"
)
```

터미널은 상태·실측시간·완료 건수, 최대 6행의 비교 요약, 해석 한계와 원본 경로만 출력한다.
exit 0 + `VERIFIED_PARTIAL`(누락 보존) 또는 `VERIFIED`가 기술적 완료이며 전략 성공은 아니다.
`INCOMPLETE`/exit 2면 private 결과의 제한된 오류 코드/미완료 목록을 확인한다.
실제 데이터·거래내역·개인 경로/로그는 공개 GitHub로 보내지 않는다.

Cloud에서는 합성 Fake 시험만 수행한다. 실제 Mac P19 자료의 후보 성과는 미검증이다.
macOS CI와 실제 Mac 데이터 분석도 다른 검증으로 기록한다. `REAL_TRADING=False`, 추가
지출 0원, Slack/launchd/자동 실행/자동 복구/자동 병합 OFF를 유지한다.
