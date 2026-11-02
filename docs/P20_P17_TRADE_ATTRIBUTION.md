# P20 — P17 실제 체결 경로의 거래별 손익 설명

`python -m backtesting.p20_attribution --input P19_ROOT --output NEW_PRIVATE_DIR`
는 기존 P19의 `data/YYYY-MM-DD/{plan.json,manifest.json,KRW-*-1.csv}`와
`analysis/results.json`만 읽는다. `--input P19_ROOT/analysis`도 같은 입력이다.
네트워크·수집·전략 선택·비용 탐색 옵션은 없다. 출력 경로는 checkout 밖의 새 private
디렉터리만 허용한다. 운영 계좌, runtime, holdout, symlink 및 기존 출력은 거부한다.
기존 계좌·HALTED 계좌·백업을 열거나 수정하지 않는다.

P19의 고정 protocol 내용/SHA, 완료 상태, 비용별 case registry와 제외 기록을 검증한다.
UTC 2026-10-30/31/11-01 × BTC/XRP 중 P19에 남아 있는 기본 5/5/2bps 사례만 재실행한다.
누락된 BTC 두 입력 등 기존 제외 사례는 그대로 제외하고 보간·대체하지 않는다.
전체 6개와 부분 결과는 `VERIFIED` / `VERIFIED_PARTIAL`로 구분한다. 제외된 날짜를
추측하거나 6개가 모두 분석된 것처럼 표시하지 않는다. 부족한 날을 추가 수집하지 않는다.

기존 minute loader가 plan/manifest 연결, 수집 시각·요청 audit·UTC 기간, CSV 바이트 SHA,
데이터 SHA·OHLCV·정렬·연속성을 재검증한다. manifest 출처 선언과 SHA는 독립적인
거래소 서명 증거가 아니다. 합성 출처는 CLI에서 거부하며 테스트에서만 명시적으로 허용한다.
선택한 입력의 metadata가 기존 결과와 같아야 하고, 분석 뒤 입력 해시도 다시 확인한다.
입력의 악의적 동시 교체를 완전히 방어하는 저장소/독립 witness로 주장하지 않는다.

## 체결 및 대사 계약

P19 `P17ResearchEngine`의 `_apply_fill` 성공 뒤에 선택적 `fill_detail` 관측만 추가했다.
실제 P17 주문대금/총예산 cap/순대금 최소 주문/영수증/수수료/영속 회계 경로를 그대로 쓴다.
기본 `details=False`와 P19 protocol/CLI 결과 형식은 유지한다. 연구용 임시 계좌는
종목·하루마다 초기자금 1,000,000원으로 독립 생성하고 종료 뒤 폐기한다.
상세 결과를 제외한 **전체 P19 기본 비용 case**(신호·체결·손익·수수료·SHA·기간 등)가
기존 `results.json`과 canonical JSON으로 일치해야 성공한다. 다르면 `INCOMPLETE`다.

기존 `trade_attribution.attribute`가 실제 확정 원장과 상세 영수증의 건수·방향·수량·대금·
수수료·시각·사유를 대조하고 P9 이동평균 원가를 대사한다. 매도량과 같은 수량의 진입
기준가격 원가를 비례 배분하여 다음 항등식을 확인한다.

```
기준가격 차익 = 매도량 × 청산 기준가격 − 배분된 진입 기준가격 원가
P9 순실현손익 = 기준가격 차익 − 배분 매수 수수료 − 매도 수수료
             − 배분 매수 슬리피지 − 매도 슬리피지
             − 배분 매수 반스프레드 − 매도 반스프레드
총손익 = 순실현손익 + 평가손익 = 최종 평가자산 − 초기자금
평가손익 = 잔여 수량 × 마지막 종가 − 수수료 포함 잔여 원가
```

스프레드 2bps는 양쪽 각 1bps다. 가격에 반영된 비용을 순손익에서 다시 빼지 않는다.
상세 매도에는 진입 첫/마지막 시각·지표 구성 태그, 청산 시각·태그·사유, 수량 가중
보유시간, 배분 비용과 손익을 기록한다. 태그는 기존 RSI/MACD/BB 등 allowlist이며,
지표 수치 전체나 원문 사유/원장 ID를 출력하지 않는다. 혼합 진입은 FIFO가 아닌
이동평균 비례 배분이다. 가격 차익부터 음수인 매도와 양수였다가 비용으로 음수인
매도를 별도로 센다. 0원 가격 차익 후 비용 손실도 별도 계수다.

청산 사유별 거래 수·순손익·평균 보유시간, 익절/손절/물타기 분류는 기존 관측을 재사용한다.
잔여 포지션은 강제로 팔지 않는다. 미실현손익은 마지막 종가 기준이며 가정 청산 비용은
차감하지 않는다. `p9_reconciliation`에 현금·평가자산·총손익·잔여 원가·전체 비용을 남긴다.
이 분해는 **고정된 실제 체결 수량의 회계 설명**이다. 비용 제거/정책 변경 때 주문 경로가
달라질 수 있으므로 정책의 인과효과, 독립 검증 또는 전략 우위로 해석하지 않는다.

## 시간 제한과 출력

분석 자식 1개가 직렬로 최대 6사례를 수행하고 부모가 600초에 중단한다. 종료 정리에
최대 4초가 추가될 수 있다. 사례마다 fsync/원자 JSON으로 완료분을 보존한다.
시간 초과·중단·불일치는 `INCOMPLETE`와 미완료 목록을 남기고 자동 재개하지 않는다.
private 출력은 `results.json`(전체 증빙), `sales.csv`, `REPORT.md`, `analysis.log`다.
성공 터미널 출력은 상태/사례수, 가격 손실 매도 수, 비용 전환 손실 매도 수, 경과시간의
4줄과 원본 경로 1줄뿐이다. 오류 코드는 한정된 코드로 기록하고 raw 예외는 노출하지 않는다.
원본 경로·전체 결과·거래내역·로그를 공개 GitHub에 올리지 않는다.

## 리뷰·병합 후 Mac에서 한 번 복사할 명령

아래는 현재 Cloud 실행 요청이 아니다. 기존 P19 루트 경로를 로컬에서 입력한다.
새 출력은 그 루트 아래 `p20-attribution`에 생기며 이미 있으면 덮어쓰지 않고 실패한다.
기존 P19 자료가 없거나 수정되었다면 중단한다. 재수집으로 자동 전환하지 않는다.

```sh
(
  set -eu
  export PYTHON_DOTENV_DISABLED=1
  umask 077
  cd "$HOME/projects/auto_trade"
  printf 'Existing private P19 root: ' >&2
  IFS= read -r p20_source
  p20_source="${p20_source%/}"
  test -n "$p20_source"
  .venv/bin/python tools/check_safety.py >/dev/null
  .venv/bin/python -m backtesting.p20_attribution \
    --input "$p20_source" --output "$p20_source/p20-attribution"
)
```

성공 판정: exit 0, `VERIFIED_PARTIAL`(누락 BTC 보존) 또는 `VERIFIED`, P19 일치 및
P9 대사 verified. `INCOMPLETE`/exit 2면 성공이 아니며 private 결과의 제한된 failure code와
미완료 목록을 확인한다. 기존 P19 입력/계좌를 고쳐 맞추지 않는다.

Cloud에서는 합성 시험만 수행했다. 실제 Mac 자료·손실 원인별 집계는 미검증이며
수익률/거래 수치를 만들어내지 않는다. 실제 Mac 실행과 macOS CI는 다른 검증이다.
REAL_TRADING=False, 비용0원, Slack/launchd/자동 실행 OFF를 유지한다.
