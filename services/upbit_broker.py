"""Private Upbit REST adapter. Constructed only after explicit real-mode opt-in."""
import hashlib
from collections.abc import Mapping
from decimal import Decimal
from urllib.parse import urlencode, unquote
import uuid


def request_parameters(params):
    """One ordered snapshot for both transmission and Upbit's decoded hash input.

    Do not sort keys, use unquote_plus, hash JSON bytes, or decode the secret key.
    See https://docs.upbit.com/kr/reference/auth (Python reference example).
    """
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        raise ValueError('Request parameters must be an ordered mapping')

    def scalar(value):
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise ValueError('Only strings, integers and finite Decimals are supported')
        if isinstance(value, Decimal) and not value.is_finite():
            raise ValueError('Non-finite parameter')
        return str(value)

    snapshot = {}
    for key, value in params.items():
        if not isinstance(key, str) or not key:
            raise ValueError('Parameter names must be non-empty strings')
        if isinstance(value, (list, tuple)):
            if not key.endswith('[]') or not value:
                raise ValueError('Repeated values require a non-empty [] parameter')
            snapshot[key] = [scalar(item) for item in value]
        else:
            snapshot[key] = scalar(value)
    encoded = urlencode(snapshot, doseq=True)
    return snapshot, encoded, unquote(encoded)


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
        if (method, path) not in {('GET', 'accounts'), ('GET', 'order'),
                                  ('GET', 'orders/open'), ('POST', 'orders')}:
            raise ValueError('Unsupported broker operation')
        defaults = getattr(self.session, 'params', None)
        if isinstance(defaults, (Mapping, list, tuple, str)) and defaults:
            raise ValueError('Session-level query parameters could change the signed request')
        body, encoded_query, hash_input = request_parameters(params)
        payload = {'access_key': self.access_key, 'nonce': str(uuid.uuid4())}
        if hash_input:
            payload.update(query_hash=hashlib.sha512(hash_input.encode('utf-8')).hexdigest(),
                           query_hash_alg='SHA512')
        token = jwt.encode(payload, self.secret_key, algorithm='HS512')
        response = self.session.request(
            method, 'https://api.upbit.com/v1/' + path,
            headers={'Authorization': 'Bearer ' + token},
            # The encoded GET query is passed verbatim; JSON uses the same snapshot.
            **({'json': body} if method == 'POST' else {'params': encoded_query}),
            timeout=(3.05, 10), allow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
        if isinstance(result, dict) and 'error' in result:
            raise RuntimeError('Upbit rejected the request')
        return result

    def submit(self, side, ticker, value, identifier):
        if side not in ('bid', 'ask'):
            raise ValueError('Invalid order side')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('Order identifier is required')
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
