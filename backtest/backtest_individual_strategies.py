import pyupbit
import pandas as pd

class Backtest:
    def __init__(self, ticker):
        self.ticker = ticker
        self.data = None

    def fetch_data(self):
        """1년치 일봉 데이터를 조회합니다."""
        try:
            self.data = pyupbit.get_ohlcv(self.ticker, interval="day", count=365)
        except Exception as e:
            print(f"데이터 조회 중 오류 발생: {e}")
            self.data = None

    def calculate_indicators(self):
        """지표를 계산합니다."""
        if self.data is None:
            print("데이터가 없습니다. 데이터를 먼저 조회해주세요.")
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

    def moving_average_strategy(self):
        """이동평균선 전략"""
        buy_signal = self.data['ma9'] > self.data['ma20']
        sell_signal = self.data['ma9'] < self.data['ma20']
        return self.calculate_returns(buy_signal, sell_signal)

    def rsi_strategy(self):
        """RSI 전략"""
        buy_signal = self.data['rsi'] < 30
        sell_signal = self.data['rsi'] > 70
        return self.calculate_returns(buy_signal, sell_signal)

    def bollinger_bands_strategy(self):
        """볼린저 밴드 전략"""
        buy_signal = self.data['close'] < self.data['bb_lower']
        sell_signal = self.data['close'] > self.data['bb_upper']
        return self.calculate_returns(buy_signal, sell_signal)

    def macd_strategy(self):
        """MACD 전략"""
        buy_signal = self.data['macd'] > self.data['signal']
        sell_signal = self.data['macd'] < self.data['signal']
        return self.calculate_returns(buy_signal, sell_signal)

    def stochastic_oscillator_strategy(self):
        """스토캐스틱 오실레이터 전략"""
        buy_signal = (self.data['%K'] > self.data['%D']) & (self.data['%K'] < 20)
        sell_signal = (self.data['%K'] < self.data['%D']) & (self.data['%K'] > 80)
        return self.calculate_returns(buy_signal, sell_signal)

    def calculate_returns(self, buy_signal, sell_signal):
        """매수/매도 신호에 따라 수익률을 계산합니다."""
        self.data['position'] = 0
        self.data.loc[buy_signal, 'position'] = 1
        self.data.loc[sell_signal, 'position'] = -1
        self.data['position'] = self.data['position'].shift(1).fillna(0)

        self.data['returns'] = self.data['close'].pct_change()
        self.data['strategy_returns'] = self.data['position'] * self.data['returns']

        cumulative_returns = (1 + self.data['strategy_returns']).cumprod() - 1
        return cumulative_returns.iloc[-1]

# 사용 예시
backtest = Backtest("KRW-BTC")
backtest.fetch_data()
backtest.calculate_indicators()

print("이동평균선 전략 수익률:", backtest.moving_average_strategy())
print("RSI 전략 수익률:", backtest.rsi_strategy())
print("볼린저 밴드 전략 수익률:", backtest.bollinger_bands_strategy())
print("MACD 전략 수익률:", backtest.macd_strategy())
print("스토캐스틱 오실레이터 전략 수익률:", backtest.stochastic_oscillator_strategy()) 