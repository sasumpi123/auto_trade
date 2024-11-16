import pyupbit
import pandas as pd
import logging
import numpy as np

# 로그 설정
logging.basicConfig(
    filename='backtest_rsi_bollinger_optimized.log',
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

class Backtest:
    def __init__(self, ticker, interval='minute5', initial_balance=1000000):
        self.ticker = ticker
        self.interval = interval
        self.initial_balance = initial_balance
        self.data = None

    def fetch_data(self):
        """3개월치 데이터를 조회합니다."""
        try:
            self.data = pyupbit.get_ohlcv(self.ticker, interval=self.interval, count=3*30*24*60//int(self.interval[-1]))
        except Exception as e:
            logging.error(f"데이터 조회 중 오류 발생: {e}")
            self.data = None

    def calculate_indicators(self, rsi_period=14, bb_period=20, bb_std=2):
        """지표를 계산합니다."""
        if self.data is None:
            logging.warning("데이터가 없습니다. 데이터를 먼저 조회해주세요.")
            return

        self.data = self.data.copy()

        # RSI 계산
        delta = self.data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
        rs = gain / loss
        self.data['rsi'] = 100 - (100 / (1 + rs))

        # 볼린저 밴드 계산
        self.data['bb_mid'] = self.data['close'].rolling(window=bb_period).mean()
        self.data['bb_std'] = self.data['close'].rolling(window=bb_period).std()
        self.data['bb_upper'] = self.data['bb_mid'] + (self.data['bb_std'] * bb_std)
        self.data['bb_lower'] = self.data['bb_mid'] - (self.data['bb_std'] * bb_std)

    def rsi_bollinger_strategy(self, rsi_buy=30, rsi_sell=70):
        """RSI + 볼린저 밴드 전략"""
        buy_signal = (self.data['rsi'] < rsi_buy) & (self.data['close'] < self.data['bb_lower'])
        sell_signal = (self.data['rsi'] > rsi_sell) & (self.data['close'] > self.data['bb_upper'])
        return self.calculate_returns(buy_signal, sell_signal)

    def calculate_returns(self, buy_signal, sell_signal):
        """매수/매도 신호에 따라 수익률을 계산합니다."""
        self.data['position'] = 0
        self.data.loc[buy_signal, 'position'] = 1
        self.data.loc[sell_signal, 'position'] = -1
        self.data['position'] = self.data['position'].shift(1).fillna(0)

        self.data['returns'] = self.data['close'].pct_change()
        self.data['strategy_returns'] = self.data['position'] * self.data['returns']

        # 매수/매도 횟수 계산
        buy_count = buy_signal.sum()
        sell_count = sell_signal.sum()

        # 일별 수익률 계산
        self.data['daily_returns'] = self.data['strategy_returns'].resample('D').sum()

        # 최종 수익률 및 금액 계산
        cumulative_returns = (1 + self.data['strategy_returns']).cumprod() - 1
        final_returns = cumulative_returns.iloc[-1]
        final_balance = self.initial_balance * (1 + final_returns)

        return buy_count, sell_count, self.data['daily_returns'], final_returns, final_balance

# 사용 예시
backtest = Backtest("KRW-BTC", interval='minute5')
backtest.fetch_data()

# 매개변수 조정
rsi_periods = [12, 14, 16]
bb_periods = [18, 20, 22]
bb_stds = [1.5, 2, 2.5]
rsi_buy_levels = [25, 30, 35]
rsi_sell_levels = [65, 70, 75]

best_result = None
best_params = None

for rsi_period in rsi_periods:
    for bb_period in bb_periods:
        for bb_std in bb_stds:
            for rsi_buy in rsi_buy_levels:
                for rsi_sell in rsi_sell_levels:
                    backtest.calculate_indicators(rsi_period=rsi_period, bb_period=bb_period, bb_std=bb_std)
                    buy_count, sell_count, daily_returns, final_returns, final_balance = backtest.rsi_bollinger_strategy(rsi_buy=rsi_buy, rsi_sell=rsi_sell)
                    
                    logging.info(f"RSI 기간: {rsi_period}, BB 기간: {bb_period}, BB 표준편차: {bb_std}, RSI 매수: {rsi_buy}, RSI 매도: {rsi_sell}")
                    logging.info(f"매수 횟수: {buy_count}, 매도 횟수: {sell_count}")
                    logging.info(f"최종 수익률: {final_returns:.2%}, 최종 금액: {final_balance:.2f}원")
                    logging.info("일별 수익률:")
                    logging.info(daily_returns.dropna().to_string())
                    logging.info("-" * 40)

                    if best_result is None or final_returns > best_result:
                        best_result = final_returns
                        best_params = (rsi_period, bb_period, bb_std, rsi_buy, rsi_sell)

logging.info(f"최적의 매개변수: RSI 기간: {best_params[0]}, BB 기간: {best_params[1]}, BB 표준편차: {best_params[2]}, RSI 매수: {best_params[3]}, RSI 매도: {best_params[4]}")
logging.info(f"최고 수익률: {best_result:.2%}") 