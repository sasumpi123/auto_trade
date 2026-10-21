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
            if not SLACK_APP_TOKEN:
                return False
            if channel_type not in SLACK_CHANNELS:
                logging.error(f"알 수 없는 채널 타입: {channel_type}")
                return False
                
            channel = SLACK_CHANNELS[channel_type]
            logging.info(f"메시지 전송 시도 - 채널: {channel}, 메시지: {message}")
            
            response = self.client.chat_postMessage(
                channel=channel,
                text=message
            )
            
            if not response['ok']:
                logging.error(f"메시지 전송 실패 - 응답: {response}")
                return False
                
            return True
            
        except Exception as e:
            logging.error(f"메시지 전송 중 오류 발생: {str(e)}")
            return False
            
    def send_trade_alert(self, message):
        """매매 알림 전송"""
        try:
            logging.info(f"매매 알림 전송 시도 - 채널: trades")
            logging.info(f"전송할 메시지: {message}")
            result = self.send_message('trades', message)
            logging.info(f"매매 알림 전송 결과: {'성공' if result else '실패'}")
            return result
        except Exception as e:
            logging.error(f"매매 알림 전송 중 오류 발생: {str(e)}")
            return False
        
    def send_status_update(self, message):
        """상태 업데이트 전송 (trading-status 채널)"""
        logging.info(f"상태 업데이트 전송 시도")
        return self.send_message('status', message)
        
    def send_error_alert(self, message):
        """에러 알림 전송 (trading-errors 채널)"""
        error_message = f"🚨 에러 발생:\n{message}"
        logging.info(f"에러 알림 전송 시도")
        return self.send_message('errors', error_message)
        
    def send_report(self, message):
        """리포트 전송 (trading-reports 채널)"""
        logging.info(f"리포트 전송 시도")
        return self.send_message('reports', message)

    def format_status_message(self, ticker, current_price, balance, coin_balance, total_profit, strategy_status):
        """상태 메시지 포맷팅"""
        return (
            f"=== {ticker} 상태 업데이트 ===\n"
            f"현재가: {current_price:,}원\n"
            f"보유량: {coin_balance}\n"
            f"현금잔액: {balance:,}원\n"
            f"누적수익: {total_profit:,.2f}%\n"
            f"\n전략상태:\n"
            f"RSI: {strategy_status.get('RSI', 'N/A')}\n"
            f"MACD: {strategy_status.get('MACD', 'N/A')}\n"
            f"BB: {strategy_status.get('BB', 'N/A')}"
        )

