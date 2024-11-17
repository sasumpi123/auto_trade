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
pip install -r requirements.txt
```

## 환경 설정

`.env` 파일을 생성하고 다음 정보를 설정합니다:
UPBIT_ACCESS_KEY=your_access_key
UPBIT_SECRET_KEY=your_secret_key
SLACK_APP_TOKEN=your_slack_token
SLACK_CHANNEL=your_slack_channel

## 트레이딩 전략

현재 구현된 트레이딩 전략은 다음과 같습니다:

### 1. RSI (Relative Strength Index)

- 과매도(RSI < 30) 상황에서 매수
- 과매수(RSI > 70) 상황에서 매도
- 매수 시 목표수익률: 5%

### 2. MACD (Moving Average Convergence Divergence)

- 골든크로스 + 음수영역에서 매수
- 데드크로스 + 양수영역에서 매도
- 매수 시 목표수익률: 3%

### 3. 볼린저 밴드 (Bollinger Bands)

- 하단밴드 하향 돌파 시 매수
- 상단밴드 상향 돌파 시 매도
- 매수 시 목표가: 중간밴드

## 주요 설정

`config.py`에서 다음 설정을 조정할 수 있습니다:

```python
REAL_TRADING = False # 실제/시뮬레이션 모드 설정
START_CASH = 1_000_000 # 시작 자금
MIN_TRADING_AMOUNT = 5000 # 최소 거래금액
CASH_USAGE_RATIO = 0.4 # 코인당 최대 투자 비율 (40%)
STOP_LOSS = 0.02 # 손절 라인 (2%)
```

## 실행 방법

```bash
python main.py
```

## 로그 및 모니터링

- 모든 거래 내역과 시스템 로그는 `trading_bot.log` 파일에 기록됩니다
- 5분마다 현재 포트폴리오 상태가 로깅됩니다
- Slack 설정 시 주요 이벤트에 대한 알림을 받을 수 있습니다

## 프로젝트 구조

```
auto_trade/
├── main.py # 메인 실행 파일
├── config.py # 설정 파일
├── requirements.txt # 필요 패키지 목록
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
- 손절매 기능 (2% 손실 시)
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
