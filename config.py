import os
import logging
from dotenv import load_dotenv
import pyupbit

# 로그 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)

# .env 파일에서 환경 변수 로드
load_dotenv()

# 환경 변수 및 API 키
UPBIT_ACCESS_KEY = os.getenv("ACC_KEY")
UPBIT_SECRET_KEY = os.getenv("SEC_KEY")
SLACK_APP_TOKEN = os.getenv("APP_TOKEN")
SLACK_CHANNEL = os.getenv("CHANNEL")

# Slack 채널 설정
SLACK_CHANNELS = {
    'status': 'trading-status',     # 주기적 상태 업데이트
    'trades': 'trading-alerts',     # 매수/매도 알림
    'reports': 'trading-reports',   # 일일/주간 리포트
    'errors': 'trading-errors'      # 에러 알림
}

def get_top_tickers(limit=10):
    """거래대금 상위 limit개 종목 조회"""
    try:
        # 원화 마켓의 모든 티커 조회
        krw_tickers = pyupbit.get_tickers(fiat="KRW")
        
        # 24시간 거래대금 조회
        all_volumes = []
        for ticker in krw_tickers:
            try:
                # 24시간 캔들 조회
                df = pyupbit.get_ohlcv(ticker, interval="day", count=1)
                if df is not None and not df.empty:
                    volume = df['value'].iloc[-1]  # 거래대금
                    all_volumes.append((ticker, volume))
            except Exception as e:
                logging.warning(f"{ticker} 거래대금 조회 실패: {str(e)}")
                continue
        
        # 거래대금 기준 정렬
        all_volumes.sort(key=lambda x: x[1], reverse=True)
        
        # 상위 limit개 티커 선택
        top_tickers = [item[0] for item in all_volumes[:limit]]
        
        logging.info(f"거래대금 상위 {limit}개 종목 선정 완료: {', '.join(top_tickers)}")
        return top_tickers
        
    except Exception as e:
        logging.error(f"거래대금 상위 종목 조회 실패: {str(e)}")
        # 실패 시 기본 티커 반환
        default_tickers = [
            "KRW-BTC",  # 비트코인
            "KRW-ETH",  # 이더리움
            "KRW-XRP",  # 리플
            "KRW-DOGE"  # 도지코인
        ]
        logging.warning(f"기본 티커 사용: {', '.join(default_tickers)}")
        return default_tickers

# 거래 대상 코인 (거래대금 상위 10개)
TICKERS = get_top_tickers(10)

# 거래 모드
REAL_TRADING = False  # True: 실제 거래, False: 테스트 거래

# 거래 설정
START_CASH = 1_000_000          # 시작 자금 (테스트 모드)
MIN_TRADING_AMOUNT = 5000       # 최소 거래금액
MAX_COINS_AT_ONCE = 2          # 동시 보유 가능한 최대 코인 수 (2개로 수정)
CASH_USAGE_RATIO = 0.4         # 코인당 최대 투자 비율 (40%)
STOP_LOSS = 0.05              # 손절 라인 (5%)

# 물타기 설정 추가
AVERAGING_DOWN_RATIO = 0.5     # 물타기 시 추가 매수 비율 (기존 보유 금액의 50%)
MAX_AVERAGING_DOWN = 1         # 코인당 최대 물타기 횟수

# 시간 간격 설정
REPORT_CHECK_INTERVAL = 30  # 리포트 체크 간격 (30초)
DATA_UPDATE_INTERVAL = 300  # 데이터 업데이트 간격 (5분)
STATUS_INTERVAL = 300      # 상태 체크 간격 (5분)