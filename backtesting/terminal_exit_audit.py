"""P28 offline audit: P21 last-close valuation beside a hypothetical full terminal exit.

Run from the repository root:
    python -m backtesting.terminal_exit_audit --input P21_RESULTS_JSON --output NEW_PRIVATE_DIRECTORY

Read-only post-processing of one saved P21_FIXED_44BPS_V1 report. No network, refetch, replay,
strategy, parameter search, subprocess or account access. The original close valuation is kept;
the hypothetical exit is a model value (same stored CostModel, half spread, sell fee on funds),
not an executed fill, realized PnL, liquidity guarantee, lower bound or profitability evidence.
"""
import argparse
import csv
import hashlib
import json
import platform
import re
import time
from decimal import Decimal
from pathlib import Path

from . import p19_baseline as base
from . import p21_entry_filter as p21
from services.notification_metrics import close
from trading.costs import CostModel

SCHEMA='P28_TERMINAL_EXIT_AUDIT_V1'
TOOL_VERSION=1
INPUT_SCHEMA='P21_FIXED_44BPS_V1'
INPUT_STATUSES=('VERIFIED','VERIFIED_PARTIAL')
INITIAL_CASH=Decimal('1000000')
P21_COSTS={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}
VALUATION='LAST_CANDLE_CLOSE_NO_HYPOTHETICAL_EXIT_COST'
MAX_PAIRS=6
MAX_POLICIES=12
MAX_INPUT_BYTES=16*1024*1024
CHUNK_BYTES=1024*1024
MAX_SECONDS=30
MAX_DECIMAL_CHARS=80
_DECIMAL=re.compile(r'-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?')
_CODE=re.compile(r'[A-Z][A-Z0-9_]{0,79}')
INTERPRETATION=('Development-period P21 results only; not independent, forward or holdout validation. '
    'Hypothetical exit is a model value at the stored last close with stored costs, not an executed fill, '
    'realized PnL, liquidity guarantee, lower bound or profitability proof. Original realized/unrealized PnL, '
    'fills, fees and MDD are unchanged; MDD is not recomputed from one terminal point.')


class Deadline:
    def __init__(self,seconds=MAX_SECONDS,clock=time.monotonic):
        base.require(type(seconds) in (int,float) and 0<seconds<=MAX_SECONDS,'TIME_LIMIT')
        self.clock=clock;self.end=clock()+seconds
    def check(self):base.require(self.clock()<self.end,'P28_TIME_LIMIT')


def num(value,*,signed=False,positive=False):
    """Strict Decimal from the producer's string form; no bool/float/NaN/Infinity/oversize."""
    base.require(type(value) is str and len(value)<=MAX_DECIMAL_CHARS and _DECIMAL.fullmatch(value),'P28_INVALID_NUMBER')
    n=Decimal(value)
    base.require(n.is_finite() and (signed or n>=0) and (not positive or n>0),'P28_INVALID_NUMBER')
    return n


def count(value):
    base.require(type(value) is int and 0<=value<=10**9,'P28_INVALID_COUNT')
    return value


def _reject_constant(name):raise ValueError('P28_INVALID_JSON')


def _open(path):return open(path,'rb')


def read_bounded(path,limit=None):
    """Read at most limit+1 bytes in bounded chunks, even if the file grows while being read."""
    limit=MAX_INPUT_BYTES if limit is None else limit
    chunks=[];total=0
    with _open(path) as f:
        while True:
            part=f.read(min(CHUNK_BYTES,limit+1-total))
            if not part:break
            total+=len(part);chunks.append(part)
            base.require(total<=limit,'P28_INPUT_SIZE')
    return b''.join(chunks)


def load(path):
    """Bounded strict read; returns (report, sha256, size). Duplicate JSON keys are rejected."""
    path=base.safe_path(path)
    base.require(path.is_file() and not path.is_symlink(),'P28_INPUT_UNREADABLE')
    base.require(not (path.parent/'INCOMPLETE').exists(),'P28_INPUT_INCOMPLETE')
    base.require(path.stat().st_size<=MAX_INPUT_BYTES,'P28_INPUT_SIZE')  # Early reject; read_bounded enforces.
    data=read_bounded(path)
    base.require(len(data)>0,'P28_INPUT_SIZE')
    def pairs(items):
        keys=[k for k,_ in items];base.require(len(keys)==len(set(keys)),'P28_DUPLICATE_KEY')
        return dict(items)
    try:report=json.loads(data.decode('utf-8'),object_pairs_hook=pairs,parse_constant=_reject_constant)
    except ValueError as exc:
        raise ValueError(str(exc) if _CODE.fullmatch(str(exc)) else 'P28_INVALID_JSON') from None
    base.require(type(report) is dict,'P28_INVALID_JSON')
    return path,report,hashlib.sha256(data).hexdigest(),len(data)


def check_policy(result,policy):
    """Numeric P9 identities with the existing reconciliation tolerance (notification_metrics.close)."""
    base.require(type(result) is dict,'P28_POLICY_FORMAT')
    base.require(result.get('costs')==P21_COSTS,'P28_COST_MISMATCH')
    base.require(result.get('p9_reconciled') is True,'P28_P9_UNVERIFIED')
    fills=result.get('fills');base.require(type(fills) is dict and set(fills)=={'buys','sells'},'P28_POLICY_FORMAT')
    trades=count(fills['buys'])+count(fills['sells'])
    base.require(result.get('status')==('TRADES' if trades else 'NO_TRADES'),'P28_STATUS_MISMATCH')
    ef=result.get('entry_filter');filtered=policy==p21.POLICIES[1]
    base.require(type(ef) is dict and ef.get('enabled') is filtered
        and ef.get('threshold_bps')==('44' if filtered else None),'P28_POLICY_MISMATCH')
    pos=result.get('open_position')
    base.require(type(pos) is dict and pos.get('valuation')==VALUATION,'P28_VALUATION_MISMATCH')
    q=num(pos.get('quantity'));remaining=num(pos.get('remaining_cost'));mark=num(pos.get('mark_price'),positive=True)
    base.require(type(pos.get('mark_at_utc')) is str and pos['mark_at_utc'],'P28_MARK_MISSING')
    if q>0:base.require(remaining>0 and type(pos.get('entry_first_at_utc')) is str,'P28_OPEN_POSITION_INCOMPLETE')
    else:base.require(remaining==0,'P28_OPEN_POSITION_INCOMPLETE')
    cash=num(result.get('cash'));equity=num(result.get('final_equity'),positive=True)
    realized=num(result.get('realized_pnl'),signed=True);unrealized=num(result.get('unrealized_pnl'),signed=True)
    total=num(result.get('total_pnl'),signed=True);num(result.get('fees'))
    num(result.get('slippage_cost'));num(result.get('half_spread_cost'))
    base.require(close(cash+q*mark,equity),'P28_CASH_EQUITY_MISMATCH')
    base.require(close(realized+unrealized,equity-INITIAL_CASH),'P28_TOTAL_PNL_MISMATCH')
    base.require(close(total,realized+unrealized),'P28_TOTAL_PNL_MISMATCH')
    base.require(close(unrealized,q*mark-remaining),'P28_UNREALIZED_MISMATCH')
    ret=result.get('net_return_pct')
    if trades:base.require(close(num(ret,signed=True),(equity-INITIAL_CASH)/INITIAL_CASH*100),'P28_RETURN_MISMATCH')
    else:base.require(ret is None and q==0 and close(equity,INITIAL_CASH),'P28_NO_TRADES_MISMATCH')
    return {'trades':trades,'cash':cash,'quantity':q,'mark':mark,'equity':equity,'realized':realized,'remaining_cost':remaining}


def exit_audit(result,policy):
    """C+Q*P versus C+Q*SELL(P)*(1-f). Past costs stay inside cash/remaining cost; never re-deducted."""
    v=check_policy(result,policy)
    model=CostModel(Decimal(P21_COSTS['fee_bps']),Decimal(P21_COSTS['slippage_bps']),Decimal(P21_COSTS['spread_bps']))
    c,q,p=v['cash'],v['quantity'],v['mark'];marked=c+q*p
    if q==0:
        exit_price=None;funds=fee=slip=spread=Decimal(0)
    else:
        exit_price=model.price(p,'SELL');funds=q*exit_price;fee=funds*model.fee_rate
        slip=q*p*model.slippage_bps/10000;spread=q*p*model.spread_bps/20000  # Half of the full spread.
    proceeds=funds-fee;hyp=c+proceeds;diff=marked-hyp
    base.require(close(diff,slip+spread+fee),'P28_EXIT_COST_IDENTITY')
    base.require(q==0 or close(hyp-INITIAL_CASH,v['realized']+proceeds-v['remaining_cost']),'P28_EXIT_PNL_IDENTITY')
    trades=v['trades']>0
    marked_pnl=marked-INITIAL_CASH;hyp_pnl=hyp-INITIAL_CASH
    return {'status':result['status'],'open_quantity':str(q),'mark_price':str(p),'mark_at_utc':result['open_position']['mark_at_utc'],
        'cash':str(c),'marked_equity':str(marked),'marked_pnl':str(marked_pnl),
        'marked_return_pct':result['net_return_pct'],
        'exit_price':None if exit_price is None else str(exit_price),'exit_funds':str(funds),'exit_fee':str(fee),
        'exit_proceeds':str(proceeds),'hypothetical_exit_equity':str(hyp),'hypothetical_exit_pnl':str(hyp_pnl),
        'hypothetical_exit_return_pct':str(hyp_pnl/INITIAL_CASH*100) if trades else None,
        'extra_exit_cost':str(diff),'extra_slippage':str(slip),'extra_half_spread':str(spread),'extra_sell_fee':str(fee),
        'marked_positive_exit_nonpositive':marked_pnl>0 and hyp_pnl<=0,
        'original_unchanged':{k:result[k] for k in ('realized_pnl','unrealized_pnl','total_pnl','final_equity','fills','fees','mdd_pct')},
        'mdd_recomputed':False,'hypothetical_sell_counted_as_fill':False}


def check_report(report,deadline):
    base.require(report.get('schema')==INPUT_SCHEMA,'P28_SCHEMA_MISMATCH')
    base.require(report.get('status') in INPUT_STATUSES,'P28_INPUT_NOT_VERIFIED')
    base.require('failure_reason' not in report,'P28_INPUT_NOT_VERIFIED')
    cases,done,excluded=report.get('cases'),report.get('completed_policies'),report.get('excluded')
    base.require(all(type(x) is list for x in (cases,done,excluded)),'P28_REPORT_FORMAT')
    base.require(len(cases)<=MAX_PAIRS and len(done)<=MAX_POLICIES and len(excluded)<=MAX_PAIRS,'P28_CASE_LIMIT')
    for key in ('unfinished','unfinished_policies'):
        base.require(report.get(key,[])==[],'P28_UNFINISHED_POLICIES')
    universe={(d,m) for d in base.DAYS for m in base.MARKETS}
    skipped=[]
    for e in excluded:
        base.require(type(e) is dict and set(e)=={'day','market','reason'} and (e['day'],e['market']) in universe
            and type(e['reason']) is str,'P28_EXCLUDED_FORMAT')
        skipped.append((e['day'],e['market']))
    pairs=[]
    for row in cases:
        base.require(type(row) is dict and type(row.get('day')) is str and type(row.get('market')) is str,'P28_REPORT_FORMAT')
        pairs.append((row['day'],row['market']))
    base.require(len(set(skipped))==len(skipped) and len(set(pairs))==len(pairs),'P28_DUPLICATE_CASE')
    base.require(not set(skipped)&set(pairs) and set(skipped)|set(pairs)==universe,'P28_CASE_COVERAGE_MISMATCH')
    base.require(bool(skipped)==(report['status']=='VERIFIED_PARTIAL'),'P28_STATUS_MISMATCH')
    base.require(pairs,'P28_NO_PAIRS')
    keys=[]
    for r in done:
        base.require(type(r) is dict and set(r)=={'day','market','policy','result'} and r['policy'] in p21.POLICIES,'P28_POLICY_FORMAT')
        keys.append((r['day'],r['market'],r['policy']))
    base.require(len(set(keys))==len(keys),'P28_DUPLICATE_POLICY')
    base.require(set(keys)=={(d,m,p) for d,m in pairs for p in p21.POLICIES},'P28_POLICY_COVERAGE_MISMATCH')
    stored={(r['day'],r['market'],r['policy']):r['result'] for r in done}
    out=[]
    for row in cases:
        deadline.check()
        a,b=row.get('baseline'),row.get('candidate')
        base.require(stored[(row['day'],row['market'],p21.POLICIES[0])]==a
            and stored[(row['day'],row['market'],p21.POLICIES[1])]==b,'P28_PAIR_POLICY_CONFLICT')
        audits=[exit_audit(a,p21.POLICIES[0]),exit_audit(b,p21.POLICIES[1])]
        expected=p21.comparison(row['day'],row['market'],a,b)
        base.require(all(row.get(k)==expected[k] for k in ('status','delta_net_percentage_points','interpretation'))
            and set(row)==set(expected),'P28_PAIR_MISMATCH')
        no_trades=row['status']=='NO_TRADES'
        delta=None if no_trades else str(Decimal(audits[1]['hypothetical_exit_return_pct'])-Decimal(audits[0]['hypothetical_exit_return_pct']))
        out.append({'day':row['day'],'market':row['market'],'status':row['status'],
            'marked_delta_net_percentage_points':row['delta_net_percentage_points'],
            'hypothetical_exit_delta_percentage_points':delta,'baseline':audits[0],'candidate':audits[1]})
    return out,[dict(e) for e in excluded]


def _git_commit():
    """Best-effort checkout commit without subprocess; UNKNOWN is reported, never guessed."""
    try:
        git=minute_root()/'.git';head=(git/'HEAD').read_text().strip()
        if not head.startswith('ref: '):return head if re.fullmatch(r'[0-9a-f]{40}',head) else 'UNKNOWN'
        ref=head[5:]
        if (git/ref).is_file():value=(git/ref).read_text().strip()
        else:value=next((l.split()[0] for l in (git/'packed-refs').read_text().splitlines() if l.endswith(' '+ref)),'')
        return value if re.fullmatch(r'[0-9a-f]{40}',value) else 'UNKNOWN'
    except (OSError,ValueError):return 'UNKNOWN'


def minute_root():return base.minute.ROOT


def write_outputs(out,report):
    base.write_json(out/'results.json',report)
    fields=('status','open_quantity','marked_return_pct','hypothetical_exit_return_pct','marked_equity','hypothetical_exit_equity',
        'extra_exit_cost','extra_slippage','extra_half_spread','extra_sell_fee','marked_positive_exit_nonpositive')
    records=[]
    lines=['# P28 terminal exit audit','',report['status'],'',INTERPRETATION]
    def show(v):return 'N/A' if v is None else str(v)
    for row in report.get('cases',[]):
        lines+=['',row['day']+' '+row['market']+' — '+row['status'],'|Metric|Legacy|44bps entry filter|','|---|---:|---:|']
        for policy,name in zip(p21.POLICIES,('baseline','candidate')):
            records.append({'day':row['day'],'market':row['market'],'policy':policy,**{k:row[name][k] for k in fields}})
        for k in fields:lines.append('|'+k+'|'+show(row['baseline'][k])+'|'+show(row['candidate'][k])+'|')
    lines+=['',f"Pairs: {len(report.get('cases',[]))}; excluded inputs preserved: {len(report.get('excluded',[]))}.",
        'Full Decimal values, input SHA-256 and environment: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'terminal_exit.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['day','market','policy',*fields]);w.writeheader();w.writerows(records)


def audit(source,output,*,seconds=MAX_SECONDS,clock=time.monotonic):
    deadline=Deadline(seconds,clock)
    out=base.fresh(output)
    report={'schema':SCHEMA,'status':'INCOMPLETE','network_calls':0,'cases':[],'excluded':[],
        'tool':{'module':'backtesting.terminal_exit_audit','version':TOOL_VERSION,'git_commit':_git_commit(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
        'environment':{'python':platform.python_version(),'implementation':platform.python_implementation(),'system':platform.system()},
        'model':{'initial_cash':str(INITIAL_CASH),'costs':P21_COSTS,'exit_price':'CostModel.price(mark,SELL): slippage + half full spread',
            'exit_fee':'exit_funds * fee_rate','past_costs':'already in cash/remaining_cost; not re-deducted',
            'tolerance':'services.notification_metrics.close (existing P19/P21 reconciliation)'},
        'interpretation':INTERPRETATION}
    try:
        base.write_json(out/'results.json',report)
        path,source_report,digest,size=load(source);deadline.check()
        report['input']={'schema':source_report.get('schema'),'status':source_report.get('status'),'sha256':digest,'bytes':size,
            'p19_results_sha256':source_report.get('p19_results_sha256'),'protocol_sha256':source_report.get('protocol_sha256'),
            'self_reported_flags_not_exchange_provenance':True}
        cases,excluded=check_report(source_report,deadline);deadline.check()
        try:again=read_bounded(path)
        except ValueError:raise ValueError('P28_INPUT_CHANGED') from None  # Grew past the bound after load.
        base.require(hashlib.sha256(again).hexdigest()==digest,'P28_INPUT_CHANGED')
        report.update(cases=cases,excluded=excluded,
            status='AUDITED_PARTIAL' if source_report['status']=='VERIFIED_PARTIAL' else 'AUDITED')
        write_outputs(out,report);deadline.check()
        (out/'INCOMPLETE').unlink()
    except Exception as exc:
        code=str(exc)
        report.update(status='INCOMPLETE',cases=[],failure_reason=code if _CODE.fullmatch(code) else 'P28_INPUT_UNVERIFIED')
        for name in ('REPORT.md','terminal_exit.csv'):(out/name).unlink(missing_ok=True)  # No stale success export.
        base.write_json(out/'results.json',report);(out/'INCOMPLETE').touch()
    return out,report


def summary(report,code):
    def pct(v):return 'N/A' if v is None else f'{Decimal(v):.4f}'
    lines=[f"P28 {report['status']} pairs={len(report.get('cases',[]))} excluded={len(report.get('excluded',[]))} exit={code}"
        +(f" reason={report['failure_reason']}" if 'failure_reason' in report else '')]
    for r in report.get('cases',[])[:MAX_PAIRS]:
        a,b=r['baseline'],r['candidate']
        flag=' MARKED_POSITIVE_EXIT_NONPOSITIVE' if a['marked_positive_exit_nonpositive'] or b['marked_positive_exit_nonpositive'] else ''
        lines.append(f"{r['day']} {r['market']} {r['status']} close%={pct(a['marked_return_pct'])}->{pct(b['marked_return_pct'])} "
            f"exit%={pct(a['hypothetical_exit_return_pct'])}->{pct(b['hypothetical_exit_return_pct'])} "
            f"cost={Decimal(a['extra_exit_cost']):.2f}/{Decimal(b['extra_exit_cost']):.2f}{flag}")
    lines.append('Model exit value only; development sample; not a fill, lower bound or profitability proof.')
    return lines[:8]


def main(argv=None):
    parser=argparse.ArgumentParser(description='P28 offline terminal exit audit of one P21 results.json')
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:_,report=audit(args.input,args.output)
    except Exception as exc:
        code=str(exc) if _CODE.fullmatch(str(exc)) else 'P28_OUTPUT_UNVERIFIED'
        report={'status':'INCOMPLETE','failure_reason':code,'cases':[],'excluded':[]}
    code=0 if report['status'] in ('AUDITED','AUDITED_PARTIAL') else 2
    for line in summary(report,code):print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
