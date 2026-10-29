"""Synthetic cost-aware PAPER accounts; no user accounts or network."""
from decimal import Decimal as D
import inspect
import json
import os
from pathlib import Path
import tempfile
import unittest

from ops import paper_experiment as exp
from trading.execution import ExecutionEngine

POLICY={'schema_version':1,'fee_bps':'5','slippage_bps':'5','full_spread_bps':'2'}


@unittest.skipUnless(os.name=='posix','Existing independent-account ownership requires POSIX')
class CostAccountTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir='/tmp');self.addCleanup(self.tmp.cleanup)
        self.parent=Path(self.tmp.name).resolve();self.account=self.parent/'new-cost-paper'
        kw={'cost_policy':POLICY} if 'cost_policy' in inspect.signature(exp.create).parameters else {}
        self.assertEqual(exp.create(self.account,**kw)['status'],'CREATED_NEW_INDEPENDENT_EXPERIMENT')
        self.state=self.account/exp.STATE
    def engine(self):
        self.token=exp.begin(self.account)
        engine=ExecutionEngine(self.state,1_000_000,real_trading=False);engine._experiment_session=self.token;engine.open()
        self.addCleanup(engine.close);return engine
    def test_fee_enabled_paper_receipt_matches_independent_all_in_budget(self):
        e=self.engine();self.assertTrue(e.buy('KRW-BTC',10000,100000))
        order=list(e.state['orders'].values())[0]
        expected_funds=D(100000)/(1+D('0.0005'))
        self.assertEqual(D(order['paid_fee']),expected_funds*D('0.0005'))
        self.assertLessEqual(D(order['funds'])+D(order['paid_fee']),100000)
    def test_adverse_price_uses_existing_cost_formula_not_reference_tick(self):
        e=self.engine();e.buy('KRW-BTC',10000,100000);e.sell('KRW-BTC',10010)
        buy,sell=e.state['orders'].values()
        self.assertAlmostEqual(D(buy['average_fill_price']),D('10006'),delta=D('1e-20'))
        self.assertAlmostEqual(D(sell['average_fill_price']),D('10003.994'),delta=D('1e-20'))
        self.assertLess(e.cash,1_000_000)
    def finish(self,e):
        e.close()
        r=exp.finish(self.account,self.token,{'status':'STOPPED','engine_started':True,
            'post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
        self.assertEqual(r['status'],'STOPPED_VERIFIED')
    def test_synthetic_round_trip_reconciles_independent_decimal_and_report(self):
        from decimal import localcontext
        from ops.paper_bot import read_report
        e=self.engine();e.buy('KRW-BTC',10000,100000);e.sell('KRW-BTC',10010)
        with localcontext() as ctx:
            ctx.prec=60
            notional=D(100000)/D('1.0005');quantity=notional/D('10006')
            sale=quantity*D('10003.994');expected=sale*D('0.9995')-D(100000)
        self.assertAlmostEqual(e.cash-D(1000000),expected,delta=D('1e-20'))
        self.assertEqual(e.state['positions'],{})
        buy,sell=e.state['orders'].values()
        self.assertGreater(D(buy['paid_fee']),0);self.assertGreater(D(sell['paid_fee']),0)
        self.finish(e);before=self.state.read_bytes();report=read_report(self.account)
        self.assertEqual(report['summary']['pending_orders'],0)
        self.assertAlmostEqual(D(report['summary']['net_realized_pnl']),expected,delta=D('1e-20'))
        self.assertEqual(report['cost_policy'],POLICY)
        self.assertNotIn('ZERO_FEE',json.dumps(report));self.assertEqual(before,self.state.read_bytes())
    def test_policy_survives_verified_stop_and_resume_with_open_position(self):
        from ops.paper_bot import read_report
        e=self.engine();e.buy('KRW-BTC',10000,100000);self.finish(e)
        before=read_report(self.account);e=self.engine()
        self.assertEqual(e._paper_cost_policy,POLICY);e.sell('KRW-BTC',10010);self.finish(e)
        after=read_report(self.account)
        self.assertEqual(before['cost_policy'],after['cost_policy'])
        self.assertEqual(after['summary']['cumulative_buys'],1);self.assertEqual(after['summary']['cumulative_sells'],1)
    def test_policy_loss_modification_or_schema_downgrade_blocks_before_network(self):
        from unittest.mock import Mock
        from ops.paper_session import run_session
        original=self.state.read_bytes();token=exp.begin(self.account)
        for change in ('missing','modified','downgrade','explicit_null'):
            saved=json.loads(original)
            if change=='missing':del saved['paper_experiment']['cost_policy']
            elif change=='modified':saved['paper_experiment']['cost_policy']['fee_bps']='0'
            elif change=='explicit_null':saved['paper_experiment']['cost_policy']=None
            else:saved['paper_experiment']={k:v for k,v in saved['paper_experiment'].items() if k!='cost_policy'};saved['paper_experiment']['schema_version']=1
            self.state.write_text(json.dumps(saved));before=self.state.read_bytes()
            client=Mock();stream=Mock(side_effect=AssertionError('No connection allowed'))
            result=run_session(self.state,seconds=10,stopped=lambda:False,emit=lambda _:None,client=client,
                               stream_factory=stream,tickers=['KRW-BTC'],experiment={'session_id':token})
            self.assertFalse(result['engine_started']);stream.assert_not_called();client.candles.assert_not_called()
            client.top_tickers.assert_not_called();self.assertEqual(before,self.state.read_bytes())
        self.state.write_bytes(original)  # Disposable fixture only, never a user account.
    def test_averaging_and_partial_sell_reuses_fill_and_p9_cost_allocation(self):
        from datetime import datetime,timezone
        from services.notification_metrics import replay
        e=self.engine();e.buy('KRW-BTC',10000,100000);e.buy('KRW-BTC',9000,100000,averaging=True)
        buys=list(e.state['orders'].values());all_in=sum(D(b['funds'])+D(b['paid_fee']) for b in buys)
        q=D(e.position('KRW-BTC')['quantity']);half=q/2
        # Existing fill application supports partial terminal receipts; no new partial-order API.
        funds=half*D('10993.4');fee=funds*D('0.0005')
        order={'identifier':'synthetic-partial','uuid':None,'ticker':'KRW-BTC','side':'ask','value':str(q),
               'price_hint':'11000','averaging':False,'reason':None,'status':'intent'}
        e.state['orders'][order['identifier']]=order
        e._apply_fill(order,{'state':'cancel','executed_volume':str(half),'paid_fee':str(fee),
                           'trades':[{'volume':str(half),'funds':str(funds)}]})
        e.store.save(e.state)
        a=replay(e.state,D(1000000),as_of=datetime.now(timezone.utc),calendar='UTC')
        self.assertAlmostEqual(a['rows'][-1]['allocated_cost'],all_in/2,delta=D('1e-20'))
        self.assertAlmostEqual(a['net_profit'],funds-fee-all_in/2,delta=D('1e-20'))
        self.assertTrue(e.position('KRW-BTC')['averaging_down_used'])
        self.assertAlmostEqual(a['positions']['KRW-BTC']['cost'],all_in/2,delta=D('1e-20'))
    def test_fee_inclusive_cash_cap_at_all_cash_and_rejected_overspend(self):
        e=self.engine();self.assertFalse(e.buy('KRW-BTC',10000,1000001));self.assertEqual(e.state['orders'],{})
        self.assertTrue(e.buy('KRW-BTC',10000,1000000));self.assertGreaterEqual(e.cash,0)
        order=list(e.state['orders'].values())[0]
        self.assertLessEqual(D(order['funds'])+D(order['paid_fee']),1000000)
        self.assertFalse(e.buy('KRW-ETH',10000,1))
    def test_init_check_status_report_show_explicit_policy_and_never_accept_run_override(self):
        import io
        from unittest.mock import patch
        from ops import paper_bot as cli
        p=self.parent/'cli-new'
        for command,flags in [('init',['--fee-bps','5','--slippage-bps','5','--full-spread-bps','2']),('check',[]),('status',[]),('report',[])]:
            with patch('sys.stdout',new_callable=io.StringIO) as out:
                code=cli.main([command,'--directory',str(p),*flags])
            self.assertEqual(code,0,out.getvalue());self.assertEqual(json.loads(out.getvalue())['cost_policy'],POLICY)
        with patch('sys.stderr',new_callable=io.StringIO),self.assertRaises(SystemExit):
            cli.main(['report','--directory',str(p),'--fee-bps','5','--slippage-bps','5','--full-spread-bps','2'])
    def test_running_status_projection_matches_report_policy(self):
        from ops.paper_session import state_summary
        from ops.paper_bot import view,read_report
        e=self.engine();e.buy('KRW-BTC',10000,100000)
        status=view(state_summary(e.state));self.finish(e)
        self.assertEqual(status['cost_policy'],read_report(self.account)['cost_policy'])
    def test_existing_accounts_halted_backups_and_legacy_receipts_are_unchanged(self):
        from ops.paper_bot import read_report
        legacy=self.parent/'legacy';exp.create(legacy)
        token=exp.begin(legacy);engine=ExecutionEngine(legacy/exp.STATE,1_000_000,real_trading=False)
        engine._experiment_session=token;engine.open();engine.buy('KRW-BTC',10000,100000);engine.sell('KRW-BTC',10010)
        engine.close();exp.finish(legacy,token,{'status':'STOPPED','engine_started':True,'post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
        report=read_report(legacy);self.assertIsNone(report['cost_policy'])
        self.assertEqual(report['summary']['cash'],'1000100');self.assertIn('ZERO_FEE',report['summary']['fee_policy'])
        backup=self.parent/'legacy-cold.json';backup.write_bytes((legacy/exp.STATE).read_bytes())
        protected={p:p.read_bytes() for p in legacy.iterdir() if p.is_file()};protected[backup]=backup.read_bytes()
        e=self.engine();e.buy('KRW-BTC',10000,100000);self.finish(e)
        for p,raw in protected.items():self.assertEqual(raw,p.read_bytes())
        self.assertEqual(exp.create(legacy,cost_policy=POLICY)['status'],'BLOCKED')
        for p,raw in protected.items():self.assertEqual(raw,p.read_bytes())
    def test_mid_session_policy_mutation_cannot_silently_disable_fees(self):
        from trading.execution import TradingHalted
        e=self.engine();e.state['paper_experiment']['cost_policy']['fee_bps']='0'
        with self.assertRaisesRegex(TradingHalted,'PAPER_COST_POLICY_MISMATCH'):e.buy('KRW-BTC',10000,100000)
        self.assertEqual(e.state['orders'],{})

    def test_actual_engine_fee_only_slip_only_spread_only_and_explicit_zero(self):
        from trading.costs import CostModel,policy
        from ops.paper_bot import read_report
        for i,args in enumerate(((5,0,0),(0,5,0),(0,0,2),(0,0,0))):
            account=self.parent/f'variant-{i}';value=policy(CostModel(*args));exp.create(account,cost_policy=value)
            token=exp.begin(account);e=ExecutionEngine(account/exp.STATE,1_000_000,real_trading=False)
            e._experiment_session=token;e.open()
            try:
                self.assertTrue(e.buy('KRW-BTC',10000,100000));self.assertTrue(e.sell('KRW-BTC',10010))
                buy,sell=e.state['orders'].values()
                rate=D(args[0])/10000;impact=(D(args[1])+D(args[2])/2)/10000
                expected_buy=D(10000)*(1+impact);expected_sell=D(10010)*(1-impact)
                self.assertAlmostEqual(D(buy['average_fill_price']),expected_buy,delta=D('1e-20'))
                self.assertAlmostEqual(D(sell['average_fill_price']),expected_sell,delta=D('1e-20'))
                self.assertEqual(D(buy['paid_fee']),D(buy['funds'])*rate)
                self.assertEqual(D(sell['paid_fee']),D(sell['funds'])*rate)
                self.assertEqual(e.state['positions'],{})
            finally:e.close()
            exp.finish(account,token,{'status':'STOPPED','engine_started':True,'post_validation':'VERIFIED_UNDER_OWNER_LOCK'})
            r=read_report(account);self.assertEqual(r['cost_policy'],value)
            if i==3:
                self.assertEqual(r['summary']['cash'],'1000100')
                saved=json.loads((account/exp.STATE).read_bytes())
                self.assertEqual(saved['paper_experiment']['schema_version'],2)
                self.assertNotIn('ZERO_FEE',r['fee_policy'])
    def test_auto_trade_net_minimum_and_averaging_use_same_cost_policy(self):
        from unittest.mock import Mock
        from trading.auto_trade import AutoTrade
        token=exp.begin(self.account)
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=self.state,real_trading=False,
                      analyzer_factory=lambda _:Mock(),notification=None)
        bot.engine._experiment_session=token;bot.initialize()
        try:
            bot.max_per_coin=D(4999)
            self.assertFalse(bot.buy_coin('KRW-BTC',10000));self.assertEqual(bot.engine.state['orders'],{})
            bot.max_per_coin=D(5000)
            self.assertFalse(bot.buy_coin('KRW-BTC',10000));self.assertFalse(bot.engine.halted)
            self.assertEqual(bot.engine.state['orders'],{})
            bot.max_per_coin=D('5002.50')
            self.assertTrue(bot.buy_coin('KRW-BTC',10000))
            self.assertFalse(bot.buy_coin('KRW-BTC',9000,reason='물타기 매수',amount=D(5000)))
            self.assertTrue(bot.buy_coin('KRW-BTC',9000,reason='물타기 매수',amount=D('5002.50')))
            self.assertFalse(bot.buy_coin('KRW-BTC',8000,reason='물타기 매수',amount=D('5002.50')))
            for o in bot.engine.state['orders'].values():
                self.assertLessEqual(D(o['funds'])+D(o['paid_fee']),D('5002.50'))
                self.assertEqual(D(o['funds']),5000)
                self.assertGreater(D(o['paid_fee']),0)
        finally:bot.stop()
    def test_halted_cost_account_and_cold_backup_are_not_restarted_or_modified(self):
        e=self.engine();e.buy('KRW-BTC',10000,100000);e.halt('SYNTHETIC_TEST_HALT');e.close()
        backup=self.parent/'halted-cold.json';backup.write_bytes(self.state.read_bytes())
        before={p:p.read_bytes() for p in self.account.iterdir() if p.is_file()};before[backup]=backup.read_bytes()
        with self.assertRaises(ValueError):exp.begin(self.account)
        from ops.paper_bot import read_report
        r=read_report(self.account);self.assertEqual(r['session_state'],'HALTED_NO_RESTART')
        self.assertEqual(r['cost_policy'],POLICY)
        for p,raw in before.items():self.assertEqual(raw,p.read_bytes())

    def test_decimal_ulp_correction_keeps_all_cash_buy_nonnegative(self):
        # At 3bps the old sequential 28-digit subtraction would leave -3.44E-23.
        from trading.costs import CostModel,policy
        p=self.parent/'rounding';exp.create(p,cost_policy=policy(CostModel(3,0,0)))
        token=exp.begin(p);e=ExecutionEngine(p/exp.STATE,1_000_000,real_trading=False);e._experiment_session=token;e.open()
        try:
            self.assertTrue(e.buy('KRW-BTC',10000,1000000));self.assertGreaterEqual(e.cash,0)
            self.assertLess(e.cash,D('1e-20'))
            from datetime import datetime,timezone
            from services.notification_metrics import replay
            a=replay(e.state,D(1000000),as_of=datetime.now(timezone.utc),calendar='UTC')
            from services.notification_metrics import close
            self.assertTrue(close(a['cash'],e.cash))  # Existing P9 Decimal reconciliation tolerance.
        finally:e.close()

    def test_normal_buy_net_notional_boundary_rejects_before_intent_without_halt(self):
        from unittest.mock import Mock
        from trading.auto_trade import AutoTrade
        from services.paper_decisions import DecisionCounters
        token=exp.begin(self.account)
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=self.state,real_trading=False,
                      analyzer_factory=lambda _:Mock(),notification=None)
        bot.engine._experiment_session=token;bot.initialize();bot.decision_observer=DecisionCounters()
        try:
            before=self.state.read_bytes();backup=self.parent/'minimum-cold.json';backup.write_bytes(before)
            for budget in (D(5000),D('5002.49'),D('5002.50').next_minus()):
                with self.subTest(budget=budget):
                    bot.max_per_coin=budget
                    self.assertFalse(bot.buy_coin('KRW-BTC',10000))
                    self.assertEqual(before,self.state.read_bytes());self.assertEqual(before,backup.read_bytes())
                    self.assertEqual(bot.engine.state['orders'],{});self.assertFalse(bot.engine.halted)
                    self.assertTrue(bot.engine.ready)
            self.assertEqual(bot.decision_observer.snapshot()['final_blocks']['ENGINE_REJECTED'],3)
            bot.max_per_coin=D('5002.50');self.assertTrue(bot.buy_coin('KRW-BTC',10000))
            order=list(bot.engine.state['orders'].values())[0]
            self.assertEqual(D(order['funds']),5000);self.assertEqual(D(order['paid_fee']),D('2.50'))
            self.assertEqual(D(order['funds'])+D(order['paid_fee']),D('5002.50'))
            self.assertEqual(before,backup.read_bytes())
        finally:bot.stop()
    def test_averaging_net_notional_boundary_rejects_before_intent_without_halt(self):
        from unittest.mock import Mock
        from trading.auto_trade import AutoTrade
        token=exp.begin(self.account)
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=self.state,real_trading=False,
                      analyzer_factory=lambda _:Mock(),notification=None)
        bot.engine._experiment_session=token;bot.initialize()
        try:
            self.assertTrue(bot.buy_coin('KRW-BTC',10000));before=self.state.read_bytes()
            for budget in (D(5000),D('5002.50').next_minus()):
                with self.subTest(budget=budget):
                    self.assertFalse(bot.buy_coin('KRW-BTC',9000,reason='물타기 매수',amount=budget))
                    self.assertEqual(before,self.state.read_bytes());self.assertFalse(bot.engine.halted)
                    self.assertFalse(bot.engine.position('KRW-BTC')['averaging_down_used'])
            self.assertTrue(bot.buy_coin('KRW-BTC',9000,reason='물타기 매수',amount=D('5002.50')))
            self.assertEqual(len(bot.engine.state['orders']),2)
            order=list(bot.engine.state['orders'].values())[-1]
            self.assertEqual(D(order['funds']),5000);self.assertEqual(D(order['paid_fee']),D('2.50'))
        finally:bot.stop()
    def test_zero_fee_slip_spread_and_explicit_zero_keep_net_minimum(self):
        from trading.costs import CostModel,policy
        for i,args in enumerate(((0,0,0),(0,5,0),(0,0,2))):
            p=self.parent/f'minimum-zero-fee-{i}';exp.create(p,cost_policy=policy(CostModel(*args)))
            token=exp.begin(p);e=ExecutionEngine(p/exp.STATE,1_000_000,real_trading=False);e._experiment_session=token;e.open()
            try:
                before=(p/exp.STATE).read_bytes()
                self.assertFalse(e.buy('KRW-BTC',10000,D(5000).next_minus()))
                self.assertEqual(before,(p/exp.STATE).read_bytes());self.assertFalse(e.halted)
                self.assertTrue(e.buy('KRW-BTC',10000,5000))
                order=list(e.state['orders'].values())[0]
                self.assertEqual(D(order['paid_fee']),0)
                self.assertAlmostEqual(D(order['funds']),5000,delta=D('1e-20'))
            finally:e.close()

    def test_normal_and_averaging_all_cash_net_minimum_and_total_cap(self):
        for averaging in (False,True):
            for n,(target,accepted) in enumerate(((D(5000),False),(D('5002.49'),False),(D('5002.50'),True))):
                with self.subTest(averaging=averaging,target=target):
                    p=self.parent/f'all-cash-{averaging}-{n}';exp.create(p,cost_policy=POLICY)
                    token=exp.begin(p);e=ExecutionEngine(p/exp.STATE,1_000_000,real_trading=False)
                    e._experiment_session=token;e.open()
                    try:
                        seed='KRW-BTC' if averaging else 'KRW-ETH'
                        self.assertTrue(e.buy(seed,10000,D(1000000)-target))
                        budget=e.cash;before=(p/exp.STATE).read_bytes();orders=len(e.state['orders'])
                        result=e.buy('KRW-BTC',9000,budget,averaging=averaging)
                        self.assertEqual(result,accepted);self.assertFalse(e.halted);self.assertTrue(e.ready)
                        if not accepted:
                            self.assertEqual(before,(p/exp.STATE).read_bytes());self.assertEqual(e.cash,budget)
                            self.assertEqual(len(e.state['orders']),orders)
                        else:
                            order=list(e.state['orders'].values())[-1]
                            self.assertGreaterEqual(D(order['funds']),5000)
                            self.assertLessEqual(D(order['funds'])+D(order['paid_fee']),budget)
                            self.assertGreaterEqual(e.cash,0)
                    finally:e.close()
    def test_legacy_zero_cost_auto_trade_minimum_retains_original_boundary(self):
        from unittest.mock import Mock
        from trading.auto_trade import AutoTrade
        p=self.parent/'legacy-minimum';exp.create(p);token=exp.begin(p)
        bot=AutoTrade(tickers=['KRW-BTC'],state_path=p/exp.STATE,real_trading=False,
                      analyzer_factory=lambda _:Mock(),notification=None)
        bot.engine._experiment_session=token;bot.initialize()
        try:
            before=(p/exp.STATE).read_bytes();bot.max_per_coin=D(4999)
            self.assertFalse(bot.buy_coin('KRW-BTC',10000));self.assertEqual(before,(p/exp.STATE).read_bytes())
            bot.max_per_coin=D(5000);self.assertTrue(bot.buy_coin('KRW-BTC',10000))
            order=list(bot.engine.state['orders'].values())[0]
            self.assertEqual(D(order['funds']),5000);self.assertEqual(D(order['paid_fee']),0)
            self.assertFalse(bot.engine.halted)
        finally:bot.stop()


class PureCostTests(unittest.TestCase):
    def test_fee_slip_spread_and_zero_models_share_original_formulas(self):
        from trading.costs import CostModel
        from backtesting.engine import CostModel as Original
        self.assertIs(CostModel,Original)
        for args,buy,sell,fee in [((5,0,0),'10000','10000','0.0005'),((0,5,0),'10005','9995','0'),
                                  ((0,0,2),'10001','9999','0'),((0,0,0),'10000','10000','0')]:
            c=CostModel(*args);self.assertEqual(c.price(10000,'BUY'),D(buy));self.assertEqual(c.price(10000,'SELL'),D(sell))
            self.assertEqual(c.fee_rate,D(fee))
    def test_invalid_policies_fail_before_creating_any_directory(self):
        from trading.costs import from_policy
        from ops.paper_bot import initialize
        for bad in ('-1','NaN','Infinity','10000',True,None):
            value=dict(POLICY,fee_bps=bad)
            with self.assertRaises(ValueError):from_policy(value)
            if os.name=='posix':
                with tempfile.TemporaryDirectory(dir='/tmp') as temp:
                    p=Path(temp).resolve()/'absent'/'bad'
                    with self.assertRaises(ValueError):initialize(p,cost_policy=value)
                    self.assertFalse(p.parent.exists())
        for field,number in [('slippage_bps','10000'),('full_spread_bps','20000')]:
            with self.assertRaises(ValueError):from_policy(dict(POLICY,**{field:number}))

    def test_cost_module_import_does_not_import_research_or_network_stack(self):
        import subprocess,sys
        code="import sys; from trading.costs import CostModel; assert not any(x in sys.modules for x in ('pandas','numpy','pyupbit','backtesting','requests'))"
        r=subprocess.run([sys.executable,'-c',code],capture_output=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr.decode())
    def test_cli_requires_all_costs_at_init_only_without_any_side_effect(self):
        import io
        from unittest.mock import patch
        from ops import paper_bot as cli
        for args in (['init','--fee-bps','5'],['init','--fee-bps','NaN','--slippage-bps','5','--full-spread-bps','2'],
                     ['check','--fee-bps','5','--slippage-bps','5','--full-spread-bps','2']):
            with patch('sys.stderr',new_callable=io.StringIO),patch.object(cli,'initialize',side_effect=AssertionError('No creation')):
                with self.assertRaises(SystemExit):cli.main([*args,'--directory','unused'])
