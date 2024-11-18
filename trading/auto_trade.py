import sys
import os
import logging
import time
import pyupbit
import traceback
from datetime import datetime, timedelta
from collections import deque
from collections import defaultdict

from config import (
    TICKERS, STOP_LOSS, UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY,
    CASH_USAGE_RATIO, MAX_COINS_AT_ONCE, REAL_TRADING,
    START_CASH, MIN_TRADING_AMOUNT,
    REPORT_CHECK_INTERVAL, DATA_UPDATE_INTERVAL, STATUS_INTERVAL
)
from services.api_service import verify_api_keys
from services.notification_service import NotificationService
from services.performance_service import PerformanceMonitor, PerformanceAnalyzer
from utils.message_queue import MessageQueue
from utils.decorators import retry_on_failure, send_error_alert
from data_analyzer.analyzer import DataAnalyzer  # 올바른 경로로 수정

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
        self.profit_taking_ratio = 0.1  # 이익 실현 비율 (10%)
        
        # 상태 변수 초기화
        self.buy_yn = {ticker: False for ticker in self.tickers}
        self.buy_price = {ticker: 0 for ticker in self.tickers}
        self.analyzers = {}
        self.price_cache = defaultdict(list)
        self.last_status_time = time.time()
        self.last_data_update = time.time()
        
        # 데이터 분석기 초기화
        for ticker in self.tickers:
            self.analyzers[ticker] = DataAnalyzer(ticker)  # DataAnalyzer 사용
            
        # 알림 서비스 초기화
        try:
            from services.notification_service import NotificationService
            self.notification = NotificationService()
        except Exception as e:
            logging.warning(f"알림 서비스 초기화 실패: {str(e)}")
            self.notification = None
            
        # PerformanceAnalyzer 추가
        self.performance_analyzer = PerformanceAnalyzer(self.tickers)
        self.last_report_check = time.time()
        
        # 잔고 관리 변수 추가
        self.coin_balance = {ticker: 0 for ticker in self.tickers}  # 각 코인별 보 수량
        self.coin_avg_price = {ticker: 0 for ticker in self.tickers}  # 각 코인별 평균 매수가
        self.total_profit = {ticker: 0 for ticker in self.tickers}  # 각 코인별 누적 수익
        
        logging.info(
            f"거래 설정:\n"
            f"- 코인당 최대 투자: {self.max_per_coin:,}원\n"
            f"- 최소 거래금액: {self.min_trading_amount:,}원\n"
            f"- 손절라인: {self.stop_loss*100}%\n"
            f"- 거래 대상: {', '.join(self.tickers)}"
        )
        
        self.running = False
        self.wm = None
        
        # 캔들 분석기 초기화
        self.candle_analyzers = {
            ticker: DataAnalyzer(ticker) for ticker in self.tickers
        }
        
        self.averaging_down_used = {}  # 물타기 사용 여부 추적
        for ticker in self.tickers:
            self.averaging_down_used[ticker] = False
    
    def stop(self):
        """트레이딩 중지"""
        try:
            self.running = False
            if self.wm:
                try:
                    self.wm.terminate()
                    self.wm.join(timeout=1)  # 스레드 종료 대기
                except:
                    pass
                self.wm = None
            
            # 모든 웹소켓 연결 강제 종료
            for ws in pyupbit.WebSocketManager._WebSocketManager__ws_list:
                try:
                    ws.close()
                except:
                    pass
                
            logging.info("트레이딩 중지")
        except Exception as e:
            logging.error(f"트레이딩 중지 중 오류 발생: {str(e)}")
        finally:
            # 프로그램 강제 종료
            import os
            os._exit(0)
    
    @send_error_alert
    def start(self):
        """자동매매 시작"""
        self.running = True
        
        while self.running:
            try:
                if self.wm is not None:
                    self.wm.terminate()
                self.wm = pyupbit.WebSocketManager("ticker", self.tickers)
                
                # 초기 데이터 가져오기
                for ticker in self.tickers:
                    self.analyzers[ticker].fetch_data()
                    self.analyzers[ticker].calculate_indicators()
                
                while self.running:
                    data = self.wm.get()
                    if data is None:
                        raise Exception("WebSocket 연결 끊김")
                    
                    current_time = time.time()
                    
                    # 리포트 시간 체크 (30초마다)
                    if current_time - self.last_report_check > REPORT_CHECK_INTERVAL:
                        try:
                            if self.performance_analyzer.check_daily_report_time():
                                report = self.performance_analyzer.generate_daily_report()
                                logging.info(f"일일 리포트 생성:\n{report}")
                                
                                if self.notification:
                                    self.notification.send_message('reports', f"📊 일일 거래 리포트\n{report}")
                                
                                # 7일 이상 된 데이터 정리
                                self.performance_analyzer.clear_old_data()
                                
                        except Exception as e:
                            logging.error(f"리포트 생성 중 오류: {str(e)}")
                            if self.notification:
                                self.notification.send_error_alert(f"리포트 생성 실패: {str(e)}")
                        
                        self.last_report_check = current_time
                    
                    # 주기적 데이터 업데이트
                    if current_time - self.last_data_update > DATA_UPDATE_INTERVAL:
                        for ticker in self.tickers:
                            try:
                                self.analyzers[ticker].fetch_data()
                                self.analyzers[ticker].calculate_indicators()
                            except Exception as e:
                                logging.error(f"{ticker} 데이터 업데이트 실패: {str(e)}")
                        self.last_data_update = current_time
                        logging.info("지표 데이터 업데이트 완료")
                    
                    # WebSocket 데이터 처리
                    ticker = data.get('code')
                    current_price = float(data.get('trade_price', 0))
                    
                    if not ticker or current_price <= 0:
                        continue
                        
                    # 현재가 캐시 업데이트
                    self.price_cache[ticker].append(current_price)
                    
                    # 상태 체크 (5분 간격)
                    if current_time - self.last_status_time > STATUS_INTERVAL:
                        self.log_status()
                        self.last_status_time = current_time
                    
                    # 매매 신호 확인
                    if ticker in self.analyzers:
                        analysis = self.analyzers[ticker].analyze()
                        
                        # 매수 신호 (보유하지 않은 경우만)
                        if analysis['action'] == "BUY" and not self.buy_yn[ticker]:
                            self.buy_coin(ticker, current_price, 
                                        reason=analysis['reason'],
                                        target_price=analysis['target_price'])
                        
                        # 매도 신호 (보유 중인 경우만)
                        elif analysis['action'] == "SELL" and self.buy_yn[ticker]:
                            logging.info(
                                f"[{ticker}] SELL 신호 발생\n"
                                f"이유: {analysis['reason']}\n"
                                f"현재가: {current_price:,} → 목표가: {analysis.get('target_price', '없음')}"
                            )
                            self.sell_coin(ticker, current_price)
                
            except Exception as e:
                logging.error(f"메인 루프 에러 발생: {str(e)}")
                if self.wm is not None:
                    self.wm.terminate()
                    self.wm = None
                if self.running:
                    time.sleep(1)

    def get_balance(self, currency="KRW"):
        """잔액 조회"""
        try:
            if self.real_trading:
                if currency == "KRW":
                    return float(self.upbit.get_balance(currency))
                else:
                    balance = self.upbit.get_balance(currency)
                    return float(balance) if balance is not None else 0
            else:
                if currency == "KRW":
                    return self.current_cash
                else:
                    return self.coin_balance.get(currency, 0)
        except Exception as e:
            logging.error(f"잔액 조회 중 오류 발생: {str(e)}")
            return 0

    @send_error_alert
    def buy_coin(self, ticker, current_price, reason=None, target_price=None, amount=None):
        """코인 매수"""
        try:
            # 이미 보유 중인 경우 물타기만 허용
            if self.buy_yn[ticker]:
                if not amount:  # 일반 매수인 경우
                    logging.warning(f"{ticker} 이미 보유 중")
                    return False
            else:
                # 새로운 코인 매수 시 실질적 보유 코인 수 체크
                current_holdings = self.get_significant_holdings_count()
                if current_holdings >= MAX_COINS_AT_ONCE:
                    logging.warning(f"최대 보유 코인 수({MAX_COINS_AT_ONCE}개) 도달, 매수 불가")
                    return False
            
            # 매수 금액 결정
            if amount:  # 물타기용 지정 금액
                buy_amount = amount
            else:  # 일반 매수
                balance = self.get_balance("KRW")
                buy_amount = min(self.max_per_coin, balance)
            
            if buy_amount < MIN_TRADING_AMOUNT:
                logging.warning(f"잔액 부족 - 현재 잔액: {balance:,}원")
                return False
            
            # 매수 수량 계산
            quantity = buy_amount / current_price
            
            success = False
            if self.real_trading:
                response = self.upbit.buy_market_order(ticker, buy_amount)
                if not response:
                    logging.error(f"{ticker} 매수 주문 실패")
                    return False
                # 실제 체결된 수량 확인
                actual_quantity = float(self.upbit.get_balance(ticker))
                actual_price = buy_amount / actual_quantity if actual_quantity > 0 else current_price
                success = actual_quantity > 0
            else:
                self.current_cash -= buy_amount
                actual_quantity = quantity
                actual_price = current_price
                success = True
            
            if success:
                # 보유 정보 업데이트
                self.coin_balance[ticker] = actual_quantity
                self.coin_avg_price[ticker] = actual_price
                self.buy_yn[ticker] = True
                self.buy_price[ticker] = actual_price
                
                # 매수 성공 메시지
                message = (
                    f"{'[실제]' if self.real_trading else '[테스트]'} {ticker} 매수 완료\n"
                    f"매수가: {actual_price:,}원\n"
                    f"매수금액: {buy_amount:,}원\n"
                    f"매수수량: {actual_quantity:.8f}\n"
                    f"매수이유: {reason}\n"
                    f"잔액: {self.get_balance('KRW'):,}원"
                )
                
                logging.info(message)
                if self.notification:
                    self.notification.send_trade_alert(message)
                
                # 거래 정보 기록
                trade_info = {
                    'type': 'buy',
                    'price': actual_price,
                    'amount': buy_amount,
                    'quantity': actual_quantity,
                    'reason': reason
                }
                self.performance_analyzer.add_trade(ticker, trade_info)
                
                return True
            
            return False
            
        except Exception as e:
            error_msg = f"{ticker} 매수 중 오류 발생: {str(e)}"
            logging.error(error_msg)
            if self.notification:
                self.notification.send_error_alert(error_msg)
            return False

    @send_error_alert
    def sell_coin(self, ticker, current_price, stop_loss_triggered=False):
        """코인 매도"""
        try:
            if not self.buy_yn[ticker]:
                logging.warning(f"{ticker} 미보유")
                return False
            
            quantity = self.coin_balance[ticker]
            if quantity <= 0:
                logging.warning(f"{ticker} 수량 0")
                return False
            
            success = False
            if self.real_trading:
                response = self.upbit.sell_market_order(ticker, quantity)
                if not response:
                    logging.error(f"{ticker} 매도 주문 실패")
                    return False
                sell_amount = float(response['price'])
                success = True
            else:
                sell_amount = quantity * current_price
                self.current_cash += sell_amount
                success = True
            
            if success:
                # 수익률 계산
                buy_price = self.coin_avg_price[ticker]
                profit_rate = ((current_price - buy_price) / buy_price) * 100
                profit_amount = sell_amount - (quantity * buy_price)
                
                # 매도 성공 시지
                message = (
                    f"{'[실제]' if self.real_trading else '[테스트]'} "
                    f"{'[손절]' if stop_loss_triggered else ''} {ticker} 매도 완료\n"
                    f"매도가: {current_price:,}원\n"
                    f"매도수량: {quantity:.8f}\n"
                    f"매도금액: {sell_amount:,}원\n"
                    f"수익률: {profit_rate:.2f}%\n"
                    f"수익금: {profit_amount:,}원\n"
                    f"잔액: {self.get_balance('KRW'):,}원"
                )
                
                logging.info(message)
                if self.notification:
                    self.notification.send_trade_alert(message)
                
                # 누적 수익 업데이트
                self.total_profit[ticker] += profit_amount
                
                # 보유 정보 초기화
                self.coin_balance[ticker] = 0
                self.coin_avg_price[ticker] = 0
                self.buy_yn[ticker] = False
                self.buy_price[ticker] = 0
                
                # 거래 정보 기록
                trade_info = {
                    'type': 'sell',
                    'price': current_price,
                    'amount': sell_amount,
                    'quantity': quantity,
                    'profit': profit_rate,
                    'profit_amount': profit_amount,
                    'stop_loss': stop_loss_triggered
                }
                self.performance_analyzer.add_trade(ticker, trade_info)
                
                # 매도 성공 시 물타기 사용 여부 초기화
                self.averaging_down_used[ticker] = False
                
                return True
            
            return False
            
        except Exception as e:
            logging.error(f"매도 중 오류 발생: {str(e)}")
            if self.notification:
                self.notification.send_error_alert(f"매도 중 오류 발생: {str(e)}")
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
            current_cash = self.get_balance("KRW")
            
            # 보유 코인 상태 및 지표 분석
            holdings = []
            indicators = []
            total_value = current_cash
            
            for ticker in self.tickers:
                # 현재가 확인
                if not self.price_cache[ticker]:
                    continue
                current_price = float(self.price_cache[ticker][-1])
                
                # 보유 수량 및 평가액 산
                quantity = self.get_balance(ticker)
                current_value = quantity * current_price
                
                # 지표 분석 가져오기
                analysis = self.analyzers[ticker].analyze()
                strategy_status = analysis['strategy_status']
                
                # 지표 정보 추가
                indicators.append(
                    f"▶ {ticker} 지표:\n"
                    f"  - RSI: {strategy_status.get('RSI', 'N/A')}\n"
                    f"  - MACD: {strategy_status.get('MACD', 'N/A')}\n"
                    f"  - BB: {strategy_status.get('BB', 'N/A')}\n"
                    f"  - 현재가: {current_price:,}원"
                )
                
                # 보유 중인 코인 정보
                if quantity > 0:
                    avg_price = self.coin_avg_price[ticker]
                    profit_rate = ((current_price - avg_price) / avg_price) * 100
                    
                    holdings.append(
                        f"- {ticker}:\n"
                        f"  수량={quantity:.8f}\n"
                        f"  균단가={avg_price:,}원\n"
                        f"  현재가={current_price:,}원\n"
                        f"  평가액={current_value:,}원\n"
                        f"  수익률={profit_rate:.2f}%\n"
                        f"  적수익={self.total_profit[ticker]:,}원"
                    )
                    
                    total_value += current_value
            
            # 전체 수익률
            total_profit_rate = ((total_value - self.start_cash) / self.start_cash) * 100
            
            # 상태 메시지 생성
            status_msg = (
                f"\n{'='*40}\n"
                f"📊 거래 상태 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
                f"{'='*40}\n"
                f"💰 자금 현황:\n"
                f"- 시작 자금: {self.start_cash:,}원\n"
                f"- 현재 현금: {current_cash:,}원\n"
                f"- 총 평가액: {total_value:,}원\n"
                f"- 총 수익률: {total_profit_rate:.2f}%\n"
            )
            
            if holdings:
                status_msg += f"\n📈 보유 코인:\n" + "\n".join(holdings)
            else:
                status_msg += "\n📈 보유 코인: 없음"
            
            status_msg += f"\n\n📉 코인 지표:\n" + "\n".join(indicators)
            
            logging.info(status_msg)
            
            # Slack 알림 전송 (설정된 경우)
            if hasattr(self, 'notification') and self.notification:
                self.notification.send_status_update(status_msg)
                
        except Exception as e:
            logging.error(f"상태 로깅 중 오류 발생: {str(e)}")
            logging.error(traceback.format_exc())

    @send_error_alert
    def update_tickers(self):
        """거래대금 상위 종목 업데이트"""
        try:
            new_tickers = get_top_tickers(10)
            MIN_PROFIT_TO_SELL = 0.01  # 매도 최소 수익률 1%
            
            # 새로운 종목 추가
            for ticker in new_tickers:
                if ticker not in self.analyzers:
                    self.analyzers[ticker] = DataAnalyzer(ticker)
                    self.buy_yn[ticker] = False
                    self.buy_price[ticker] = 0
                    self.coin_balance[ticker] = 0
                    self.coin_avg_price[ticker] = 0
                    self.total_profit[ticker] = 0
                    logging.info(f"새로운 감시 종목 추가: {ticker}")
            
            # 제외된 종목 처리
            for ticker in list(self.analyzers.keys()):
                if ticker not in new_tickers:
                    # 보유 중인 종목이면 수익률 확인
                    if self.buy_yn[ticker]:
                        current_price = float(self.price_cache[ticker][-1]) if self.price_cache[ticker] else 0
                        if current_price > 0:
                            profit_rate = (current_price - self.buy_price[ticker]) / self.buy_price[ticker]
                            
                            if profit_rate >= MIN_PROFIT_TO_SELL:
                                logging.info(f"감시 제외 종목 매도 (수익률 {profit_rate:.2%}): {ticker}")
                                self.sell_coin(ticker, current_price)
                            else:
                                logging.info(f"감시 제외 종목 유지 (수익률 {profit_rate:.2%}): {ticker}")
                                # 감시 대상에서는 제외되지만 보유는 유지
                                continue
                
                    # 분석기 및 상태 제거 (매도되지 않은 종목은 제외)
                    if not self.buy_yn[ticker]:
                        del self.analyzers[ticker]
                        del self.buy_yn[ticker]
                        del self.buy_price[ticker]
                        del self.coin_balance[ticker]
                        del self.coin_avg_price[ticker]
                        del self.total_profit[ticker]
                        logging.info(f"감시 종목 제외: {ticker}")
            
            self.tickers = new_tickers
            logging.info(f"감시 종목 업데이트 완료: {', '.join(self.tickers)}")
            
        except Exception as e:
            logging.error(f"감시 종목 업데이트 실패: {str(e)}")

    def check_stop_loss(self, ticker, current_price):
        """손절 라인 체크 및 물타기 처리"""
        try:
            if not self.buy_yn[ticker]:
                return False
                
            buy_price = self.buy_price[ticker]
            loss_rate = (current_price - buy_price) / buy_price
            
            # 손절 라인 도달
            if loss_rate <= -STOP_LOSS:
                # 아직 물타기를 사용하지 않은 경우
                if not self.averaging_down_used[ticker]:
                    logging.info(f"{ticker} 손절라인 도달, 물타기 시도...")
                    
                    # 현재 보유 수량의 50%만큼 추가 매수
                    current_amount = self.coin_balance[ticker] * current_price
                    averaging_down_amount = current_amount * 0.5
                    
                    if self.buy_coin(ticker, current_price, 
                                   reason="물타기 매수",
                                   amount=averaging_down_amount):
                        self.averaging_down_used[ticker] = True
                        logging.info(f"{ticker} 물타기 성공")
                        return False  # 손절하지 않음
                    else:
                        logging.warning(f"{ticker} 물타기 실패, 손절 진행")
                        return True  # 물타기 실패시 손절
                
                # 이미 물타기를 사용한 경우
                return True  # 손절 진행
                
            return False
            
        except Exception as e:
            logging.error(f"손절 체크 중 오류 발생: {str(e)}")
            if self.notification:
                self.notification.send_error_alert(f"손절 체크 중 오류 발생: {str(e)}")
            return False

    def process_market_data(self, ticker, data):
        """시장 데이터 처리 및 매매 신호 분석"""
        try:
            current_price = float(data['trade_price'])
            self.price_cache[ticker].append(current_price)
            
            # 보유 중이 아닐 때만 매수 신호 체크
            if not self.buy_yn[ticker]:
                # 실질적 보유 코인 수 체크
                current_holdings = self.get_significant_holdings_count()
                if current_holdings >= MAX_COINS_AT_ONCE:
                    return  # 최대 보유 코인 수 도달, 매수 신호 무시
                
                # 매수 신호 분석
                analysis = self.analyzers[ticker].analyze()
                if analysis['action'] == 'BUY':
                    self.buy_coin(ticker, current_price, 
                                reason=analysis['reason'],
                                target_price=analysis['target_price'])
            
            # ... rest of the existing code ...

        except Exception as e:
            error_msg = f"시장 데이터 처리 중 오류 발생: {str(e)}"
            logging.error(error_msg)
            if self.notification:
                self.notification.send_error_alert(error_msg)

    def get_significant_holdings_count(self):
        """실질적인 보유 코인 수 계산 (최소 보유 가치 이상인 코인만)"""
        try:
            significant_count = 0
            MIN_HOLDING_VALUE = 5000  # 최소 의미 있는 보유 가치 (5000원)
            
            for ticker in self.tickers:
                if self.buy_yn[ticker]:
                    # 현재가로 보유 가치 계산
                    current_price = float(self.price_cache[ticker][-1]) if self.price_cache[ticker] else 0
                    holding_value = self.coin_balance[ticker] * current_price
                    
                    if holding_value >= MIN_HOLDING_VALUE:
                        significant_count += 1
                    else:
                        logging.debug(f"{ticker} 소량 보유 무시 (보유가치: {holding_value:,.0f}원)")
            
            return significant_count
            
        except Exception as e:
            logging.error(f"보유 코인 수 계산 중 오류: {str(e)}")
            return sum(1 for t in self.tickers if self.buy_yn[t])  # 에러 시 기본 카운트 반환
