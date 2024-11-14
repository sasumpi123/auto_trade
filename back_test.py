import pyupbit
import pandas as pd
from data_analyzer import DataAnalyzer
from datetime import datetime, timedelta
import logging
import time

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('backtest.log'),
        logging.StreamHandler()
    ]
)

def fetch_historical_data(ticker, years):
    """지정된 연도만큼의 1시간 봉 데이터를 가져옵니다."""
    logging.info(f"{ticker}의 {years}년치 데이터 수집 시작")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * years)
    all_data = pd.DataFrame()
    current_date = end_date

    try:
        while current_date > start_date:
            logging.debug(f"데이터 조회 중... (to: {current_date.strftime('%Y-%m-%d %H:%M:%S')})")
            data = pyupbit.get_ohlcv(ticker, interval="minute60", to=current_date.strftime("%Y-%m-%d %H:%M:%S"))
            
            if data is None or data.empty:
                logging.warning(f"{current_date} 시점의 데이터를 가져올 수 없습니다.")
                break
                
            all_data = pd.concat([data, all_data])
            current_date = data.index[0]  # 다음 조회할 날짜를 가장 오래된 데이터의 시점으로 설정
            time.sleep(0.1)  # API 호출 제한 준수

        all_data = all_data[~all_data.index.duplicated(keep='first')]
        all_data = all_data.sort_index()
        
        logging.info(f"수집된 데이터 개수: {len(all_data)}")
        return all_data

    except Exception as e:
        logging.error(f"데이터 수집 중 오류 발생: {e}")
        return None

class Backtester:
    def __init__(self, ticker, initial_balance, data):
        self.ticker = ticker
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.position = 0
        self.trades = []
        self.data = data

    def run_backtest(self):
        if self.data is None or self.data.empty:
            logging.error("데이터가 없습니다. 백테스팅을 수행할 수 없습니다.")
            return

        # 백테스팅 시작 시간 기록
        start_time = datetime.now()
        logging.info("\n" + "="*60)
        logging.info(f"백테스팅 시작 시간: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logging.info(f"대상 코인: {self.ticker}")
        logging.info(f"테스트 기간: {self.data.index[0]} ~ {self.data.index[-1]}")
        logging.info(f"초기 투자금: {self.initial_balance:,}원")
        logging.info("="*60)

        analyzer = DataAnalyzer(self.ticker)
        analyzer.data = self.data.copy()
        analyzer.calculate_indicators()

        # 거래 기록을 위한 변수들
        max_balance = self.initial_balance  # 최고 자산
        min_balance = self.initial_balance  # 최저 자산
        win_trades = 0  # 수익 거래 횟수
        loss_trades = 0  # 손실 거래 횟수
        last_buy_price = 0  # 마지막 매수 가격

        for index in range(1, len(self.data)):
            action = analyzer.analyze(index)
            current_price = self.data.iloc[index]['close']

            if action == "BUY" and self.balance > 0:
                self.position = self.balance / current_price
                self.balance = 0
                last_buy_price = current_price
                self.trades.append((self.data.index[index], "BUY", current_price))
                logging.debug(f"{self.data.index[index]}: 매수 - 가격: {current_price:,}원")

            elif action == "SELL" and self.position > 0:
                self.balance = self.position * current_price
                # 수익/손실 거래 계산
                if current_price > last_buy_price:
                    win_trades += 1
                else:
                    loss_trades += 1
                
                self.position = 0
                self.trades.append((self.data.index[index], "SELL", current_price))
                logging.debug(f"{self.data.index[index]}: 매도 - 가격: {current_price:,}원")

            # 현재 자산 계산 및 최고/최저 자산 갱신
            current_value = self.balance + (self.position * current_price)
            max_balance = max(max_balance, current_value)
            min_balance = min(min_balance, current_value)

        # 최종 결과 계산
        final_value = self.balance + (self.position * self.data.iloc[-1]['close'])
        total_return = ((final_value - self.initial_balance) / self.initial_balance) * 100
        total_trades = len(self.trades)
        buy_trades = len([t for t in self.trades if t[1] == "BUY"])
        sell_trades = len([t for t in self.trades if t[1] == "SELL"])
        
        # 결과 리포트 생성
        report = "\n" + "="*60 + "\n"
        report += "백테스팅 결과 리포트\n"
        report += "="*60 + "\n"
        report += f"테스트 기간: {self.data.index[0]} ~ {self.data.index[-1]}\n"
        report += f"총 테스트 기간: {(self.data.index[-1] - self.data.index[0]).days}일\n"
        report += f"\n[자산 정보]\n"
        report += f"초기 자산: {self.initial_balance:,}원\n"
        report += f"최종 자산: {final_value:,.2f}원\n"
        report += f"순수익: {final_value - self.initial_balance:,.2f}원\n"
        report += f"수익률: {total_return:.2f}%\n"
        report += f"최고 자산: {max_balance:,.2f}원\n"
        report += f"최저 자산: {min_balance:,.2f}원\n"
        report += f"\n[거래 정보]\n"
        report += f"총 거래 횟수: {total_trades}회\n"
        report += f"매수 횟수: {buy_trades}회\n"
        report += f"매도 횟수: {sell_trades}회\n"
        report += f"수익 거래: {win_trades}회\n"
        report += f"손실 거래: {loss_trades}회\n"
        if total_trades > 0:
            report += f"승률: {(win_trades/total_trades)*100:.2f}%\n"
        
        if self.position > 0:
            report += f"\n미청산 포지션: {self.position:.8f} {self.ticker.split('-')[1]}\n"
        
        report += "="*60 + "\n"
        
        # 거래 내역 추가
        if self.trades:
            report += "\n[상세 거래 내역]\n"
            report += "-"*60 + "\n"
            for date, action, price in self.trades:
                report += f"{date}: {action:4} @ {price:,}원\n"
            report += "-"*60 + "\n"
        
        # 백테스팅 소요 시간 추가
        end_time = datetime.now()
        duration = end_time - start_time
        report += f"\n백테스팅 소요 시간: {duration.total_seconds():.2f}초\n"
        report += "="*60 + "\n"

        # 로그 파일에 기록
        logging.info(report)
        
        # 별도의 결과 파일 생성
        result_filename = f"backtest_result_{self.ticker}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(result_filename, 'w', encoding='utf-8') as f:
            f.write(report)
        logging.info(f"상세 결과가 {result_filename}에 저장되었습니다.")

# 백테스팅 실행
if __name__ == "__main__":
    ticker = "KRW-BTC"
    initial_balance = 1000000  # 초기 자산 (100만 원)
    years = 1  # 몇 년치 데이터를 사용할지 설정

    data = fetch_historical_data(ticker, years)
    if data is not None:
        backtester = Backtester(ticker, initial_balance, data)
        backtester.run_backtest()
