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
