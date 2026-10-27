"""Synthetic REST fixtures and offline PAPER only; no actual API/credentials."""
import io
import json
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from backtesting.minute_study import collect,plan,load_sample,analyze,main,MinuteError,MAX_CALLS,ENDPOINT,canonical,sha
from backtesting.data import demo_candles
from backtesting.collection import write_json


class MinuteStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.data=self.root/'sample'
        self.start=pd.Timestamp('2026-10-23T00:00:00Z');self.end=self.start+pd.Timedelta(minutes=240)
        self.now=pd.Timestamp('2026-10-24T00:00:00Z').timestamp()
        self.frame=demo_candles(240);self.frame.index=pd.date_range(self.start,periods=240,freq='min')
        self.calls=[];self.mutate=None;self.close_fail=False;owner=self
        class FakeREST:
            def get(self,endpoint,params):
                owner.calls.append((endpoint,params.copy()))
                f=owner.frame.loc[(owner.frame.index<pd.Timestamp(params['to'])) & (owner.frame.index>=owner.start)].iloc[-params['count']:]
                rows=[{'market':params['market'],'unit':1,'candle_date_time_utc':stamp.tz_localize(None).isoformat(),
                    'opening_price':float(r.open),'high_price':float(r.high),'low_price':float(r.low),'trade_price':float(r.close),'candle_acc_trade_volume':float(r.volume)} for stamp,r in f.iloc[::-1].iterrows()]
                return owner.mutate(rows) if owner.mutate else rows
            def close(self):
                if owner.close_fail:raise OSError('private-close-canary')
        self.client=FakeREST()
        blocker=patch.object(socket.socket,'connect',side_effect=AssertionError('No actual network'))
        blocker.start();self.addCleanup(blocker.stop)
    def acquire(self,**kwargs):
        return collect(self.data,['KRW-BTC'],self.start,self.end,connect_public=True,client=self.client,
            clock=lambda:self.now,monotonic=lambda:0,**kwargs)
    def edit_manifest(self,fn):
        p=self.data/'manifest.json';m=json.loads(p.read_text());fn(m);write_json(p,m)
    def test_default_offline_no_files_no_session_no_request(self):
        with patch('backtesting.minute_study.PublicMarketClient',side_effect=AssertionError('No client')):
            r=collect(self.data,['KRW-BTC'],self.start,self.end,clock=lambda:self.now)
        self.assertEqual(r['status'],'OFFLINE_PLAN_ONLY');self.assertEqual(r['network_calls'],0)
        self.assertFalse(self.data.exists());self.assertEqual(self.calls,[])
    def test_frozen_bounds_future_sealed_and_duplicate_markets_before_network(self):
        bad=[(['KRW-BTC']*2,self.start,self.end),(['KRW-USDT'],self.start,self.end),
            (['KRW-BTC'],self.start,self.start+pd.Timedelta(minutes=200)),
            (['KRW-BTC'],self.start,self.start+pd.Timedelta(days=2)),
            (['KRW-BTC'],pd.Timestamp('2026-10-20T00:00Z'),pd.Timestamp('2026-10-20T06:00Z')),
            (['KRW-BTC'],pd.Timestamp('2026-10-24T00:00Z'),pd.Timestamp('2026-10-24T06:00Z'))]
        for markets,start,end in bad:
            with self.assertRaises(ValueError):plan(markets,start,end,now=pd.Timestamp(self.now,unit='s',tz='UTC'))
        self.assertFalse(self.data.exists());self.assertEqual(self.calls,[])
    def test_two_bounded_pages_sha_quality_utc_and_synthetic_label(self):
        r=self.acquire();self.assertEqual(r['status'],'COLLECTED_DEVELOPMENT_SAMPLE')
        self.assertEqual(len(self.calls),2);self.assertTrue(all(c[0]=='candles/minutes/1' for c in self.calls))
        self.assertEqual([c[1]['count'] for c in self.calls],[200,40])
        self.assertEqual(r['source'],'SYNTHETIC_TEST_TRANSPORT')
        m,frames=load_sample(self.data,allow_synthetic=True)
        self.assertEqual(len(frames['KRW-BTC']),240);self.assertEqual(str(frames['KRW-BTC'].index.tz),'UTC')
        self.assertFalse(m['plan']['sealed_holdout_overlap'])
        with self.assertRaisesRegex(MinuteError,'REAL_SOURCE_UNVERIFIED'):load_sample(self.data)
    def test_gaps_remain_missing_and_cannot_generate_performance(self):
        self.frame=self.frame.drop(self.frame.index[100]);r=self.acquire()
        self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA')
        self.assertEqual(r['datasets']['KRW-BTC']['quality']['missing'],1)
        with self.assertRaises(ValueError):analyze(self.data,self.root/'report',allow_synthetic=True)
        self.assertFalse((self.root/'report').exists())
    def test_duplicate_unordered_wrong_market_and_future_responses_fail_closed(self):
        mutators=[lambda r:[r[0],r[0],*r[2:]],lambda r:list(reversed(r)),
            lambda r:[{**r[0],'market':'KRW-ETH'},*r[1:]],
            lambda r:[{**r[0],'candle_date_time_utc':self.end.tz_localize(None).isoformat()},*r[1:]]]
        for i,fn in enumerate(mutators):
            self.data=self.root/f'bad-{i}';self.mutate=fn;r=self.acquire()
            self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA');self.assertFalse(r['complete'])
            self.assertTrue((self.data/'INCOMPLETE').exists())
    def test_invalid_prices_volumes_and_ohlc_are_not_repaired(self):
        for i,(field,value) in enumerate((('trade_price',-1),('trade_price',float('nan')),('candle_acc_trade_volume',-1),('high_price',1))):
            self.data=self.root/f'price-{i}';self.mutate=lambda rows,f=field,v=value:[{**rows[0],f:v},*rows[1:]]
            self.assertEqual(self.acquire()['status'],'INSUFFICIENT_REAL_DATA')
    def test_request_budget_429_and_timeout_do_not_retry(self):
        from services.public_market import PublicDataError
        for i,reason in enumerate(('PUBLIC_RATE_LIMIT','PUBLIC_REQUEST_FAILED')):
            self.data=self.root/f'fail-{i}'
            with patch.object(self.client,'get',side_effect=PublicDataError(reason)) as get:
                r=self.acquire();self.assertEqual(r['network_calls'],1);self.assertEqual(get.call_count,1)
                self.assertEqual(r['reason'],reason)
        self.data=self.root/'budget'
        with patch('backtesting.minute_study.MAX_CALLS',1):r=self.acquire()
        self.assertEqual(r['network_calls'],1);self.assertEqual(r['reason'],'REQUEST_OR_DEADLINE_LIMIT')
    def test_sha_audit_and_manifest_tamper_cannot_become_ready(self):
        self.acquire();csv=self.data/'KRW-BTC-1.csv';raw=csv.read_bytes();csv.write_bytes(raw+b'\n')
        with self.assertRaisesRegex(MinuteError,'FILE_SHA_MISMATCH'):load_sample(self.data,allow_synthetic=True)
        csv.write_bytes(raw)
        self.edit_manifest(lambda m:m['requests'][0].update(endpoint='https://private.invalid/orders'))
        with self.assertRaisesRegex(MinuteError,'AUDIT_UNVERIFIED'):load_sample(self.data,allow_synthetic=True)
    def test_existing_output_operational_path_symlink_and_close_failure(self):
        self.acquire();before=(self.data/'manifest.json').read_bytes()
        with self.assertRaises(MinuteError):self.acquire()
        self.assertEqual(before,(self.data/'manifest.json').read_bytes())
        with self.assertRaises(MinuteError):collect(self.root/'runtime'/'new',['KRW-BTC'],self.start,self.end,
            connect_public=True,client=self.client,clock=lambda:self.now)
        self.data=self.root/'close-failure';self.close_fail=True;r=self.acquire()
        self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA');self.assertTrue((self.data/'INCOMPLETE').exists())
        self.assertNotIn('private-close-canary',json.dumps(r))
    def test_minute_cooldown_real_signal_paper_costs_and_reports_use_synthetic_source(self):
        self.acquire();report=analyze(self.data,self.root/'analysis',allow_synthetic=True)
        self.assertEqual(len(report['comparisons']),36)
        self.assertEqual(report['production_minute1_status'],'SYNTHETIC_ONLY_REAL_DATA_INSUFFICIENT')
        self.assertTrue(all(r['accounting_verified'] for r in report['comparisons']))
        self.assertTrue(any(r['counts']['cooldown_blocked']>0 for r in report['comparisons']))
        self.assertTrue(any(r['fills']['buys']>0 for r in report['comparisons']))
        self.assertTrue(any(r['fills']['sells']>0 for r in report['comparisons']))
        self.assertTrue((self.root/'analysis'/'metrics.csv').exists())
        text=(self.root/'analysis'/'REPORT.md').read_text();self.assertIn('SYNTHETIC_TEST_TRANSPORT',text)
        self.assertIn('Cooldown',text);self.assertNotIn('Holdout rows evaluated: 0.',text)
    def test_zero_trade_valid_strategy_returns_na_not_fabricated_profit(self):
        self.acquire();r=analyze(self.data,self.root/'zero',allow_synthetic=True)
        zero=[x for x in r['comparisons'] if sum(x['fills'].values())==0]
        self.assertTrue(zero)
        self.assertTrue(all(x['metrics']['net_return_pct'] is None and x['counts']['evaluations']>0 for x in zero))
    def test_no_evaluable_indicators_is_insufficient_not_success(self):
        for col in ('open','close'):self.frame[col]=10000.
        self.frame['high']=10010.;self.frame['low']=9990.
        self.acquire()
        with self.assertRaisesRegex(MinuteError,'INSUFFICIENT_EVALUABLE_MINUTES'):analyze(self.data,self.root/'no-features',allow_synthetic=True)
        self.assertTrue((self.root/'no-features'/'INCOMPLETE').exists())
    def test_cli_arguments_and_offline_analysis_never_enable_network(self):
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):main([])
        self.acquire()
        with patch('sys.stdout',new_callable=io.StringIO) as out:
            self.assertEqual(main(['analyze','--data',str(self.data),'--output',str(self.root/'no-real')]),2)
        self.assertEqual(json.loads(out.getvalue())['status'],'INSUFFICIENT_REAL_DATA')

    def test_deadline_and_clock_jump_stop_after_first_request(self):
        base=self.client.get
        for i,wall_jump in enumerate((0,20)):
            self.data=self.root/f'clock-{i}';elapsed=[0]
            def get(endpoint,params):
                rows=base(endpoint,params);elapsed[0]=121 if wall_jump==0 else 1;return rows
            with patch.object(self.client,'get',side_effect=get):
                r=collect(self.data,['KRW-BTC'],self.start,self.end,connect_public=True,client=self.client,
                    clock=lambda:self.now+elapsed[0]+(wall_jump if elapsed[0] else 0),monotonic=lambda:elapsed[0])
            self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA');self.assertEqual(r['network_calls'],1)
    def test_too_few_actual_rows_and_failed_manifest_cannot_analyze(self):
        self.frame=self.frame.iloc[-100:];r=self.acquire()
        self.assertEqual(r['status'],'INSUFFICIENT_REAL_DATA')
        with self.assertRaises(ValueError):load_sample(self.data,allow_synthetic=True)
    def test_sealed_or_future_plan_tampering_is_rejected_before_csv_read(self):
        self.acquire()
        path=self.data/'plan.json';spec=json.loads(path.read_text())
        spec['start']='2026-10-03T00:00:00+00:00';write_json(path,spec)
        self.edit_manifest(lambda m:m.update(plan=spec,plan_sha256=sha(canonical(spec))))
        with patch('backtesting.minute_study.load_csv',side_effect=AssertionError('Never read sealed input')):
            with self.assertRaises(ValueError):load_sample(self.data,allow_synthetic=True)
