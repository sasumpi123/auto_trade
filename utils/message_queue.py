from datetime import datetime, timedelta
import logging

class MessageQueue:
    def __init__(self):
        self.last_sent_time = datetime.now()
        self.daily_count = 0
        self.daily_limit = 900
        self.last_count_reset = datetime.now()
        self.min_interval = 2
        self.warning_logged = False

    def can_send_message(self):
        now = datetime.now()
        
        if now - self.last_count_reset > timedelta(days=1):
            self.daily_count = 0
            self.last_count_reset = now
            self.warning_logged = False

        if self.daily_count >= self.daily_limit:
            if not self.warning_logged:
                logging.warning("일일 메시지 전송 한도에 도달했습니다.")
                self.warning_logged = True
            return False

        if now - self.last_sent_time < timedelta(seconds=self.min_interval):
            return False

        return True

    def log_message_sent(self):
        self.last_sent_time = datetime.now()
        self.daily_count += 1