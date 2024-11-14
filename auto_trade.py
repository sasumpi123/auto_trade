import pyupbit
import datetime
import requests
import traceback
import time
from collections import deque
from dotenv import load_dotenv
import os
from data_analyzer import DataAnalyzer

# .env 파일에서 환경 변수 로드
load_dotenv()

# 환경 변수 불러오기
acc_key = os.getenv("ACC_KEY")
sec_key = os.getenv("SEC_KEY")
app_token = os.getenv("APP_TOKEN")
channel = os.getenv("CHANNEL")

class AutoTrade:
    def __init__(self, start_cash, ticker):
        self.ticker = ticker
        self.start_cash = start_cash
        self.price_cache = deque(maxlen=100)
        self.analyzer = DataAnalyzer(ticker)
        self.analyzer.fetch_data()
        self.analyzer.calculate_indicators()
        self.last_order_time = 0
        self.buy_yn = False

    def start(self):
        try:
            wm = pyupbit.WebSocketManager("ticker", [self.ticker])
            while True:
                data = wm.get()
                current_price = data['trade_price']
                self.price_cache.append(current_price)

                action = self.analyzer.analyze(-1)  # 가장 최신 데이터 사용
                if action == "BUY" and not self.buy_yn:
                    self.buy_coin()
                elif action == "SELL" and self.buy_yn:
                    self.sell_coin()

        except Exception as err:
            traceback.print_exc()

    def buy_coin(self):
        """매수 로직"""
        # 코인 매수 로직 추가
        pass

    def sell_coin(self):
        """매도 로직"""
        # 코인 매도 로직 추가
        pass

if __name__ == "__main__":
    upbit = pyupbit.Upbit(acc_key, sec_key)
    start_cash = upbit.get_balance()
    ticker = "KRW-BTC"
    auto_trader = AutoTrade(start_cash, ticker)
