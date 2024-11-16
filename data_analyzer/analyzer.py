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
        self.ticker = ticker
        logging.info("DataAnalyzer 초기화 시작")
        self.df = pd.DataFrame()
        logging.info("DataAnalyzer 초기화 완료")

    def fetch_data(self, interval="minute1", count=200):
        """데이터 조회"""
        try:
            logging.info("데이터 조회 시작")
            df = pyupbit.get_ohlcv(self.ticker, interval=interval, count=count)
            logging.info(f"데이터 조회 결과: {type(df)}")
            
            if df is None or df.empty:
                logging.error("데이터 조회 실패")
                return
                
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

    def update_data(self):
        """데이터 업데이트"""
        try:
            self.fetch_data()
            self.calculate_indicators()
        except Exception as e:
            logging.error(f"데이터 업데이트 중 오류 발생: {str(e)}")
            raise 

    def analyze(self, index=-1):
        """매매 신호 분석"""
        try:
            if self.df.empty:
                return {
                    'action': 'HOLD',
                    'reason': None,
                    'target_price': None,
                    'strategy_status': {}
                }
            
            current_price = self.df['close'].iloc[index]
            
            # 전략별 상태 확인
            strategy_status = {}
            
            # RSI 전략
            rsi = self.df['rsi'].iloc[index]
            rsi_status = '과매수' if rsi > 70 else '과매도' if rsi < 30 else '중립'
            strategy_status['RSI'] = f"{rsi:.1f} ({rsi_status})"
            
            # MACD 전략
            macd = self.df['macd'].iloc[index]
            macd_signal = self.df['macd_signal'].iloc[index]
            macd_diff = macd - macd_signal
            macd_status = '골든크로스' if macd_diff > 0 and macd < 0 else '데드크로스' if macd_diff < 0 and macd > 0 else '중립'
            strategy_status['MACD'] = f"{macd_diff:.1f} ({macd_status})"
            
            # 볼린저 밴드 전략
            bb_upper = self.df['bb_upper'].iloc[index]
            bb_lower = self.df['bb_lower'].iloc[index]
            bb_middle = self.df['bb_middle'].iloc[index]
            bb_position = ((current_price - bb_middle) / bb_middle) * 100
            bb_status = "상단돌파" if current_price > bb_upper else "하단돌파" if current_price < bb_lower else "밴드내"
            strategy_status['BB'] = f"{bb_position:.1f}% ({bb_status})"
            
            # 매매 신호 및 이유 결정
            action = "HOLD"
            reasons = []
            target_price = None
            
            # RSI 기반 매매 신호
            if rsi < 30:
                action = "BUY"
                reasons.append(f"RSI 과매도({rsi:.1f})")
                target_price = current_price * 1.05  # 5% 목표수익
            elif rsi > 70:
                action = "SELL"
                reasons.append(f"RSI 과매수({rsi:.1f})")
            
            # MACD 기반 매매 신호
            if macd > macd_signal and macd < 0:
                action = "BUY"
                reasons.append(f"MACD 골든크로스({macd_diff:.1f})")
                target_price = current_price * 1.03  # 3% 목표수익
            elif macd < macd_signal and macd > 0:
                action = "SELL"
                reasons.append(f"MACD 데드크로스({macd_diff:.1f})")
            
            # 볼린저 밴드 기반 매매 신호
            if current_price < bb_lower:
                action = "BUY"
                reasons.append(f"BB 하단 지지({bb_position:.1f}%)")
                target_price = bb_middle  # 중간선까지 목표
            elif current_price > bb_upper:
                action = "SELL"
                reasons.append(f"BB 상단 돌파({bb_position:.1f}%)")
            
            # 매매 신호가 있을 때만 로깅
            if action != "HOLD":
                target_price_str = f"{target_price:,}" if target_price else "없음"
                logging.info(
                    f"[{self.ticker}] {action} 신호 발생\n"
                    f"이유: {' & '.join(reasons)}\n"
                    f"현재가: {current_price:,} → 목표가: {target_price_str}"
                )
            
            return {
                'action': action,
                'reason': ' & '.join(reasons) if reasons else None,
                'target_price': target_price,
                'strategy_status': strategy_status
            }
            
        except Exception as e:
            logging.error(f"분석 중 오류 발생: {str(e)}")
            return {
                'action': 'HOLD',
                'reason': None,
                'target_price': None,
                'strategy_status': {}
            }