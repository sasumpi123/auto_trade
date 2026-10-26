# P11-A: 기존 운영 전략 진단과 오프라인 PAPER 체결 연결

목표는 전략 조건을 바꾸거나 성과를 최적화하는 것이 아니라, 기존 신호·운영 의사결정·가상 체결을 연결하여 측정하는 것이다. 기존 연구 엔진/위험관리 연구를 대체하지 않는다. RSI/MACD/볼린저·거래량/쿨다운·보유/익절/손절/물타기·수수료/운영 원장 계약은 변경하지 않았다. 실제 계좌/세 HALTED 원장/백업을 열거나 복구하지 않는다.

## 실행 경로

`backtesting.legacy_diagnostic`는 다음 기존 코드를 호출한다.

1. `load_development`: 기존 frozen protocol, 등록된 snapshot, train/validation segment hash, 연속성/가격 검증. 추가로 알려진 archive manifest의 고정 SHA를 검사하여 임의 synthetic 입력을 real로 표시하지 못하게 한다.
2. `DataAnalyzer.calculate_indicators.__wrapped__`: 최근 완료 200봉의 지표를 그대로 계산한다. legacy bfill/ffill도 변경하지 않지만 미래 봉은 함수에 전달하지 않고 최신/직전 봉만 신호에 사용한다. 전체 데이터에 지표를 계산한 뒤 과거 index로 분석하는 방식은 사용하지 않는다. `analyze(index=-1)`을 사용하여 기존 latest-delta 의미를 유지한다.
3. `LegacyCompositeStrategy.decide`: 필터 전 raw BUY/SELL/HOLD. 각 원자 조건 및 규칙별 첫 실패 원인을 병렬 관찰한다. 진단용 predicate는 주문 action 결정에 쓰지 않는다.
4. `AutoTrade.process_market_data`: 다음 봉 시가에서 기존 보유 포지션 위험관리(+5% 익절/-5% 1회 물타기 후 재손절)를 먼저 실행하고, 필요하면 이전 완료 봉의 `DataAnalyzer.analyze.__wrapped__` 결과를 사용한다. 거래량50% 필터/모든 신호에 적용되는300초 쿨다운/보유·최소금액 조건을 재사용한다. "동일 신호만 쿨다운"이라는 코드 주석과 달리 실제 구현은 방향과 무관하게 차단하므로 진단도 실제 구현을 따른다.
5. `AutoTrade.buy_coin/sell_coin` → `ExecutionEngine._execute/_apply_fill`: disposable 임시 연구 원장에 intent·확정 체결·기존 평균매입단가/현금을 기록한다. 연구 전용 `CostPaperEngine`은 PAPER 영수증에 고정 비용 모델을 적용한다. 실제 broker 생성/HTTP/계좌 인증은 없다.
6. P9 `notification_metrics.replay`: 확정 체결 순서·현금·수량·평균단가·수수료 포함 원가를 대사한다. 엔진 `profit_amount`는 매수 수수료 제외 평균단가 기준이며, 보고 순실현 손익은 매수 수수료까지 배분한 P9 정의다.

데코레이터 본체를 호출해 legacy raw-error Slack 경로를 사용하지 않는다. 알림 객체를 연결하지 않고 `.env` 자동 로드를 비활성화한다. 역사 시각은 전용 단일 프로세스 연구의 해당 모듈 clock에만 한정해 주입한다. 운영 중인 봇과 같은 프로세스에서 이 함수를 병렬 실행하는 도구가 아니다.

## 평가 및 체결 시점

- 봉 timestamp는 UTC open이다. 완료 봉 i의 200봉 창을 사용하고 **봉 i+1 시가**에서 매매한다. i+1의 high/low/close는 의사결정에 전달하지 않는다. close는 체결 후 성과 평가에만 사용한다.
- 처음199개는 200봉 워밍업 문맥이다. validation은 과거 train의 최대199봉을 문맥으로 사용하지만 현금·포지션·신호 쿨다운은 각 split에서 새 연구 계좌로 시작한다. 마지막 봉은 이후 시가가 없어 평가/체결하지 않았음을 명시한다.
- 운영의 틱별 가격/5분 데이터 갱신을 재현하지 않는다. 연구는 완료 봉당 한 번 평가한다. 동일 봉 반복 시도는 별도 계수 후 bot 호출 전에 차단하며, synthetic 반복 테스트로 중복 체결 방지를 검증한다. 운영의 중복 평가를 실제로 관측했다고 주장하지 않는다.
- 위험관리도 연구의 다음 시가 관측에서만 실행된다. 장중 익절/손절 도달 순서, gap 체결, 호가 잔량·대기열·지연·부분 체결 가능성을 OHLCV로 알 수 없다. 정상 시뮬레이션은 전량 즉시 체결 가정이다. 부분 매도/취소 영수증은 별도 synthetic 회귀로 실제 엔진/P9 배분을 검증했으며 실제 데이터에서 부분 체결을 관측했다고 주장하지 않는다.
- 신호·위험관리 평가 시점과 다음 open 매수/매도 가격 차이, 200봉 유한 EWM, 워밍업199개, 비용 포함 PAPER 영수증 때문에 기존 `BacktestEngine`의 지표/체결 결과와 숫자가 같다고 가정하지 않는다. 이전 위험관리 연구의 비교 표를 이 결과로 덮어쓰지 않는다.

## 집계 정의

`input_candles`는 문맥 포함 입력, `evaluations`는 거래 기회가 있는 유효 완료 봉 수다. raw 신호는 거래량·쿨다운 전 잠재 신호이며 effective 신호는 실제 bot이 분석기를 호출하여 필터를 적용한 결과다. 위험관리에서 처리하여 분석기를 호출하지 않은 봉은 `risk_bypassed_analyzer`로 별도 집계한다. 잠재 신호를 곧 주문/체결 수로 해석하지 않는다.

각 RSI/MACD/BB BUY/SELL 조건의 met/not_met 및 규칙별 첫 실패/가장 많은 첫 실패를 보고한다. 여러 규칙과 조건은 겹치므로 합계를 HOLD 건수처럼 더하지 않는다. `volume_blocked`, `cooldown_blocked`는 차단된 분석 횟수이며 `cooldown_blocked_actionable`은 그중 raw BUY/SELL이 실제로 차단된 횟수다. 보유 중 BUY, 미보유 SELL, 주문 시도, 현금/최소금액/보유한도 거절과 확정 매수·매도 횟수를 분리한다. 현금이 최소금액 미만인 경우와 40% 배정액이 최소금액 미만인 경우도 구분한다.

- 순수익률: `(최종 현금 + 잔여 수량×마지막 종가 - 초기현금)/초기현금×100`, 매수/매도 수수료 포함. 강제 청산은 하지 않는다.
- gross_fee_addback_return_pct: 같은 체결·수량 경로의 최종 평가자산에 누적 수수료만 더한 수익률. **별도 무비용 전략 재실행이나 슬리피지 없는 수익률이 아니다.** 비용으로 수량/경로가 달라지는 반사실 수익률을 만들지 않는다.
- MDD: 초기 현금과 체결 후 완료 봉 종가 평가 곡선의 최대 낙폭. 장중 MDD가 아니다.
- 승률: P9 수수료 포함 순실현 손익이 양수인 매도/확정 매도 수. 평균 손익과 거래당 기대값은 같은 매도 표본의 평균 순실현 손익이며 승률×평균익 등에서 임의 추정하지 않는다. 미청산 매수는 매도 표본에 넣지 않는다.
- 비용: frozen fee5/10bps × slippage0/5/10bps 전체6조건, full spread2bps. 이는 실제 계정 요율/실현 slippage 증거가 아닌 고정 연구 가정이다. BUY는 기존 all-in 배정액 내에서 수수료를 지급하므로 매수 수량을 줄이고, SELL은 순수령액에서 차감한다. slippage/half-spread 비용은 체결 수량×원시시가×각 bps이며 반사실 계좌 성과 차이가 아니다.
- Buy & Hold: 같은 첫 평가 가능 시가·동일 비용의 100% 및40% 초기 매수, 마지막 종가 평가, 강제 매도/청산 수수료 없음. 같은 기간의 비교 기준이며 다종목 포트폴리오가 아니다.
- 확정 거래0건이면 수익률·MDD·승률·평균/기대손익·B&H 지표를 null/Markdown N/A로 표시한다. 승률/평균은 매도0건에서도 N/A다. 보유 중 지표가 비정상이면 바를 건너뛰어 손실을 숨기지 않고 전체 결과를 실패 처리한다.

## 실제 자료 범위와 한계

재사용 자료는 `research/2026-10-21/data`: BTC/ETH/XRP/SOL, 15분/60분, train `[2026-07-23,2026-09-15)`, validation `[2026-09-15,2026-10-03)` UTC다. 이전에 공개된 자료이므로 탐색적 재사용이며 독립 검증/최적화/전략 선정이 아니다. 비용 격자도 기존 조건을 그대로 보고한다.

`[2026-10-03,2026-10-21)` holdout은 논리적 행을 요청·파싱·분석·평가하지 않았다. 기존 gzip prefix reader를 그대로 재사용하므로 내부 압축 버퍼가 tail byte를 읽을 수 있다는 기존 한계는 남는다. 독립 파일 vault/full gzip CRC 검증으로 주장하지 않는다. 원본 CSV/protocol/manifest/기존 결과는 변경하지 않는다.

1분봉이나 이번 Mac 시험의 선택 종목별 봉·틱·평가 입력 기록은 없다. 따라서 `production_minute1_status=INSUFFICIENT_REAL_DATA`. 짧은 실시간 시험의0건을 특정 조건 탓으로 확정할 수 없다. 오프라인의 신호 존재는 운영에서 그 신호가 발생했다는 증거가 아니다. 현행 복합 전략은 세 지표의 만장일치가 필요한 구조도 아니며, 순서대로 action을 갱신하고 2개 이상 동일 방향이면 복합 신호로 덮어쓰는 실제 코드를 그대로 사용한다.

실제16개 dataset/split ×6비용 결과는 [연구 요약](../research/p11a-diagnostic-2026-10-26/README.md) 및 각 JSON/Markdown에 기록한다. synthetic 테스트는 별도로 표시하며 실제 수익성 자료로 혼합하지 않는다. 임의 파일/manifest 누락·hash 불일치/자료 부족/잘못된 interval 또는 sealed split은 다운로드 없이 실패한다.

## 재현 명령

```sh
cd "$HOME/projects/auto_trade"
.venv/bin/python -m backtesting.legacy_diagnostic \
  --data research/2026-10-21/data --market KRW-BTC --unit 60 \
  --split validation --format json
.venv/bin/python -m backtesting.legacy_diagnostic \
  --data research/2026-10-21/data --market KRW-BTC --unit 60 \
  --split validation --format markdown
```

모든 인자는 필수다. market은 BTC/ETH/XRP/SOL의 KRW 코드, unit은15/60, split은train/validation만 가능하다. format은json/markdown이다. 파일 저장은 사용자가 stdout redirect를 명시한다. 출력 경로에 운영 원장/백업을 지정하지 않는다. 전체 공개 데이터 결과의 재현 runner는 연구 디렉터리의 `reproduce.py`를 사용한다. API/시장 연결/계좌 생성/운영 PAPER로의 fallback은 없다. 외부 서비스·모델·유료 인프라·Slack·launchd·자동 시작/재시작/병합은 OFF, REAL_TRADING=False 유지다.

신규 테스트는 미래 suffix 불변, 실제 신호/필터/거절, 다음 시가 체결, 중복 평가, 수수료·잔액·부분 매도·P9 정산,0건N/A, 명시CLI와 sealed split/manifest 차단을 검증한다. 전체 OS CI exact head/run/attempt/수/skip은 PR에 기록한다. Mac 실기기나 실제 원장 검증은 수행하지 않았다.
