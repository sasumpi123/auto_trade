# P11-B: 여섯 전략의 동일 조건 PAPER 비교

P11-A의 데이터 검증·PAPER 체결·수수료·P9 회계를 재사용하여 Legacy Composite, RSI, MACD, Bollinger, Consensus2/3, Consensus3/3을 비교한다. 운영 기본 전략/파라미터/체결/복구 코드는 바꾸지 않았다. 이것은 이미 공개된 개발 자료의 탐색적 비교이며 독립 검증이나 운영 선정이 아니다.

## 공통 조건과 유일한 차이

`backtesting.legacy_diagnostic`에 명시적 연구용 `strategy` 인자를 추가했다. 기본값과 Legacy 경로는 기존 DataAnalyzer.analyze 본체를 그대로 호출한다. 연구 후보만 그 신호 결정 호출을 기존 `strategies.signals`의 고정 객체로 대체한다. 거래량50% 필터, 신호 방향과 무관한300초 쿨다운, 분석 시 신호 기록 시점, 포지션 제약은 동일하게 적용한다. 후보는 각각 새 계좌/분석기에서 시작하고 신호/포지션/현금을 공유하지 않는다.

다음 조건은 모든 후보에 같다.

- 초기현금1,000,000KRW, 종목당40% sizing, 최소주문5,000KRW, 실제 AutoTrade의 +5% 익절/-5% 1회 물타기 후 재손절 정책. 따라서 결과는 순수한 신호 전략만의 성과가 아니라 **같은 위험관리 오버레이를 결합한 전략**의 성과다.
- 최근 완료200봉의 기존 DataAnalyzer 지표 계산, 최초199봉 워밍업, 완료봉 i의 신호 → 다음봉 i+1 시가 체결. 위험관리는 다음 시가 관측에서 신호보다 먼저 처리한다. 다음봉 high/low/close는 의사결정에 전달하지 않는다. 동일 봉 반복 시도는 기존 seen 검사로 체결 전에 억제한다.
- 기존 `CostPaperEngine`/ExecutionEngine의 PAPER intent/확정 체결과 P9 수수료 포함 원가 대사. 매수 수수료는 원래 all-in 배정액 안에서 지급한다. 비용은 fee5/10bps × slippage0/5/10bps, full spread2bps의 기존6조건이며 숫자를 결과에 맞춰 조정하지 않는다.
- 종가 평가 MDD, 매도별 수수료 포함 승률·평균/기대 손익, 미청산 잔여 포지션 평가, 같은 첫 평가 가능 시가의 B&H100%/40% 비교. 강제 청산·예측 체결·호가/부분 체결 추정은 없다. 지표 정의와 한계는 [P11-A 계약](P11A_STRATEGY_DIAGNOSTICS.md)을 유지한다.

전략 차이는 원래 구현 그대로다. Bollinger는 독립 밴드 규칙이며 Legacy BB의 RSI/MACD 상승 확인을 요구하지 않는다. Consensus는 연구용 RSI/MACD/Bollinger 표결과 반대 표 없음 조건을 사용한다. MACD는 기존 level-and-trend 규칙이지 새 crossover 정의가 아니다. Legacy는 원래 순차 우선순위/복합 신호를 유지한다. 목표가격이 다르더라도 공통 AutoTrade의5% 익절 정책을 후보별로 튜닝하지 않는다.

P11-A의 원자 조건/첫 실패 집계는 지표와 legacy 조건의 관찰값이다. 연구 후보의 주문 결정으로 사용하지 않는다. P11-B에서는 후보별 raw/effective BUY/SELL/HOLD와 실제 체결·손익을 비교하며, 과거 Legacy 조건 집계를 후보별 전용 규칙으로 오해하지 않는다.

## 자료·분할·holdout

기존 등록 archive `research/2026-10-21/data`만 사용한다. manifest 고정SHA, protocol 일치, train/validation prefix의 hash·순서·연속성을 기존 loader에서 확인한다.

- BTC/ETH/XRP/SOL ×15분/60분 ×train/validation의16개 case.
- train `[2026-07-23,2026-09-15)`, validation `[2026-09-15,2026-10-03)` UTC. validation은 과거 train의 지표 문맥을 사용하지만 계좌/쿨다운은 새로 시작한다.
- 6전략 ×16case ×6비용 =576비교. 한 case에서 준비한 prefix 지표를 모든 후보가 공유하며 전략 사이에서 데이터/시점/비용이 다르면 paired 요약을 거부한다.
- Legacy의96개 결과는 보관된 P11-A의 counts/fills/metrics/수수료/slippage/spread/최종자산/data hash 및 전체 trace hash/count와 정확히 대조한다. 차이가 있으면 완료 처리하지 않는다.
- holdout `[2026-10-03,2026-10-21)`은 행 요청·파싱·지표·성과 계산을 하지 않는다. 기존 gzip prefix loader의 내부 압축 tail buffering 한계를 vault/full CRC 보장으로 바꾸지 않는다. 원본 데이터/manifest/protocol/P11-A 결과는 변경하지 않는다.
- 1분봉 실자료 부족은 `production_minute1_status=INSUFFICIENT_REAL_DATA`다. 15/60분 비교가 실제 짧은 Mac1분봉 시험의 성과나0건 원인을 증명하지 않는다.

## 통계와 단일 종목 우연성

`results.json`/`metrics.csv`에는 모든576개 case별 신호·체결·순수익률·MDD·승률·기대값·비용·B&H/Legacy 대비 percentage-point 차이가 들어간다. paired 차이는 같은 종목/주기/split/비용/data hash/시작·종료/초기자금끼리만 계산한다. 표본/비용이 다르거나 중복 case면 실패한다.

요약은 split/전략/비용별8case의 동등 가중 **기술통계**다. 평균·중앙값·최소/최대·양수/음수/0건수, 유효/누락 수, 거래0case 수, 매수·매도 수, Legacy 및 B&H 초과case 수를 함께 공개한다. 서로 다른 계좌의 수익률을 누적·복리화하거나 포트폴리오 손익으로 보고하지 않는다. 매도 횟수 합계는 별도 실험의 합계이며 실제 고유 시장 거래 수가 아니다. 승률·기대값 요약도 case별 값의 중앙값이며, 모든 매도를 하나의 독립 표본으로 합친 통계가 아니다.

확정 거래0건은 기존 정책대로 성과N/A다. 이런 case를0% 이익으로 채우지 않는다. 비교의 유효 분모/누락/0거래 수를 숨기지 않으므로, 거래가 드문 전략의 선택된 유효case만 보고 우위로 주장하지 않는다. B&H/Legacy paired 기준이N/A면 그 차이도N/A다.

각 종목을 하나씩 제외하여 남은6case의 평균/중앙값과 Legacy 대비 평균 차이를 다시 계산한다. 보고서에는 기본비용의 leave-one-market-out을, JSON에는 모든 비용의 해당 결과를 공개한다. 이는 한 종목의 큰 이익에 대한 민감도 점검일 뿐 통계적 유의성 검정이나 일반화 증명이 아니다. 가상의 큰 양수 outlier 하나가 평균을 올리더라도 중앙값과 해당 종목 제외 결과는 음수로 남는 회귀를 검증한다.

같은 기간의 crypto 종목과15/60분 데이터는 서로 상관되고 겹친다. train/validation도 이미 공개되어 독립 평가로 간주하지 않는다. 다중 전략·비용 비교, 적은 매도 표본, 한정된 시장 국면, 장중 위험/호가/지연/부분 체결 미관측 때문에 p값·신뢰구간·확정 우위나 수익성 보장을 만들지 않는다. 좋은 단일 case를 전체 전략의 우위로 포장하거나 운영 기본값을 바꾸지 않는다.

## 오프라인 재현

```sh
cd "$HOME/projects/auto_trade"
.venv/bin/python -m backtesting.strategy_comparison \
  --data research/2026-10-21/data \
  --output /tmp/auto-trade-p11b-fresh-results
```

두 인자가 모두 필수이며 비용/전략 선택·튜닝 옵션은 없다. 새 출력 디렉터리만 허용하고 기존 디렉터리/파일을 덮어쓰지 않는다. 실패·중단 시 새 연구 출력의 `INCOMPLETE`가 남고 완료를 주장하지 않는다. 성공 시 JSON·CSV·Markdown을 모두 쓴 뒤 이 새 연구용 marker만 제거한다. 이 marker는 운영 원장/복구 authority가 아니다. 외부 자료 다운로드나 PAPER 운영으로 자동 전환하지 않는다.

모든 실행은 disposable 임시 **연구용** PAPER 원장만 사용한다. 기존 HALTED 계좌/락/백업을 입력받는 옵션이 없다. 실제 공개 API/WebSocket·거래소 주문·Slack·launchd·자동 실행/재시작·유료 API/AI/서버·DB는 사용하지 않는다. GitHub에는 공개 연구 집계와 비밀 없는 trace hash만 포함하며 운영 원장/인증정보는 올리지 않는다. 추가 지출0원 및 실제 자동화OFF를 유지한다.

결과는 [P11-B 연구 디렉터리](../research/p11b-comparison-2026-10-27/README.md)를 참고한다. 576개 실제 자료 계산은 Cloud Linux에서 수행하며, 3OS CI는 공통 코드와 synthetic 회귀를 검증한다. 실제 matrix를 모든 OS에서 재계산했다고 주장하지 않는다. 실제 결과와 테스트/3OS CI exact head/run/attempt/skip은 PR에도 기록한다. Mac 실기기 검증이나 무인 운영 활성화는 수행하지 않는다.

후속 [P11-C1분봉 수집·진단](P11C_MINUTE_DATA.md)은 별도 명시적 공개REST 수집과 오프라인 분석을 제공한다. 기존15/60분 자료/holdout/운영계좌를 변경하지 않으며, 실제1분봉 확보 실패와 synthetic 검증을 구분한다.
