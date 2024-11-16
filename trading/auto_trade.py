import sys
import os
import logging
import time
import pyupbit
import traceback
from datetime import datetime, timedelta
from collections import deque

from config import TICKERS, STOP_LOSS, ACC_KEY, SEC_KEY
from services.api_service import verify_api_keys
from services.notification_service import NotificationService
from services.performance_service import PerformanceMonitor, PerformanceAnalyzer
from utils.message_queue import MessageQueue
from utils.decorators import retry_on_failure
from data_analyzer.analyzer import DataAnalyzer

class AutoTrade:
    def __init__(self, start_cash):
        self.setup_initial_state(start_cash)
        self.setup_services()
        
    def setup_initial_state(self, start_cash):
        """초기 상태 설정"""
        logging.info("AutoTrade 초기화 시작")
        try:
            self.start_cash = start_cash
            self.tickers = TICKERS
            self.stop_loss = STOP_LOSS
            self.min_trading_amount = 5000  # 최소 거래금액
            self.max_per_coin = start_cash / (len(TICKERS) * 2)  # 코인당 최대 투자금액
            
            # 각 티커별 캐시 및 상태 초기화
            self.price_cache = {ticker: deque(maxlen=100) for ticker in self.tickers}
            self.analyzers = {}
            self.buy_yn = {ticker: False for ticker in self.tickers}
            self.buy_price = {ticker: None for ticker in self.tickers}
            self.total_profit = {ticker: 0.0 for ticker in self.tickers}
            self.trade_history = {ticker: [] for ticker in self.tickers}
            
            # 각 티커별 분석기 초기화
            for ticker in self.tickers:
                logging.info(f"{ticker} DataAnalyzer 생성 시작")
                self.analyzers[ticker] = DataAnalyzer(ticker)
                self.analyzers[ticker].fetch_data()
                self.analyzers[ticker].calculate_indicators()
            
            # 시간 관련 변수 초기화
            self.last_order_time = 0
            self.last_update_time = time.time()
            self.last_status_time = time.time()
            
            # 잔고 캐시 초기화
            self.balance_cache = {"KRW": 0.0}
            self.balance_cache_time = 0
            self.balance_cache_duration = 5
            
            # Upbit 연결
            logging.info("Upbit 연결 시작")
            self.upbit = pyupbit.Upbit(ACC_KEY, SEC_KEY)
            
            logging.info("초기 상태 설정 완료")
            
        except Exception as e:
            logging.error(f"초기화 중 오류 발생: {e}")
            raise

    def setup_services(self):
        """서비스 초기화"""
        try:
            self.message_queue = MessageQueue()
            self.notification = NotificationService(self.message_queue)
            self.performance = PerformanceMonitor()
            self.analyzer = PerformanceAnalyzer(self.tickers)
            logging.info("서비스 설정 완료")
        except Exception as e:
            logging.error(f"서비스 설정 중 오류 발생: {e}")
            raise

    @retry_on_failure(max_attempts=3)
    def buy_coin(self, ticker, current_price):
        """코인 매수"""
        try:
            balance = self.get_balance("KRW")
            if balance < self.min_trading_amount:
                self.notification.send_message(f"잔고 부족: {balance} KRW")
                return
                
            amount_to_buy = min(balance, self.max_per_coin) / current_price
            
            if amount_to_buy * current_price < self.min_trading_amount:
                logging.info(f"{ticker} 최소 주문금액 미달")
                return
                
            order = self.upbit.buy_market_order(ticker, amount_to_buy * current_price)
            if order and order['status'] == 'done':
                self.buy_yn[ticker] = True
                self.buy_price[ticker] = current_price
                trade_info = {
                    'type': 'buy',
                    'price': current_price,
                    'amount': amount_to_buy,
                    'timestamp': datetime.now()
                }
                self.analyzer.add_trade(ticker, trade_info)
                self.notification.send_message(
                    f"{ticker} 매수 성공\n"
                    f"가격: {current_price:,} KRW\n"
                    f"수량: {amount_to_buy:.8f}",
                    is_important=True
                )
                
        except Exception as e:
            logging.error(f"매수 중 오류 발생: {str(e)}")
            raise

    @retry_on_failure(max_attempts=3)
    def sell_coin(self, ticker, current_price, stop_loss_triggered=False):
        """코인 매도"""
        try:
            balance = self.get_balance(ticker)
            if balance <= 0:
                return
                
            order = self.upbit.sell_market_order(ticker, balance)
            if order and order['status'] == 'done':
                profit = (current_price - self.buy_price[ticker]) / self.buy_price[ticker] * 100
                self.total_profit[ticker] += profit
                
                trade_info = {
                    'type': 'sell',
                    'price': current_price,
                    'amount': balance,
                    'profit': profit,
                    'timestamp': datetime.now()
                }
                self.analyzer.add_trade(ticker, trade_info)
                
                message = (
                    f"{ticker} {'스탑로스' if stop_loss_triggered else '매도'} 성공\n"
                    f"매수가: {self.buy_price[ticker]:,} KRW\n"
                    f"매도가: {current_price:,} KRW\n"
                    f"수익률: {profit:.2f}%"
                )
                self.notification.send_message(message, is_important=True)
                
                self.buy_yn[ticker] = False
                self.buy_price[ticker] = None
                
        except Exception as e:
            logging.error(f"매도 중 오류 발생: {str(e)}")
            raise

    def get_balance(self, ticker="KRW"):
        """캐시된 잔고 조회"""
        try:
            current_time = time.time()
            if current_time - self.balance_cache_time > self.balance_cache_duration:
                self.balance_cache = {}
                for t in self.tickers + ["KRW"]:
                    try:
                        balance = float(self.upbit.get_balance(t) or 0)
                        self.balance_cache[t] = balance
                    except Exception as e:
                        logging.error(f"{t} 잔고 조회 실패: {str(e)}")
                        self.balance_cache[t] = 0.0
                self.balance_cache_time = current_time
            return self.balance_cache.get(ticker, 0.0)
        except Exception as e:
            logging.error(f"잔고 조회 중 오류 발생: {str(e)}")
            return 0.0

    def log_current_status(self):
        """현재 상태 로깅"""
        try:
            status_messages = []
            for ticker in self.tickers:
                if not self.price_cache[ticker]:  # 가격 데이터가 없으면 스킵
                    continue
                
                current_price = self.price_cache[ticker][-1]
                krw_balance = self.get_balance("KRW")
                coin_balance = self.get_balance(ticker)
                coin_value = coin_balance * current_price
                
                # 중요한 변화가 있을 때만 상태 메시지 추가
                if (self.buy_yn[ticker] or  # 보유 중이거나
                    coin_balance > 0 or      # 코인 잔고가 있거나
                    self.total_profit[ticker] != 0):  # 수익이 발생했을 때
                    
                    status_message = self.notification.format_status_message(
                        ticker, current_price, krw_balance, coin_balance, self.total_profit[ticker]
                    )
                    status_messages.append(status_message)
            
            if status_messages:  # 상태 메시지가 있을 때만 전송
                combined_message = "\n".join(status_messages)
                self.notification.send_message(combined_message, is_important=False)
                
        except Exception as e:
            logging.error(f"상태 로깅 중 오류 발생: {str(e)}")
            raise

    def start(self):
        """자동매매 시작"""
        wm = None
        while True:
            try:
                if wm is not None:
                    wm.terminate()
                wm = pyupbit.WebSocketManager("ticker", self.tickers)
                
                while True:
                    data = wm.get()
                    if data is None:
                        self.performance.websocket_disconnects += 1
                        raise Exception("WebSocket 연결 끊김")
                    
                    ticker = data['code']
                    current_price = data['trade_price']
                    self.price_cache[ticker].append(current_price)
                    
                    # 상태 체크 및 보고
                    current_time = time.time()
                    if current_time - self.last_status_time > 30:
                        self.log_current_status()
                        self.last_status_time = current_time
                    
                    # 데이터 갱신
                    if current_time - self.last_update_time > 300:
                        for t in self.tickers:
                            self.analyzers[t].update_data()
                        self.last_update_time = current_time
                        
                    # 일일 리포트 체크
                    if self.analyzer.check_daily_report_time():
                        report = self.analyzer.generate_daily_report()
                        self.notification.send_message(report, is_important=True)
                    
                    # 매매 신호 확인
                    action = self.analyzers[ticker].analyze(-1)
                    if action == "BUY" and not self.buy_yn[ticker]:
                        self.buy_coin(ticker, current_price)
                    elif action == "SELL" and self.buy_yn[ticker]:
                        self.sell_coin(ticker, current_price)
                    elif self.buy_yn[ticker] and self.buy_price[ticker]:
                        if current_price < self.buy_price[ticker] * (1 - self.stop_loss):
                            self.sell_coin(ticker, current_price, stop_loss_triggered=True)
                    
            except Exception as e:
                logging.error(f"메인 루프 에러 발생: {str(e)}")
                if wm is not None:
                    try:
                        wm.terminate()
                    except:
                        pass
                time.sleep(60)
