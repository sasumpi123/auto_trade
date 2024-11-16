import logging
import traceback
import pyupbit
from config import ACC_KEY, SEC_KEY

def verify_api_keys():
    try:
        logging.info("API 키 검증 시작")
        if not ACC_KEY or not SEC_KEY:
            raise Exception("API 키가 설정되지 않았습니다.")
            
        upbit = pyupbit.Upbit(ACC_KEY, SEC_KEY)
        
        try:
            balance = upbit.get_balance("KRW")
            if balance is not None:
                logging.info("잔고 조회 권한 확인 완료")
            else:
                logging.warning("잔고가 None으로 반환됨")
        except Exception as e:
            logging.error(f"잔고 조회 권한 없음: {str(e)}")
            raise Exception("잔고 조회 권한이 없습니다.")
            
        return upbit
    except Exception as e:
        logging.error(f"API 키 검증 실패: {str(e)}")
        raise