import time
from datetime import datetime, time as dt_time, timedelta
import logging
from utils.decorators import send_error_alert

class PerformanceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.api_calls = 0
        self.api_errors = 0
        self.websocket_disconnects = 0
        self.last_report_time = time.time()
        self.report_interval = 3600  # 1시간마다 리포트

    def log_api_call(self):
        """API 호출 기록"""
        try:
            self.api_calls += 1
        except Exception as e:
            logging.error(f"API 호출 로깅 실패: {str(e)}")

    def log_api_error(self):
        self.api_errors += 1

    def log_websocket_disconnect(self):
        self.websocket_disconnects += 1

    def should_report(self):
        return time.time() - self.last_report_time > self.report_interval

    def generate_report(self):
        uptime = time.time() - self.start_time
        report = (
            f"\n=== 성능 모니터링 리포트 ===\n"
            f"작동 시간: {uptime/3600:.1f}시간\n"
            f"API 호출 수: {self.api_calls}\n"
            f"API 에러 수: {self.api_errors}\n"
            f"웹소켓 재연결 수: {self.websocket_disconnects}\n"
            f"시간당 API 호출: {self.api_calls/(uptime/3600):.1f}회\n"
        )
        self.last_report_time = time.time()
        return report

class PerformanceAnalyzer:
    def __init__(self, tickers):
        self.daily_trades = {}
        self.last_report_date = None
        self.last_report_time = None
        self.tickers = tickers
        self.report_times = [
            dt_time(9, 0),   # 오전 9시
            dt_time(18, 0)   # 오후 6시
        ]

    def add_trade(self, ticker, trade_info):
        date = datetime.now().date()
        if date not in self.daily_trades:
            self.daily_trades[date] = {t: [] for t in self.tickers}
        self.daily_trades[date][ticker].append(trade_info)

    def check_daily_report_time(self):
        """리포트 시간 체크 (오전 9시, 오후 6시)"""
        try:
            now = datetime.now()
            current_time = now.time()
            
            # 현재 분이 0분인지 확인
            if current_time.minute != 0:
                return False
            
            # 현재 시간이 9시 또는 18시인지 확인
            if current_time.hour not in [9, 18]:
                return False
            
            # 이미 오늘 해당 시간에 리포트를 생성했는지 확인
            if (self.last_report_date == now.date() and 
                self.last_report_time == current_time.replace(minute=0, second=0, microsecond=0)):
                return False
            
            # 리포트 생성 시간 업데이트
            self.last_report_date = now.date()
            self.last_report_time = current_time.replace(minute=0, second=0, microsecond=0)
            
            logging.info(f"리포트 생성 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")
            return True
            
        except Exception as e:
            logging.error(f"리포트 시간 체크 중 오류 발생: {str(e)}")
            return False

    @send_error_alert
    def generate_daily_report(self):
        """일일 거래 리포트 생성"""
        now = datetime.now()
        today = now.date()
        
        if now.time() < dt_time(12, 0):  # 오전 리포트
            target_date = today - timedelta(days=1)
            report_prefix = f"전일({target_date})"
        else:  # 오후 리포트
            target_date = today
            report_prefix = f"금일({target_date})"
        
        report = f"\n=== {report_prefix} 거래 리포트 ===\n"
        report += f"생성 시각: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        
        if target_date in self.daily_trades:
            total_profit = 0
            trade_count = 0
            
            for ticker in self.tickers:
                ticker_trades = self.daily_trades[target_date][ticker]
                if ticker_trades:
                    profit = sum(trade.get('profit', 0) for trade in ticker_trades)
                    count = len(ticker_trades)
                    win_trades = len([t for t in ticker_trades if t.get('profit', 0) > 0])
                    
                    report += (
                        f"\n{ticker}:\n"
                        f"거래 횟수: {count}\n"
                        f"승률: {(win_trades/count)*100:.1f}%\n"
                        f"수익률: {profit:.2f}%\n"
                        f"평균 수익률: {profit/count:.2f}%\n"
                    )
                    
                    total_profit += profit
                    trade_count += count
            
            if trade_count > 0:
                report += (
                    f"\n=== 종합 정보 ===\n"
                    f"총 거래 횟수: {trade_count}\n"
                    f"총 수익률: {total_profit:.2f}%\n"
                    f"거래당 평균 수익률: {total_profit/trade_count:.2f}%\n"
                )
            else:
                report += "\n해당 기간 거래 없음\n"
        else:
            report += "거래 기록 없음\n"
            
        return report

    def clear_old_data(self, days_to_keep=7):
        """오래된 거래 데이터 정리"""
        today = datetime.now().date()
        delete_before = today - timedelta(days=days_to_keep)
        
        for date in list(self.daily_trades.keys()):
            if date < delete_before:
                del self.daily_trades[date]
