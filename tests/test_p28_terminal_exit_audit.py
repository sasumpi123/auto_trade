"""P28 synthetic-only tests: offline terminal-exit audit of saved P21 reports. Sockets forbidden."""
from copy import deepcopy
from decimal import Decimal as D
import json,os,socket,subprocess,sys,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backtesting import p19_baseline as base
from backtesting import p21_entry_filter as p21
from backtesting import terminal_exit_audit as t
from trading.costs import CostModel

ROOT=Path(__file__).resolve().parents[1]
I=D('1000000')
DOC_COMMAND='python -m backtesting.terminal_exit_audit --input "$P21_RESULTS" --output "$P28_OUT"'
BEGIN,END='<!-- P28_COMMAND_BEGIN -->','<!-- P28_COMMAND_END -->'


def doc_block():
    """The whole documented bash block, exactly as a user copies it."""
    doc=(ROOT/'docs/P28_TERMINAL_EXIT_AUDIT.md').read_text(encoding='utf-8')
    body=doc.split(BEGIN,1)[1].split(END,1)[0].strip()
    assert body.startswith('```bash\n') and body.endswith('\n```'),body
    return body[len('```bash\n'):-len('\n```')]


class Recorder:
    """Wraps a real binary file; records every requested and returned read size."""
    def __init__(self,path,log,on_read=None):self.f=open(path,'rb');self.log=log;self.on_read=on_read
    def __enter__(self):return self
    def __exit__(self,*a):self.f.close()
    def read(self,n=-1):
        assert type(n) is int and n>0,'unbounded read request'
        data=self.f.read(n);self.log.append((n,len(data)))
        if self.on_read:self.on_read()
        return data


def policy(*,cash,q='0',mark='100',remaining='0',buys=1,sells=1,filtered=False,costs=None):
    """Self-consistent compact() record (P9 identities hold exactly)."""
    cash,q,mark,remaining=D(cash),D(q),D(mark),D(remaining)
    equity=cash+q*mark;unrealized=q*mark-remaining;realized=equity-I-unrealized;trades=buys+sells
    return {'status':'TRADES' if trades else 'NO_TRADES','net_return_pct':str((equity-I)/I*100) if trades else None,
        'fills':{'buys':buys,'sells':sells},'fees':'12.5','mdd_pct':'1.25' if trades else None,
        'holding_fraction':'0.5','holding_basis':'HELD_MINUTE_OPEN_SAMPLES_OVER_EVALUATED_OPENS',
        'time_held_pct':'50','mean_capital_exposure_pct':'20',
        'realized_pnl':str(realized),'unrealized_pnl':str(unrealized),'total_pnl':str(realized+unrealized),
        'final_equity':str(equity),'cash':str(cash),
        'open_position':{'quantity':str(q),'remaining_cost':str(remaining),'mark_price':str(mark),
            'mark_at_utc':'2026-10-31T00:00:00+00:00','valuation':t.VALUATION,
            'entry_first_at_utc':'2026-10-30T23:00:00+00:00' if q>0 else None},
        'p9_reconciled':True,
        'entry_filter':{'enabled':filtered,'threshold_bps':'44' if filtered else None,'checks':0,'blocked':0,'allowed':0},
        'costs':dict(costs or t.P21_COSTS),'slippage_cost':'1','half_spread_cost':'0.5'}


def flat(filtered=False):return policy(cash='999000',buys=1,sells=1,filtered=filtered)
def no_trades(filtered=False):return policy(cash='1000000',buys=0,sells=0,filtered=filtered)


def report(pairs=None,excluded=()):
    """P21_FIXED_44BPS_V1 as written by p21.work/analyze/write_report."""
    excluded=list(excluded)
    universe=[(d,m) for d in base.DAYS for m in base.MARKETS if (d,m) not in excluded]
    pairs=pairs or {}
    cases=[];done=[]
    for d,m in universe:
        a,b=pairs.get((d,m),(flat(),flat(True)))
        done+= [{'day':d,'market':m,'policy':p21.POLICIES[0],'result':a},{'day':d,'market':m,'policy':p21.POLICIES[1],'result':b}]
        cases.append(p21.comparison(d,m,a,b))
    return {'schema':'P21_FIXED_44BPS_V1','status':'VERIFIED_PARTIAL' if excluded else 'VERIFIED','hypothesis':p21.hypothesis(),
        'maximum_cases':12,'network_calls':0,'cases':cases,'completed_policies':done,
        'excluded':[{'day':d,'market':m,'reason':'P19_BASELINE_EXCLUDED'} for d,m in excluded],
        'p19_results_sha256':'a'*64,'protocol_sha256':'b'*64,'elapsed_seconds':1.0,'unfinished':[],'unfinished_policies':[]}


class Workspace:
    def __enter__(self):
        self.temp=TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
        return self
    def __exit__(self,*a):self.guard.stop();self.temp.cleanup()
    def write(self,value,name='p21',raw=None):
        folder=self.root/name;folder.mkdir();path=folder/'results.json'
        path.write_bytes(raw if raw is not None else json.dumps(value,indent=2).encode())
        return path
    def run(self,value,name='p21',raw=None,**kw):
        path=self.write(value,name,raw);before=path.read_bytes()
        out,r=t.audit(path,self.root/(name+'-out'),**kw)
        assert path.read_bytes()==before
        return out,r


class FormulaTests(unittest.TestCase):
    def test_independent_marked_positive_becomes_nonpositive_after_exit(self):
        """Hand-computed: Q=1, P=1,000,500, C=0, remaining 1,000,000; impact (5+2/2)/10000; fee 5bps on funds."""
        r=policy(cash='0',q='1',mark='1000500',remaining='1000000',buys=1,sells=0)
        self.assertEqual(r['open_position']['valuation'],'LAST_CANDLE_CLOSE_NO_HYPOTHETICAL_EXIT_COST')
        self.assertNotIn('hypothetical_exit_equity',r)  # compact() never provided a post-exit value.
        self.assertEqual(D(r['net_return_pct']),D('0.05'))
        a=t.exit_audit(r,p21.POLICIES[0])
        self.assertEqual(D(a['exit_price']),D('999899.7'))
        self.assertEqual(D(a['exit_funds']),D('999899.7'))
        self.assertEqual(D(a['exit_fee']),D('499.94985'))
        self.assertEqual(D(a['exit_proceeds']),D('999399.75015'))
        self.assertEqual(D(a['hypothetical_exit_equity']),D('999399.75015'))
        self.assertEqual(D(a['hypothetical_exit_pnl']),D('-600.24985'))
        self.assertEqual(D(a['hypothetical_exit_return_pct']),D('-0.060024985'))
        self.assertEqual(D(a['extra_exit_cost']),D('1100.24985'))
        self.assertEqual((D(a['extra_slippage']),D(a['extra_half_spread'])),(D('500.25'),D('100.05')))
        self.assertTrue(a['marked_positive_exit_nonpositive'])
        self.assertEqual(D(a['marked_equity']),D('1000500'))
        self.assertEqual(a['original_unchanged']['fills'],{'buys':1,'sells':0})
        self.assertFalse(a['mdd_recomputed']);self.assertFalse(a['hypothetical_sell_counted_as_fill'])

    def test_flat_round_trip_has_exactly_zero_extra_cost(self):
        a=t.exit_audit(flat(),p21.POLICIES[0])
        self.assertIsNone(a['exit_price'])
        for k in ('exit_funds','exit_fee','extra_exit_cost','extra_slippage','extra_half_spread'):self.assertEqual(D(a[k]),0)
        self.assertEqual(a['hypothetical_exit_equity'],a['marked_equity'])
        self.assertEqual(D(a['hypothetical_exit_return_pct']),D(flat()['net_return_pct']))
        self.assertEqual(a['original_unchanged']['realized_pnl'],flat()['realized_pnl'])
        self.assertFalse(a['marked_positive_exit_nonpositive'])

    def test_held_negative_fractional_fee_on_funds_half_spread(self):
        q,p=D('0.00012345'),D('98765432.1')
        r=policy(cash='980000',q=str(q),mark=str(p),remaining='12000',buys=2,sells=1)
        a=t.exit_audit(r,p21.POLICIES[0])
        price=p*(1-D('0.0006'));funds=q*price;fee=funds*D('0.0005')
        self.assertEqual(D(a['exit_price']),price);self.assertEqual(D(a['exit_fee']),fee)
        self.assertNotEqual(fee,q*p*D('0.0005'))  # Fee is charged on executed funds, not the mark.
        self.assertEqual(D(a['extra_half_spread']),q*p*D('0.0001'))  # Half of the 2bps full spread.
        self.assertEqual(D(a['hypothetical_exit_equity']),D('980000')+funds-fee)
        self.assertLess(D(a['hypothetical_exit_pnl']),D(a['marked_pnl']))
        self.assertLess(D(a['marked_pnl']),0);self.assertFalse(a['marked_positive_exit_nonpositive'])

    def test_held_positive_stays_positive_and_past_costs_not_deducted_twice(self):
        r=policy(cash='600000',q='4',mark='110000',remaining='400200',buys=1,sells=0)
        a=t.exit_audit(r,p21.POLICIES[0])
        heavy=deepcopy(r);heavy.update(fees='99999',slippage_cost='88888',half_spread_cost='7777')
        self.assertEqual(t.exit_audit(heavy,p21.POLICIES[0])['hypothetical_exit_equity'],a['hypothetical_exit_equity'])
        self.assertEqual(D(a['hypothetical_exit_pnl']),D(r['realized_pnl'])+D(a['exit_proceeds'])-D('400200'))
        self.assertGreater(D(a['hypothetical_exit_pnl']),0);self.assertFalse(a['marked_positive_exit_nonpositive'])

    def test_no_trades_keeps_na_returns_and_zero_cost(self):
        a=t.exit_audit(no_trades(),p21.POLICIES[0])
        self.assertIsNone(a['marked_return_pct']);self.assertIsNone(a['hypothetical_exit_return_pct'])
        self.assertEqual(D(a['extra_exit_cost']),0)
        bad=no_trades();bad['net_return_pct']='0'
        with self.assertRaisesRegex(ValueError,'P28_NO_TRADES_MISMATCH'):t.exit_audit(bad,p21.POLICIES[0])

    def test_invalid_numbers_costs_and_accounting_are_blocked(self):
        held=policy(cash='0',q='1',mark='1000500',remaining='1000000',buys=1,sells=0)
        cases=[(lambda r:r['open_position'].update(mark_price=None),'P28_INVALID_NUMBER'),
            (lambda r:r['open_position'].update(quantity=True),'P28_INVALID_NUMBER'),
            (lambda r:r['open_position'].update(quantity='NaN'),'P28_INVALID_NUMBER'),
            (lambda r:r['open_position'].update(quantity='-1'),'P28_INVALID_NUMBER'),
            (lambda r:r.update(cash=0.0),'P28_INVALID_NUMBER'),
            (lambda r:r.update(cash='1'*81),'P28_INVALID_NUMBER'),
            (lambda r:r['open_position'].update(mark_at_utc=None),'P28_MARK_MISSING'),
            (lambda r:r['open_position'].update(remaining_cost='0'),'P28_OPEN_POSITION_INCOMPLETE'),
            (lambda r:r['open_position'].update(valuation='OTHER'),'P28_VALUATION_MISMATCH'),
            (lambda r:r.update(costs={'fee_bps':'10','slippage_bps':'10','spread_bps':'2'}),'P28_COST_MISMATCH'),
            (lambda r:r.update(costs=None),'P28_COST_MISMATCH'),
            (lambda r:r.update(p9_reconciled='true'),'P28_P9_UNVERIFIED'),
            (lambda r:r.update(cash='1'),'P28_CASH_EQUITY_MISMATCH'),
            (lambda r:r.update(realized_pnl='1'),'P28_TOTAL_PNL_MISMATCH'),
            (lambda r:r.update(total_pnl='1'),'P28_TOTAL_PNL_MISMATCH'),
            (lambda r:r.update(unrealized_pnl='1',realized_pnl=str(D(r['final_equity'])-I-1)),'P28_UNREALIZED_MISMATCH'),
            (lambda r:r.update(net_return_pct='9'),'P28_RETURN_MISMATCH'),
            (lambda r:r['fills'].update(buys=False),'P28_INVALID_COUNT'),
            (lambda r:r.update(status='NO_TRADES'),'P28_STATUS_MISMATCH')]
        for change,reason in cases:
            r=deepcopy(held);change(r)
            with self.subTest(reason=reason),self.assertRaisesRegex(ValueError,reason):t.exit_audit(r,p21.POLICIES[0])
        with self.assertRaisesRegex(ValueError,'P28_POLICY_MISMATCH'):t.exit_audit(held,p21.POLICIES[1])


@unittest.skipUnless(os.name=='posix','P17 disposable accounts require POSIX')
class PaperEngineTests(unittest.TestCase):
    def engine(self,folder,clock):
        folder.mkdir(mode=0o700)
        e=base.P17ResearchEngine(folder/'unused',1000000,CostModel(5,5,2),lambda:clock[0]);e.open();return e

    def test_held_audit_matches_disposable_p17_paper_sell_at_same_reference(self):
        for exit_reference in (D('10100'),D('9800'),D('10012')):
            with self.subTest(mark=exit_reference),TemporaryDirectory() as temp,patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
                root=Path(temp).resolve();clock=[1800000000]
                held=self.engine(root/'held',clock);sold=self.engine(root/'sold',clock)
                try:
                    for e in (held,sold):self.assertTrue(e.buy('KRW-BTC',10000,400000))
                    clock[0]+=60;self.assertTrue(sold.sell('KRW-BTC',exit_reference))
                    cash=held.cash;q=D(held.position('KRW-BTC')['quantity'])
                    r=policy(cash=str(cash),q=str(q),mark=str(exit_reference),remaining=str(I-cash),buys=1,sells=0)
                    a=t.exit_audit(r,p21.POLICIES[0])
                    self.assertTrue(t.close(D(a['hypothetical_exit_equity']),sold.cash))
                    self.assertEqual(D(sold.position('KRW-BTC')['quantity']),0)
                    self.assertTrue(t.close(D(a['exit_fee']),D(next(o for o in sold.state['orders'].values() if o['side']=='ask')['paid_fee'])))
                    self.assertEqual(sum(o['side']=='ask' for o in held.state['orders'].values()),0)  # Audit never sells.
                finally:held.close();sold.close()

    def test_round_trip_account_has_zero_extra_cost_and_ledger_bytes_unchanged(self):
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();clock=[1800000000];e=self.engine(root/'rt',clock)
            try:
                e.buy('KRW-BTC',10000,400000);clock[0]+=60;e.sell('KRW-BTC',10050);cash=e.cash
                state=sorted((root/'rt').rglob('*'));before={f:f.read_bytes() for f in state if f.is_file()}
                r=policy(cash=str(cash),buys=1,sells=1);a=t.exit_audit(r,p21.POLICIES[0])
                self.assertEqual(D(a['extra_exit_cost']),0);self.assertEqual(D(a['hypothetical_exit_equity']),cash)
                self.assertEqual(a['original_unchanged']['realized_pnl'],str(cash-I))
                self.assertEqual(len(e.state['orders']),2)
                self.assertEqual(before,{f:f.read_bytes() for f in state if f.is_file()})
            finally:e.close()


class ReportTests(unittest.TestCase):
    def test_complete_report_six_pairs_twelve_policies(self):
        held=(policy(cash='0',q='1',mark='1000500',remaining='1000000',buys=1,sells=0),
              policy(cash='600000',q='4',mark='110000',remaining='400200',buys=1,sells=0,filtered=True))
        nt=(no_trades(),flat(True))
        with Workspace() as w:
            out,r=w.run(report({(base.DAYS[0],'KRW-BTC'):held,(base.DAYS[1],'KRW-XRP'):nt}))
            self.assertEqual(r['status'],'AUDITED');self.assertEqual(len(r['cases']),6);self.assertEqual(r['excluded'],[])
            self.assertFalse((out/'INCOMPLETE').exists())
            for name in ('results.json','REPORT.md','terminal_exit.csv'):self.assertTrue((out/name).is_file())
            first=r['cases'][0];self.assertTrue(first['baseline']['marked_positive_exit_nonpositive'])
            self.assertEqual(D(first['hypothetical_exit_delta_percentage_points']),
                D(first['candidate']['hypothetical_exit_return_pct'])-D(first['baseline']['hypothetical_exit_return_pct']))
            na=next(c for c in r['cases'] if c['market']=='KRW-XRP' and c['day']==base.DAYS[1])
            self.assertEqual(na['status'],'NO_TRADES');self.assertIsNone(na['hypothetical_exit_delta_percentage_points'])
            self.assertEqual(len(r['input']['sha256']),64);self.assertEqual(r['input']['schema'],'P21_FIXED_44BPS_V1')
            self.assertIn('git_commit',r['tool']);self.assertIn('python',r['environment'])
            self.assertEqual(len((out/'terminal_exit.csv').read_text().splitlines()),13)
            saved=json.loads((out/'results.json').read_text());self.assertEqual(saved['status'],'AUDITED')

    def test_partial_preserves_excluded_and_is_never_promoted(self):
        gone=[(base.DAYS[0],'KRW-BTC'),(base.DAYS[2],'KRW-XRP')]
        with Workspace() as w:
            _,r=w.run(report(excluded=gone))
            self.assertEqual(r['status'],'AUDITED_PARTIAL');self.assertEqual(len(r['cases']),4)
            self.assertEqual({(e['day'],e['market']) for e in r['excluded']},set(gone))
            bad=report(excluded=gone);bad['status']='VERIFIED'
            _,r=w.run(bad,'promoted');self.assertEqual(r['failure_reason'],'P28_STATUS_MISMATCH')

    def test_schema_duplicates_conflicts_and_missing_fail_closed(self):
        def mutate(f):
            r=report();f(r);return r
        held=policy(cash='0',q='1',mark='1000500',remaining='1000000',buys=1,sells=0)
        cases=[('schema',lambda r:r.update(schema='P22'),'P28_SCHEMA_MISMATCH'),
            ('incomplete',lambda r:r.update(status='INCOMPLETE'),'P28_INPUT_NOT_VERIFIED'),
            ('failure',lambda r:r.update(failure_reason='X'),'P28_INPUT_NOT_VERIFIED'),
            ('unfinished',lambda r:r.update(unfinished_policies=[{'day':base.DAYS[0],'market':'KRW-BTC','policy':'legacy'}]),'P28_UNFINISHED_POLICIES'),
            ('dupcase',lambda r:r['cases'].__setitem__(1,deepcopy(r['cases'][0])),'P28_DUPLICATE_CASE'),
            ('dupexcl',lambda r:r['excluded'].append({'day':r['cases'][0]['day'],'market':r['cases'][0]['market'],'reason':'X'}),'P28_CASE_COVERAGE_MISMATCH'),
            ('missing',lambda r:r['cases'].pop(),'P28_CASE_COVERAGE_MISMATCH'),
            ('dupolicy',lambda r:r['completed_policies'].__setitem__(1,deepcopy(r['completed_policies'][0])),'P28_DUPLICATE_POLICY'),
            ('nopolicy',lambda r:r['completed_policies'].pop(),'P28_POLICY_COVERAGE_MISMATCH'),
            ('extra',lambda r:r['completed_policies'].append(deepcopy(r['completed_policies'][0])),'P28_CASE_LIMIT'),
            ('conflict',lambda r:r['completed_policies'][0].update(result=flat()|{'fees':'1'}),'P28_PAIR_POLICY_CONFLICT'),
            ('pair',lambda r:r['cases'][0].update(delta_net_percentage_points='1'),'P28_PAIR_MISMATCH'),
            ('both',lambda r:(r['cases'][0].update(baseline=held),r['completed_policies'][0].update(result=held)),'P28_PAIR_MISMATCH'),
            ('limit',lambda r:r['cases'].extend([r['cases'][0]]),'P28_CASE_LIMIT'),
            ('format',lambda r:r.update(cases={}),'P28_REPORT_FORMAT'),
            ('cost',lambda r:[x['result'].update(costs={'fee_bps':'0','slippage_bps':'0','spread_bps':'0'}) for x in r['completed_policies']],'P28_COST_MISMATCH')]
        with Workspace() as w:
            for name,f,reason in cases:
                with self.subTest(name):
                    out,r=w.run(mutate(f),name)
                    self.assertEqual((r['status'],r['failure_reason']),('INCOMPLETE',reason))
                    self.assertTrue((out/'INCOMPLETE').exists());self.assertFalse((out/'REPORT.md').exists())
                    self.assertEqual(json.loads((out/'results.json').read_text())['cases'],[])

    def test_corrupt_json_nan_duplicate_keys_size_incomplete_marker(self):
        good=json.dumps(report()).encode()
        raws=[('corrupt',good[:-5],'P28_INVALID_JSON'),('nan',good.replace(b'"12.5"',b'NaN',1),'P28_INVALID_JSON'),
            ('dupkey',b'{"schema":"a","schema":"b"}','P28_DUPLICATE_KEY'),('array',b'[]','P28_INVALID_JSON'),
            ('empty',b'','P28_INPUT_SIZE'),('utf',b'\xff\xfe','P28_INVALID_JSON')]
        with Workspace() as w:
            for name,raw,reason in raws:
                with self.subTest(name):self.assertEqual(w.run(None,name,raw=raw)[1]['failure_reason'],reason)
            with patch.object(t,'MAX_INPUT_BYTES',10):self.assertEqual(w.run(report(),'big')[1]['failure_reason'],'P28_INPUT_SIZE')
            path=w.write(report(),'marked');(path.parent/'INCOMPLETE').touch()
            self.assertEqual(t.audit(path,w.root/'marked-out')[1]['failure_reason'],'P28_INPUT_INCOMPLETE')

    def test_input_changed_existing_output_symlink_and_timeout(self):
        with Workspace() as w:
            path=w.write(report(),'changed');real=t.check_report
            def tamper(*a):
                value=real(*a);path.write_bytes(path.read_bytes()+b' ');return value
            with patch.object(t,'check_report',side_effect=tamper):
                self.assertEqual(t.audit(path,w.root/'changed-out')[1]['failure_reason'],'P28_INPUT_CHANGED')
            path=w.write(report(),'ok');(w.root/'exists').mkdir()
            with self.assertRaisesRegex(ValueError,'OUTPUT_EXISTS_NO_OVERWRITE'):t.audit(path,w.root/'exists')
            (w.root/'link').symlink_to(path.parent)
            _,r=t.audit(w.root/'link'/'results.json',w.root/'link-out');self.assertEqual(r['failure_reason'],'SYMLINK_FORBIDDEN')
            with self.assertRaises(ValueError):t.audit(path,w.root/'link'/'nested')
            ticks=iter([0,0,31,31,31,31,31])
            _,r=t.audit(path,w.root/'slow',clock=lambda:next(ticks));self.assertEqual(r['failure_reason'],'P28_TIME_LIMIT')
            with self.assertRaisesRegex(ValueError,'TIME_LIMIT'):t.audit(path,w.root/'long',seconds=31)
            with self.assertRaisesRegex(ValueError,'PRIVATE_OUTPUT_OUTSIDE_CHECKOUT_REQUIRED'):t.audit(path,ROOT/'research'/'p28-never')

    def test_summary_has_at_most_eight_lines(self):
        with Workspace() as w:
            held=(policy(cash='0',q='1',mark='1000500',remaining='1000000',buys=1,sells=0),flat(True))
            _,r=w.run(report({(base.DAYS[0],'KRW-BTC'):held}))
            lines=t.summary(r,0);self.assertLessEqual(len(lines),8);self.assertEqual(len(lines),8)
            self.assertIn('MARKED_POSITIVE_EXIT_NONPOSITIVE',lines[1]);self.assertTrue(lines[0].startswith('P28 AUDITED pairs=6'))


class BoundedReadTests(unittest.TestCase):
    def setUp(self):
        self.w=Workspace().__enter__();self.raw=json.dumps(report()).encode()
    def tearDown(self):self.w.__exit__()
    def rec(self,logs,grow=None):
        def opener(path):
            log=[];logs.append(log);return Recorder(path,log,grow)
        return patch.object(t,'_open',side_effect=opener)

    def test_exact_limit_accepted_and_reads_never_exceed_limit_plus_one(self):
        logs=[]
        with patch.object(t,'MAX_INPUT_BYTES',len(self.raw)),patch.object(t,'CHUNK_BYTES',4096),self.rec(logs):
            _,r=self.w.run(None,'exact',raw=self.raw)
        self.assertEqual(r['status'],'AUDITED');self.assertEqual(len(logs),2)  # Initial load and re-hash.
        for log in logs:
            self.assertLessEqual(sum(n for _,n in log),len(self.raw)+1)
            self.assertLessEqual(max(n for n,_ in log),len(self.raw)+1)

    def test_large_file_is_rejected_after_at_most_limit_plus_one_bytes(self):
        logs=[]
        with patch.object(t,'MAX_INPUT_BYTES',64),patch.object(t,'CHUNK_BYTES',16),self.rec(logs):
            out,r=self.w.run(None,'large',raw=b' '*100000)
        self.assertEqual(r['failure_reason'],'P28_INPUT_SIZE');self.assertTrue((out/'INCOMPLETE').exists())
        # Early stat rejection reads nothing; any read that happens is still bounded.
        self.assertLessEqual(sum(n for log in logs for _,n in log),65);self.assertLessEqual(sum(n for log in logs for n,_ in log),65)
        with patch.object(t,'MAX_INPUT_BYTES',64),patch.object(t,'CHUNK_BYTES',16),self.rec(logs):
            path=self.w.write(None,'direct',raw=b' '*100000)
            with self.assertRaisesRegex(ValueError,'P28_INPUT_SIZE'):t.read_bounded(path)  # Without the stat shortcut.
        self.assertEqual(sum(n for _,n in logs[-1]),65);self.assertLessEqual(max(n for n,_ in logs[-1]),16)

    def test_growth_during_read_stays_bounded(self):
        path=self.w.write(None,'growing',raw=b'{'+b' '*40);logs=[]
        def grow():
            with open(path,'ab') as f:f.write(b' '*100000)
        with patch.object(t,'MAX_INPUT_BYTES',64),patch.object(t,'CHUNK_BYTES',16),self.rec(logs,grow):
            _,r=t.audit(path,self.w.root/'growing-out')
        self.assertEqual(r['failure_reason'],'P28_INPUT_SIZE')
        self.assertLessEqual(sum(n for _,n in logs[0]),65)

    def test_growth_before_rehash_is_change_and_bounded(self):
        path=self.w.write(None,'regrow',raw=self.raw);real=t.check_report;logs=[]
        def tamper(*a):
            value=real(*a)
            with open(path,'ab') as f:f.write(b' '*1000000)
            return value
        with patch.object(t,'MAX_INPUT_BYTES',len(self.raw)),patch.object(t,'CHUNK_BYTES',4096),\
                patch.object(t,'check_report',side_effect=tamper),self.rec(logs):
            out,r=t.audit(path,self.w.root/'regrow-out')
        self.assertEqual(r['failure_reason'],'P28_INPUT_CHANGED');self.assertTrue((out/'INCOMPLETE').exists())
        self.assertEqual(len(logs),2);self.assertLessEqual(sum(n for _,n in logs[1]),len(self.raw)+1)


class CommandTests(unittest.TestCase):
    def run_block(self,root,stale):
        """Run the full documented block with bash; only the interpreter path is substituted."""
        home=root/'home';source=home/'research'/'p21-output';source.mkdir(parents=True)
        (source/'results.json').write_text(json.dumps(report(excluded=[(base.DAYS[0],'KRW-BTC')])))
        block=doc_block();self.assertIn('.venv/bin/python -m backtesting.terminal_exit_audit',block)
        script=block.replace('.venv/bin/python',f'"{sys.executable}"')
        env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','P21_RESULTS','P28_OUT')}
        env['HOME']=str(home)
        if stale:env.update(P21_RESULTS=str(root/'stale.json'),P28_OUT=str(root/'stale-out'))
        done=subprocess.run(['bash','-c',script],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
        return home,done

    def test_full_documented_block_passes_real_paths_unset_and_stale(self):
        for stale in (False,True):
            with self.subTest(stale=stale),TemporaryDirectory() as temp:
                root=Path(temp).resolve();home,done=self.run_block(root,stale);lines=done.stdout.splitlines()
                self.assertEqual(done.returncode,0,done.stdout+done.stderr);self.assertLessEqual(len(lines),8)
                self.assertTrue(lines[0].startswith('P28 AUDITED_PARTIAL pairs=5 excluded=1 exit=0'),lines)
                outs=list((home/'research').glob('p28-*'))
                self.assertEqual(len(outs),1);self.assertTrue((outs[0]/'results.json').is_file())
                self.assertFalse((root/'stale-out').exists())
                self.assertNotIn(str(root),done.stdout);self.assertNotIn('Traceback',done.stdout+done.stderr)

    def test_documented_command_in_clean_environment(self):
        doc=(ROOT/'docs/P28_TERMINAL_EXIT_AUDIT.md').read_text(encoding='utf-8')
        self.assertIn(DOC_COMMAND,doc)
        with TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'p21';source.mkdir()
            (source/'results.json').write_text(json.dumps(report(excluded=[(base.DAYS[0],'KRW-BTC')])))
            env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')}
            env.update(PYTHON_DOTENV_DISABLED='1',P21_RESULTS=str(source/'results.json'),P28_OUT=str(root/'p28'))
            command=DOC_COMMAND.replace('python ',f'"{sys.executable}" ',1)
            def run():return subprocess.run(command,shell=True,cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
            done=run();lines=done.stdout.splitlines()
            self.assertEqual(done.returncode,0,done.stderr);self.assertLessEqual(len(lines),8);self.assertEqual(done.stderr,'')
            self.assertTrue(lines[0].startswith('P28 AUDITED_PARTIAL pairs=5 excluded=1 exit=0'))
            self.assertNotIn(str(root),done.stdout)
            again=run();lines=again.stdout.splitlines()  # Existing output is never overwritten.
            self.assertEqual(again.returncode,2);self.assertLessEqual(len(lines),8)
            self.assertIn('reason=OUTPUT_EXISTS_NO_OVERWRITE',lines[0]);self.assertNotIn(str(root),again.stdout)
            self.assertNotIn('Traceback',again.stdout+again.stderr)


if __name__=='__main__':unittest.main()
