import requests
import logging
from datetime import datetime
from config import APP_TOKEN, CHANNEL

class NotificationService:
    def __init__(self, message_queue):
        self.message_queue = message_queue
        self.important_messages = []

    def send_message(self, message, is_important=False):
        """슬랙으로 메시지 전송"""
        try:
            if is_important:
                self.important_messages.append(message)
            
            if not self.message_queue.can_send_message():
                if is_important:
                    return  # 중요 메시지는 나중에 재시도
                return False  # 일반 메시지는 그냥 스킵
            
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {APP_TOKEN}"},
                json={
                    "channel": CHANNEL,
                    "text": message,
                    "mrkdwn": True
                }
            )
            
            if response.status_code == 429:  # Rate limit
                if is_important:
                    self.important_messages.append(message)
                logging.warning("슬랙 API 호출 제한에 도달했습니다.")
                return False
                
            if not response.json()['ok']:
                logging.error(f"슬랙 메시지 전송 실패: {response.json()['error']}")
                return False

            self.message_queue.log_message_sent()
            
            if is_important:
                logging.info(f"중요 메시지 전송 완료: {message[:100]}...")
            else:
                logging.debug(f"일반 메시지 전송 완료: {message[:100]}...")
            
            return True
                
        except Exception as e:
            logging.error(f"슬랙 메시지 전송 중 오류 발생: {str(e)}")
            if is_important:
                self.important_messages.append(message)
            return False

    def send_pending_important_messages(self):
        """대기 중인 중요 메시지 전송 시도"""
        if not self.important_messages:
            return

        messages_to_retry = self.important_messages[:]
        self.important_messages.clear()

        for message in messages_to_retry:
            self.send_message(message, is_important=True)

    def format_error_message(self, error_type, error_message, additional_info=None):
        """에러 메시지 포맷팅"""
        message = (
            f"*🚨 에러 발생*\n"
            f"*타입:* {error_type}\n"
            f"*메시지:* {error_message}\n"
        )
        if additional_info:
            message += f"*추가 정보:* {additional_info}\n"
        message += f"*시간:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return message

    def format_trade_message(self, trade_type, ticker, price, amount, profit=None):
        """거래 메시지 포맷팅"""
        message = (
            f"*{'🔵 매수' if trade_type == 'BUY' else '🔴 매도'}*\n"
            f"*코인:* {ticker}\n"
            f"*가격:* {price:,} KRW\n"
            f"*수량:* {amount:.8f}\n"
        )
        if profit is not None:
            message += f"*수익률:* {profit:.2f}%\n"
        message += f"*시간:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return message

    def format_status_message(self, ticker, current_price, balance, coin_balance, profit):
        """상태 메시지 포맷팅"""
        return (
            f"*{ticker} 현재 상태*\n"
            f"*현재가:* {current_price:,} KRW\n"
            f"*보유 현금:* {balance:,.0f} KRW\n"
            f"*보유 코인:* {coin_balance:.8f}\n"
            f"*평가 금액:* {(coin_balance * current_price):,.0f} KRW\n"
            f"*누적 수익률:* {profit:.2f}%\n"
            f"*시간:* {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
