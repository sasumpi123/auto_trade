"""Offline signature verification against requests.PreparedRequest contents."""
import hashlib
import json
import socket
import unittest
from decimal import Decimal
from urllib.parse import unquote, urlsplit
import uuid
from unittest.mock import patch

import jwt
import requests

from services.upbit_broker import UpbitBroker, request_parameters

# Deterministic, fake test values only. No environment variables or real keys.
FAKE_ACCESS = 'offline-test-access'
FAKE_SECRET = 'offline-test-secret-raw-not-base64-' * 3


class RecordingSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.options = []
        self.trust_env = False

    def send(self, request, **kwargs):
        # Actual requests encoding/preparation, but no transport or socket use.
        self.requests.append(request)
        self.options.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        response.request = request
        return response


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden'))
        cls.network.start()

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()

    def setUp(self):
        self.session = RecordingSession()
        self.addCleanup(self.session.close)
        self.broker = UpbitBroker(FAKE_ACCESS, FAKE_SECRET, enabled=True, session=self.session)

    def token(self, request=None):
        request = request or self.session.requests[-1]
        header = request.headers['Authorization']
        self.assertTrue(header.startswith('Bearer '))
        token = header.removeprefix('Bearer ')
        self.assertEqual(jwt.get_unverified_header(token)['alg'], 'HS512')
        return token, jwt.decode(token, FAKE_SECRET, algorithms=['HS512'])

    def assert_hash(self, expected):
        _, payload = self.token()
        self.assertEqual(payload['query_hash'], hashlib.sha512(expected.encode('utf-8')).hexdigest())
        self.assertEqual(payload['query_hash_alg'], 'SHA512')
        self.assertEqual(payload['access_key'], FAKE_ACCESS)
        self.assertEqual(str(uuid.UUID(payload['nonce'])), payload['nonce'])

    def test_hs512_signature_verifies_with_raw_secret_and_rejects_wrong_key(self):
        self.broker.balances()
        token, payload = self.token()
        self.assertEqual(set(payload), {'access_key', 'nonce'})
        with self.assertRaises(jwt.InvalidSignatureError):
            jwt.decode(token, 'another-fake-key-' * 5, algorithms=['HS512'])
        with self.assertRaises(jwt.InvalidAlgorithmError):
            jwt.decode(token, FAKE_SECRET, algorithms=['HS256'])

    def test_nonce_is_unique_for_identical_requests(self):
        self.broker.balances()
        _, first = self.token()
        self.broker.balances()
        _, second = self.token()
        self.assertNotEqual(first['nonce'], second['nonce'])

    def test_get_array_and_order_match_actual_wire_query(self):
        params = {'market': 'KRW-BTC', 'states[]': ['wait', 'watch'], 'limit': 10, 'page': 2}
        self.broker._request('GET', 'orders/open', params)
        request = self.session.requests[-1]
        query = urlsplit(request.url).query
        expected = 'market=KRW-BTC&states[]=wait&states[]=watch&limit=10&page=2'
        self.assertEqual(unquote(query), expected)
        self.assertIsNone(request.body)
        self.assert_hash(expected)
        self.assertEqual(self.session.options[-1]['timeout'], (3.05, 10))
        self.assertFalse(self.session.options[-1]['allow_redirects'])

    def test_get_uuid_or_identifier_hash_matches_wire(self):
        for kwargs, expected in [({'uuid': 'fake-uuid'}, 'uuid=fake-uuid'),
                                 ({'identifier': 'fake-identifier'}, 'identifier=fake-identifier')]:
            with self.subTest(kwargs=kwargs):
                self.broker.order(**kwargs)
                self.assertEqual(unquote(urlsplit(self.session.requests[-1].url).query), expected)
                self.assert_hash(expected)

    def test_no_params_omits_hash_fields_and_query(self):
        self.broker.balances()
        _, payload = self.token()
        self.assertNotIn('query_hash', payload)
        self.assertNotIn('query_hash_alg', payload)
        self.assertEqual(urlsplit(self.session.requests[-1].url).query, '')

    def test_post_bid_hash_matches_transmitted_json_field_order(self):
        self.broker.submit('bid', 'KRW-BTC', Decimal('12345.6700'), 'test-bid')
        request = self.session.requests[-1]
        body = json.loads(request.body)
        expected = 'market=KRW-BTC&side=bid&identifier=test-bid&ord_type=price&price=12345.6700'
        self.assertEqual('&'.join(f'{key}={value}' for key, value in body.items()), expected)
        self.assertEqual(body['price'], '12345.6700')
        self.assertNotIn('volume', body)
        self.assertEqual(request.headers['Content-Type'], 'application/json')
        self.assertEqual(urlsplit(request.url).query, '')
        self.assert_hash(expected)

    def test_post_ask_hash_matches_transmitted_json_no_price(self):
        self.broker.submit('ask', 'KRW-ETH', Decimal('0.00001000'), 'test-ask')
        body = json.loads(self.session.requests[-1].body)
        expected = 'market=KRW-ETH&side=ask&identifier=test-ask&ord_type=market&volume=0.00001000'
        self.assertEqual('&'.join(f'{key}={value}' for key, value in body.items()), expected)
        self.assertNotIn('price', body)
        self.assert_hash(expected)

    def test_post_hash_is_not_json_bytes_hash(self):
        self.broker.submit('bid', 'KRW-BTC', 10000, 'test-json')
        _, payload = self.token()
        self.assertNotEqual(payload['query_hash'], hashlib.sha512(self.session.requests[-1].body).hexdigest())

    def test_session_default_query_parameters_cannot_change_signed_request(self):
        self.session.params = {'unexpected': 'unsigned-session-default'}
        with self.assertRaises(ValueError):
            self.broker.order(identifier='test-session')
        with self.assertRaises(ValueError):
            self.broker.submit('bid', 'KRW-BTC', 10000, 'test-session')
        self.assertEqual(self.session.requests, [])

    def test_parameter_order_is_preserved_not_sorted(self):
        self.broker._request('GET', 'order', {'identifier': 'test-order', 'uuid': 'test-uuid'})
        self.assert_hash('identifier=test-order&uuid=test-uuid')
        self.broker._request('GET', 'order', {'uuid': 'test-uuid', 'identifier': 'test-order'})
        self.assert_hash('uuid=test-uuid&identifier=test-order')

    def test_unicode_and_encoded_characters_follow_official_unquote_convention(self):
        self.broker.order(identifier='test +/%=&한글')
        query = urlsplit(self.session.requests[-1].url).query
        self.assertIn('%2B', query)
        self.assertIn('%26', query)
        # urlencode uses + for spaces; unquote deliberately does not turn + into spaces.
        expected = 'identifier=test++/%=&한글'
        self.assertEqual(unquote(query), expected)
        self.assert_hash(expected)

    def test_post_array_and_numeric_values_use_same_frozen_snapshot(self):
        params = {'states[]': ['wait', 'watch'], 'page': 1, 'price': Decimal('100.00')}
        snapshot, encoded, decoded = request_parameters(params)
        params['states[]'].append('changed-after-snapshot')
        self.assertEqual(snapshot['states[]'], ['wait', 'watch'])
        self.assertEqual(snapshot['page'], '1')
        self.assertEqual(snapshot['price'], '100.00')
        self.assertEqual(decoded, 'states[]=wait&states[]=watch&page=1&price=100.00')
        self.broker._request('POST', 'orders', snapshot)
        body = json.loads(self.session.requests[-1].body)
        self.assertEqual(body, snapshot)
        self.assert_hash(decoded)

    def test_ambiguous_parameter_types_fail_before_transport(self):
        invalid = [{'price': None}, {'price': 1.1}, {'price': True}, {'price': {'nested': 'bad'}},
                   {'states[]': []}, {'states': ['wait']}, {'states[]': [['nested']]},
                   {'price': Decimal('NaN')}, {'': 'bad'}, {1: 'bad'}]
        for params in invalid:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    self.broker._request('POST', 'orders', params)
        self.assertEqual(self.session.requests, [])

    def test_auth_error_and_invalid_json_are_not_retried(self):
        for content, status in [(b'{"error":{"name":"invalid_query_payload"}}', 200),
                                (b'{}', 401), (b'invalid json', 200)]:
            with self.subTest(status=status, content=content):
                def fake_send(request, **kwargs):
                    self.session.requests.append(request)
                    response = requests.Response()
                    response.status_code = status
                    response._content = content
                    response.request = request
                    return response
                previous = len(self.session.requests)
                with patch.object(self.session, 'send', side_effect=fake_send):
                    with self.assertRaises((RuntimeError, requests.RequestException)):
                        self.broker.balances()
                self.assertEqual(len(self.session.requests), previous + 1)

    def test_unsupported_operation_and_side_fail_before_transport(self):
        with self.assertRaises(ValueError):
            self.broker._request('DELETE', 'orders')
        with self.assertRaises(ValueError):
            self.broker.submit('sell', 'KRW-BTC', 1, 'test-id')
        with self.assertRaises(ValueError):
            self.broker.submit('bid', 'KRW-BTC', 1, '')
        self.assertEqual(self.session.requests, [])


if __name__ == '__main__':
    unittest.main()
