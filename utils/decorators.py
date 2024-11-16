import time
import logging
import traceback

def retry_on_failure(max_attempts=3, delay=1):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    logging.warning(f"{func.__name__} 실패, 재시도 중... ({attempt + 1}/{max_attempts})")
                    time.sleep(delay)
            return None
        return wrapper
    return decorator