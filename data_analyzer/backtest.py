import pandas as pd
from datetime import datetime
import candle_analyzer

class Backtest:
    def __init__(self, ticker, initial_capital=1_000_000):
        self.ticker = ticker
        self.analyzer = candle_analyzer.CandleAnalyzer(ticker)
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.position = None
        self.trades = []
        
    def run(self, start_date="2024-03-01", end_date="2024-09-30"):
        # 데이터 가져오기
        df = self.analyzer.fetch_historical_data_by_date(start_date, end_date)
        if df is None or df.empty:
            print("데이터 조회 실패")
            return
        
        self.analyzer.prepare_data(df)
        
        # 백테스팅 실행
        for i in range(len(df)-1):
            current_row = df.iloc[i]
            next_row = df.iloc[i+1]
            
            current_price = current_row['close']
            next_price = next_row['close']
            
            # 현재 데이터까지만 분석하기 위해 데이터 슬라이싱
            self.analyzer.df = df.iloc[:i+1].copy()
            self.analyzer.prepare_data()
            
            # 매매 신호 분석
            signal = self.analyzer.analyze(
                current_price=current_price,
                buy_price=self.position['price'] if self.position else None
            )
            
            # 매매 실행
            if signal['action'] == 'BUY' and not self.position:
                self.position = {
                    'price': next_price,
                    'amount': self.capital / next_price,
                    'date': next_row.name
                }
                
            elif signal['action'] == 'SELL' and self.position:
                profit = (next_price - self.position['price']) / self.position['price']
                self.capital = self.position['amount'] * next_price
                
                self.trades.append({
                    'entry_date': self.position['date'],
                    'exit_date': next_row.name,
                    'entry_price': self.position['price'],
                    'exit_price': next_price,
                    'profit_rate': profit,
                    'profit_amount': self.capital - self.initial_capital
                })
                
                self.position = None
        
        self._print_results()
    
    def _print_results(self):
        if not self.trades:
            print("거래 내역이 없습니다.")
            return
            
        total_trades = len(self.trades)
        winning_trades = len([t for t in self.trades if t['profit_rate'] > 0])
        total_profit_rate = (self.capital - self.initial_capital) / self.initial_capital
        
        print("\n=== 백테스팅 결과 ===")
        print(f"초기 자본금: {self.initial_capital:,}원")
        print(f"최종 자본금: {self.capital:,.0f}원")
        print(f"총 수익률: {total_profit_rate:.2%}")
        print(f"총 거래 횟수: {total_trades}")
        print(f"승률: {winning_trades/total_trades:.2%}")
        
        # 상세 거래 내역 출력
        print("\n=== 거래 내역 ===")
        for trade in self.trades:
            print(f"진입: {trade['entry_date']} ({trade['entry_price']:,}원)")
            print(f"청산: {trade['exit_date']} ({trade['exit_price']:,}원)")
            print(f"수익률: {trade['profit_rate']:.2%}")
            print("---")


# 메인 실행 코드
if __name__ == "__main__":
    import sys
    import os
    
    # 현재 디렉토리를 파이썬 경로에 추가
    current_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path.append(current_dir)
    
    # 백테스팅 실행
    backtest = Backtest("KRW-BTC")
    backtest.run("2024-03-01", "2024-09-30")