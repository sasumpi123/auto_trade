import time
import logging
import traceback
from functools import wraps
from services.notification_service import NotificationService

notification_service = NotificationService()

def send_error_alert(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = (
                f"함수: {func.__name__}\n"
                f"에러: {str(e)}\n"
                f"상세:\n{traceback.format_exc()}"
            )
            logging.error(error_msg)
            notification_service.send_error_alert(error_msg)
            raise
    return wrapper

def retry_on_failure(max_attempts=3, delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        error_msg = (
                            f"함수: {func.__name__}\n"
                            f"최대 재시도 횟수 도달\n"
                            f"에러: {str(e)}\n"
                            f"상세:\n{traceback.format_exc()}"
                        )
                        logging.error(error_msg)
                        notification_service.send_error_alert(error_msg)
                        raise
                    logging.warning(f"{func.__name__} 실패, 재시도 중... ({attempt + 1}/{max_attempts})")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator