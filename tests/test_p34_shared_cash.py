"""P34 synthetic-only tests: one PAPER account, two markets, shared cash, averaging and stop. No network."""
from decimal import Decimal as D
import json,os,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backtesting import p19_baseline as base
from backtesting import p34_shared_cash as p34

ROOT=Path(__file__).resolve().parents[1]
F=D('0.0005');BUY=D('1.0006');SELL=D('0.9994')


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class SharedCashTests(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
    def tearDown(self):self.guard.stop();self.t.cleanup()
    def run_case(self,scenario,order):
        self.n=getattr(self,'n',0)+1  # Every run gets its own brand-new disposable account.
        return p34.run(p34.ORDERS[order],scenario,self.root/f'{scenario}-{order}-{self.n}')

    def test_entries_use_fee_inclusive_40_percent_each_from_one_cash_pool(self):
        steps,_=self.run_case('drop_then_further_drop','btc_first')
        e1,e2=steps[0],steps[1]
        self.assertEqual(D(e1['after']['cash']),D(600000));self.assertEqual(D(e2['after']['cash']),D(200000))
        fee=D(400000)/(1+F)*F
        for s,m in ((e1,p34.BTC),(e2,p34.XRP)):
            order=s['orders'][0];self.assertEqual((order['side'],order['market'],order['averaging']),('BUY',m,False))
            self.assertTrue(base.close(D(order['fee']),fee))
            q=D(400000)/(1+F)/(p34.ENTRY[m]*BUY);self.assertTrue(base.close(D(s['after']['positions'][m]['quantity']),q))
        self.assertTrue(base.close(D(e2['after']['total_exposure_ratio']),(2*q*p34.ENTRY[p34.XRP])/(D(200000)+2*q*p34.ENTRY[p34.XRP])))

    def test_first_processed_market_averages_second_is_stopped_for_lack_of_cash(self):
        for order in ('btc_first','xrp_first'):
            with self.subTest(order=order):
                steps,rec=self.run_case('drop_then_further_drop',order);first,second=p34.ORDERS[order]
                avg,stop=steps[2],steps[3]
                self.assertEqual([(e['market'],e['reason'],e['averaging']) for e in avg['orders']],[(first,'물타기 매수',True)])
                q=D(avg['before']['positions'][first]['quantity']);price=D(avg['price'])
                self.assertTrue(base.close(D(avg['after']['cash']),D(200000)-q*price*D('0.5')))  # Fee inside the requested amount.
                need=D(stop['before']['positions'][second]['quantity'])*D(stop['price'])*D('0.5')
                self.assertGreater(need,D(stop['before']['cash']))  # Averaging request exceeds the shared cash...
                self.assertEqual([(e['market'],e['reason']) for e in stop['orders']],[(second,'손절')])  # ...so the bot stops instead.
                qs=D(stop['before']['positions'][second]['quantity'])
                self.assertTrue(base.close(D(stop['after']['cash'])-D(stop['before']['cash']),qs*D(stop['price'])*SELL*(1-F)))
                self.assertTrue(rec['p9_verified']);self.assertEqual((rec['buys'],rec['sells']),(3,2))
                self.assertGreater(D(avg['after']['total_exposure_ratio']),D('0.98'))

    def test_gap_and_further_drop_stop_after_averaging_and_symmetry(self):
        a=self.run_case('gap_down_then_recover','btc_first')[1];b=self.run_case('gap_down_then_recover','xrp_first')[1]
        self.assertEqual(a['total_pnl'],b['total_pnl'])  # Symmetric paths: the order only changes WHICH market averaged.
        steps,_=self.run_case('gap_down_then_recover','btc_first')
        gap_stop=steps[3];self.assertEqual(gap_stop['orders'][0]['reason'],'손절')
        self.assertLess(D(gap_stop['price']),p34.ENTRY[p34.XRP]*D('0.95'))  # Gap: realized beyond the nominal -5% line.
        rebound=steps[4];self.assertEqual(rebound['orders'][0]['reason'],'손절')  # -10% is still below -5% of the new average.

    def test_processing_order_changes_the_diverging_outcome_and_end_positions(self):
        btc=self.run_case('drop_then_diverge','btc_first')[1];xrp=self.run_case('drop_then_diverge','xrp_first')[1]
        self.assertGreater(D(btc['remaining_quantity'][p34.BTC]),0);self.assertEqual(D(btc['remaining_quantity'][p34.XRP]),0)
        self.assertEqual(D(xrp['remaining_quantity'][p34.BTC]),0);self.assertEqual(D(xrp['remaining_quantity'][p34.XRP]),0)
        self.assertNotEqual(D(btc['total_pnl']),D(xrp['total_pnl']))
        self.assertGreater(D(btc['theoretical_max_account_loss_at_peak_exposure']),D(btc['realized_and_marked_synthetic_loss']))
        self.assertEqual(btc['theoretical_max_account_loss_at_peak_exposure'],xrp['theoretical_max_account_loss_at_peak_exposure'])

    def test_p9_reconciliation_detects_tampering(self):
        _,rec=self.run_case('drop_then_diverge','btc_first')
        self.assertTrue(base.close(D(rec['marked_equity'])-D(1000000),D(rec['net_realized_pnl'])+D(rec['unrealized_pnl'])))
        state={'cash':rec['cash'],'positions':{},'orders':{}}
        ledger={'cash':D(rec['cash'])+1,'fees':D(0),'positions':{},'net_profit':D(0),'buy_count':0,'sell_count':0}
        with self.assertRaisesRegex(ValueError,'P34_P9_CASH_MISMATCH'):p34.reconcile(state,ledger,{})

    def test_cli_output_bounds_and_fresh_output(self):
        out=self.root/'out'
        with patch('sys.stdout') as stdout:code=p34.main(['--output',str(out)])
        self.assertEqual(code,0);report=json.loads((out/'results.json').read_text())
        self.assertEqual((report['status'],len(report['runs'])),('VERIFIED',6))
        self.assertLessEqual(len(p34.summary(report)),8);self.assertTrue((out/'steps.csv').is_file());self.assertFalse((out/'INCOMPLETE').exists())
        with patch('sys.stdout'):self.assertEqual(p34.main(['--output',str(out)]),2)  # Existing output never overwritten.

    def test_p33_registration_and_frozen_sources_untouched(self):
        from backtesting import p33_forward as p33
        from tools import p22_isolated as iso
        p33.protocol();self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])


if __name__=='__main__':unittest.main()
