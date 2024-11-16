import sys
import os
import logging
import time
import pyupbit
import traceback
from datetime import datetime, timedelta
from collections import deque
from collections import defaultdict

from config import TICKERS, STOP_LOSS, UPBIT_ACCESS_KEY, UPBIT_ACCESS_KEY, CASH_USAGE_RATIO, MAX_COINS_AT_ONCE, REAL_TRADING, UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY, START_CASH, MIN_TRADING_AMOUNT
from services.api_service import verify_api_keys
from services.notification_service import NotificationService
from services.performance_service import PerformanceMonitor, PerformanceAnalyzer
from utils.message_queue import MessageQueue
from utils.decorators import retry_on_failure
from data_analyzer.analyzer import DataAnalyzer

class AutoTrade:
    def __init__(self, start_cash=1_000_000):
        """
        자동매매 클래스 초기화
        :param start_cash: 시작 자금 (기본값: 100만원)
        """
        self.start_cash = start_cash  # 시작 자금 저장
        self.current_cash = start_cash  # 현재 보유 현금
        
        # 거래 모드 설정
        self.real_trading = REAL_TRADING
        if self.real_trading:
            self.upbit = pyupbit.Upbit(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY)
            self.current_cash = float(self.upbit.get_balance("KRW"))
            logging.info(f"실제 거래 모드 시작 (보유 현금: {self.current_cash:,}원)")
        else:
            self.upbit = None
            logging.info(f"테스트 모드 시작 (시작 자금: {self.start_cash:,}원)")
        
        # 기본 설정
        self.tickers = TICKERS
        self.min_trading_amount = MIN_TRADING_AMOUNT
        self.max_per_coin = start_cash * CASH_USAGE_RATIO  # 코인당 최대 투자금액
        self.stop_loss = STOP_LOSS
        
        # 상태 변수 초기화
        self.buy_yn = {ticker: False for ticker in self.tickers}
        self.buy_price = {ticker: 0 for ticker in self.tickers}
        self.analyzers = {}
        self.price_cache = defaultdict(list)
        self.last_status_time = time.time()
        
        # 데이터 분석기 초기화
        for ticker in self.tickers:
            self.analyzers[ticker] = DataAnalyzer(ticker)
            
        # 알림 서비스 초기화
        try:
            from services.notification_service import NotificationService
            self.notification = NotificationService()
        except Exception as e:
            logging.warning(f"알림 서비스 초기화 실패: {str(e)}")
            self.notification = None
            
        logging.info(
            f"거래 설정:\n"
            f"- 코인당 최대 투자: {self.max_per_coin:,}원\n"
            f"- 최소 거래금액: {self.min_trading_amount:,}원\n"
            f"- 손절라인: {self.stop_loss*100}%\n"
            f"- 거래 대상: {', '.join(self.tickers)}"
        )

    def get_balance(self, currency="KRW"):
        """잔액 조회"""
        if self.real_trading:
            return float(self.upbit.get_balance(currency))
        return self.current_cash

    def buy_coin(self, ticker, current_price, reason=None, target_price=None, strategy_status=None):
        """코인 매수"""
        try:
            if self.buy_yn[ticker]:
                return False
                
            balance = self.get_balance("KRW")
            logging.info(f"현재 보유 현금: {balance:,}원")
            
            if balance < MIN_TRADING_AMOUNT:
                logging.warning(f"잔액 부족 - 현재 잔액: {balance:,}원")
                return False
                
            buy_amount = min(self.max_per_coin, balance)
            
            logging.info(
                f"{'[실제]' if self.real_trading else '[테스트]'} {ticker} 매수 시도:\n"
                f"재가: {current_price:,}원\n"
                f"매수금액: {buy_amount:,}원\n"
                f"매수이유: {reason}"
            )
            
            if self.real_trading:
                response = self.upbit.buy_market_order(ticker, buy_amount)
                if not response:
                    logging.error(f"{ticker} 매수 주문 실패")
                    return False
            else:
                self.current_cash -= buy_amount
            
            self.buy_yn[ticker] = True
            self.buy_price[ticker] = current_price
            
            message = f"{'[실제]' if self.real_trading else '[테스트]'} {ticker} 매수 성공\n" \
                     f"매수가: {current_price:,}원\n" \
                     f"매수금액: {buy_amount:,}원\n" \
                     f"잔액: {self.get_balance('KRW'):,}원"
            logging.info(message)
            
            return True
            
        except Exception as e:
            logging.error(f"매수 중 오류 발생: {str(e)}")
            return False

    def sell_coin(self, ticker, current_price, stop_loss_triggered=False):
        """코인 매도"""
        try:
            if not self.buy_yn[ticker]:
                return False
                
            buy_price = self.buy_price[ticker]
            profit_rate = (current_price - buy_price) / buy_price * 100
            
            if self.real_trading:
                coin_balance = self.upbit.get_balance(ticker)
                response = self.upbit.sell_market_order(ticker, coin_balance)
                if not response:
                    logging.error(f"{ticker} 매도 주문 실패")
                    return False
            else:
                sell_amount = self.max_per_coin * (current_price / buy_price)
                self.current_cash += sell_amount
            
            self.buy_yn[ticker] = False
            self.buy_price[ticker] = 0
            
            message = f"{'[실제]' if self.real_trading else '[테스트]'} "
            message += f"{'[손절]' if stop_loss_triggered else ''} {ticker} 매도 완료\n" \
                      f"매도가: {current_price:,}원\n" \
                      f"수익률: {profit_rate:.2f}%\n" \
                      f"잔액: {self.get_balance('KRW'):,}원"
            logging.info(message)
            
            return True
            
        except Exception as e:
            logging.error(f"매도 중 오류 발생: {str(e)}")
            return False

    def log_current_status(self):
        """현재 상태 로깅"""
        try:
            status_messages = []
            for ticker in self.tickers:
                if not self.price_cache[ticker]:
                    continue
                
                current_price = self.price_cache[ticker][-1]
                
                # 현재 전략 상태 확인
                analysis_result = self.analyzers[ticker].analyze(-1)
                strategy_status = analysis_result['strategy_status']
                
                status_message = self.notification.format_status_message(
                    ticker,
                    current_price,
                    self.get_balance("KRW"),
                    self.get_balance(ticker),
                    self.total_profit[ticker],
                    strategy_status
                )
                status_messages.append(status_message)
            
            if status_messages:
                combined_message = "\n\n".join(status_messages)
                self.notification.send_status_update(combined_message)
                
        except Exception as e:
            error_message = f"상태 로깅 중 오류 발생: {str(e)}"
            logging.error(error_message)
            self.notification.send_error_alert(error_message)
            raise

    def log_status(self):
        """현재 거래 상태 로깅"""
        try:
            # 보유 현금
            current_cash = self.current_cash if not self.real_trading else self.get_balance("KRW")
            
            # 보유 코인 상태
            holdings = []
            total_value = current_cash
            
            for ticker in self.tickers:
                if self.buy_yn[ticker]:
                    buy_price = self.buy_price[ticker]
                    current_price = float(self.price_cache[ticker][-1])
                    quantity = self.max_per_coin / buy_price
                    current_value = quantity * current_price
                    profit_rate = ((current_price - buy_price) / buy_price) * 100
                    
                    holdings.append(
                        f"- {ticker}: "
                        f"수량={quantity:.4f}, "
                        f"매수가={buy_price:,}원, "
                        f"현재가={current_price:,}원, "
                        f"수익률={profit_rate:.2f}%"
                    )
                    
                    total_value += current_value
            
            # 전체 수익률
            total_profit_rate = ((total_value - self.start_cash) / self.start_cash) * 100
            
            # 상태 메시지 생성
            status_msg = (
                f"\n===== 거래 상태 =====\n"
                f"시작 자금: {self.start_cash:,}원\n"
                f"현재 현금: {current_cash:,}원\n"
                f"총 평가액: {total_value:,}원\n"
                f"총 수익률: {total_profit_rate:.2f}%\n"
            )
            
            if holdings:
                status_msg += "\n보유 코인:\n" + "\n".join(holdings)
            else:
                status_msg += "\n보유 코인: 없음"
                
            logging.info(status_msg)
            
            # Slack 알림 전송 (설정된 경우)
            if hasattr(self, 'notification') and self.notification:
                self.notification.send_status_update(status_msg)
                
        except Exception as e:
            logging.error(f"상태 로깅 중 오류 발생: {str(e)}")

    def start(self):
        """자동매매 시작"""
        wm = None
        STATUS_INTERVAL = 300  # 상태 업데이트 주기 5분
        
        while True:
            try:
                if wm is not None:
                    wm.terminate()
                wm = pyupbit.WebSocketManager("ticker", self.tickers)
                
                while True:
                    data = wm.get()
                    if data is None:
                        raise Exception("WebSocket 연결 끊김")
                    
                    # WebSocket 데이터 처리
                    ticker = data.get('code')
                    current_price = float(data.get('trade_price', 0))
                    
                    if not ticker or current_price <= 0:
                        continue
                        
                    # 현재가 캐시 업데이트
                    self.price_cache[ticker].append(current_price)
                    
                    # 상태 체크 (5분 간격)
                    current_time = time.time()
                    if current_time - self.last_status_time > STATUS_INTERVAL:
                        self.log_status()
                        self.last_status_time = current_time
                    
                    # 매매 신호 확인
                    if ticker in self.analyzers:
                        analysis = self.analyzers[ticker].analyze()
                        if analysis['action'] == "BUY" and not self.buy_yn[ticker]:
                            self.buy_coin(ticker, current_price, 
                                        reason=analysis['reason'],
                                        target_price=analysis['target_price'])
                        elif analysis['action'] == "SELL" and self.buy_yn[ticker]:
                            self.sell_coin(ticker, current_price)
                    
            except Exception as e:
                logging.error(f"메인 루프 에러 발생: {str(e)}")
                if wm is not None:
                    wm.terminate()
                time.sleep(1)
