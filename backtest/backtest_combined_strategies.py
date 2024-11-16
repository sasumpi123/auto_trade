import pyupbit
import pandas as pd
import logging

# 로그 설정
logging.basicConfig(
    filename='backtest_combined_results.log',
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

    def calculate_indicators(self):
        """지표를 계산합니다."""
        if self.data is None:
            logging.warning("데이터가 없습니다. 데이터를 먼저 조회해주세요.")
            return

        self.data = self.data.copy()

        # 이동평균선 계산
        self.data['ma9'] = self.data['close'].rolling(window=9).mean()
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

        # MACD 계산
        self.data['ema12'] = self.data['close'].ewm(span=12, adjust=False).mean()
        self.data['ema26'] = self.data['close'].ewm(span=26, adjust=False).mean()
        self.data['macd'] = self.data['ema12'] - self.data['ema26']
        self.data['signal'] = self.data['macd'].ewm(span=9, adjust=False).mean()

        # 스토캐스틱 오실레이터 계산
        low_min = self.data['low'].rolling(window=14).min()
        high_max = self.data['high'].rolling(window=14).max()
        self.data['%K'] = 100 * ((self.data['close'] - low_min) / (high_max - low_min))
        self.data['%D'] = self.data['%K'].rolling(window=3).mean()

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

    def moving_average_macd_strategy(self):
        """이동평균선 + MACD 전략"""
        buy_signal = (self.data['ma9'] > self.data['ma20']) & (self.data['macd'] > self.data['signal'])
        sell_signal = (self.data['ma9'] < self.data['ma20']) & (self.data['macd'] < self.data['signal'])
        return self.calculate_returns(buy_signal, sell_signal)

    def rsi_bollinger_strategy(self):
        """RSI + 볼린저 밴드 전략"""
        buy_signal = (self.data['rsi'] < 30) & (self.data['close'] < self.data['bb_lower'])
        sell_signal = (self.data['rsi'] > 70) & (self.data['close'] > self.data['bb_upper'])
        return self.calculate_returns(buy_signal, sell_signal)

    def macd_bollinger_strategy(self):
        """MACD + 볼린저 밴드 전략"""
        buy_signal = (self.data['macd'] > self.data['signal']) & (self.data['close'] < self.data['bb_lower'])
        sell_signal = (self.data['macd'] < self.data['signal']) & (self.data['close'] > self.data['bb_upper'])
        return self.calculate_returns(buy_signal, sell_signal)

# 사용 예시
backtest = Backtest("KRW-BTC", interval='minute5')
backtest.fetch_data()
backtest.calculate_indicators()

combined_strategies = {
    "이동평균선 + MACD 전략": backtest.moving_average_macd_strategy,
    "RSI + 볼린저 밴드 전략": backtest.rsi_bollinger_strategy,
    "MACD + 볼린저 밴드 전략": backtest.macd_bollinger_strategy
}

for name, strategy in combined_strategies.items():
    buy_count, sell_count, daily_returns, final_returns, final_balance = strategy()
    logging.info(f"{name} 결과:")
    logging.info(f"매수 횟수: {buy_count}, 매도 횟수: {sell_count}")
    logging.info(f"최종 수익률: {final_returns:.2%}, 최종 금액: {final_balance:.2f}원")
    logging.info("일별 수익률:")
    logging.info(daily_returns.dropna().to_string())
    logging.info("-" * 40) 