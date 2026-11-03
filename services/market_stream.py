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

    def connection_state(self):
        """P23 diagnostics only: RFC 6455 state name, never a liveness or restart decision."""
        name=getattr(getattr(self.socket,'state',None),'name',None)
        return name if name in ('OPEN','CLOSING','CLOSED') else 'UNKNOWN'

    def send_ping(self):
        """P23 diagnostics only. The event is set by the matching pong, not by connection close."""
        return self.socket.ping(ack_on_close=False)

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None
