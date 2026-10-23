"""Actual PAPER storage and injected public I/O only; no network or user ledger."""
import copy
from email.utils import formatdate
import json
import os
import socket
import unittest
from unittest.mock import Mock, patch

from services.public_market import PublicMarketClient, PublicDataError
from ops.paper_recovery_diagnostic import diagnose
import tests.test_p10c_public_market as market_fixtures
import tests.test_p10c_paper_watch as watch_fixtures


# Reuse fixture methods without inheriting/re-running the existing test classes.
class FreshnessTests(unittest.TestCase):
    setUp=market_fixtures.PublicMarketTests.setUp

    def test_stale_tail_is_ranked_not_dropped_and_does_not_block_top(self):
        self.rows[0]['timestamp']-=900000
        self.rows[0]['trade_timestamp']-=900000
        self.assertEqual(self.client.top_tickers(2),['KRW-ETH','KRW-XRP'])
        self.rows[0]['acc_trade_price_24h']=40
        with self.assertRaisesRegex(PublicDataError,'PUBLIC_STALE_TICKER'):self.client.top_tickers(2)

    def test_top_stale_and_whole_ranking_stale_fail(self):
        for index in (1,2):
            old=self.rows[index]['timestamp'];self.rows[index]['timestamp']-=300001
            with self.assertRaisesRegex(PublicDataError,'PUBLIC_STALE_TICKER'):self.client.top_tickers(2)
            self.rows[index]['timestamp']=old
        for row in self.rows:row['timestamp']-=300001
        with self.assertRaises(PublicDataError):self.client.top_tickers(1)

    def test_invalid_lower_row_still_blocks_complete_ranking(self):
        self.rows[0]['timestamp']-=900000;self.rows[0]['acc_trade_price_24h']=None
        with self.assertRaises(PublicDataError):self.client.top_tickers(2)

    def test_scan_clock_discontinuity_and_expired_ranking(self):
        for times in ([0,31],[31,0]):
            self.client.monotonic=Mock(side_effect=times)
            with self.assertRaisesRegex(PublicDataError,'PUBLIC_RANKING_EXPIRED'):self.client.top_tickers(2)
        self.client.monotonic=Mock(side_effect=[0,1])
        clocks=[self.now]*7+[self.now+10]
        self.client.clock=Mock(side_effect=clocks)
        with self.assertRaisesRegex(PublicDataError,'PUBLIC_RANKING_EXPIRED'):self.client.top_tickers(2)

    def test_http_snapshot_date_and_mac_clock_errors(self):
        for delta in (-600,600,0,None):
            response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            response.status_code=200;response.headers={} if delta is None else {'Date':formatdate(self.now+delta,usegmt=True)}
            response.iter_content.return_value=[b'[{"market":"KRW-BTC"}]']
            session=Mock();session.get.return_value=response
            client=PublicMarketClient(session=session,clock=lambda:self.now)
            with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                if delta==0:self.assertEqual(client.get('market/all',{}),[{'market':'KRW-BTC'}])
                else:
                    with self.assertRaisesRegex(PublicDataError,'PUBLIC_RESPONSE_CLOCK_UNVERIFIED'):client.get('market/all',{})
            self.assertEqual(session.get.call_count,1)

    def test_stale_trade_timestamp_and_missing_time_fail_closed(self):
        for value in (self.now*1000-300001,None,True,self.now*1000+5001):
            self.rows[1]['trade_timestamp']=value
            with self.subTest(value=value),self.assertRaises(PublicDataError):self.client.top_tickers(2)

    def test_real_no_trade_minute_gap_stays_blocked_without_fabricated_bars(self):
        rows=market_fixtures.PublicMarketTests.candles(self)
        # A whole minute is absent, but 200 genuine candles remain; not a malformed
        # half-minute fixture. The strategy's equally-spaced contract is preserved.
        import pandas as pd
        for row in rows[10:]:
            row['candle_date_time_utc']=(pd.Timestamp(row['candle_date_time_utc'])-pd.Timedelta(minutes=1)).isoformat()
        self.client.get=Mock(return_value=rows)
        with self.assertRaisesRegex(PublicDataError,'PUBLIC_CANDLE_GAP'):self.client.candles('KRW-BTC')

    def test_complete_http_snapshot_not_just_selected_row_is_checked(self):
        def response(data,date):
            r=Mock();r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False)
            r.status_code=200;r.headers={'Date':formatdate(date,usegmt=True)}
            r.iter_content.return_value=[json.dumps(data).encode()]
            return r
        self.rows[0]['timestamp']-=900000;self.rows[0]['trade_timestamp']-=900000
        for age in (0,600):
            session=Mock();session.get.side_effect=[response(self.registry,self.now),response(self.rows,self.now-age)]
            client=PublicMarketClient(session=session,clock=lambda:self.now,sleep=lambda _:None)
            with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                if age:
                    with self.assertRaisesRegex(PublicDataError,'PUBLIC_RESPONSE_CLOCK_UNVERIFIED'):client.top_tickers(2)
                else:self.assertEqual(client.top_tickers(2),['KRW-ETH','KRW-XRP'])
            self.assertEqual(session.get.call_count,2)


class RecoveryTests(unittest.TestCase):
    setUp=watch_fixtures.PaperWatchTests.setUp
    factory=watch_fixtures.PaperWatchTests.factory
    run_session=watch_fixtures.PaperWatchTests.run_session

    def position(self):
        self.actions=['BUY'];self.run_session();self.stop.clear();self.now=0
        self.actions=[]
        return self.state.read_bytes()

    def test_discovery_failure_keeps_existing_btc_ledger_byte_identical(self):
        before=self.position()
        self.client.top_tickers.side_effect=PublicDataError('PUBLIC_STALE_TICKER')
        with patch('ops.paper_session.ExistingPaperStore.save',side_effect=AssertionError('No ledger save before discovery')) as save:
            result=self.run_session()
        save.assert_not_called()
        self.assertEqual(result['status'],'HALTED');self.assertFalse(result['engine_started'])
        self.assertEqual(result['reason'],'PUBLIC_STALE_TICKER')
        self.assertEqual(before,self.state.read_bytes())
        saved=json.loads(before);self.assertEqual(len(saved['orders']),1);self.assertEqual(__import__('decimal').Decimal(saved['cash']),600000)
        self.assertIsNone(saved['halt_reason'])

    def test_discovery_owns_original_lock_before_any_public_request(self):
        from ops.local_preflight import cold_lock
        def discover(n):
            with cold_lock(self.state) as owner:self.assertNotEqual(owner,'READY_IDLE_AT_SNAPSHOT')
            raise PublicDataError('PUBLIC_RATE_LIMIT')
        self.client.top_tickers.side_effect=discover
        before=self.state.read_bytes();self.run_session();self.assertEqual(before,self.state.read_bytes())

    def test_existing_halt_restart_blocked_without_discovery_or_implicit_open(self):
        self.position();saved=json.loads(self.state.read_bytes());saved['halt_reason']='PAPER_SESSION_FAILURE'
        self.state.write_text(json.dumps(saved));before=self.state.read_bytes()
        self.client.top_tickers.reset_mock()
        for _ in range(2):
            self.assertEqual(self.run_session()['status'],'HALTED')
            self.assertEqual(before,self.state.read_bytes())
        self.client.top_tickers.assert_not_called()

    @unittest.skipUnless(os.name=='posix','Read-only ownership probe not certified on Windows')
    def test_halted_reconciliation_backup_and_unproven_cause_never_authorize(self):
        self.position();backup=self.root/'backup.json';backup.write_bytes(self.state.read_bytes());backup.chmod(0o600)
        saved=json.loads(self.state.read_bytes());saved['halt_reason']='PAPER_SESSION_FAILURE'
        self.state.write_text(json.dumps(saved));before=self.state.read_bytes()
        r=diagnose(self.state,backup)
        self.assertEqual(r['ledger'],'RECONCILED_READ_ONLY');self.assertEqual(r['confirmed_buys'],1)
        self.assertEqual(r['pending_orders'],0);self.assertTrue(r['backup_accounting_equal'])
        self.assertFalse(r['backup_bytes_equal']);self.assertFalse(r['recovery_authorized'])
        self.assertEqual(r['cause'],'PENDING_EXTERNAL_SESSION_EVIDENCE_REQUIRED')
        self.assertEqual(r['status'],'HALTED');self.assertEqual(before,self.state.read_bytes())

    @unittest.skipUnless(os.name=='posix','Read-only ownership probe not certified on Windows')
    def test_healthy_ledger_is_not_a_recovery_approval(self):
        self.position();before=self.state.read_bytes();r=diagnose(self.state)
        self.assertEqual(r['status'],'NO_HALT_TO_RECOVER');self.assertFalse(r['restart_authorized'])
        self.assertEqual(before,self.state.read_bytes())

    def test_diagnostic_rejects_mismatch_pending_unknown_and_secrets(self):
        before=self.state.read_bytes()
        for issue in ('cash','unknown','pending','reason'):
            saved=json.loads(before)
            if issue=='cash':saved['cash']='1'
            elif issue=='reason':saved['halt_reason']='private-secret-canary'
            else:saved['orders']={'id':{'identifier':'id','ticker':'KRW-BTC','side':'bid','averaging':False,
                  'uuid':None,'status':'UNKNOWN' if issue=='unknown' else 'intent','value':'10000'}}
            self.state.write_text(json.dumps(saved));raw=self.state.read_bytes();r=diagnose(self.state)
            self.assertFalse(r['recovery_authorized']);self.assertEqual(raw,self.state.read_bytes())
            self.assertNotIn('private-secret-canary',json.dumps(r))
            if issue!='reason':self.assertNotEqual(r['ledger'],'RECONCILED_READ_ONLY')

    @unittest.skipUnless(os.name=='posix','Read-only ownership probe not certified on Windows')
    def test_busy_diagnostic_does_not_read_or_repair_ledger(self):
        from trading.execution import ExecutionEngine
        engine=ExecutionEngine(self.state,1000000);engine.open()
        try:
            before=self.state.read_bytes();r=diagnose(self.state)
            self.assertNotEqual(r['ownership'],'READY_IDLE_AT_SNAPSHOT')
            self.assertNotIn('cash',r);self.assertEqual(before,self.state.read_bytes())
        finally:engine.close()

    @unittest.skipUnless(os.name=='posix','Read-only ownership probe not certified on Windows')
    def test_corrupt_backup_cannot_authorize_or_repair_valid_state(self):
        self.position();backup=self.root/'backup.json';backup.write_text('{private-secret-canary');backup.chmod(0o600)
        before=self.state.read_bytes();r=diagnose(self.state,backup)
        self.assertEqual(r['status'],'HALTED');self.assertFalse(r['recovery_authorized'])
        self.assertEqual(before,self.state.read_bytes());self.assertNotIn('private-secret-canary',json.dumps(r))

    def test_runtime_failure_still_persists_halt_and_blocks_restart(self):
        self.position();self.stream_error=OSError('private-secret-canary')
        first=self.run_session();self.assertTrue(first['engine_started']);self.assertTrue(first['halted'])
        before=self.state.read_bytes();self.client.top_tickers.reset_mock();self.stream_error=None
        second=self.run_session()
        self.assertEqual(second['status'],'HALTED');self.assertEqual(before,self.state.read_bytes())
        self.client.top_tickers.assert_not_called()
