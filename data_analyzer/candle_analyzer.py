import pyupbit
import numpy as np
import logging
import pandas as pd
from datetime import datetime, timedelta
import time
from tqdm import tqdm
import traceback

class CandleAnalyzer:
    def __init__(self, ticker):
        self.ticker = ticker
        self.df = pd.DataFrame()
        self.is_prepared = False
        
        self.params = {
            'RED_CANDLES_REQUIRED': 3,
            'TAKE_PROFIT': 0.03,
            'STOP_LOSS': 0.05,
            'MAX_CUMULATIVE_DROP': 0.03,
            'MAX_SINGLE_DROP': 0.015
        }

    def set_params(self, params):
        """파라미터 일괄 설정 - 최적화에 유용"""
        self.params.update(params)

    def prepare_data(self, df=None):
        """데이터 전처리"""
        try:
            if df is not None:
                self.df = df.copy()
            
            if self.df.empty:
                return False
            
            # 기본 지표 계산
            self.df['candle_color'] = np.where(self.df['close'] < self.df['open'], 'red', 'green')
            self.df['candle_change'] = (self.df['close'] - self.df['open']) / self.df['open']
            
            # 연속 음봉 계산
            is_red = (self.df['candle_color'] == 'red').astype(int)
            consecutive_reds = []
            cumulative_drops = []
            red_count = 0
            cum_drop = 0
            
            for i, (is_red_candle, change) in enumerate(zip(is_red, self.df['candle_change'])):
                if is_red_candle:
                    red_count += 1
                    cum_drop += abs(change)
                else:
                    red_count = 0
                    cum_drop = 0
                consecutive_reds.append(red_count)
                cumulative_drops.append(cum_drop)
            
            self.df['consecutive_reds'] = consecutive_reds
            self.df['cumulative_drop'] = cumulative_drops
            
            self.is_prepared = True
            return True
            
        except Exception as e:
            logging.error(f"데이터 전처리 중 오류 발생: {str(e)}")
            self.is_prepared = False
            return False

    def analyze(self, current_price, buy_price=None):
        """매매 신호 분석"""
        try:
            if self.df.empty or not self.is_prepared:
                return self._create_hold_signal("데이터 준비 안됨")
            
            # 필요한 컬럼이 있는지 확인
            required_columns = ['consecutive_reds', 'cumulative_drop', 'candle_change']
            if not all(col in self.df.columns for col in required_columns):
                self.prepare_data()
                if not self.is_prepared:
                    return self._create_hold_signal("데이터 준비 실패")
            
            # 매수 중인 경우
            if buy_price:
                profit_rate = (current_price - buy_price) / buy_price
                
                if profit_rate >= self.params['TAKE_PROFIT']:
                    return {
                        'action': 'SELL',
                        'reason': f'익절 ({profit_rate:.1%})',
                        'target_price': None
                    }
                
                if profit_rate <= -self.params['STOP_LOSS']:
                    return {
                        'action': 'SELL',
                        'reason': f'손절 ({profit_rate:.1%})',
                        'target_price': None
                    }
            
            # 매수 신호 확인
            last_row = self.df.iloc[-1]
            consecutive_reds = last_row['consecutive_reds']
            cumulative_drop = last_row['cumulative_drop']
            last_candle_drop = abs((last_row['close'] - last_row['open']) / last_row['open'])
            
            # 매수 조건 체크 - MAX_CONSECUTIVE_REDS 제거
            buy_conditions = [
                consecutive_reds >= self.params['RED_CANDLES_REQUIRED'],
                cumulative_drop <= self.params['MAX_CUMULATIVE_DROP'],
                last_candle_drop <= self.params['MAX_SINGLE_DROP']
            ]
            
            if all(buy_conditions):
                return {
                    'action': 'BUY',
                    'reason': f'연속 {consecutive_reds}개 음봉 (누적하락: {cumulative_drop:.1%})',
                    'target_price': current_price * (1 + self.params['TAKE_PROFIT'])
                }
            
            return self._create_hold_signal()
            
        except Exception as e:
            logging.error(f"분석 중 오류 발생: {str(e)}")
            return self._create_hold_signal(f"에러: {str(e)}")

    def _create_hold_signal(self, reason=None):
        return {
            'action': 'HOLD',
            'reason': reason,
            'target_price': None
        }

    def update_data(self):
        """데이터 업데이트"""
        self.fetch_data() 

    def fetch_historical_data(self, months=1):
        """과거 데이터 조회"""
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=30*months)
            
            df_list = []
            with tqdm(total=30, desc="데이터 수집") as pbar:
                while True:
                    try:
                        df = pyupbit.get_ohlcv(self.ticker, interval="minute60", 
                                             to=end_date.strftime("%Y%m%d %H:%M:%S"),
                                             count=200)
                        if df is None or df.empty:
                            break
                        
                        df_list.append(df)
                        end_date = df.index[0]
                        
                        if end_date < start_date:
                            break
                        
                        if len(df_list) % 24 == 0:  # 하루치 데이터를 모았을 때
                            pbar.update(1)  # 진행률 업데이트
                        
                    except Exception as e:
                        logging.error(f"데이터 조회 중 오류: {str(e)}")
                        time.sleep(1)
                        continue
            
            if not df_list:
                raise ValueError("데이터 조회 실패")
            
            df = pd.concat(df_list)
            df = df[~df.index.duplicated(keep='first')]
            df = df[df.index >= start_date]
            df.sort_index(inplace=True)
            
            return df
            
        except Exception as e:
            logging.error(f"히스토리 데이터 수집 중 오류 발생: {str(e)}")
            raise 

    def fetch_historical_data_by_date(self, start_date, end_date):
        """특정 기간의 데이터 조회"""
        try:
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)
            
            df_list = []
            current_end = end_dt
            
            print(f"\n{self.ticker} 데이터 수집 중...")
            with tqdm(total=30) as pbar:
                while True:
                    try:
                        df = pyupbit.get_ohlcv(
                            self.ticker, 
                            interval="minute60",
                            to=current_end.strftime("%Y%m%d %H:%M:%S"),
                            count=200
                        )
                        
                        if df is None or df.empty:
                            break
                            
                        df_list.append(df)
                        current_end = df.index[0]
                            
                        if current_end < start_dt:
                            break
                            
                        if len(df_list) % 24 == 0:  # 하루치 데이터를 모았을 때
                            pbar.update(1)
                            
                    except Exception as e:
                        logging.error(f"데이터 조회 중 오류: {str(e)}")
                        time.sleep(1)
                        continue
            
            if not df_list:
                raise ValueError("데이터 조회 실패")
            
            df = pd.concat(df_list)
            df = df[~df.index.duplicated(keep='first')]
            df = df[(df.index >= start_dt) & (df.index <= end_dt)]
            df.sort_index(inplace=True)
            
            print(f"수집된 데이터: {df.index[0]} ~ {df.index[-1]} ({len(df)}개 데이터)")
            return df
            
        except Exception as e:
            logging.error(f"히스토리 데이터 수집 중 오류 발생: {str(e)}")
            return None