import pyupbit
import numpy as np
import logging
from datetime import datetime
import pandas as pd

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),  # 로그를 파일에 기록
        logging.StreamHandler()  # 로그를 콘솔에 출력
    ]
)

class DataAnalyzer:
    def __init__(self, ticker):
        self.ticker = ticker
        self.data = None
        self.signals = None

    def fetch_data(self):
        """가격 및 그래프 데이터를 조회합니다."""
        try:
            self.data = pyupbit.get_ohlcv(self.ticker, interval="minute60", count=200)
            logging.info("데이터 조회 완료")
        except Exception as e:
            logging.error(f"데이터 조회 중 오류 발생: {e}")
            self.data = None

    def calculate_indicators(self):
        """모든 지표를 한 번에 계산합니다."""
        if self.data is None:
            logging.warning("데이터가 없습니다. 데이터를 먼저 조회해주세요.")
            return

        self.data = self.data.copy()

        # 이동평균선 계산
        self.data['ma5'] = self.data['close'].rolling(window=5).mean()
        self.data['ma20'] = self.data['close'].rolling(window=20).mean()

        # RSI 계산
        delta = self.data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        self.data['rsi'] = 100 - (100 / (1 + rs))

        # 볼린저 밴드 계산
        self.data['bb_mid'] = self.data['close'].rolling(window=20).mean()
        self.data['bb_std'] = self.data['close'].rolling(window=20).std()
        self.data['bb_upper'] = self.data['bb_mid'] + (self.data['bb_std'] * 2)
        self.data['bb_lower'] = self.data['bb_mid'] - (self.data['bb_std'] * 2)

        self.calculate_signals()

    def calculate_signals(self):
        """모든 거래 시그널을 미리 계산합니다."""
        self.signals = pd.DataFrame(index=self.data.index)
        
        # 매수 조건
        buy_condition = (self.data['close'] < self.data['bb_lower']) & (self.data['rsi'] < 30)
        
        # 매도 조건
        sell_condition = (self.data['close'] > self.data['bb_upper']) & (self.data['rsi'] > 70)
        
        self.signals['signal'] = 'HOLD'
        self.signals.loc[buy_condition, 'signal'] = 'BUY'
        self.signals.loc[sell_condition, 'signal'] = 'SELL'

    def analyze(self, current_index):
        """특정 시점의 매매 시그널을 반환합니다."""
        if self.signals is None:
            return "HOLD"
        try:
            return self.signals.iloc[current_index]['signal']
        except IndexError:
            logging.warning(f"인덱스 {current_index}가 범위를 벗어났습니다.")
            return "HOLD"
