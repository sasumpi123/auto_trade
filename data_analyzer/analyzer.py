import pyupbit
import numpy as np
import logging
import pandas as pd
import traceback

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
        logging.info("DataAnalyzer 초기화 시작")
        self.ticker = ticker
        self.data = None
        self.signals = None
        logging.info("DataAnalyzer 초기화 완료")

    def fetch_data(self):
        """가격 및 그래프 데이터를 조회합니다."""
        logging.info("데이터 조회 시작")
        try:
            self.data = pyupbit.get_ohlcv(self.ticker, interval="minute5", count=200)
            logging.info(f"데이터 조회 결과: {type(self.data)}")
            
            if self.data is not None and not self.data.empty:
                logging.info("데이터 형변환 시작")
                # 데이터가 존재할 때만 형변환 수행
                self.data = self.data.astype({
                    'open': 'float32',
                    'high': 'float32',
                    'low': 'float32',
                    'close': 'float32',
                    'volume': 'float32'
                })
                logging.info("데이터 조회 및 형변환 완료")
            else:
                logging.error("데이터 조회 실패: 빈 데이터셋")
                self.data = None
        except Exception as e:
            logging.error(f"데이터 조회 중 오류 발생: {e}")
            logging.error(f"오류 타입: {type(e)}")
            traceback.print_exc()
            self.data = None

    def calculate_indicators(self):
        """모든 지표를 한 번에 계산합니다."""
        if self.data is None or self.data.empty:
            logging.warning("데이터가 없습니다. 데이터를 먼저 조회해주세요.")
            return

        try:
            self.data = self.data.copy()

            # RSI 계산
            delta = self.data['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=12).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=12).mean()
            rs = gain / loss
            self.data['rsi'] = 100 - (100 / (1 + rs))

            # 볼린저 밴드 계산
            self.data['bb_mid'] = self.data['close'].rolling(window=20).mean()
            self.data['bb_std'] = self.data['close'].rolling(window=20).std()
            self.data['bb_upper'] = self.data['bb_mid'] + (self.data['bb_std'] * 1.5)
            self.data['bb_lower'] = self.data['bb_mid'] - (self.data['bb_std'] * 1.5)

            self.calculate_signals()
            
        except Exception as e:
            logging.error(f"지표 계산 중 오류 발생: {e}")
            traceback.print_exc()

    def calculate_signals(self):
        """모든 거래 시그널을 미리 계산합니다."""
        try:
            if self.data is None or self.data.empty:
                logging.warning("데이터가 없어 시그널을 계산할 수 없습니다.")
                return

            self.signals = pd.DataFrame(index=self.data.index)
            
            # NaN 값 처리
            rsi_valid = self.data['rsi'].notna()
            bb_valid = self.data['bb_lower'].notna() & self.data['bb_upper'].notna()
            
            # 매수 조건
            buy_condition = (self.data['close'] < self.data['bb_lower']) & (self.data['rsi'] < 35) & rsi_valid & bb_valid
            
            # 매도 조건
            sell_condition = (self.data['close'] > self.data['bb_upper']) & (self.data['rsi'] > 75) & rsi_valid & bb_valid
            
            self.signals['signal'] = 'HOLD'
            self.signals.loc[buy_condition, 'signal'] = 'BUY'
            self.signals.loc[sell_condition, 'signal'] = 'SELL'
            
        except Exception as e:
            logging.error(f"시그널 계산 중 오류 발생: {e}")
            traceback.print_exc()

    def analyze(self, current_index):
        """특정 시점의 매매 시그널을 반환합니다."""
        if self.signals is None:
            return "HOLD"
        try:
            return self.signals.iloc[current_index]['signal']
        except IndexError:
            logging.warning(f"인덱스 {current_index}가 범위를 벗어났습니다.")
            return "HOLD"

    def update_data(self):
        """5분마다 데이터를 갱신합니다."""
        try:
            new_data = pyupbit.get_ohlcv(self.ticker, interval="minute5", count=1)
            if new_data is not None and not new_data.empty:
                self.data = pd.concat([self.data[:-1], new_data])
                self.calculate_indicators()
                logging.info("데이터 갱신 완료")
        except Exception as e:
            logging.error(f"데이터 갱신 중 오류 발생: {e}") 