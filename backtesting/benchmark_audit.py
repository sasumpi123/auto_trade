"""P29 offline benchmark: Legacy / fixed 44bps / cash 100% / buy-and-hold 40% on the same P19 window.

Run from the repository root:
    python -m backtesting.benchmark_audit --p19-root P19_ROOT --p21-results P21_RESULTS_JSON --output NEW_PRIVATE_DIRECTORY

Read-only post-processing of one verified P19 collection and the P21_FIXED_44BPS_V1 report built from it.
Inputs are first copied into a private temporary snapshot with bounded reads; the unchanged P19/P20/P21
helpers then validate that snapshot. No network, refetch, strategy replay, parameter search, subprocess
or user account. The only execution is one disposable research P17 engine BUY per pair, exactly as
p19_baseline.buy_hold. Differences are descriptive; they are not statistical superiority, risk-adjusted
outperformance or profitability evidence.
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import platform
import re
import tempfile
import time
from contextlib import redirect_stdout,redirect_stderr
from decimal import Decimal
from pathlib import Path

from . import p19_baseline as base
from . import p20_attribution as p20
from . import p21_entry_filter as p21
from . import minute_study as minute
from . import terminal_exit_audit as p28
from .legacy_diagnostic import validate_candles,dataset_hash
from services.notification_metrics import close
from trading.costs import CostModel
from trading.execution import number

SCHEMA='P29_CASH_BH40_BENCHMARK_V1'
TOOL_VERSION=1
MAX_SECONDS=60
INITIAL_CASH=p28.INITIAL_CASH
BH_FRACTION=Decimal('0.4')
P19_COST={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}
_CSV=re.compile(r'KRW-[A-Z]{2,10}-1\.csv')
INTERPRETATION=('Development-period P19/P21 sample only; not independent, forward or holdout validation. '
    'Cash 100% is a defined 0% baseline (no interest, fee or trade). Buy-and-hold 40% buys once at the common P19 '
    'execution_first_open with a fee-inclusive 400,000 budget and holds 600,000 cash. Differences are per day/market, '
    'same valuation basis only; no aggregation, ranking, compounding, risk adjustment or profitability claim. '
    'Hypothetical exits are model values, not fills, realized PnL or liquidity lower bounds.')


class Deadline(p28.Deadline):
    def __init__(self,seconds=MAX_SECONDS,clock=time.monotonic):
        base.require(type(seconds) in (int,float) and 0<seconds<=MAX_SECONDS,'TIME_LIMIT')
        self.clock=clock;self.end=clock()+seconds
    def check(self):base.require(self.clock()<self.end,'P29_TIME_LIMIT')


def _file(path):
    base.require(path.is_file() and not path.is_symlink(),'P29_INPUT_UNREADABLE')
    return path


def _copy(src,dst,limit):
    """Bounded copy into the private snapshot; returns the SHA-256 of exactly what the helpers will read."""
    raw=p28.read_bounded(_file(src),limit)
    dst.parent.mkdir(parents=True,exist_ok=True,mode=0o700);dst.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def snapshot(source,snap,deadline):
    """Copy P19 results + the needed daily plan/manifest/CSV files; original paths are never handed to helpers."""
    root=base.safe_path(source)
    if root.name=='analysis' and (root/'results.json').is_file():root=base.safe_path(root.parent)
    base.require(root.is_dir(),'P29_INPUT_UNREADABLE')
    hashes={'analysis/results.json':_copy(root/'analysis/results.json',snap/'analysis/results.json',p28.MAX_INPUT_BYTES)}
    _,_,primary,digest=p20.inputs(snap);deadline.check()
    for day in sorted({d for d,_ in primary}):
        daily=root/'data'/day;base.require(not any(x.is_symlink() for x in (daily,daily.parent)),'SYMLINK_FORBIDDEN')
        base.require(daily.is_dir() and not (daily/'INCOMPLETE').exists(),'P29_P19_DAY_INCOMPLETE')
        for name in ('plan.json','manifest.json'):
            hashes[f'data/{day}/{name}']=_copy(daily/name,snap/'data'/day/name,minute.MAX_FILE_BYTES)
        manifest=json.loads((snap/'data'/day/'manifest.json').read_bytes())
        datasets=manifest.get('datasets') if type(manifest) is dict else None
        base.require(type(datasets) is dict and 0<len(datasets)<=len(base.MARKETS),'P29_P19_MANIFEST_UNVERIFIED')
        for meta in datasets.values():
            name=meta.get('file') if type(meta) is dict else None
            base.require(type(name) is str and _CSV.fullmatch(name),'P29_P19_MANIFEST_UNVERIFIED')
            hashes[f'data/{day}/{name}']=_copy(daily/name,snap/'data'/day/name,minute.MAX_FILE_BYTES)
        deadline.check()
    return root,primary,digest,hashes


def unchanged(root,hashes):
    for rel,digest in hashes.items():
        try:raw=p28.read_bounded(_file(root/rel),p28.MAX_INPUT_BYTES)
        except ValueError:raise ValueError('P29_INPUT_CHANGED') from None
        base.require(hashlib.sha256(raw).hexdigest()==digest,'P29_INPUT_CHANGED')


def buy_hold_40(frame,old,market,folder):
    """Same P17 path, reference open, fee-inclusive budget and last close as p19_baseline.buy_hold."""
    first=old.get('execution_first_open');stored=old['metrics'].get('buy_hold_40_net_pct')
    if first is None:
        base.require(stored is None,'P29_BH40_MISMATCH')
        return {'status':'NA_NO_COMMON_EXECUTION_OPEN'}
    stamp=minute.utc(first);base.require(stamp in frame.index and stamp>frame.index[0],'P29_WINDOW_MISMATCH')
    reference=number(frame.loc[stamp,'open']);mark=number(frame.close.iloc[-1])
    folder.mkdir(mode=0o700)
    engine=base.P17ResearchEngine(folder/'unused.json',1000000,CostModel(5,5,2),lambda:stamp.timestamp())
    engine.open()
    try:
        base.require(engine.buy(market,reference,400000),'P29_BH40_REJECTED')
        cash=engine.cash;q=number(engine.position(market)['quantity'])
        orders=list(engine.state['orders'].values())
        base.require(len(orders)==1 and orders[0]['side']=='bid','P29_BH40_REJECTED')
        fee=number(orders[0]['paid_fee']);funds=number(orders[0]['funds'])
    finally:engine.close()
    base.require(close(cash+funds+fee,INITIAL_CASH) and close(funds+fee,INITIAL_CASH*BH_FRACTION),'P29_BH40_BUDGET_MISMATCH')
    e=p28.exit_values(cash,q,mark);marked=e['marked']
    marked_ret=(marked/INITIAL_CASH-1)*100
    base.require(type(stored) is str and close(p28.num(stored,signed=True),marked_ret),'P29_BH40_MISMATCH')
    hyp_ret=(e['hyp']-INITIAL_CASH)/INITIAL_CASH*100
    return {'status':'EVALUATED','entry_open_utc':stamp.isoformat(),'entry_reference_open':str(reference),
        'budget_fee_inclusive':str(INITIAL_CASH*BH_FRACTION),'buy_funds':str(funds),'buy_fee':str(fee),'quantity':str(q),
        'cash':str(cash),'mark_price':str(mark),'marked_equity':str(marked),'marked_return_pct':str(marked_ret),
        'p19_buy_hold_40_net_pct':stored,'exit_price':None if e['exit_price'] is None else str(e['exit_price']),
        'exit_fee':str(e['fee']),'extra_exit_cost':str(e['diff']),'hypothetical_exit_equity':str(e['hyp']),
        'hypothetical_exit_return_pct':str(hyp_ret),'fills':{'buys':1,'sells':0},
        'hypothetical_sell_counted_as_fill':False}


def compare(audit,raw,bh):
    """Same-basis differences only; NO_TRADES and missing benchmark stay N/A."""
    trades=audit['status']=='TRADES';row={'status':audit['status'],'fills':raw['fills'],
        'time_held_pct':raw.get('time_held_pct'),'mean_capital_exposure_pct':raw.get('mean_capital_exposure_pct')}
    for basis,key in (('marked','marked_return_pct'),('exit','hypothetical_exit_return_pct')):
        value=audit[key];row[basis+'_return_pct']=value
        row[basis+'_vs_cash_pp']=value if trades else None  # Cash baseline is exactly 0%.
        ok=trades and bh['status']=='EVALUATED'
        row[basis+'_vs_bh40_pp']=str(Decimal(value)-Decimal(bh[key])) if ok else None
    return row


def flags(rows,bh):
    """Candidate better than Legacy, yet not better than cash or BH40 on the same basis."""
    out={};bh_key={'marked':'marked_return_pct','exit':'hypothetical_exit_return_pct'}
    for basis in ('marked','exit'):
        a,b=rows['legacy'][basis+'_return_pct'],rows['entry_bandwidth_44bps'][basis+'_return_pct']
        if a is None or b is None:out[basis]=None;continue
        better=Decimal(b)>Decimal(a)
        out[basis]={'candidate_better_than_legacy':better,
            'candidate_not_above_cash':Decimal(b)<=0,
            'candidate_not_above_bh40':None if bh['status']!='EVALUATED' else Decimal(b)<=Decimal(bh[bh_key[basis]]),
            'candidate_negative_return':Decimal(b)<0}
    return out


def evaluate(snap,primary,p21_report,deadline,work):
    """Exact P21 work() input checks on the snapshot, then one BH40 per verified pair."""
    cases,excluded=p28.check_report(p21_report,deadline)
    base.require({(c['day'],c['market']) for c in cases}==set(primary),'P29_PAIR_SET_MISMATCH')
    base.require({(e['day'],e['market']) for e in excluded}=={(d,m) for d in base.DAYS for m in base.MARKETS}-set(primary),'P29_EXCLUDED_MISMATCH')
    raw={(c['day'],c['market']):c for c in p21_report['cases']}
    hashes=p21_report.get('input_hashes');base.require(type(hashes) is dict,'P29_INPUT_HASH_MISMATCH')
    base.require(set(hashes)=={d for d,_ in primary},'P29_INPUT_HASH_MISMATCH')
    out=[];index={(c['day'],c['market']):c for c in cases}
    for day in base.DAYS:
        selected=[m for m in base.MARKETS if (day,m) in primary]
        if not selected:continue
        daily=base.safe_path(snap/'data'/day)
        plan,plan_hash=p20.metadata(daily/'plan.json');_,manifest_hash=p20.metadata(daily/'manifest.json')
        base.require(plan['markets']==list(base.MARKETS) and minute.utc(plan['start'])==base.day_start(day)
            and minute.utc(plan['end_exclusive'])==base.day_start(day)+minute.pd.Timedelta(days=1),'P19_PERIOD_MISMATCH')
        meta,frames,_=minute.load_partitioned_sample(daily,quarantine_gaps=True)
        expected={'manifest':manifest_hash,'plan':plan_hash,'files':{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in selected}}
        base.require(hashes[day]==expected,'P29_INPUT_HASH_MISMATCH')
        for market in selected:
            deadline.check()
            old=primary[(day,market)]
            base.require(old.get('costs')==P19_COST,'P29_COST_MISMATCH')
            base.require(market in frames and old['input_quality']==meta['datasets'][market],'P19_INPUT_MISMATCH')
            frame=validate_candles(frames[market],60)
            base.require(dataset_hash(frame)==old['data_sha256'] and len(frame)==old['input_candles'],'P29_WINDOW_MISMATCH')
            end=(frame.index[-1]+minute.pd.Timedelta(seconds=60)).isoformat();mark=number(frame.close.iloc[-1])
            pair=index[(day,market)];source=raw[(day,market)]
            for name in ('baseline','candidate'):
                pos=source[name]['open_position']
                base.require(p28.num(pos['mark_price'],positive=True)==mark and pos['mark_at_utc']==end,'P29_MARK_MISMATCH')
            bh=buy_hold_40(frame,old,market,work/f'{day}-{market}')
            rows={p21.POLICIES[0]:compare(pair['baseline'],source['baseline'],bh),
                  p21.POLICIES[1]:compare(pair['candidate'],source['candidate'],bh)}
            out.append({'day':day,'market':market,'status':pair['status'],
                'window':{'execution_first_open':old['execution_first_open'],'execution_last_open':old['execution_last_open'],
                    'evaluation_last_open':old['evaluation_last_open'],'mark_at_utc':end,'mark_price':str(mark)},
                'cash_100':{'status':'DEFINED_BASELINE','marked_return_pct':'0','hypothetical_exit_return_pct':'0',
                    'fills':{'buys':0,'sells':0},'interest':'0','fee':'0'},
                'buy_hold_40':bh,'policies':rows,'flags':flags(rows,bh),
                'exposure_note':'BH40 holds a fixed fee-inclusive 400,000 from execution_first_open to the last close; '
                    'strategy exposure and trade counts differ (see policies[].fills/time_held_pct).'})
    return out,excluded


def audit(p19_root,p21_results,output,*,seconds=MAX_SECONDS,clock=time.monotonic):
    deadline=Deadline(seconds,clock);out=base.fresh(output)
    report={'schema':SCHEMA,'status':'INCOMPLETE','network_calls':0,'cases':[],'excluded':[],
        'tool':{'module':'backtesting.benchmark_audit','version':TOOL_VERSION,'git_commit':p28._git_commit(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'p28_source_sha256':hashlib.sha256(Path(p28.__file__).read_bytes()).hexdigest()},
        'environment':{'python':platform.python_version(),'implementation':platform.python_implementation(),'system':platform.system()},
        'model':{'initial_cash':str(INITIAL_CASH),'costs':P19_COST,'cash_100':'0% by definition: no interest, fee or trade',
            'buy_hold_40':'P17 research engine BUY of fee-inclusive 400,000 at P19 execution_first_open open; '
                'qty=400000/(1+f)/BUY(open); marked at last close; same as p19_baseline.buy_hold',
            'hypothetical_exit':'terminal_exit_audit.exit_values (slippage + half spread, sell fee on funds)',
            'tolerance':'services.notification_metrics.close (existing P19/P21 reconciliation)'},
        'interpretation':INTERPRETATION}
    prior=logging.root.manager.disable
    try:
        base.write_json(out/'results.json',report)
        p21_path,p21_report,p21_digest,p21_size=p28.load(p21_results);deadline.check()
        with tempfile.TemporaryDirectory(prefix='p29-') as temp:
            temp=Path(temp).resolve();snap=temp/'p19';work=temp/'engines';snap.mkdir(mode=0o700);work.mkdir(mode=0o700)
            logging.disable(logging.CRITICAL)
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                root,primary,digest,hashes=snapshot(p19_root,snap,deadline)
                base.require(p21_report.get('p19_results_sha256')==digest
                    and p21_report.get('protocol_sha256')==base.protocol_hash(),'P29_P19_P21_LINK_MISMATCH')
                cases,excluded=evaluate(snap,primary,p21_report,deadline,work)
        report['input']={'p21':{'schema':p21_report.get('schema'),'status':p21_report.get('status'),'sha256':p21_digest,'bytes':p21_size},
            'p19':{'results_sha256':digest,'protocol_sha256':base.protocol_hash(),'files_sha256':hashes},
            'self_reported_flags_not_exchange_provenance':True}
        deadline.check()
        unchanged(root,hashes)
        try:again=p28.read_bounded(p21_path)
        except ValueError:raise ValueError('P29_INPUT_CHANGED') from None
        base.require(hashlib.sha256(again).hexdigest()==p21_digest,'P29_INPUT_CHANGED')
        report.update(cases=cases,excluded=excluded,
            status='BENCHMARKED_PARTIAL' if p21_report['status']=='VERIFIED_PARTIAL' else 'BENCHMARKED')
        write_outputs(out,report);deadline.check()
        (out/'INCOMPLETE').unlink()
    except (Exception,KeyboardInterrupt) as exc:
        code='P29_INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else str(exc)
        report.update(status='INCOMPLETE',cases=[],failure_reason=code if p28._CODE.fullmatch(code) else 'P29_INPUT_UNVERIFIED')
        for name in ('REPORT.md','benchmark.csv'):(out/name).unlink(missing_ok=True)
        base.write_json(out/'results.json',report);(out/'INCOMPLETE').touch()
    finally:logging.disable(prior)
    return out,report


FIELDS=('marked_return_pct','exit_return_pct','marked_vs_cash_pp','marked_vs_bh40_pp','exit_vs_cash_pp','exit_vs_bh40_pp')


def write_outputs(out,report):
    base.write_json(out/'results.json',report)
    def show(v):return 'N/A' if v is None else str(v)
    records=[];lines=['# P29 cash / buy-and-hold 40% benchmark','',report['status'],'',INTERPRETATION]
    for c in report['cases']:
        bh=c['buy_hold_40']
        lines+=['',f"{c['day']} {c['market']} — {c['status']}",'|Policy|close %|exit %|close vs cash pp|close vs BH40 pp|exit vs cash pp|exit vs BH40 pp|buys/sells|','|---|---:|---:|---:|---:|---:|---:|---:|']
        for name,r in c['policies'].items():
            records.append({'day':c['day'],'market':c['market'],'policy':name,**{k:r[k] for k in FIELDS},
                'buys':r['fills']['buys'],'sells':r['fills']['sells'],'time_held_pct':r['time_held_pct']})
            lines.append('|'+name+'|'+'|'.join(show(r[k]) for k in FIELDS)+f"|{r['fills']['buys']}/{r['fills']['sells']}|")
        for name,ret,exit_ret,fills in (('cash_100','0','0',(0,0)),
                ('buy_hold_40',bh.get('marked_return_pct'),bh.get('hypothetical_exit_return_pct'),(1,0) if bh['status']=='EVALUATED' else (0,0))):
            records.append({'day':c['day'],'market':c['market'],'policy':name,'marked_return_pct':ret,'exit_return_pct':exit_ret,
                **{k:None for k in FIELDS[2:]},'buys':fills[0],'sells':fills[1],'time_held_pct':None})
            lines.append(f'|{name}|{show(ret)}|{show(exit_ret)}|—|—|—|—|{fills[0]}/{fills[1]}|')
        f=c['flags'].get('exit')
        if f and f['candidate_better_than_legacy'] and (f['candidate_not_above_cash'] or f['candidate_not_above_bh40']):
            lines.append('Candidate beats Legacy but not cash or BH40 after hypothetical exit.')
    lines+=['',f"Pairs: {len(report['cases'])}; excluded inputs preserved: {len(report['excluded'])}.",
        'Full Decimal values, input hashes, window and environment: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'benchmark.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=['day','market','policy',*FIELDS,'buys','sells','time_held_pct']);w.writeheader();w.writerows(records)


def summary(report,code):
    def pct(v):return 'N/A' if v is None else f'{Decimal(v):.2f}'
    lines=[f"P29 {report['status']} pairs={len(report.get('cases',[]))} excluded={len(report.get('excluded',[]))} exit={code}"
        +(f" reason={report['failure_reason']}" if 'failure_reason' in report else '')]
    for c in report.get('cases',[])[:6]:
        a,b=c['policies'][p21.POLICIES[0]],c['policies'][p21.POLICIES[1]];bh=c['buy_hold_40']
        f=c['flags'].get('exit');warn=' CAND>LEGACY_BUT<=CASH/BH40' if f and f['candidate_better_than_legacy'] and (
            f['candidate_not_above_cash'] or f['candidate_not_above_bh40']) else ''
        lines.append(f"{c['day']} {c['market']} close% L/44/BH={pct(a['marked_return_pct'])}/{pct(b['marked_return_pct'])}/{pct(bh.get('marked_return_pct'))} "
            f"exit% L/44/BH={pct(a['exit_return_pct'])}/{pct(b['exit_return_pct'])}/{pct(bh.get('hypothetical_exit_return_pct'))} cash=0{warn}")
    lines.append('Same-window development comparison; not superiority, risk-adjusted or profitability proof.')
    return lines[:8]


def main(argv=None):
    parser=argparse.ArgumentParser(description='P29 offline cash/BH40 benchmark of one P19 root and its P21 results.json')
    parser.add_argument('--p19-root',required=True);parser.add_argument('--p21-results',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:_,report=audit(args.p19_root,args.p21_results,args.output)
    except Exception as exc:
        code=str(exc) if p28._CODE.fullmatch(str(exc)) else 'P29_OUTPUT_UNVERIFIED'
        report={'status':'INCOMPLETE','failure_reason':code,'cases':[],'excluded':[]}
    code=0 if report['status'] in ('BENCHMARKED','BENCHMARKED_PARTIAL') else 2
    for line in summary(report,code):print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
