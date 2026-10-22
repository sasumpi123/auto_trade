# 암호화폐 자동매매 봇

암호화폐 시장에서 기술적 분석을 기반으로 자동으로 매매를 수행하는 트레이딩 봇입니다.

## 주요 기능

- 실시간 시세 모니터링
- 기술적 지표 기반 매매 신호 분석
- 자동 매수/매도 실행
- 실시간 포트폴리오 상태 모니터링
- Slack을 통한 알림 서비스
- 백테스팅 기능 (시뮬레이션 모드)

## 기술 스택

- Python 3.8+
- pyupbit (업비트 API)
- pandas (데이터 분석)
- numpy (수치 계산)
- slack-sdk (알림 서비스)

## 설치 방법

```bash
1. 가상환경 생성
python -m venv myenv
가상환경 활성화
source myenv/bin/activate # Linux/Mac
myenv\Scripts\activate # Windows
필요 패키지 설치
pip install -r requirement.txt
```

## 환경 설정

`.env` 파일을 생성하고 다음 정보를 설정합니다:
ACC_KEY=your_access_key
SEC_KEY=your_secret_key
APP_TOKEN=your_slack_token
CHANNEL=your_slack_channel

## 트레이딩 전략

현재 구현된 트레이딩 전략은 다음과 같습니다:

### 1. RSI (Relative Strength Index)

- RSI가 30 이하이면서 상승 추세일 때 매수
- RSI가 70 이상이면서 하락 추세일 때 매도
- 매수 시 목표수익률: 5%

### 2. MACD (Moving Average Convergence Divergence)

- 골든크로스 + 음수영역 + 상승추세일 때 매수
- 데드크로스 + 양수영역 + 하락추세일 때 매도
- 매수 시 목표수익률: 3%

### 3. 볼린저 밴드 (Bollinger Bands)

- 하단밴드 돌파 후 반등 신호 확인 시 매수 (RSI 상승 또는 MACD 반등)
- 상단밴드 상향 돌파 시 매도
- 매수 시 목표가: 중간밴드

### 4. 복합 매매 전략

- 2개 이상의 지표에서 동시에 매수/매도 신호가 발생할 때 신뢰도 증가
- 복합 매수 신호 발생 시 목표수익률: 5%
- 각 지표의 방향성(추세)을 함께 고려하여 신뢰도 향상

## 주요 설정

`config.py`에서 다음 설정을 조정할 수 있습니다:

```python
REAL_TRADING = False # 실제/시뮬레이션 모드 설정
START_CASH = 1_000_000 # 시작 자금
MIN_TRADING_AMOUNT = 5000 # 최소 거래금액
CASH_USAGE_RATIO = 0.4 # 코인당 최대 투자 비율 (40%)
STOP_LOSS = 0.05 # 손절 라인 (5%)
```

## 실행 방법

```bash
python main.py
```

## 오프라인 전략 연구 및 백테스트

기존 봇은 호환 전략을 유지합니다. 새 RSI·MACD·볼린저 단독 및 2/3·3/3 합의 전략은
실거래에 연결되지 않은 연구 후보입니다. 로컬 CSV 또는 합성 데이터로만 비교합니다.

```bash
# 숫자는 연구 비용 가정 예시이며 현재 업비트 요율이 아닙니다.
python -m backtesting --demo --fee-bps 5 --slippage-bps 3 --spread-bps 2 --output runtime/research-demo
python -m backtesting --csv candles.csv --bar-seconds 60 --fee-bps 5 --slippage-bps 3 --spread-bps 2 --output runtime/research-csv
python -m unittest discover -v
```

출력 폴더는 새 경로여야 합니다. JSON 비교 보고서, 전략별 체결·신호·평가금액 CSV와 지표표를 저장합니다.
CSV timestamp는 시간대를 포함한 **봉 시작 시각**이어야 합니다. 완료된 봉의 신호는 다음 봉 시가에
비용을 적용해 모의 체결하며, 신호만의 비교를 위해 연구 기본값에는 손절·익절·물타기를 적용하지 않습니다.
자세한 정의·가정·한계와 기존 봇과의 차이는 [연구 설계](docs/STRATEGY_RESEARCH.md)를 참고하세요.

**실제 거래소 주문·인증 계좌 조회·운영 배포는 이 연구에서 수행하지 않습니다.**

## 로그 및 모니터링

- 모든 거래 내역과 시스템 로그는 `trading_bot.log` 파일에 기록됩니다
- 5분마다 현재 포트폴리오 상태가 로깅됩니다
- Slack 설정 시 주요 이벤트에 대한 알림을 받을 수 있습니다

## 프로젝트 구조

```
auto_trade/
├── main.py # 메인 실행 파일
├── config.py # 설정 파일
├── requirement.txt # 필요 패키지 목록
├── trading/
│ └── auto_trade.py # 자동매매 핵심 로직
├── data_analyzer/
│ └── analyzer.py # 데이터 분석 및 신호 생성
├── services/
│ ├── api_service.py # API 서비스
│ ├── notification_service.py # 알림 서비스
│ └── performance_service.py # 성능 모니터링
└── utils/
├── decorators.py # 유틸리티 데코레이터
└── message_queue.py # 메시지 큐 관리
```

## 안전장치

- 신호 중복 방지 (5분 쿨다운)
- 에러 발생 시 안전한 기본값 반환
- 손절매 기능 (5% 손실 시)
- API 호출 제한 관리
- 실시간 모니터링 및 알림

## 주의사항

- 실제 거래 전 반드시 시뮬레이션 모드에서 충분한 테스트 필요
- 암호화폐 시장의 높은 변동성 주의
- API 키 보안 관리 필수

## 라이선스

MIT License

## 기여

버그 리포트, 기능 제안, PR은 언제나 환영합니다.


## 실제 과거 데이터 연구

BTC·ETH·XRP·SOL KRW의 15분/1시간봉 공개 OHLCV 수집과 고정 비용 격자 비교는 [연구 설계](docs/HISTORICAL_RESEARCH.md)를 참고하세요.
학습/검증/미사용 테스트를 시간순으로 분리하며 미사용 테스트는 평가하지 않습니다. [고정 스냅샷과 실제 결과](research/2026-10-21/)를 보존합니다. 누락 봉은 채우지 않으며 개발 구간의 품질이 나쁘면 중단합니다. 실제 주문이나 계정 API 없이 실행합니다.

```bash
python -m backtesting.historical collect --data runtime/new-snapshot
python -m backtesting.historical evaluate --data research/2026-10-21/data --output runtime/reproduced
```

## 개발 자동화 제안

승인 이슈 → 격리 구현 → 독립 ChatGPT 검토 → 보호된 병합의 [아키텍처·권한·비용·보안·설정 경계](docs/DEVELOPMENT_AUTOMATION.md)를 참고하세요. 현재는 비밀 없는 읽기 전용 이벤트 관찰, 오프라인 정책 및 SQLite 영속 Fake-only 흐름을 구현했습니다. 유료 모델 작업자·수정 push·자동 병합은 연결/활성화하지 않았습니다. 키/권한 변경은 사용자 설정과 별도 검토가 필요합니다.

## P1 영속 Dry-run 개발 흐름

[SQLite 원장·상태 전이·lease·백업/복구와 활성화 전제](docs/P1_LEDGER.md)를 참고하세요. 외부 API, 키, 실제 PR/Push/병합 없이 모의 흐름만 실행합니다. 기본 데모는 병합 가능 판정에서 멈추며 자동화는 계속 비활성 상태입니다.

```bash
python -m automation demo --database runtime/p1-normal.sqlite3 --scenario normal
python -m automation demo --database runtime/p1-revision.sqlite3 --scenario revision
python -m automation demo --database runtime/p1-failure.sqlite3 --scenario failure
python -m automation status --database runtime/p1-normal.sqlite3
```

DB는 명시적으로 새로 만들며 덮어쓰지 않습니다. 만료/모호한 작업과 복원된 백업은 격리하며, 예약은 자동 환급하지 않습니다.

## P2 GitHub 읽기 전용 검증

[인증·이벤트·별도 관측 DB·P3 전제](docs/P2_GITHUB_READONLY.md)를 참고하세요. master 코드로 실제 REST 상태를 재조회하지만 실행/Push/병합과 연결하지 않습니다. 자동화와 실거래는 비활성 상태이며 API 권한 부족은 검증 보류입니다. 테스트는 Fake HTTP만 사용합니다.

## P3: 추가 지출 0원 로컬 모의 Worker

[Mac M3·Plus CLI·로컬 SQLite·launchd 설계와 확인 사항](docs/P3_LOCAL_WORKER.md)을 참고하세요. 실제 CLI/API 과금/게시/병합은 비활성이고, Plus 한도·추가 크레딧 미사용을 증명하지 못하면 중단합니다. API 키/서버/유료 DB는 필요하지 않으며 금지합니다.

```bash
python -m automation.local_runner --dry-run
python -m automation.local_runner --dry-run --database runtime/new-p3.sqlite3 --scenario revision
```

launchd 예제는 작성만 했고 설치/등록하지 않았습니다. 실제 구독 인증정보를 추출·복사하지 않습니다.

## P4: 독립 리뷰·최종 모의 병합 게이트

[표준 Review 인증, REST/GraphQL 최종 검증, Fake Merge와 P5 설정](docs/P4_REVIEW_MERGE_GATE.md)을 참고하세요. 실제 관측 결과는 읽기 전용이며 `MERGE_ELIGIBLE`은 Fake 후보만 의미합니다. 실제 CLI·자동 게시·병합은 비활성이고 독립 자동 리뷰가 없으면 WAIT_REVIEW로 유지합니다. 중단된 intent/응답 유실은 자동 재전송하지 않습니다.

```bash
python -m unittest tests.test_review_merge_gate -v
```
