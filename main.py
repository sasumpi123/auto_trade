import os
import sys
import logging
from pathlib import Path

# 프로젝트 루트 디렉토리를 Python 경로에 추가
project_root = str(Path(__file__).parent)
sys.path.append(project_root)

from config import TICKERS
from services.api_service import verify_api_keys
from trading.auto_trade import AutoTrade

def main():
    try:
        logging.info("프로그램 시작")
        
        upbit = verify_api_keys()
        logging.info("업비트 연결 성공")
        
        start_cash = float(upbit.get_balance("KRW") or 0)
        logging.info(f"시작 잔고: {start_cash:,.0f} KRW")
        
        auto_trader = AutoTrade(start_cash)
        logging.info("자동매매 시작")
        auto_trader.start()
            
    except Exception as e:
        logging.error(f"프로그램 시작 실패: {e}")
        logging.error(traceback.format_exc())

if __name__ == "__main__":
    main()
