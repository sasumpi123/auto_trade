import pyupbit
import pandas as pd
from datetime import datetime, timedelta
import logging
import time

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('scalping_backtest.log'),
        logging.StreamHandler()
    ]
)

def fetch_minute_data(ticker, minutes, count):
    """지정된 분봉 데이터를 가져옵니다."""
    logging.info(f"{ticker}의 {count}개의 {minutes}분봉 데이터 수집 시작")
    
    try:
        data = pyupbit.get_ohlcv(ticker, interval=f"minute{minutes}", count=count)
        if data is not None and not data.empty:
            logging.info(f"수집된 데이터 개수: {len(data)}")
        else:
            logging.error("데이터를 가져오는 데 실패했습니다.")
        return data
    except Exception as e:
        logging.error(f"데이터 수집 중 오류 발생: {e}")
        return None

class ScalpingBacktester:
    def __init__(self, ticker, initial_balance, data, profit_target=1.01, stop_loss=0.99):
        self.ticker = ticker
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = 0
        self.trades = []
        self.data = data
        self.profit_target = profit_target  # 1% 수익 목표
        self.stop_loss = stop_loss  # 1% 손절

    def run_backtest(self):
        if self.data is None or self.data.empty:
            logging.error("데이터가 없습니다. 백테스팅을 수행할 수 없습니다.")
            return

        for index in range(1, len(self.data)):
            current_price = self.data.iloc[index]['close']
            high = self.data.iloc[index]['high']
            low = self.data.iloc[index]['low']

            # 매수 조건: 이전 봉 대비 현재 봉의 가격이 상승하고, 거래량이 증가하는 경우
            if self.position == 0 and self.data.iloc[index]['close'] > self.data.iloc[index - 1]['close']:
                self.position = self.balance / current_price
                self.entry_price = current_price
                self.balance = 0
                self.trades.append((self.data.index[index], "BUY", current_price))
                logging.info(f"{self.data.index[index]}: 매수 실행 - 가격: {current_price:,}원")

            # 수익 목표 및 손절 조건
            if self.position > 0:
                if high >= self.entry_price * self.profit_target:
                    self.balance = self.position * self.entry_price * self.profit_target
                    self.position = 0
                    self.trades.append((self.data.index[index], "TAKE PROFIT", self.entry_price * self.profit_target))
                    logging.info(f"{self.data.index[index]}: 수익 목표 도달 - 가격: {self.entry_price * self.profit_target:,}원")
                elif low <= self.entry_price * self.stop_loss:
                    self.balance = self.position * self.entry_price * self.stop_loss
                    self.position = 0
                    self.trades.append((self.data.index[index], "STOP LOSS", self.entry_price * self.stop_loss))
                    logging.info(f"{self.data.index[index]}: 손절 - 가격: {self.entry_price * self.stop_loss:,}원")

        final_value = self.balance + (self.position * self.data.iloc[-1]['close'])
        logging.info(f"최종 자산: {final_value:,.2f}원")

# 백테스팅 실행
if __name__ == "__main__":
    ticker = "KRW-BTC"
    initial_balance = 1000000  # 초기 자산 (100만 원)
    minutes = 1  # 1분봉
    count = 1000  # 1000개의 데이터 가져오기

    data = fetch_minute_data(ticker, minutes, count)
    if data is not None:
        backtester = ScalpingBacktester(ticker, initial_balance, data)
        backtester.run_backtest()
