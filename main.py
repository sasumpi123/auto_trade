import os
import sys
import logging
import traceback
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)

# 프로젝트 루트 디렉토리를 Python 경로에 추가
project_root = str(Path(__file__).parent)
sys.path.append(project_root)

from config import TICKERS
from services.api_service import verify_api_keys
from trading.auto_trade import AutoTrade

def main():
    try:
        logging.info("프로그램 시작")
        
        # 시뮬레이션 설정
        simulation_mode = True
        start_cash = 1000000  # 100만원
        
        if simulation_mode:
            logging.info(f"시뮬레이션 모드 시작 (초기 자본: {start_cash:,} KRW)")
            auto_trader = AutoTrade(start_cash, simulation_mode=True)
        else:
            upbit = verify_api_keys()
            logging.info("업비트 연결 성공")
            start_cash = float(upbit.get_balance("KRW") or 0)
            logging.info(f"시작 잔고: {start_cash:,.0f} KRW")
            auto_trader = AutoTrade(start_cash, simulation_mode=False)
            
        logging.info("자동매매 시작")
        auto_trader.start()
            
    except Exception as e:
        logging.error(f"프로그램 시작 실패: {e}")
        logging.error(traceback.format_exc())

if __name__ == "__main__":
    main()
