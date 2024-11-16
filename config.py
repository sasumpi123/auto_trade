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

# 환경 변수
ACC_KEY = os.getenv("ACC_KEY")
SEC_KEY = os.getenv("SEC_KEY")
APP_TOKEN = os.getenv("APP_TOKEN")
CHANNEL = os.getenv("CHANNEL")

# 거래 설정
TICKERS = [
    "KRW-BTC",  # 비트코인
    "KRW-ETH",  # 이더리움
    "KRW-DOGE",  # 도지코인
    "KRW-XRP",  # 리플코인
]

STOP_LOSS = 0.05
MIN_ORDER_AMOUNT = 5000