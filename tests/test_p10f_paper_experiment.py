"""Disposable independent PAPER accounts; protected HALTED fixture never changed."""
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from ops import paper_experiment as exp
from ops.paper_session import ExistingPaperStore,run_session
from ops.paper_watch import supervise
from services.public_market import PublicMarketClient,PublicDataError
from trading.execution import ExecutionEngine,TradingHalted
from trading.state_store import StateStore
import tests.test_p10c_paper_watch as fixtures
import tests.test_p10c_public_market as public_fixtures

RANK=['KRW-BLAST','KRW-AXIS','KRW-BABY','KRW-IRYS','KRW-SOPH','KRW-T','KRW-ORDER','KRW-XRP','KRW-USDT','KRW-CAP']
GAPS={'KRW-AXIS','KRW-SOPH','KRW-USDT'}


class PortableExperimentTests(unittest.TestCase):
    def test_default_no_directory_never_falls_back_to_original_account(self):
        with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                self.assertEqual(exp.main([]),2)
        r=json.loads(output.getvalue());self.assertEqual(r['status'],'BLOCKED')
        self.assertEqual(r['account_scope'],exp.SCOPE)

    def test_invalid_cli_modes_and_no_reset_or_force_option(self):
        for args in (['--create','--connect-public'],['--seconds','10'],['--reset'],['--force'],
                     ['--connect-public'],['--connect-public','--seconds','10','--eligible-top','10'],
                     ['--connect-public','--seconds','10','--candidate-limit','20'],
                     ['--connect-public','--seconds','10','--top','31']):
            with self.subTest(args=args),patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):exp.main(args)

    def test_request_budget_blocks_before_network_without_fallback(self):
        session=Mock();client=PublicMarketClient(session=session,max_calls=0)
        with self.assertRaisesRegex(PublicDataError,'PUBLIC_REQUEST_BUDGET'):client.get('market/all',{})
        session.get.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Windows explicit private ownership block')
    def test_windows_creation_is_blocked_without_filesystem_mutation(self):
        with patch('ops.paper_experiment.Path') as path:
            self.assertEqual(exp.create('not-created')['status'],'BLOCKED')
            path.assert_not_called()


@unittest.skipUnless(os.name=='posix','POSIX private-directory/session ownership not certified on Windows')
class ExperimentTests(unittest.TestCase):
    factory=fixtures.PaperWatchTests.factory

    def setUp(self):
        fixtures.PaperWatchTests.setUp(self)
        old=ExecutionEngine(self.state,1000000);old.open()
        old.buy('KRW-BTC',109212000,400000);old.halt('PAPER_SESSION_FAILURE');old.close()
        self.protected=self.state;self.backup=self.root/'old-cold-backup.json'
        self.backup.write_bytes(self.protected.read_bytes());self.backup.chmod(0o600)
        self.protected_paths=[self.protected,self.backup,Path(str(self.protected)+'.lock')]
        self.before={p:p.read_bytes() for p in self.protected_paths}
        self.root=self.root.resolve()  # macOS TMPDIR may use the /var symlink.
        self.parent=self.root/'experiments';self.parent.mkdir(mode=0o700)
        self.experiment=self.parent/'run-one'
        self.assertEqual(exp.create(self.experiment)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        self.state=self.experiment/exp.STATE
        self.gaps=set();self.candle_errors={}
        self.client.clock=lambda:self.wall;self.client.monotonic=lambda:self.now
        def candles(t):
            if t in self.candle_errors:raise PublicDataError(self.candle_errors[t])
            rows=public_fixtures.PublicMarketTests.candles(SimpleNamespace(now=self.wall))
            for row in rows:row['market']=t
            if t in self.gaps:
                for row in rows[10:]:
                    row['candle_date_time_utc']=(pd.Timestamp(row['candle_date_time_utc'])-pd.Timedelta(minutes=1)).isoformat()
            client=PublicMarketClient(clock=lambda:self.wall)
            try:
                client.get=Mock(return_value=rows)
                return client.candles(t)
            finally:client.close()
        self.client.candles=Mock(side_effect=candles)

    def tearDown(self):
        for p,raw in self.before.items():self.assertEqual(p.read_bytes(),raw,'Protected ledger/backup/lock changed')

    def run_experiment(self,*,top=1,eligible=None,limit=None,tickers=None,finish=True):
        token=exp.begin(self.experiment)
        options={'session_id':token,'eligible_top':eligible,'candidate_limit':limit}
        r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=self.factory,
            monotonic=lambda:self.now,wall=lambda:self.wall,top=top,tickers=tickers,experiment=options)
        terminal=exp.finish(self.experiment,token,r) if finish else None
        return r,terminal

    def test_new_account_has_exact_capital_no_inherited_btc_or_fills(self):
        saved=json.loads(self.state.read_bytes())
        self.assertEqual(saved['cash'],'1000000');self.assertEqual(saved['positions'],{});self.assertEqual(saved['orders'],{})
        r=exp.inspect_experiment(self.experiment)
        self.assertEqual(r['session_state'],'NEVER_STARTED');self.assertFalse(r['execution_authorized'])
        self.assertEqual(r['diagnostic']['summary']['net_return_percent'],'0')
        self.assertFalse((self.experiment/'notifications').exists())

    def test_creation_is_single_use_even_after_leaf_directory_loss(self):
        self.assertEqual(exp.create(self.experiment)['status'],'BLOCKED')
        shutil.rmtree(self.experiment)  # Isolated fixture only, NEVER an operational recovery step.
        self.assertEqual(exp.create(self.experiment)['status'],'BLOCKED')
        self.assertFalse(self.experiment.exists());self.assertTrue(exp.receipt_path(self.experiment).exists())

    def test_project_alias_symlink_and_nonprivate_parent_are_rejected(self):
        self.assertEqual(exp.create(exp.ROOT/'runtime/new-experiment')['status'],'BLOCKED')
        alias=self.parent/'alias';alias.symlink_to(self.protected.parent,target_is_directory=True)
        self.assertEqual(exp.create(alias)['status'],'BLOCKED')
        self.parent.chmod(0o755)
        try:self.assertEqual(exp.create(self.parent/'bad')['status'],'BLOCKED')
        finally:self.parent.chmod(0o700)

    def test_incomplete_creation_preserves_reservation_and_does_not_retry(self):
        target=self.parent/'partial';real=exp.exclusive_write
        def failed(path,raw):
            if Path(path).parent==target:raise OSError('private-canary')
            return real(path,raw)
        with patch.object(exp,'exclusive_write',side_effect=failed):r=exp.create(target)
        self.assertEqual(r['status'],'BLOCKED');self.assertTrue(exp.receipt_path(target).exists())
        self.assertEqual(exp.create(target)['status'],'BLOCKED');self.assertFalse((target/exp.STATE).exists())
        self.assertNotIn('private-canary',json.dumps(r))

    def test_fake_buy_sell_persistence_returns_and_explicit_resume(self):
        r,end=self.run_experiment()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(r['status'],'STOPPED')
        self.assertEqual(Decimal(r['cash']),1008000);self.assertEqual(r['cumulative_buys'],1);self.assertEqual(r['cumulative_sells'],1)
        self.assertEqual(Decimal(r['net_return_percent']),Decimal('.8'));self.assertEqual(r['session_ticks'],2)
        self.assertEqual(r['markets']['KRW-BTC']['cumulative_sells'],1)
        old=json.loads(self.state.read_bytes())['orders']
        self.stop.clear();self.now=0;self.actions=[]
        second,end=self.run_experiment()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(old,json.loads(self.state.read_bytes())['orders'])
        self.assertEqual(second['session_ticks'],2);self.assertEqual(second['account_scope'],exp.SCOPE)

    def test_offline_open_position_valuation_is_na_no_price_invention(self):
        self.actions=['BUY'];r,end=self.run_experiment()
        self.assertEqual(end['status'],'STOPPED_VERIFIED')
        self.assertEqual(Decimal(r['net_return_percent']),Decimal('.8'))
        offline=exp.inspect_experiment(self.experiment)
        self.assertEqual(offline['diagnostic']['summary']['net_return_percent'],'N/A')

    def test_strict_top10_three_no_trade_gaps_fail_before_engine_write(self):
        self.client.top_tickers.return_value=RANK;self.gaps=GAPS.copy();before=self.state.read_bytes()
        token=exp.begin(self.experiment)
        with patch.object(ExistingPaperStore,'save',side_effect=AssertionError('No engine write')) as save:
            r=run_session(self.state,seconds=30,stopped=self.stop.is_set,emit=self.reports.append,
                client=self.client,stream_factory=self.stream_factory,analyzer_factory=self.factory,top=10,
                experiment={'session_id':token,'eligible_top':None,'candidate_limit':None})
        save.assert_not_called();self.assertFalse(r['engine_started']);self.assertEqual(r['reason'],'PUBLIC_CANDLE_GAP')
        self.assertEqual(before,self.state.read_bytes());self.assertEqual(r['status'],'HALTED');self.assertFalse(r['halted'])
        report=next(v['selection'] for v in self.reports if v['status']=='PREPARATION_FAILED')
        self.assertEqual({v['market'] for v in report['excluded']},GAPS)
        self.assertEqual(report['ranked_candidates'],RANK);self.assertEqual(len(report['selected']),7)
        self.assertEqual(exp.finish(self.experiment,token,r)['status'],'PREPARATION_FAILED')
        self.assertEqual(exp.inspect_experiment(self.experiment)['session_state'],'PREPARATION_FAILED')

    def test_explicit_eligibility_mode_preserves_rank_and_exclusion_reasons(self):
        self.client.top_tickers.return_value=RANK;self.gaps=GAPS.copy()
        saved=json.loads(self.state.read_bytes())
        chosen,_=exp.prepare_markets(saved,self.client,10,None,{'eligible_top':7,'candidate_limit':10},self.reports.append,self.factory)
        self.assertEqual(chosen,[t for t in RANK if t not in GAPS])
        self.client.top_tickers.assert_called_once_with(10)
        report=self.reports[-1]['selection'];self.assertEqual(report['mode'],'CONTIGUOUS_CANDLES_ELIGIBLE')
        self.assertEqual({v['market'] for v in report['excluded']},GAPS)
        self.assertTrue(all('NOT_PROOF_OF_API_FAILURE' in v['meaning'] for v in report['excluded']))
        self.assertEqual(self.client.candles.call_count,10)

    def test_insufficient_candidates_is_preparation_failure_not_persistent_halt(self):
        self.client.top_tickers.return_value=RANK;self.gaps=GAPS.copy();before=self.state.read_bytes()
        r,end=self.run_experiment(eligible=10,limit=10)
        self.assertEqual(r['reason'],'INSUFFICIENT_CONTIGUOUS_CANDIDATES');self.assertEqual(end['status'],'PREPARATION_FAILED')
        self.assertEqual(before,self.state.read_bytes());self.assertFalse(r['engine_started'])

    def test_api_429_stale_or_invalid_response_never_skipped_as_no_trade_gap(self):
        before=self.state.read_bytes()
        for error in ('PUBLIC_RATE_LIMIT','PUBLIC_STALE_CANDLE','PUBLIC_INVALID_RESPONSE'):
            self.client.candles.side_effect=PublicDataError(error)
            r,end=self.run_experiment(eligible=1,limit=1)
            self.assertEqual(r['reason'],error);self.assertEqual(end['status'],'PREPARATION_FAILED')
            self.assertEqual(before,self.state.read_bytes());self.assertFalse(r['engine_started'])

    def test_prepared_public_candles_reused_by_real_indicator_factory(self):
        before_calls=self.client.candles.call_count
        saved=json.loads(self.state.read_bytes())
        chosen,analyzers=exp.prepare_markets(saved,self.client,1,['KRW-BTC'],{},self.reports.append)
        self.assertEqual(chosen,['KRW-BTC']);self.assertEqual(self.client.candles.call_count-before_calls,1)
        self.assertEqual(len(analyzers['KRW-BTC'].df),200)
        analyzers['KRW-BTC'].update_data();self.assertEqual(self.client.candles.call_count-before_calls,2)

    def test_held_market_gap_blocks_without_dropping_or_changing_inventory(self):
        self.actions=['BUY'];self.run_experiment();self.stop.clear();self.now=0;self.actions=[]
        self.client.top_tickers.return_value=['KRW-ETH'];self.gaps.add('KRW-BTC');before=self.state.read_bytes()
        r,end=self.run_experiment()
        self.assertEqual(r['reason'],'HELD_CANDLE_GAP_RISK_MANAGEMENT_UNAVAILABLE')
        self.assertEqual(end['status'],'PREPARATION_FAILED');self.assertEqual(before,self.state.read_bytes())
        report=[v['selection'] for v in self.reports if v['status']=='PREPARATION_FAILED'][-1]
        self.assertEqual(report['held_required'],['KRW-BTC']);self.assertEqual(report['excluded'][0]['role'],'HELD_CANNOT_BE_DROPPED')

    def test_held_valid_market_is_added_instead_of_silently_unsubscribed(self):
        self.actions=['BUY'];self.run_experiment();self.stop.clear();self.now=0;self.actions=[]
        self.client.top_tickers.return_value=['KRW-ETH']
        r,end=self.run_experiment()
        self.assertEqual(end['status'],'STOPPED_VERIFIED');self.assertEqual(set(self.stream.codes),{'KRW-BTC','KRW-ETH'})
        self.assertIn('KRW-BTC',r['positions'])

    def test_runtime_websocket_fault_persists_halt_and_prevents_restart(self):
        self.stream_error=OSError('private-canary');r,end=self.run_experiment()
        self.assertTrue(r['engine_started']);self.assertTrue(r['halted']);self.assertEqual(end['status'],'HALTED')
        raw=self.state.read_bytes()
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(raw,self.state.read_bytes());self.assertNotIn('private-canary',json.dumps(r))

    def test_unknown_orders_corruption_or_missing_receipts_never_initialized(self):
        before=self.state.read_bytes();self.state.write_bytes(b'{bad')
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(self.state.read_bytes(),b'{bad')
        self.state.write_bytes(before);(self.experiment/'experiment.json').unlink()
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(self.state.read_bytes(),before)

    def test_legacy_engine_open_cannot_clear_or_start_an_experiment(self):
        before=self.state.read_bytes()
        with self.assertRaises(TradingHalted):ExecutionEngine(self.state,1000000).open()
        self.assertEqual(before,self.state.read_bytes())

    def test_duplicate_begin_and_lost_session_receipt_block_restart(self):
        exp.begin(self.experiment);before=self.state.read_bytes()
        with self.assertRaises(Exception):exp.begin(self.experiment)
        (self.experiment/'session.json').unlink()
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(before,self.state.read_bytes())

    def test_active_kernel_owner_blocks_second_process_before_market_request(self):
        owner=ExistingPaperStore(self.state,1000000);owner.acquire()
        try:
            code="from ops.paper_experiment import begin;import sys\ntry:begin(sys.argv[1]);sys.exit(1)\nexcept Exception:sys.exit(0)"
            r=subprocess.run([sys.executable,'-c',code,str(self.experiment)],capture_output=True,timeout=15)
            self.assertEqual(r.returncode,0);self.client.top_tickers.assert_not_called()
        finally:owner.close()

    def test_force_killed_worker_with_clean_ledger_is_still_not_restartable(self):
        token=exp.begin(self.experiment)
        r=supervise(self.state,.1,1,None,emit=self.reports.append,worker=fixtures._hang_worker,grace=.1,
                    outbox=self.experiment/'notifications/outbox.sqlite3')
        self.assertEqual(r['status'],'HALTED');self.assertEqual(exp.finish(self.experiment,token,r)['status'],'HALTED')
        with self.assertRaises(Exception):exp.begin(self.experiment)

    def test_missing_session_and_ledger_joint_rollback_limit_remains_explicit(self):
        initial=self.state.read_bytes();self.run_experiment()
        self.state.write_bytes(initial);(self.experiment/'session.json').unlink()
        with self.assertRaisesRegex(exp.ExperimentError,'UNKNOWN_SESSION_NOT_RESET'):exp.begin(self.experiment)
        self.assertTrue(exp.first_use_path(self.experiment).exists())

    def test_default_inspection_and_cli_do_not_start_workers_or_modify_files(self):
        before={p:p.read_bytes() for p in self.experiment.iterdir() if p.is_file()}
        with patch('ops.paper_watch.supervise',side_effect=AssertionError('No public connection')):
            with patch('sys.stdout',new_callable=io.StringIO):self.assertEqual(exp.main(['--directory',str(self.experiment)]),2)
        self.assertEqual(before,{p:p.read_bytes() for p in self.experiment.iterdir() if p.is_file()})

    def test_eligibility_substitutions_are_only_in_explicit_ranked_candidate_mode(self):
        ranks=RANK+['KRW-BTC','KRW-ETH','KRW-SOL'];self.gaps=GAPS.copy()
        self.client.top_tickers.return_value=ranks
        chosen,_=exp.prepare_markets(json.loads(self.state.read_bytes()),self.client,10,None,
            {'eligible_top':10,'candidate_limit':13},self.reports.append,self.factory)
        self.assertEqual(chosen,[t for t in ranks if t not in GAPS])
        self.client.top_tickers.assert_called_once_with(13)
        self.assertEqual(self.reports[-1]['selection']['selected'],chosen)
        self.assertEqual(self.client.candles.call_count,13)

    def test_eligible_held_gap_is_fatal_not_a_skippable_candidate(self):
        self.actions=['BUY'];self.run_experiment();self.stop.clear();self.now=0;self.actions=[]
        self.client.top_tickers.return_value=['KRW-BTC','KRW-ETH'];self.gaps.add('KRW-BTC')
        self.client.candles.reset_mock();before=self.state.read_bytes()
        r,end=self.run_experiment(eligible=1,limit=2)
        self.assertEqual(r['reason'],'HELD_CANDLE_GAP_RISK_MANAGEMENT_UNAVAILABLE')
        self.assertEqual(self.client.candles.call_count,2);self.assertEqual(before,self.state.read_bytes())
        self.assertEqual(end['status'],'PREPARATION_FAILED')

    def test_runtime_candle_gap_persists_halt_after_a_confirmed_fake_fill(self):
        token=exp.begin(self.experiment)
        def factory(t):
            a=self.factory(t);calls=[0]
            def update():
                calls[0]+=1
                if calls[0]>1:
                    self.stop.set()  # A simultaneous stop must not disguise a data error.
                    raise PublicDataError('PUBLIC_CANDLE_GAP')
            a.update_data=update;return a
        owner=self
        class Stream:
            def connect(self):pass
            def close(self):pass
            def get(self):
                owner.now+=301
                return {'type':'ticker','code':'KRW-BTC','trade_price':10000,
                        'timestamp':int((owner.wall+owner.now)*1000)}
        r=run_session(self.state,seconds=1000,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=lambda _:Stream(),analyzer_factory=factory,top=1,
            experiment={'session_id':token},monotonic=lambda:self.now,wall=lambda:self.wall+self.now)
        self.assertTrue(r['engine_started']);self.assertTrue(r['halted']);self.assertEqual(r['reason'],'PUBLIC_CANDLE_GAP')
        self.assertEqual(r['orders'],1);self.assertEqual(r['markets']['KRW-BTC']['cumulative_buys'],'N/A')
        self.assertEqual(exp.finish(self.experiment,token,r)['status'],'HALTED')
        with self.assertRaises(Exception):exp.begin(self.experiment)

    def test_session_storage_failure_does_not_touch_financial_state_or_reset(self):
        before=self.state.read_bytes();real=StateStore.save
        def fail(owner,value):
            if owner.path.name=='session.json':raise OSError('private-canary')
            return real(owner,value)
        with patch.object(StateStore,'save',new=fail):
            with self.assertRaises(OSError):exp.begin(self.experiment)
        self.assertEqual(before,self.state.read_bytes());self.assertTrue(exp.first_use_path(self.experiment).exists())
        with self.assertRaises(Exception):exp.begin(self.experiment)

    def test_foreign_git_checkout_and_copied_ledger_are_not_experiment_directories(self):
        git=self.parent/'.git';git.mkdir();(git/'HEAD').write_text('ref: refs/heads/master')
        self.assertEqual(exp.create(self.parent/'foreign')['status'],'BLOCKED')
        shutil.rmtree(git)
        raw=self.state.read_bytes();self.state.write_bytes(self.protected.read_bytes())
        with self.assertRaises(Exception):exp.begin(self.experiment)
        self.assertEqual(self.state.read_bytes(),self.protected.read_bytes())
        self.state.write_bytes(raw)

    def test_ssh_sighup_stops_supervisor_and_never_authorizes_unknown_session(self):
        token=exp.begin(self.experiment)
        sender=threading.Timer(.2,lambda:os.kill(os.getpid(),signal.SIGHUP));sender.start()
        try:
            r=supervise(self.state,5,1,None,emit=self.reports.append,worker=fixtures._cooperative_worker,grace=.5,
                        outbox=self.experiment/'notifications/outbox.sqlite3')
        finally:sender.join()
        self.assertFalse(r['automatic_restart'])
        self.assertEqual(exp.finish(self.experiment,token,r)['status'],'HALTED')
        with self.assertRaises(Exception):exp.begin(self.experiment)

    def test_pending_and_unknown_experiment_orders_are_never_reset(self):
        initial=self.state.read_bytes()
        for status in ('intent','UNKNOWN'):
            saved=json.loads(initial)
            saved['orders']={'id':{'identifier':'id','ticker':'KRW-BTC','side':'bid','averaging':False,
                'uuid':None,'status':status,'value':'10000'}}
            self.state.write_bytes(exp.encoded(saved));raw=self.state.read_bytes()
            with self.assertRaises(Exception):exp.begin(self.experiment)
            self.assertEqual(self.state.read_bytes(),raw)
        self.client.top_tickers.assert_not_called()

    def test_mac_extended_acl_cannot_be_declared_private_from_mode_bits_alone(self):
        with patch('ops.paper_experiment.sys.platform','darwin'):
            with patch('ops.paper_experiment.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='permissions path\n 0: user:other allow read\n')):
                self.assertEqual(exp.create(self.parent/'acl-test')['reason'],'EXTENDED_ACL_UNVERIFIED')
        self.assertFalse((self.parent/'acl-test').exists())

    def test_lost_directory_cannot_be_recreated_by_case_variant(self):
        shutil.rmtree(self.experiment)  # Disposable Fake account, no operational cleanup.
        alternate=self.parent/'RUN-ONE'
        self.assertEqual(exp.create(alternate)['status'],'BLOCKED')
        self.assertFalse(alternate.exists())

    def test_cached_candles_are_rechecked_at_end_of_preparation_without_refetch(self):
        before=self.state.read_bytes();original=self.factory
        def factory(t):
            a=original(t)
            def update():self.wall+=181;self.now+=181
            a.update_data=update;return a
        token=exp.begin(self.experiment)
        r=run_session(self.state,seconds=1000,stopped=self.stop.is_set,emit=self.reports.append,
            client=self.client,stream_factory=self.stream_factory,analyzer_factory=factory,top=1,
            experiment={'session_id':token})
        self.assertEqual(r['reason'],'PUBLIC_STALE_CANDLE');self.assertFalse(r['engine_started'])
        self.assertEqual(self.client.candles.call_count,1);self.assertEqual(self.state.read_bytes(),before)
        self.assertEqual(exp.finish(self.experiment,token,r)['status'],'PREPARATION_FAILED')

    def test_copied_case_variant_directory_is_not_the_same_account_on_case_sensitive_fs(self):
        alternate=self.parent/'RUN-ONE'
        if alternate.exists():self.skipTest('Case-insensitive filesystem: this spelling is the original directory')
        shutil.copytree(self.experiment,alternate)  # Fake copy; no operational import feature.
        with self.assertRaisesRegex(exp.ExperimentError,'EXPERIMENT_IDENTITY_UNVERIFIED'):exp.begin(alternate)
        self.assertEqual((alternate/exp.STATE).read_bytes(),self.state.read_bytes())
        self.assertFalse(exp.first_use_path(alternate).exists())
