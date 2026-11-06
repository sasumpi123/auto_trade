# P33 Consensus 2/3 전방 평가 사전등록 **제안** (검토 전, 미등록)

이슈 #69. 상위 연구 목표 #7의 한정된 하위 작업입니다. P32에서 연결한 기존 `ConsensusStrategy(2)`를 연구 후보로 유지하면서 다음 질문에 답하려는 제안입니다.

> 이미 본 P19 개발 구간을 떠나서도, 고정 비용을 반영한 뒤 현금 보유와 BH40을 넘는가?

이 문서와 `backtesting/p33_protocol.json`은 **검토를 기다리는 제안**입니다. 전방 평가로 인정되려면 **2026-11-08T00:00Z 전에** 아래 조건이 모두 갖춰져야 합니다.
1. 이 protocol의 정확한 SHA-256을 검토하고 명시적으로 승인합니다.
2. 등록 영수증 `backtesting/p33_registration.json`을 작성합니다.
3. 별도로 검토된 **등록 PR**이 영수증 digest와 `approval_reference`를 `backtesting/p33_registration_anchor.json`에 고정하고, 기한 전에 병합됩니다. 검토자는 병합 전에 GitHub에서 기한 전 승인 이벤트, 제안 commit, protocol 바이트, 게시된 영수증을 확인합니다.

**gate는 이 anchor로만 열립니다** (리뷰 5796192064 반영).
- 영수증 파일만 있어서는 등록되지 않습니다. 추적되지 않거나, 수정됐거나, anchor에 없거나, 소급됐거나, 미래 시각인 영수증은 모두 거절합니다.
- 영수증과 anchor는 git에 추적되고 수정되지 않은 상태여야 합니다.
- anchor의 마지막 commit 시각과 영수증의 `observed_at_utc`는 모두 START 이전이어야 하고, `observed_at_utc`는 현재보다 미래일 수 없습니다.
- commit 시각은 **전체 이력**에서만 확인합니다. shallow clone에서는 tip commit이 anchor commit처럼 보일 수 있어서, 이 경우 MISSED로 단정하지 않고 `P33_HISTORY_UNVERIFIED`로 차단합니다(리뷰 5796595696 반영). CI checkout은 `fetch-depth: 0`으로 정확한 head와 전체 이력을 받습니다. 기한 뒤 문서만 바뀐 commit은 유효한 등록에 영향을 주지 않지만, 기한 뒤 anchor 자체가 바뀌면 계속 거부합니다.
- anchor는 protocol의 코드 고정 목록에 포함되지 않습니다. 그래서 등록 PR이 protocol SHA를 바꾸지 않습니다.
- 수집 결과(`collection.json`)와 분석 결과는 등록 id(영수증 SHA-256)에 묶입니다. 다른 승인으로 재해석하지 않습니다(`P33_INPUT_REGISTRATION_MISMATCH`, `P33_REGISTRATION_CHANGED`).

기한을 놓치면 이번 창은 영구 차단됩니다(`MISSED_REGISTRATION`). 날짜를 조용히 옮기거나 소급해서 등록하지 않습니다. 개발 중 실자료 접근과 예약은 한 번도 하지 않았고, 이번 수정에도 실제 승인이나 등록은 포함되지 않습니다.

## 고정 내용 (`backtesting/p33_protocol.json`)

**대상**
- 기간: UTC [2026-11-08 00:00, 2026-11-15 00:00), 7일
- 종목: KRW-BTC, KRW-XRP
- 최대 14개 날짜·종목

**전략과 조건**
- 전략은 기존 `consensus_2of3` 하나뿐입니다(`strategies.signals.ConsensusStrategy`, quorum 2).
- Legacy, 44bps, 다른 파라미터는 실행하지 않습니다.
- 지표 정의(RSI/MACD/BB)와 다음 규칙은 그대로입니다: 쿨다운 300초, 거래량 0.5, 익절·손절·1회 물타기·최소주문.
- 날짜·종목마다 시작금 100만 원, 처음 진입 비중 40%로 시작합니다.
- 비용은 5/5/2bps, warmup은 200봉입니다.
- 완료된 봉의 신호로 다음 분 open에 체결합니다. 종료 시점에 강제로 매도하지 않습니다.

**비교 기준**
- 현금 100%(0%)
- 같은 창의 P17 비용 반영 BH40: 같은 `execution_first_open`에 진입하고, 같은 마지막 종가로 평가합니다.

**비교 방법**
- **주 비교**는 원래 종가 기준(net_return_pct)입니다.
- **보조 비교**는 P28 비용 반영 가상 청산입니다. 주 비교를 대체하지 않습니다.
- 반올림 전 Decimal 값으로 판별합니다. 값이 같으면 TIE이며 승리가 아닙니다.
- NO_TRADES는 승리로 세지 않고, 분모를 명시합니다.
- 시장·날짜 합산, 복리, 포트폴리오 수익률은 계산하지 않습니다.

**보존·집계 항목**
- 사례별로 순수익률, 수수료, MDD, 체결 수, 잔여 포지션, window, BH40, exit를 보존합니다.
- 다음 수를 집계합니다:
  - 예정, 유효, 격리, 미완료
  - 거래 사례, 무거래 사례
  - 거래 사례 중 현금 대비 EXCEED/TIE/BELOW
  - 거래 사례 중 BH40 대비 EXCEED/TIE/BELOW/NA
  - 현금과 BH40을 동시에 초과한 사례 수와 그 종목

**판정 규칙 (제안)**
판정 범위는 "추가 사전등록 검증을 계속할 근거가 있는가"까지입니다.

| 판정 | 조건 |
|---|---|
| `INSUFFICIENT_SAMPLE_HOLD` | 유효 사례 < 10 또는 거래 사례 < 6 |
| `CONCENTRATED_OR_ABSENT_HOLD` | 주 비교에서 동시 초과한 종목이 2개 미만 |
| `FURTHER_VALIDATION_WARRANTED` | 주 비교 동시 초과 ≥ ceil(0.6 × 거래 사례) |
| `NOT_SUPPORTED` | 그 밖의 경우 |

어떤 판정도 PAPER 기본 전략이나 실거래로 전환하는 근거가 되지 않습니다. CI 통과나 일부 수익 사례도 마찬가지입니다.

**누락과 상한**
- 분봉 누락이 있으면 해당 날짜·종목을 격리하고 PARTIAL로 보고합니다. 보간, 재수집, 기간 연장은 하지 않습니다.
- 수집 상한: 일 40회·120초, 전체 280회
- 분석 상한: 600초(기존 `p20.analyze` worker와 정리 방식)

**평가 코드 고정**
- protocol의 `code_sha256`에 평가 코드와 필수 의존 소스 24개의 해시를 고정합니다.
  - P22의 20개 소스
  - P32, P29, P28
  - `p33_forward.py`
- 고정 파일이 하나라도 바뀌면 `P33_FROZEN_CODE_CHANGED`로 차단합니다.

## 구현 (기존 경로 재사용, 새 대형 runner 없음)

`backtesting/p33_forward.py`의 구성:

- `status`: 제안·등록 상태, protocol SHA, 기한을 출력합니다. 네트워크 호출이나 파일 생성은 없습니다.
- `collect`: 등록 영수증과 `now ≥ 2026-11-15Z`가 모두 확인될 때만 동작합니다. P22와 같은 방식으로 일별 `minute.collect`를 호출하며 일 40회, 전체 280회를 넘지 않습니다.
- `analyze`: 등록, 기간 종료, 입력 경로와 다른 출력 경로를 확인한 뒤 실행합니다(P32 `require_outside` 재사용). 내부 처리는 다음과 같습니다.
  - P22 방식의 일별 검증: manifest/plan/CSV 해시, 품질, 격리
  - P32 `strategy_case(ConsensusStrategy(2))`와 P21 `compact`(P9 대사)
  - P29 `buy_hold_40_core`(공식 대조), P28 `exit_audit`
  - P32 `rehash`로 최종 입력 해시를 다시 확인(경로 검사와 bounded 읽기)
- P22 고정 파일은 수정하지 않았습니다. P19/P22 재분석, 공개 시세 수집, 실제 계좌 실행도 없습니다.

## 지금 실행 가능한 Mac 명령 (상태 확인만, 네트워크·파일 0)

<!-- P33_COMMAND_BEGIN -->
```bash
if test -z "$(git status --porcelain)"; then PYTHON_DOTENV_DISABLED=1 .venv/bin/python -m backtesting.p33_forward status; else echo "STOP: local changes in checkout"; false; fi
```
<!-- P33_COMMAND_END -->

- 등록 전에는 `PROPOSED_NOT_REGISTERED`가 출력되고 exit 0입니다.
- 기한이 지나도 등록되지 않았으면 `MISSED_REGISTRATION`이 출력되고 exit 2입니다.
- 실자료 수집과 분석은 승인·등록을 마치고 기간이 끝난 뒤, 별도의 수동 단계에서만 진행합니다.

## 한계

- 합성 fixture로만 검증했습니다. 실자료는 보지 않았습니다.
- 등록 영수증은 서버 시각을 증명하지 않습니다. GitHub에서 확인할 수 있는 commit과 검토 기록으로 보완합니다.
- 짧은 표본입니다. 결과가 좋아도 수익성 입증이나 운영 채택의 근거가 아닙니다.
- `REAL_TRADING=False`, 추가 지출은 0원입니다.
