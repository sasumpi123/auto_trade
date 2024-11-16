import os
import logging
from dotenv import load_dotenv

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)

# .env 파일에서 환경 변수 로드
load_dotenv()

# 환경 변수 및 API 키
UPBIT_ACCESS_KEY = os.getenv("ACC_KEY")
UPBIT_SECRET_KEY = os.getenv("SEC_KEY")
SLACK_APP_TOKEN = os.getenv("APP_TOKEN")
SLACK_CHANNEL = os.getenv("CHANNEL")

# Slack 채널 설정
SLACK_CHANNELS = {
    'status': 'trading-status',     # 주기적 상태 업데이트
    'trades': 'trading-alerts',     # 매수/매도 알림
    'reports': 'trading-reports',   # 일일/주간 리포트
    'errors': 'trading-errors'      # 에러 알림
}

# 거래 대상 코인
TICKERS = [
    "KRW-BTC",  # 비트코인
    "KRW-ETH",  # 이더리움
    "KRW-DOGE", # 도지코인
    "KRW-XRP",  # 리플코인
]

# 거래 모드
REAL_TRADING = False  # True: 실제 거래, False: 테스트 거래

# 거래 설정
START_CASH = 1_000_000          # 시작 자금 (테스트 모드)
MIN_TRADING_AMOUNT = 5000       # 최소 거래금액
MAX_COINS_AT_ONCE = 2          # 동시 보유 가능한 최대 코인 수
CASH_USAGE_RATIO = 0.4         # 코인당 최대 투자 비율 (40%)
STOP_LOSS = 0.02              # 손절 라인 (2%)