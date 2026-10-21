import logging
from logging.handlers import RotatingFileHandler
import signal

from config import REAL_TRADING, START_CASH, SLACK_APP_TOKEN, get_top_tickers
from services.notification_service import NotificationService
from trading.auto_trade import AutoTrade


def setup_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
                        handlers=[RotatingFileHandler('trading_bot.log', maxBytes=5_000_000,
                                                      backupCount=3, encoding='utf-8'),
                                  logging.StreamHandler()], force=True)


class TradingBot:
    def __init__(self):
        self.auto_trader = None

    def signal_handler(self, signum, frame):
        if self.auto_trader:
            self.auto_trader.request_stop()

    def run(self):
        try:
            self.auto_trader = AutoTrade(
                start_cash=START_CASH, tickers=get_top_tickers(10), real_trading=REAL_TRADING,
                notification=NotificationService() if SLACK_APP_TOKEN else None,
            )
            signal.signal(signal.SIGINT, self.signal_handler)
            signal.signal(signal.SIGTERM, self.signal_handler)
            self.auto_trader.start()
            return 0
        except Exception:
            logging.exception('Bot halted. Inspect journal before restarting; no orders retried.')
            return 1
        finally:
            if self.auto_trader:
                self.auto_trader.stop()


def main():
    setup_logging()
    return TradingBot().run()


if __name__ == '__main__':
    raise SystemExit(main())
