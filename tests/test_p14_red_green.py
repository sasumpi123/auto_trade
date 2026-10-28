"""Same finite market fixture must be RED on pinned master and GREEN on the fix."""
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from tests.p14_market_replay import MinuteFixture,FIXTURE
from tests.p14_source_fixture import baseline_tree,ROOT


class UpbitAccumulationFixtureTests(unittest.TestCase):
    def test_api_shape_utc_kst_and_real_trade_accumulation_model(self):
        spec=json.loads(FIXTURE.read_text());f=MinuteFixture(spec,'forming_only')
        initial=f.row(0);f.elapsed=29;middle=f.row(0);f.elapsed=59;complete=f.row(0)
        self.assertEqual(initial['candle_acc_trade_volume'],1.04)
        self.assertLess(initial['candle_acc_trade_volume'],middle['candle_acc_trade_volume'])
        self.assertLess(middle['candle_acc_trade_volume'],complete['candle_acc_trade_volume'])
        self.assertEqual(complete['candle_acc_trade_volume'],100)
        self.assertEqual(complete['timestamp'],int((f.anchor+59.75)*1000))
        self.assertEqual(set(initial),{'market','unit','candle_date_time_utc','candle_date_time_kst','opening_price','high_price',
            'low_price','trade_price','timestamp','candle_acc_trade_price','candle_acc_trade_volume'})
        self.assertEqual(initial['candle_date_time_utc'],'2026-10-28T00:00:00')
        self.assertEqual(initial['candle_date_time_kst'],'2026-10-28T09:00:00')
        self.assertEqual(spec['origin'],'SYNTHETIC_NOT_CAPTURED_FROM_MAC_OR_UPBIT')
    def test_small_local_clock_lead_cannot_turn_exchange_forming_bar_into_completed(self):
        from services.public_market import PublicMarketClient,PublicDataError
        f=MinuteFixture(json.loads(FIXTURE.read_text()),'forming_only');f.elapsed=58
        c=PublicMarketClient(session=f.session(),clock=lambda:f.clock()+4,monotonic=lambda:f.elapsed,
            sleep=lambda _:None,deadline=120,max_calls=64)
        try:
            with self.assertRaisesRegex(PublicDataError,'PUBLIC_RESPONSE_CLOCK_UNVERIFIED'):c.candles('KRW-BTC')
        finally:c.close()

    def test_exclusive_to_changes_only_selected_window_not_historical_candles(self):
        f=MinuteFixture(json.loads(FIXTURE.read_text()),'forming_only')
        latest=f.rows({'market':'KRW-BTC','count':200})
        completed=f.rows({'market':'KRW-BTC','count':200,'to':'2026-10-28T00:00:00+00:00'})
        self.assertEqual(latest[0]['candle_acc_trade_volume'],1.04)
        self.assertEqual(completed[0]['candle_acc_trade_volume'],100)
        self.assertEqual(latest[1:],completed[:-1])


@unittest.skipUnless(os.name=='posix','Existing private PAPER lifecycle requires Mac/Linux; no Windows bypass')
class BeforeAfterRuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script=Path(__file__).with_name('p14_market_replay.py').resolve()
        def execute(source):
            r=subprocess.run([sys.executable,str(script),str(source)],cwd=source,capture_output=True,text=True,timeout=90)
            try:result=json.loads(r.stdout)
            except ValueError:raise AssertionError('Replay must emit structured evidence, not count a runtime error as RED') from None
            return r.returncode,result
        with baseline_tree() as (source,hashes):cls.before_code,cls.before=execute(source);cls.source_hashes=hashes
        cls.after_code,cls.after=execute(ROOT)
    def test_identical_success_contract_fails_before_and_passes_after(self):
        self.assertEqual(self.before_code,1);self.assertFalse(self.before['contract_passed'])
        self.assertEqual(len(self.before['violations']),6)
        self.assertIn('forming_only: strategy_evaluations must be > 0 on normal completed candles',self.before['violations'])
        self.assertIn('cache_only: old volume must not gate newer eligible completed candles',self.before['violations'])
        self.assertEqual(self.after_code,0);self.assertTrue(self.after['contract_passed']);self.assertEqual(self.after['violations'],[])
        self.assertEqual(self.source_hashes,json.loads(FIXTURE.read_text())['baseline_source_sha256'])
    def test_baseline_reproduces_all_1085_volume_gates_numerically(self):
        old=self.before['cases']['repeated_1085'];new=self.after['cases']['repeated_1085']
        self.assertEqual((old['counts']['stream_messages_received'],old['counts']['timing_accepted'],old['counts']['analyzer_calls']),(1085,1085,1085))
        self.assertEqual((old['volume_gate'],old['counts']['strategy_evaluations'],old['buys'],old['sells']),(1085,0,0,0))
        self.assertEqual((new['counts']['stream_messages_received'],new['counts']['strategy_evaluations'],new['volume_gate']),(1085,11,0))
        self.assertEqual(new['counts']['analyzer_calls'],11);self.assertEqual(new['rest_calls'],11);self.assertLessEqual(new['rest_calls'],64)
    def test_forming_candle_problem_separate_from_five_minute_refresh(self):
        old=self.before['cases']['forming_only']['first_evaluation_input']
        new=self.after['cases']['forming_only']['first_evaluation_input']
        self.assertEqual(old['open_utc'],'2026-10-28T00:00:00+00:00')
        self.assertEqual(old['cached_volume'],1.04);self.assertAlmostEqual(old['mean_20'],95.052)
        self.assertLess(old['cached_volume'],old['mean_20']*.5);self.assertAlmostEqual(old['mean_20']*.5,47.526)
        self.assertEqual(old['latest_completed_volume_on_server'],100)
        self.assertEqual(new['open_utc'],'2026-10-27T23:59:00+00:00');self.assertEqual(new['cached_volume'],100)
        self.assertEqual(new['mean_20'],100);self.assertEqual(new['action'],'HOLD')  # No forced BUY.
    def test_completed_query_control_still_exposes_old_cache_stall(self):
        old=self.before['cases']['cache_only'];new=self.after['cases']['cache_only']
        self.assertEqual(old['control_to_overrides'],2);self.assertEqual(new['control_to_overrides'],0)
        self.assertEqual(old['first_evaluation_input']['open_utc'],new['first_evaluation_input']['open_utc'])
        self.assertEqual(old['first_evaluation_input']['cached_volume'],1)
        self.assertEqual((old['volume_gate'],old['counts']['strategy_evaluations']),(16,1))
        self.assertEqual(old['stale_gate_despite_eligible_completed_volume'],12)
        stale=old['first_stale_gate'];self.assertEqual(stale['elapsed'],61)
        self.assertEqual(stale['cached_volume'],1);self.assertEqual(stale['latest_completed_volume_on_server'],100)
        self.assertEqual((new['volume_gate'],new['counts']['strategy_evaluations']),(1,5))
        self.assertEqual(new['stale_gate_despite_eligible_completed_volume'],0)
    def test_genuinely_low_completed_volume_must_still_block(self):
        new=self.after['cases']['legitimate_low_completed'];basis=new['first_evaluation_input']
        self.assertEqual(basis['cached_volume'],1);self.assertEqual(basis['mean_20'],95.05)
        self.assertEqual((new['volume_gate'],new['counts']['strategy_evaluations']),(1,0))
    def test_rest_real_analyzer_strategy_paper_and_fee_accounting_round_trip(self):
        old=self.before['cases']['paper_round_trip'];new=self.after['cases']['paper_round_trip']
        self.assertEqual((old['buys'],old['sells']),(0,0));self.assertEqual((new['buys'],new['sells']),(1,1))
        self.assertEqual(new['counts']['buy_engine_attempts'],1);self.assertEqual(new['counts']['sell_engine_attempts'],1)
        self.assertEqual(Decimal(new['net_realized_pnl']),20000)
        self.assertEqual(Decimal(new['cash']),1020000);self.assertEqual(Decimal(new['fees']),0)
        self.assertEqual(new['pending_orders'],0);self.assertEqual(new['post_validation'],'VERIFIED_UNDER_OWNER_LOCK')
        self.assertEqual(new['terminal'],'STOPPED_VERIFIED')
        self.assertEqual(new['fixture_origin'],'SYNTHETIC_NOT_CAPTURED_FROM_MAC_OR_UPBIT')
