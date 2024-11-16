import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_APP_TOKEN, SLACK_CHANNELS

class NotificationService:
    def __init__(self):
        """알림 서비스 초기화"""
        self.client = WebClient(token=SLACK_APP_TOKEN)
        
    def send_message(self, channel_type, message):
        """슬랙 메시지 전송"""
        try:
            if channel_type not in SLACK_CHANNELS:
                logging.error(f"알 수 없는 채널 타입: {channel_type}")
                return False
                
            channel = SLACK_CHANNELS[channel_type]
            response = self.client.chat_postMessage(
                channel=channel,
                text=message
            )
            return response['ok']
            
        except SlackApiError as e:
            logging.error(f"슬랙 메시지 전송 실패: {str(e)}")
            return False
            
    def send_trade_alert(self, message):
        """매매 알림 전송"""
        return self.send_message('trades', message)
        
    def send_status_update(self, message):
        """상태 업데이트 전송"""
        return self.send_message('status', message)
        
    def send_error_alert(self, message):
        """에러 알림 전송"""
        return self.send_message('errors', message)
        
    def send_report(self, message):
        """리포트 전송"""
        return self.send_message('reports', message)
