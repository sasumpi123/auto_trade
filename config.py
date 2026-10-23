import os
import logging
from dotenv import load_dotenv
import pyupbit

# .env 파일에서 환경 변수 로드
load_dotenv()

# 환경 변수 및 API 키
UPBIT_ACCESS_KEY = os.getenv("ACC_KEY")
UPBIT_SECRET_KEY = os.getenv("SEC_KEY")
# Legacy APP_TOKEN accepted only when it actually has bot-token syntax.
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN") or os.getenv("APP_TOKEN")
SLACK_APP_TOKEN = SLACK_BOT_TOKEN  # compatibility alias, not an app-level token
SLACK_NOTIFICATIONS_ENABLED = False  # separate reviewed approval required
SLACK_CHANNEL = os.getenv("CHANNEL")

# Slack 채널 설정
SLACK_CHANNELS = {
    'status': 'trading-status',     # 주기적 상태 업데이트
    'trades': 'trading-alerts',     # 매수/매도 알림
    'reports': 'trading-reports',   # 일일/주간 리포트
    'errors': 'trading-errors'      # 에러 알림
}

def get_top_tickers(limit=10, *, client=None):
    """Explicit public rolling-24h turnover discovery; no per-market day candles."""
    from services.public_market import get_top_tickers as discover
    return discover(limit, client=client)

# 거래 대상 코인 (거래대금 상위 10개)
# Network discovery is explicit at startup, never during module import.
TICKERS = None

# 거래 모드
REAL_TRADING = False  # True: 실제 거래, False: 테스트 거래

# 거래 설정
START_CASH = 1_000_000          # 시작 자금 (테스트 모드)
MIN_TRADING_AMOUNT = 5000       # 최소 거래금액
MAX_COINS_AT_ONCE = 2          # 동시 보유 가능한 최대 코인 수 (2개로 수정)
CASH_USAGE_RATIO = 0.4         # 코인당 최대 투자 비율 (40%)
STOP_LOSS = 0.05              # 손절 라인 (5%)

# 물타기 설정 추가
AVERAGING_DOWN_RATIO = 0.5     # 물타기 시 추가 매수 비율 (기존 보유 금액의 50%)
MAX_AVERAGING_DOWN = 1         # 코인당 최대 물타기 횟수

# 시간 간격 설정
REPORT_CHECK_INTERVAL = 30  # 리포트 체크 간격 (30초)
DATA_UPDATE_INTERVAL = 300  # 데이터 업데이트 간격 (5분)
STATUS_INTERVAL = 300      # 상태 체크 간격 (5분)
