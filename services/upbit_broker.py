"""Private Upbit REST adapter. Constructed only after explicit real-mode opt-in."""
import hashlib
from urllib.parse import urlencode, unquote
import uuid


class UpbitBroker:
    def __init__(self, access_key, secret_key, *, enabled=False, session=None):
        if not enabled:
            raise RuntimeError('Live broker is disabled')
        if not access_key or not secret_key:
            raise ValueError('API keys are missing')
        import requests
        self.session = session if session is not None else requests.Session()
        self.access_key = access_key
        self.secret_key = secret_key
        self.identity = hashlib.sha256(access_key.encode()).hexdigest()

    def _request(self, method, path, params=None):
        import jwt
        params = params or {}
        payload = {'access_key': self.access_key, 'nonce': str(uuid.uuid4())}
        if params:
            query = unquote(urlencode(params, doseq=True)).encode()
            payload.update(query_hash=hashlib.sha512(query).hexdigest(), query_hash_alg='SHA512')
        token = jwt.encode(payload, self.secret_key, algorithm='HS256')
        response = self.session.request(
            method, 'https://api.upbit.com/v1/' + path,
            headers={'Authorization': 'Bearer ' + token},
            **({'json': params} if method == 'POST' else {'params': params}),
            timeout=(3.05, 10), allow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and 'error' in result:
            raise RuntimeError('Upbit rejected the request')
        return result

    def submit(self, side, ticker, value, identifier):
        params = {'market': ticker, 'side': side, 'identifier': identifier}
        params.update({'ord_type': 'price', 'price': str(value)} if side == 'bid'
                      else {'ord_type': 'market', 'volume': str(value)})
        return self._request('POST', 'orders', params)

    def order(self, *, uuid=None, identifier=None):
        if not uuid and not identifier:
            raise ValueError('Order UUID or identifier is required')
        return self._request('GET', 'order', {'uuid': uuid} if uuid else {'identifier': identifier})

    def balances(self):
        return self._request('GET', 'accounts')

    def open_orders(self):
        orders = []
        for page in range(1, 101):
            batch = self._request('GET', 'orders/open', {
                'states[]': ['wait', 'watch'], 'page': page, 'limit': 100,
            })
            if not isinstance(batch, list):
                raise ValueError('Invalid open orders response')
            orders.extend(batch)
            if len(batch) < 100:
                return orders
        raise RuntimeError('Open order pagination limit exceeded; cannot reconcile safely')
