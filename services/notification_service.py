import requests
import logging
from datetime import datetime
from config import APP_TOKEN, SLACK_CHANNELS

class NotificationService:
    def __init__(self, message_queue):
        self.message_queue = message_queue
        self.important_messages = {channel: [] for channel in SLACK_CHANNELS.values()}

    def send_message(self, message, channel_type='status', is_important=False):
        """슬랙으로 메시지 전송"""
        try:
            channel = SLACK_CHANNELS.get(channel_type, SLACK_CHANNELS['status'])
            
            if is_important:
                self.important_messages[channel].append(message)
            
            if not self.message_queue.can_send_message():
                if is_important:
                    return  # 중요 메시지는 나중에 재시도
                return False
            
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {APP_TOKEN}"},
                json={
                    "channel": channel,
                    "text": message,
                    "mrkdwn": True
                }
            )
            
            if response.status_code == 429:  # Rate limit
                if is_important:
                    self.important_messages[channel].append(message)
                logging.warning("슬랙 API 호출 제한에 도달했습니다.")
                return False
                
            if not response.json()['ok']:
                logging.error(f"슬랙 메시지 전송 실패: {response.json()['error']}")
                return False

            self.message_queue.log_message_sent()
            return True
            
        except Exception as e:
            logging.error(f"슬랙 메시지 전송 중 오류 발생: {str(e)}")
            if is_important:
                self.important_messages[channel].append(message)
            return False

    def send_trade_alert(self, message):
        """매수/매도 알림 전송"""
        return self.send_message(message, channel_type='trades', is_important=True)

    def send_status_update(self, message):
        """상태 업데이트 전송"""
        return self.send_message(message, channel_type='status', is_important=False)

    def send_report(self, message):
        """리포트 전송"""
        return self.send_message(message, channel_type='reports', is_important=True)

    def send_error_alert(self, message):
        """에러 알림 전송"""
        return self.send_message(message, channel_type='errors', is_important=True)

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
