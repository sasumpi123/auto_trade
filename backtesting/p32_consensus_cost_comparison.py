"""P32 development comparison on existing P19 inputs: legacy / fixed 44bps / existing consensus_2of3.

Run from the repository root:
    python -m backtesting.p32_consensus_cost_comparison --input EXISTING_P19_ROOT --output NEW_PRIVATE_OUTPUT

The existing strategies.signals.ConsensusStrategy(quorum=2) is injected unchanged into the existing
legacy_diagnostic.simulate through the same P17ResearchEngine, 5/5/2 bps CostModel, warmup, next-open
execution, cooldown, volume, sizing and risk path as P19/P21 (no 44bps filter on consensus, no quorum 3,
no parameter search). Legacy is re-matched to the stored P19 case and 44bps reuses p21.candidate_case.
P19 development data only (UTC 2026-10-30..11-01); not independent or forward validation; P22 is not an
input. Runs inside the existing p20.analyze worker (600 s limit and its child cleanup). Network 0.
No operational adoption; no portfolio, summed or compounded return.
"""
import argparse
import csv
import hashlib
import logging
import re
import tempfile
from contextlib import redirect_stdout,redirect_stderr
from decimal import Decimal
from pathlib import Path

from . import benchmark_audit as p29
from . import minute_study as minute
from . import p19_baseline as base
from . import p20_attribution as p20
from . import p21_entry_filter as p21
from . import terminal_exit_audit as p28
from .legacy_diagnostic import simulate
from strategies.signals import ConsensusStrategy
from trading.costs import CostModel

SCHEMA='P32_CONSENSUS_COST_V1'
POLICIES=('legacy','entry_bandwidth_44bps','consensus_2of3')
MAX_INPUTS=6
MAX_POLICY_CASES=18
WINDOW_KEYS=('market','bar_seconds','input_candles','warmup_window','terminal_candles_without_next_open','data_sha256',
    'evaluation_first_open','evaluation_last_open','execution_first_open','execution_last_open')
INTERPRETATION=('P19 development sample only; not independent, forward or holdout validation and not a pre-registered '
    'acceptance criterion. consensus_2of3 is the existing P11 ConsensusStrategy(2) run through the P17/P9 path; '
    'no operational adoption, portfolio aggregation, ranking or profitability claim. NO_TRADES is not improvement.')


def rehash(path,limit):
    """Final input check under the SAME contract as P29: full safe_path on the original file and every
    ancestor (symlink/account/holdout markers) before any open, then a bounded read (never the whole of a
    file that grew). A swapped, linked or oversized input fails closed instead of being re-read blindly."""
    try:raw=p28.read_bounded(p29._file(path),limit)
    except ValueError as exc:
        code=str(exc)
        raise ValueError('P19_INPUT_CHANGED' if code in ('P28_INPUT_SIZE','P29_INPUT_UNREADABLE') else code) from None
    return hashlib.sha256(raw).hexdigest()


def input_root(source):
    """The real P19 root, also when given as its `analysis` alias (same rule as p20.inputs)."""
    root=base.safe_path(source)
    if root.name=='analysis' and (root/'results.json').is_file():root=base.safe_path(root.parent)
    return root


def require_outside(source,output):
    """Reject an output inside the P19 input tree BEFORE any directory or INCOMPLETE marker exists."""
    root=input_root(source);target=Path(output).expanduser().absolute().resolve()
    base.require(not (target==root or target.is_relative_to(root)),'P32_OUTPUT_INSIDE_INPUT')


def strategy_case(frame,windows,market,strategy):
    """Exactly p19_baseline.case(..., (5,5,2), details=True) with an injected existing strategy."""
    c=CostModel(5,5,2)
    r=simulate(frame,windows,market=market,bar_seconds=60,costs=c,evaluation_start=frame.index[199],
               engine_factory=base.P17ResearchEngine,sample_opens=True,details=True,strategy=strategy)
    r['execution_last_open']=(frame.index[frame.index.get_loc(minute.utc(r['evaluation_last_open']))+1].isoformat() if r['evaluation_last_open'] else None)
    r['source']='NEW_DEVELOPMENT_SAMPLE_NOT_INDEPENDENT_VALIDATION'
    r['metrics']['buy_hold_40_net_pct']=base.buy_hold(frame,r,c)
    r['metrics'].pop('buy_hold_100_net_pct',None)
    if r['fills']['buys']+r['fills']['sells']:
        base.require(base.close(Decimal(r['metrics']['net_realized_pnl'])+Decimal(r['metrics']['unrealized_pnl']),
                     Decimal(r['final_equity'])-1000000),'PNL_RECONCILIATION_FAILED')
    r['mdd_basis']='MINUTE_OPEN_AFTER_DECISION_AND_NEXT_CLOSE_NOT_INTRABAR'
    return r


def consensus_case(frame,windows,old):
    result=strategy_case(frame,windows,old['market'],ConsensusStrategy(quorum=2))
    for key in WINDOW_KEYS:base.require(result[key]==old[key],'P32_COMPARISON_WINDOW_MISMATCH')
    value=p21.compact(result,result['attribution'],filtered=False)
    value['strategy']='consensus_2of3';value['entry_filter']={'enabled':False,'threshold_bps':None,'checks':0,'blocked':0,'allowed':0}
    return value


def compare(day,market,results,bh):
    """Same-basis differences vs legacy, cash (0%) and BH40; NO_TRADES stays N/A; nothing is summed."""
    audits={p:p28.exit_audit(results[p],p if p!='consensus_2of3' else 'legacy') for p in POLICIES}
    rows={}
    for p in POLICIES:
        a=audits[p];trades=a['status']=='TRADES';row={'status':a['status'],'fills':results[p]['fills'],
            'time_held_pct':results[p]['time_held_pct'],'mean_capital_exposure_pct':results[p]['mean_capital_exposure_pct']}
        for basis,key,bkey in (('marked','marked_return_pct','marked_return_pct'),('exit','hypothetical_exit_return_pct','hypothetical_exit_return_pct')):
            value=a[key];row[basis+'_return_pct']=value
            legacy=audits['legacy'][key]
            row[basis+'_vs_legacy_pp']=None if p=='legacy' or not trades or legacy is None else str(Decimal(value)-Decimal(legacy))
            row[basis+'_vs_cash_pp']=value if trades else None
            row[basis+'_vs_bh40_pp']=str(Decimal(value)-Decimal(bh[bkey])) if trades and bh['status']=='EVALUATED' else None
        rows[p]=row
    no_trades=all(r['status']=='NO_TRADES' for r in rows.values())
    return {'day':day,'market':market,'status':'NO_TRADES_ALL_POLICIES' if no_trades else 'DEVELOPMENT_COMPARISON',
        'policies':rows,'cash_100':{'marked_return_pct':'0','hypothetical_exit_return_pct':'0'},
        'buy_hold_40':bh,'exit_audit':audits,'interpretation':INTERPRETATION}


def work(source,out,pipe):
    prior=logging.root.manager.disable
    report={'schema':SCHEMA,'status':'INCOMPLETE','policies':list(POLICIES),'maximum_policy_cases':MAX_POLICY_CASES,
        'network_calls':0,'interpretation':INTERPRETATION,'cases':[],'completed_policies':[],'excluded':[]}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log),\
                tempfile.TemporaryDirectory(prefix='p32-') as temp:
            engines=Path(temp).resolve();(engines/'bh').mkdir(mode=0o700)
            root,_,primary,digest=p20.inputs(source)
            base.require(len(primary)<=MAX_INPUTS,'P32_INPUT_LIMIT')
            report.update(p19_results_sha256=digest,protocol_sha256=base.protocol_hash())
            report['excluded']=[{'day':d,'market':m,'reason':'P19_BASELINE_EXCLUDED'}
                for d in base.DAYS for m in base.MARKETS if (d,m) not in primary]
            base.write_json(out/'results.json',report)
            for day in base.DAYS:
                selected=[m for m in base.MARKETS if (day,m) in primary]
                if not selected:continue
                daily=base.safe_path(root/'data'/day)
                plan,plan_hash=p20.metadata(daily/'plan.json');_,manifest_hash=p20.metadata(daily/'manifest.json')
                base.require(plan['markets']==list(base.MARKETS) and minute.utc(plan['start'])==base.day_start(day)
                    and minute.utc(plan['end_exclusive'])==base.day_start(day)+minute.pd.Timedelta(days=1),'P19_PERIOD_MISMATCH')
                meta,frames,_=minute.load_partitioned_sample(daily,quarantine_gaps=True)
                report.setdefault('input_hashes',{})[day]={'manifest':manifest_hash,'plan':plan_hash,
                    'files':{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in selected}}
                for market in selected:
                    old=primary[(day,market)]
                    base.require(market in frames and old['input_quality']==meta['datasets'][market],'P19_INPUT_MISMATCH')
                    frame,windows=base.prepare(frames[market],60)  # Shared prefix-only preparation for all three.
                    results={}
                    for policy,run in zip(POLICIES,(p21.baseline_case,p21.candidate_case,consensus_case)):
                        results[policy]=run(frame,windows,old)
                        report['completed_policies'].append({'day':day,'market':market,'policy':policy,'result':results[policy]})
                        base.write_json(out/'results.json',report)
                    bh=p29.buy_hold_40(frame,old,market,engines/'bh'/f'{day}-{market}')
                    report['cases'].append(compare(day,market,results,bh));base.write_json(out/'results.json',report)
            for day,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in {'manifest.json':hashes['manifest'],'plan.json':hashes['plan'],**hashes['files']}.items():
                    base.require(rehash(root/'data'/day/name,minute.MAX_FILE_BYTES)==digest0,'P19_INPUT_CHANGED')
            base.require(rehash(root/'analysis/results.json',p28.MAX_INPUT_BYTES)==digest,'P19_INPUT_CHANGED')
            base.require(len(report['completed_policies'])==3*len(primary)<=MAX_POLICY_CASES,'P32_CASE_LIMIT')
            report['evidence']='NO_TRADES_ONLY' if all(c['status']=='NO_TRADES_ALL_POLICIES' for c in report['cases']) else 'HAS_TRADES'
            report['status']='VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED'
            base.write_json(out/'results.json',report);(out/'INCOMPLETE').unlink()
    except Exception as exc:
        code=str(exc);report.update(status='INCOMPLETE',failure_type=type(exc).__name__,
            failure_reason=code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',code) else 'INPUT_OR_REPLAY_UNVERIFIED')
        base.write_json(out/'results.json',report)
    finally:logging.disable(prior);pipe.close()


FIELDS=('marked_return_pct','exit_return_pct','marked_vs_legacy_pp','exit_vs_legacy_pp','marked_vs_cash_pp',
    'exit_vs_cash_pp','marked_vs_bh40_pp','exit_vs_bh40_pp')


def write_report(out,report):
    done={(r['day'],r['market'],r['policy']) for r in report.get('completed_policies',[])}
    excluded={(r['day'],r['market']) for r in report['excluded']}
    report['unfinished_policies']=[{'day':d,'market':m,'policy':p} for d in base.DAYS for m in base.MARKETS
        if (d,m) not in excluded for p in POLICIES if (d,m,p) not in done]
    base.write_json(out/'results.json',report)
    def show(v):return 'N/A' if v is None else str(v)
    records=[];lines=['# P32 consensus_2of3 cost-aware development comparison','',report['status'],'',INTERPRETATION]
    for c in report['cases']:
        lines+=['',f"{c['day']} {c['market']} — {c['status']}",'|Policy|'+'|'.join(FIELDS)+'|buys/sells|','|---|'+'---:|'*(len(FIELDS)+1)]
        for p,r in c['policies'].items():
            records.append({'day':c['day'],'market':c['market'],'policy':p,**{k:r[k] for k in FIELDS},
                'buys':r['fills']['buys'],'sells':r['fills']['sells']})
            lines.append('|'+p+'|'+'|'.join(show(r[k]) for k in FIELDS)+f"|{r['fills']['buys']}/{r['fills']['sells']}|")
        bh=c['buy_hold_40'];lines.append(f"cash_100: 0 / 0; buy_hold_40: {show(bh.get('marked_return_pct'))} / {show(bh.get('hypothetical_exit_return_pct'))}")
    lines+=['',f"Completed policies: {len(done)}/{MAX_POLICY_CASES} maximum; excluded inputs: {len(excluded)}; unfinished: {len(report['unfinished_policies'])}.",
        'Full Decimal values, hashes and windows: results.json. Returns are never summed across days/markets.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['day','market','policy',*FIELDS,'buys','sells']);w.writeheader();w.writerows(records)


def analyze(source,output,*,seconds=600,worker=work):
    require_outside(source,output)  # p20.analyze creates the output right after checking the source.
    return p20.analyze(source,output,seconds=seconds,worker=worker,report_writer=write_report)


def summary(report):
    def pct(v):return 'N/A' if v is None else f'{Decimal(v):.2f}'
    code=0 if report['status'] in ('VERIFIED','VERIFIED_PARTIAL') else 2
    lines=[f"P32 {report['status']} exit={code} pairs={len(report.get('cases',[]))} policies={len(report.get('completed_policies',[]))}/{MAX_POLICY_CASES} "
        f"excluded={len(report.get('excluded',[]))} unfinished={len(report.get('unfinished_policies',[]))} evidence={report.get('evidence','N/A')}"
        +(f" reason={report['failure_reason']}" if 'failure_reason' in report else '')]
    for c in report.get('cases',[])[:6]:
        r=c['policies'];bh=c['buy_hold_40']
        lines.append(f"{c['day']} {c['market']} close% L/44/C={pct(r['legacy']['marked_return_pct'])}/{pct(r['entry_bandwidth_44bps']['marked_return_pct'])}/"
            f"{pct(r['consensus_2of3']['marked_return_pct'])} exit% L/44/C={pct(r['legacy']['exit_return_pct'])}/{pct(r['entry_bandwidth_44bps']['exit_return_pct'])}/"
            f"{pct(r['consensus_2of3']['exit_return_pct'])} BH={pct(bh.get('marked_return_pct'))} trades C={r['consensus_2of3']['fills']['buys']}/{r['consensus_2of3']['fills']['sells']}")
    lines.append('P19 development comparison only; no adoption, no sum/portfolio, NO_TRADES is not improvement.')
    return lines[:8],code


def main(argv=None):
    parser=argparse.ArgumentParser(description='P32 offline legacy/44bps/consensus_2of3 comparison on existing P19 inputs')
    parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:_,report=analyze(args.input,args.output)
    except Exception as exc:
        code=str(exc) if p28._CODE.fullmatch(str(exc)) else 'P32_INPUT_OR_OUTPUT_UNVERIFIED'
        print(f'P32 INCOMPLETE exit=2 reason={code}');return 2
    lines,code=summary(report)
    for line in lines:print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
