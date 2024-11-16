import sys
import logging
import traceback
from trading.auto_trade import AutoTrade
from services.api_service import verify_api_keys
from services.notification_service import NotificationService
from config import (
    REAL_TRADING, START_CASH, UPBIT_ACCESS_KEY, 
    UPBIT_SECRET_KEY, SLACK_APP_TOKEN, TICKERS
)

def setup_logging():
    """로깅 설정"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def system_check():
    """시스템 전체 점검"""
    checks = {
        "설정 파일 검사": False,
        "API 키 검증": False,
        "Slack 연동 확인": False,
        "데이터 분석기 초기화": False
    }
    
    try:
        # 1. 설정 파일 검사
        if not all([TICKERS, START_CASH > 0]):
            raise ValueError("기본 설정값 오류")
        checks["설정 파일 검사"] = True
        
        # 2. API 키 검증
        if REAL_TRADING:
            if not verify_api_keys(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY):
                raise ValueError("Upbit API 키 검증 실패")
        checks["API 키 검증"] = True
        
        # 3. Slack 연동 확인
        if SLACK_APP_TOKEN:
            notification = NotificationService()
            test_result = notification.send_message(
                'status',
                "시스템 점검 중... Slack 연동 테스트"
            )
            if not test_result:
                raise ValueError("Slack 연동 실패")
        checks["Slack 연동 확인"] = True
        
        # 4. 데이터 분석기 테스트
        test_analyzer = AutoTrade(start_cash=1000)
        for ticker in TICKERS[:1]:  # 첫 번째 코인으로만 테스트
            analysis = test_analyzer.analyzers[ticker].analyze()
            if not isinstance(analysis, dict) or 'action' not in analysis:
                raise ValueError(f"데이터 분석기 초기화 실패: {ticker}")
        checks["데이터 분석기 초기화"] = True
        
        # 모든 검사 통과
        logging.info("시스템 점검 완료:")
        for check, status in checks.items():
            logging.info(f"- {check}: {'성공' if status else '실패'}")
        return True
        
    except Exception as e:
        logging.error(f"시스템 점검 실패: {str(e)}")
        logging.error("점검 결과:")
        for check, status in checks.items():
            logging.error(f"- {check}: {'성공' if status else '실패'}")
        return False

def main():
    """메인 함수"""
    try:
        logging.info("프로그램 시작")
        
        # 시스템 점검 실행
        if not system_check():
            logging.error("시스템 점검 실패. 프로그램을 종료합니다.")
            sys.exit(1)
        
        # 거래 모드 설정
        if not REAL_TRADING:
            logging.info(f"시뮬레이션 모드 시작 (초기 자본: {START_CASH:,} KRW)")
        else:
            logging.info("실제 거래 모드 시작")
        
        # AutoTrade 인스턴스 생성 및 시작
        auto_trader = AutoTrade(start_cash=START_CASH)
        auto_trader.start()
        
    except Exception as e:
        logging.error(f"프로그램 시작 실패: {str(e)}")
        logging.error(f"Traceback (most recent call last):\n{traceback.format_exc()}")
        
        # Slack으로 에러 알림 전송
        try:
            notification = NotificationService()
            notification.send_error_alert(
                f"프로그램 시작 실패:\n"
                f"에러: {str(e)}\n"
                f"상세: {traceback.format_exc()}"
            )
        except:
            pass
            
        sys.exit(1)

if __name__ == "__main__":
    setup_logging()
    main()
