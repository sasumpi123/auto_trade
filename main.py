import sys
import logging
import traceback
from trading.auto_trade import AutoTrade
from services.api_service import verify_api_keys
from services.notification_service import NotificationService
from config import (
    REAL_TRADING, START_CASH, UPBIT_ACCESS_KEY, 
    UPBIT_SECRET_KEY, SLACK_APP_TOKEN, TICKERS, MIN_TRADING_AMOUNT
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
        if START_CASH < MIN_TRADING_AMOUNT:
            raise ValueError(f"시작 금액이 최소 거래금액보다 작습니다. (최소: {MIN_TRADING_AMOUNT:,}원)")
        checks["설정 파일 검사"] = True
        
        # 2. API 키 검증
        if REAL_TRADING:
            if not verify_api_keys(UPBIT_ACCESS_KEY, UPBIT_SECRET_KEY):
                raise ValueError("Upbit API 키 검증 실패")
        checks["API 키 검증"] = True
        
        # 3. Slack 연동 확인
        if SLACK_APP_TOKEN:
            notification = NotificationService()
            
            # 모든 채널 테스트
            test_messages = {
                'status': "시스템 점검 중... 상태 채널 테스트",
                'trades': "시스템 점검 중... 거래 알림 채널 테스트",
                'reports': "시스템 점검 중... 리포트 채널 테스트",
                'errors': "시스템 점검 중... 에러 채널 테스트"
            }
            
            channel_results = []
            for channel_type, message in test_messages.items():
                result = notification.send_message(channel_type, message)
                channel_results.append((channel_type, result))
                logging.info(f"Slack {channel_type} 채널 테스트: {'성공' if result else '실패'}")
            
            if not all(result for _, result in channel_results):
                failed_channels = [channel for channel, result in channel_results if not result]
                raise ValueError(f"Slack 연동 실패 (실패한 채널: {', '.join(failed_channels)})")
                
        checks["Slack 연동 확인"] = True
        
        # 4. 데이터 분석기 테스트
        from trading.auto_trade import AutoTrade
        test_trader = AutoTrade(start_cash=1000000)
        if not test_trader.analyzers or not test_trader.analyzers[TICKERS[0]]:
            raise ValueError("데이터 분석기 초기화 실패")
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
        
        # 거래 모드 설정 및 AutoTrade 인스턴스 생성
        mode = "실제" if REAL_TRADING else "시뮬레이션"
        logging.info(f"{mode} 거래 모드 시작 (초기 자본: {START_CASH:,}원)")
        
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
