"""Bounded receive: connection loss stops the trading loop."""
import json
import uuid


class MarketStream:
    def __init__(self, tickers, timeout=15):
        self.tickers = tickers
        self.timeout = timeout
        self.socket = None

    def connect(self):
        from websockets.sync.client import connect
        self.socket = connect('wss://api.upbit.com/websocket/v1', open_timeout=10,
                              close_timeout=2, ping_interval=20, ping_timeout=10)
        self.socket.send(json.dumps([
            {'ticket': str(uuid.uuid4())},
            {'type': 'ticker', 'codes': self.tickers, 'isOnlyRealtime': True},
        ]))

    def get(self):
        return json.loads(self.socket.recv(timeout=self.timeout))

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
