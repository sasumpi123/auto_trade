"""Public 24h ranking contract and strict bounded GET transport, all offline."""
import copy
from email.utils import formatdate
import json
import time
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests
from services.public_market import PublicMarketClient, PublicDataError, PublicOnlyAuth, get_top_tickers


class PublicMarketTests(unittest.TestCase):
    def setUp(self):
        self.now=1800000000
        self.client=PublicMarketClient(clock=lambda:self.now,sleep=lambda _:None)
        self.addCleanup(self.client.close)
        self.registry=[{'market':c} for c in ('KRW-BTC','KRW-ETH','KRW-XRP','BTC-ETH')]
        self.rows=[{'market':c,'trade_price':1,'acc_trade_price_24h':n,'timestamp':self.now*1000,'trade_timestamp':self.now*1000}
                   for c,n in [('KRW-BTC',10),('KRW-ETH',30),('KRW-XRP',20)]]
        self.client.get=Mock(side_effect=lambda endpoint,params:self.registry if endpoint=='market/all' else self.rows)

    def test_rolling_24h_batch_ranking_not_day_candle(self):
        self.assertEqual(self.client.top_tickers(2),['KRW-ETH','KRW-XRP'])
        self.assertEqual(self.client.get.call_count,2)
        self.assertEqual(self.client.get.call_args.args[0],'ticker')
        self.assertEqual(set(self.client.get.call_args.args[1]['markets'].split(',')),{'KRW-BTC','KRW-ETH','KRW-XRP'})

    def test_tied_turnover_has_stable_market_order(self):
        for row in self.rows:row['acc_trade_price_24h']=10
        self.assertEqual(self.client.top_tickers(3),['KRW-BTC','KRW-ETH','KRW-XRP'])

    def test_missing_duplicate_or_unknown_market_fails_closed(self):
        for data in (self.rows[:-1],self.rows+[self.rows[0]],self.rows+[{'market':'KRW-OTHER'}]):
            with self.subTest(data=len(data)):
                self.client.get=Mock(side_effect=[self.registry,data])
                with self.assertRaises(PublicDataError):self.client.top_tickers(2)

    def test_invalid_turnover_price_time_and_nonfinite_fail(self):
        for key,value in [('acc_trade_price_24h',None),('acc_trade_price_24h',-1),('acc_trade_price_24h','NaN'),
                          ('acc_trade_price_24h','1e999'),('trade_price',True),('trade_price',0),
                          ('timestamp',0),('timestamp',self.now*1000+5001)]:
            rows=copy.deepcopy(self.rows);rows[0][key]=value
            self.client.get=Mock(side_effect=[self.registry,rows])
            with self.subTest(key=key,value=value),self.assertRaises(PublicDataError):self.client.top_tickers(2)

    def test_duplicate_registry_and_invalid_limit_fail_before_tickers(self):
        self.registry.append(self.registry[0])
        with self.assertRaises(PublicDataError):self.client.top_tickers(2)
        for limit in (0,101,True,'10'):
            with self.assertRaises(PublicDataError):self.client.top_tickers(limit)

    def test_more_than_100_markets_are_chunked_and_complete(self):
        codes=['KRW-C'+str(i) for i in range(205)]
        def data(endpoint,params):
            if endpoint=='market/all':return [{'market':c} for c in codes]
            return [{'market':c,'trade_price':1,'acc_trade_price_24h':int(c[5:]),'timestamp':self.now*1000,'trade_timestamp':self.now*1000}
                    for c in params['markets'].split(',')]
        self.client.get=Mock(side_effect=data)
        self.assertEqual(self.client.top_tickers(1),['KRW-C204'])
        self.assertEqual(self.client.get.call_count,4)

    def test_transport_429_http_bad_json_and_size_never_retries(self):
        for status,body in [(429,b'private-canary'),(403,b'private-canary'),(302,b'private-canary'),
                            (200,b'{private-canary'),(200,b'{}'),(200,b'['+b' '*2_000_000+b']')]:
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.status_code=status;response.headers={'Date':formatdate(time.time(),usegmt=True)}
            response.iter_content.return_value=[body]
            session=Mock();session.get.return_value=response
            c=PublicMarketClient(session=session)
            with self.subTest(status=status),self.assertRaises(PublicDataError) as caught:c.get('market/all',{})
            self.assertNotIn('private-canary',str(caught.exception));self.assertEqual(session.get.call_count,1)
            self.assertFalse(session.get.call_args.kwargs['allow_redirects'])
            self.assertIsInstance(session.get.call_args.kwargs['auth'],PublicOnlyAuth)

    def test_no_ambient_netrc_authorization_or_cookie(self):
        session=requests.Session()
        with patch('requests.sessions.get_netrc_auth',side_effect=AssertionError('No credential access')):
            prepared=session.prepare_request(requests.Request('GET','https://api.upbit.com/v1/market/all',
                headers={'Authorization':'private-canary','Cookie':'private-canary'},auth=PublicOnlyAuth()))
        self.assertNotIn('Authorization',prepared.headers);self.assertNotIn('Cookie',prepared.headers)

    def test_stop_deadline_and_endpoint_reject_before_network(self):
        session=Mock()
        for client in (PublicMarketClient(session=session,deadline=0),PublicMarketClient(session=session,stopped=lambda:True)):
            with self.assertRaises(PublicDataError):client.get('market/all',{})
        with self.assertRaises(PublicDataError):PublicMarketClient(session=session).get('accounts',{})
        session.get.assert_not_called()

    def candles(self):
        start=pd.Timestamp(self.now,unit='s').floor('min')
        return [{'market':'KRW-BTC','unit':1,'candle_date_time_utc':(start-pd.Timedelta(minutes=i)).isoformat(),
                 'opening_price':100,'high_price':101,'low_price':99,'trade_price':100,
                 'candle_acc_trade_volume':10,'candle_acc_trade_price':1000} for i in range(200)]

    def test_public_candles_preserve_existing_index_and_columns(self):
        self.client.get=Mock(return_value=self.candles())
        frame=self.client.candles('KRW-BTC')
        self.assertEqual(len(frame),200);self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertIsNone(frame.index.tz);self.assertEqual(frame.index[-1].hour,((self.now//3600)%24+9)%24)
        self.assertEqual(list(frame.columns),['open','high','low','close','volume','value'])

    def test_candle_gaps_duplicates_wrong_markets_and_nan_blocked(self):
        for variant in ('gap','duplicate','market','nan','missing'):
            rows=self.candles()
            if variant=='gap':rows[10]['candle_date_time_utc']=(pd.Timestamp(rows[10]['candle_date_time_utc'])-pd.Timedelta(seconds=30)).isoformat()
            if variant=='duplicate':rows[1]=rows[0]
            if variant=='market':rows[0]['market']='KRW-ETH'
            if variant=='nan':rows[0]['trade_price']='NaN'
            if variant=='missing':rows.pop()
            self.client.get=Mock(return_value=rows)
            with self.subTest(variant=variant),self.assertRaises(PublicDataError):self.client.candles('KRW-BTC')


    def test_config_discovery_wrapper_uses_injected_public_client_not_pyupbit(self):
        import config
        client=Mock();client.top_tickers.return_value=['KRW-BTC']
        with patch.object(config.pyupbit,'get_ohlcv',side_effect=AssertionError('No per-market discovery candles')):
            self.assertEqual(config.get_top_tickers(1,client=client),['KRW-BTC'])
        client.top_tickers.assert_called_once_with(1);client.close.assert_not_called()
