import pyupbit
import numpy as np
import logging
import pandas as pd
import traceback
import time
from utils.decorators import send_error_alert
from strategies.signals import LegacyCompositeStrategy, Snapshot
from services.paper_decisions import observe

class DataAnalyzer:
    def __init__(self, ticker):
        self.ticker = ticker
        logging.info("DataAnalyzer 초기화 시작")
        self.df = pd.DataFrame()
        self.last_signal = None
        self.last_signal_time = None
        self.signal_cooldown = 300  # 신호 재발생 대기시간 (5분)
        logging.info("DataAnalyzer 초기화 완료")

    @send_error_alert
    def fetch_data(self, interval="minute1", count=200):
        """데이터 조회"""
        try:
            logging.info("데이터 조회 시작")
            df = pyupbit.get_ohlcv(self.ticker, interval=interval, count=count)
            logging.info(f"데이터 조회 결과: {type(df)}")
            
            if df is None or df.empty:
                raise ValueError('Candle fetch failed; stale data must not be traded')
                
            logging.info("데이터 형변환 시작")
            df = df.astype({
                'open': 'float64',
                'high': 'float64',
                'low': 'float64',
                'close': 'float64',
                'volume': 'float64',
                'value': 'float64'
            })
            
            self.df = df
            logging.info("데이터 조회 및 형변환 완료")
            
        except Exception as e:
            logging.error(f"데이터 조회 중 오류 발생: {str(e)}")
            raise

    @send_error_alert
    def calculate_indicators(self):
        """기술적 지표 계산"""
        try:
            df = self.df.copy()
            
            # RSI 계산 (14일)
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # MACD 계산 (12,26,9)
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['macd'] = exp1 - exp2
            df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
            
            # 볼린저 밴드 (20일, 2표준편차)
            df['bb_middle'] = df['close'].rolling(window=20).mean()
            bb_std = df['close'].rolling(window=20).std()
            df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
            df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
            
            # NaN 값을 앞뒤 값으로 채우기
            df = df.bfill()  # 뒤의 값으로 채우기
            df = df.ffill()  # 앞의 값으로 채우기
            
            # 모든 지표가 계산되었는지 확인
            required_columns = ['rsi', 'macd', 'macd_signal', 'bb_upper', 'bb_lower', 'bb_middle']
            for col in required_columns:
                if df[col].isna().any():
                    logging.warning(f"{self.ticker}의 {col} 지표에 NaN 값이 있습니다")
            
            self.df = df
            
            # 현재 지표값 로깅
            last_idx = -1
            logging.info(
                f"{self.ticker} 지표 계산 완료\n"
                f"- RSI: {df['rsi'].iloc[last_idx]:.1f}\n"
                f"- MACD: {df['macd'].iloc[last_idx]:.1f}\n"
                f"- Signal: {df['macd_signal'].iloc[last_idx]:.1f}\n"
                f"- BB 위치: {((df['close'].iloc[last_idx] - df['bb_middle'].iloc[last_idx]) / df['bb_middle'].iloc[last_idx] * 100):.1f}%"
            )
            
        except Exception as e:
            logging.error(f"지표 계산 중 오류 발생: {str(e)}")
            raise

    @send_error_alert
    def update_data(self):
        """데이터 업데이트"""
        try:
            self.fetch_data()
            self.calculate_indicators()
        except Exception as e:
            logging.error(f"데이터 업데이트 중 오류 발생: {str(e)}")
            raise 

    @send_error_alert
    def analyze(self, index=-1):
        """매매 신호 분석"""
        try:
            if self.df.empty or len(self.df) < 20:
                raise ValueError('Insufficient candles for analysis')
            columns = ['close', 'volume', 'rsi', 'macd', 'macd_signal', 'bb_upper', 'bb_lower', 'bb_middle']
            if not np.isfinite(self.df[columns].iloc[-2:].to_numpy(dtype=float)).all():
                raise ValueError('Non-finite market data or indicators')
            # 거래량 확인
            volume = self.df['volume'].iloc[index]
            avg_volume = self.df['volume'].rolling(window=20).mean().iloc[index]
            
            # 거래량이 평균 거래량의 50% 미만이면 거래 제한
            if volume < avg_volume * 0.5:
                if getattr(self,'decision_observer',None) is not None:
                    observe(self,'gate_conditions',self.ticker,VOLUME_GATE=True,
                        COOLDOWN=bool(self.last_signal and self.last_signal_time and time.time()-self.last_signal_time<self.signal_cooldown))
                observe(self,'gate',self.ticker,reason='VOLUME_GATE')
                return {
                    'action': 'HOLD',
                    'reason': '거래량 부족',
                    'target_price': None,
                    'strategy_status': self.get_strategy_status(index)
                }            
            current_time = time.time()
            observe(self,'gate_conditions',self.ticker,VOLUME_GATE=False,
                COOLDOWN=bool(self.last_signal and self.last_signal_time and current_time-self.last_signal_time<self.signal_cooldown))
            
            # 이전 신호와 동일한 신호가 쿨다운 시간 내에 발생하면 HOLD 반환
            if (self.last_signal and 
                self.last_signal_time and 
                current_time - self.last_signal_time < self.signal_cooldown):
                observe(self,'gate',self.ticker,reason='COOLDOWN')
                return {
                    'action': 'HOLD',
                    'reason': None,
                    'target_price': None,
                    'strategy_status': self.get_strategy_status(index)
                }

            # 데이터가 없으면 데이터 가져오기 시도
            if self.df.empty:
                self.fetch_data()
                self.calculate_indicators()
            
            if self.df.empty:
                return {
                    'action': 'HOLD',
                    'reason': None,
                    'target_price': None,
                    'strategy_status': {
                        'RSI': 'N/A',
                        'MACD': 'N/A',
                        'BB': 'N/A'
                    }
                }

            current_price = self.df['close'].iloc[index]
            
            # 전략별 상태 확인
            strategy_status = {}
            
            # RSI 상태
            rsi = self.df['rsi'].iloc[index]
            rsi_status = '과매수' if rsi > 70 else '과매도' if rsi < 30 else '중립'
            strategy_status['RSI'] = f"{rsi:.1f} ({rsi_status})"
            
            # MACD 상태
            macd = self.df['macd'].iloc[index]
            macd_signal = self.df['macd_signal'].iloc[index]
            macd_diff = macd - macd_signal
            macd_status = '골든크로스' if macd_diff > 0 else '데드크로스' if macd_diff < 0 else '중립'
            strategy_status['MACD'] = f"{macd_diff:.1f} ({macd_status})"
            
            # BB 상태
            bb_upper = self.df['bb_upper'].iloc[index]
            bb_lower = self.df['bb_lower'].iloc[index]
            bb_middle = self.df['bb_middle'].iloc[index]
            bb_position = ((current_price - bb_middle) / bb_middle) * 100
            bb_status = "상단돌파" if current_price > bb_upper else "하단돌파" if current_price < bb_lower else "밴드내"
            strategy_status['BB'] = f"{bb_position:.1f}% ({bb_status})"
            
            # Pure strategy: the analyzer owns data/cooldown, never order execution.
            # Keep the legacy latest-delta semantics for compatibility with the bot.
            observe(self,'strategy_attempts',self.ticker)
            decision = LegacyCompositeStrategy().decide(Snapshot(
                current_price, rsi, self.df['rsi'].diff().iloc[-1],
                macd, macd_signal, self.df['macd'].diff().iloc[-1],
                bb_lower, bb_middle, bb_upper,
            ))
            action = decision.action
            observe(self,'strategy_result',self.ticker,action=action)
            reasons = [decision.reason] if decision.reason else []
            target_price = decision.target_price
            if action != "HOLD":
                logging.info(f"[{self.ticker}] {action} 신호: {decision.reason}")

            # 매수/매도 신호가 발생하면 시간 기록
            if action in ['BUY', 'SELL']:
                self.last_signal = action
                self.last_signal_time = current_time
            
            return {
                'action': action,
                'reason': ' & '.join(reasons) if reasons else None,
                'target_price': target_price,
                'strategy_status': strategy_status
            }
            
        except Exception as e:
            logging.error(f"분석 중 오류 발생: {str(e)}")
            raise

    def get_strategy_status(self, index=-1):
        """현재 전략 상태 반환"""
        try:
            if self.df.empty:
                return {
                    'RSI': 'N/A',
                    'MACD': 'N/A',
                    'BB': 'N/A'
                }

            current_price = self.df['close'].iloc[index]
            
            # RSI 상태
            rsi = self.df['rsi'].iloc[index]
            rsi_status = '과매수' if rsi > 70 else '과매도' if rsi < 30 else '중립'
            
            # MACD 상태
            macd = self.df['macd'].iloc[index]
            macd_signal = self.df['macd_signal'].iloc[index]
            macd_diff = macd - macd_signal
            macd_status = '골든크로스' if macd_diff > 0 else '데드크로스' if macd_diff < 0 else '중립'
            
            # BB 상태
            bb_upper = self.df['bb_upper'].iloc[index]
            bb_lower = self.df['bb_lower'].iloc[index]
            bb_middle = self.df['bb_middle'].iloc[index]
            bb_position = ((current_price - bb_middle) / bb_middle) * 100
            bb_status = "상단돌파" if current_price > bb_upper else "하단돌파" if current_price < bb_lower else "밴드내"
            
            return {
                'RSI': f"{rsi:.1f} ({rsi_status})",
                'MACD': f"{macd_diff:.1f} ({macd_status})",
                'BB': f"{bb_position:.1f}% ({bb_status})"
            }
            
        except Exception as e:
            logging.error(f"전략 상태 조회 중 오류 발생: {str(e)}")
            return {
                'RSI': 'N/A',
                'MACD': 'N/A',
                'BB': 'N/A'
            }
