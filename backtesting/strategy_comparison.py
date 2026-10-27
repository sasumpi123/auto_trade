"""Fixed offline six-strategy PAPER study on already disclosed development data."""
import argparse
import csv
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
from statistics import median

from .legacy_diagnostic import load_context,simulate
from .engine import CostModel,serializable
from .historical import load_protocol
from strategies.signals import LegacyCompositeStrategy,research_strategies

ROOT=Path(__file__).resolve().parents[1]
NAMES=('legacy_composite','rsi','macd','bollinger','consensus_2of3','consensus_3of3')


def candidates():
    values=[LegacyCompositeStrategy(),*research_strategies()]
    if tuple(s.name for s in values)!=NAMES:raise ValueError('STRATEGY_REGISTRY_CHANGED')
    return values


def number_or_none(value):return None if value is None else Decimal(str(value))
def delta(a,b):return None if a is None or b is None else Decimal(str(a))-Decimal(str(b))


def describe(values):
    numbers=[number_or_none(v) for v in values if v is not None]
    return {'available':len(numbers),'missing':len(values)-len(numbers),
        'mean':sum(numbers)/len(numbers) if numbers else None,'median':median(numbers) if numbers else None,
        'minimum':min(numbers) if numbers else None,'maximum':max(numbers) if numbers else None,
        'positive':sum(v>0 for v in numbers),'negative':sum(v<0 for v in numbers),'zero':sum(v==0 for v in numbers)}


def key(item):return (item['market'],item['bar_seconds'],item['split'],str(item['costs']['fee_bps']),str(item['costs']['slippage_bps']))


def summarize(comparisons):
    """Equal-case descriptive statistics, NOT a portfolio or independent samples."""
    baselines={key(r):r for r in comparisons if r['strategy']=='legacy_composite'}
    if len(baselines)!=sum(r['strategy']=='legacy_composite' for r in comparisons):raise ValueError('DUPLICATE_BASELINE')
    enriched=[]
    seen=set()
    for row in comparisons:
        token=(*key(row),row['strategy'])
        if token in seen:raise ValueError('DUPLICATE_COMPARISON')
        seen.add(token)
        base=baselines[key(row)]
        for field in ('data_sha256','initial_cash','bar_seconds','evaluation_first_open','execution_last_open'):
            if row[field]!=base[field]:raise ValueError('UNPAIRED_RESEARCH_INPUTS')
        if row['costs']!=base['costs']:raise ValueError('UNPAIRED_RESEARCH_COSTS')
        item=dict(row);m=item['metrics']
        item['paired']={'legacy_net_delta_pp':delta(m['net_return_pct'],base['metrics']['net_return_pct']),
            'buy_hold_100_delta_pp':delta(m['net_return_pct'],m['buy_hold_100_net_pct']),
            'buy_hold_40_delta_pp':delta(m['net_return_pct'],m['buy_hold_40_net_pct'])}
        enriched.append(item)
    summaries=[];sensitivity=[]
    groups=sorted({(r['strategy'],r['split'],str(r['costs']['fee_bps']),str(r['costs']['slippage_bps'])) for r in enriched})
    for strategy,split,fee,slip in groups:
        selected=[r for r in enriched if (r['strategy'],r['split'],str(r['costs']['fee_bps']),str(r['costs']['slippage_bps']))==(strategy,split,fee,slip)]
        summary={'strategy':strategy,'split':split,'fee_bps':fee,'slippage_bps':slip,'cases':len(selected),
            'zero_trade_cases':sum(r['fills']['buys']+r['fills']['sells']==0 for r in selected),
            'buys_across_independent_runs':sum(r['fills']['buys'] for r in selected),
            'sells_across_independent_runs':sum(r['fills']['sells'] for r in selected)}
        for metric in ('net_return_pct','mdd_pct','win_rate_pct','expectancy_per_sell'):
            summary[metric]=describe([r['metrics'][metric] for r in selected])
        for metric in ('legacy_net_delta_pp','buy_hold_100_delta_pp','buy_hold_40_delta_pp'):
            summary[metric]=describe([r['paired'][metric] for r in selected])
        summaries.append(summary)
        for excluded in sorted({r['market'] for r in selected}):
            subset=[r for r in selected if r['market']!=excluded]
            sensitivity.append({'strategy':strategy,'split':split,'fee_bps':fee,'slippage_bps':slip,
                'excluded_market':excluded,'cases':len(subset),
                'net_return_pct':describe([r['metrics']['net_return_pct'] for r in subset]),
                'legacy_net_delta_pp':describe([r['paired']['legacy_net_delta_pp'] for r in subset])})
    return serializable(enriched),serializable(summaries),serializable(sensitivity)


def archive_baselines():
    path=ROOT/'research/p11a-diagnostic-2026-10-26/results.json'
    report=json.loads(path.read_text(encoding='utf-8'))
    if report.get('holdout_evaluated') is not False or report.get('independent_validation') is not False:raise ValueError('INVALID_P11A_ARCHIVE')
    rows=report['comparisons'];result={key(r):r for r in rows}
    if len(result)!=len(rows) or len(result)!=96:raise ValueError('INVALID_P11A_ARCHIVE')
    return result


def check_baseline(row,old):
    for field in ('counts','fills','metrics','fees','slippage_cost','half_spread_cost','initial_cash','final_equity','data_sha256'):
        if row[field]!=old[field]:raise ValueError('P11A_BASELINE_REGRESSION')
    digest=hashlib.sha256(json.dumps(row['trace'],sort_keys=True).encode()).hexdigest()
    if digest!=old['trace_sha256'] or len(row['trace'])!=old['trace_count']:raise ValueError('P11A_TRACE_REGRESSION')


def display(v):return 'N/A' if v is None else f'{Decimal(str(v)):.4f}'


def markdown(report):
    lines=['# P11-B fixed six-strategy PAPER comparison','',
        'Exploratory disclosed train/validation; no independent validation, portfolio or selected winner.',
        '1-minute real data: INSUFFICIENT_REAL_DATA. Holdout rows evaluated: 0.',
        '', 'Cases share 1,000,000 KRW, 40% sizing, legacy risk overlay, 200-bar history, next-open fills and fixed costs.',
        'Means/medians describe overlapping single-asset cases. They are NOT pooled or compounded account returns.',
        'Win rate/expectancy cells include their own valid-case denominators; zero-trade cases are N/A, never zero-profit observations.',
        '', '|Strategy|Split|Fee/slip bps|Valid/total|Zero trades|Buys/sells (separate runs)|Median net %|Min/max net %|Positive cases|Median MDD %|Median win %|Median expectancy KRW|Beat Legacy|Beat B&H100|Beat B&H40|',
        '|---|---|---|---:|---:|---|---:|---|---:|---:|---:|---:|---|---|---|']
    for r in report['summaries']:
        n=r['net_return_pct'];wins=lambda k:f"{r[k]['positive']}/{r[k]['available']}"
        lines.append(f"|{r['strategy']}|{r['split']}|{r['fee_bps']}/{r['slippage_bps']}|{n['available']}/{r['cases']}|{r['zero_trade_cases']}|{r['buys_across_independent_runs']}/{r['sells_across_independent_runs']}|{display(n['median'])}|{display(n['minimum'])}/{display(n['maximum'])}|{n['positive']}|{display(r['mdd_pct']['median'])}|{display(r['win_rate_pct']['median'])} ({r['win_rate_pct']['available']}/{r['cases']})|{display(r['expectancy_per_sell']['median'])} ({r['expectancy_per_sell']['available']}/{r['cases']})|{wins('legacy_net_delta_pp')}|{wins('buy_hold_100_delta_pp')}|{wins('buy_hold_40_delta_pp')}|")
    lines+=['','## Primary costs: leave one market out','',
        'fee5/slip5/full-spread2bps. Removing one market leaves six overlapping cases per split, not independent samples.',
        '', '|Strategy|Split|Excluded|Valid/total|Mean net %|Median net %|Mean delta vs Legacy pp|',
        '|---|---|---|---:|---:|---:|---:|']
    for r in report['leave_one_market_out']:
        if r['fee_bps']!='5' or r['slippage_bps']!='5':continue
        n=r['net_return_pct'];lines.append(f"|{r['strategy']}|{r['split']}|{r['excluded_market']}|{n['available']}/{r['cases']}|{display(n['mean'])}|{display(n['median'])}|{display(r['legacy_net_delta_pp']['mean'])}|")
    lines+=['','## All paired cases (full costs and diagnostics in JSON/CSV)','',
        '|Strategy|Market|Minutes|Split|Fee/slip|Raw BUY/SELL|Fills buy/sell|Net %|MDD %|Win %|Expectancy KRW|Legacy delta pp|B&H100 delta pp|B&H40 delta pp|Fees KRW|',
        '|---|---|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in report['comparisons']:
        c=r['counts'];m=r['metrics'];p=r['paired']
        lines.append(f"|{r['strategy']}|{r['market']}|{r['bar_seconds']//60}|{r['split']}|{r['costs']['fee_bps']}/{r['costs']['slippage_bps']}|{c['raw_BUY']}/{c['raw_SELL']}|{r['fills']['buys']}/{r['fills']['sells']}|{display(m['net_return_pct'])}|{display(m['mdd_pct'])}|{display(m['win_rate_pct'])}|{display(m['expectancy_per_sell'])}|{display(p['legacy_net_delta_pp'])}|{display(p['buy_hold_100_delta_pp'])}|{display(p['buy_hold_40_delta_pp'])}|{display(r['fees'])}|")
    return '\n'.join(lines)+'\n'


def write_report(report,output,*,render=markdown):
    # JSON keeps one comparison per line so generated public-data diffs remain reviewable.
    metadata={k:v for k,v in report.items() if k!='comparisons'}
    (output/'results.json').write_text(json.dumps(metadata,ensure_ascii=False,allow_nan=False)[:-1]+',\n"comparisons":[\n'+',\n'.join(json.dumps(r,ensure_ascii=False,allow_nan=False) for r in report['comparisons'])+'\n]}\n',encoding='utf-8')
    (output/'REPORT.md').write_text(render(report),encoding='utf-8')
    rows=[]
    for r in report['comparisons']:
        rows.append({'strategy':r['strategy'],'market':r['market'],'minutes':r['bar_seconds']//60,'split':r['split'],
            'period_start':r.get('period_start'),'period_end_exclusive':r.get('period_end_exclusive'),
            'data_sha256':r['data_sha256'],'initial_cash':r['initial_cash'],
            **r['costs'],**r['counts'],**{'filled_'+k:v for k,v in r['fills'].items()},**r['metrics'],**r['paired'],
            **{k:r[k] for k in ('fees','slippage_cost','half_spread_cost','na_reason','trace_count','trace_sha256')}})
    with (output/'metrics.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)


def run(data,output,*,progress=None):
    progress=progress or (lambda value:print(value,flush=True))
    protocol,ph=load_protocol();old=archive_baselines();output=Path(output)
    output.mkdir(parents=True,exist_ok=False)
    # A failed/interrupted study never gets this marker removed or replaced by success.
    (output/'INCOMPLETE').write_text('Do not use as a completed study.\n',encoding='utf-8')
    rows=[];checked=set()
    for market in protocol['markets']:
        for unit in protocol['units_minutes']:
            for split in ('train','validation'):
                p,h,start,end,frame,windows=load_context(data,market,unit,split)
                if p!=protocol or h!=ph:raise ValueError('PROTOCOL_CHANGED_DURING_STUDY')
                for strategy in candidates():
                    for fee in protocol['fee_bps_per_side']:
                        for slip in protocol['slippage_bps_per_side']:
                            r=simulate(frame,windows,market=market,bar_seconds=unit*60,costs=CostModel(fee,slip,protocol['full_spread_bps']),
                                evaluation_start=start,initial_cash=protocol['initial_cash'],strategy=strategy)
                            r.update(strategy=strategy.name,split=split,period_start=start,period_end_exclusive=end,
                                source='VERIFIED_ARCHIVED_REAL_DEVELOPMENT_PREFIX')
                            if strategy.name=='legacy_composite':check_baseline(r,old[key(r)]);checked.add(key(r))
                            trace=r.pop('trace');r.update(trace_count=len(trace),trace_sha256=hashlib.sha256(json.dumps(trace,sort_keys=True).encode()).hexdigest())
                            rows.append(r)
                    progress(f'{market}-{unit}-{split}: {strategy.name} six costs complete')
    if len(rows)!=576 or set(old)!=checked:raise ValueError('INCOMPLETE_MATRIX_OR_BASELINE')
    comparisons,summaries,sensitivity=summarize(rows)
    report={'schema_version':1,'complete':True,'source':'VERIFIED_ARCHIVED_REAL_DATA','research_only':True,
        'independent_validation':False,'holdout_evaluated':False,'holdout_rows_parsed':0,
        'production_minute1_status':'INSUFFICIENT_REAL_DATA','multi_asset_portfolio':False,'selection_performed':False,
        'p11a_baselines_checked':len(checked),'protocol_sha256':ph,'strategies':list(NAMES),
        'common_risk_policy':'legacy_5pct_take_profit_stop_average_once_at_next_open',
        'statistical_scope':'descriptive_equal_case_overlapping_development_samples_no_significance_claim',
        'summaries':summaries,'leave_one_market_out':sensitivity,'comparisons':comparisons}
    write_report(report,output)
    (output/'INCOMPLETE').unlink()  # Public research marker only, never an operational file.
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);logging.disable(logging.CRITICAL)
    try:
        report=run(args.data,args.output)
    except Exception:
        print(json.dumps({'status':'HALTED_OFFLINE_STUDY_INCOMPLETE','network_calls':0,'execution_enabled':False}));return 2
    print(json.dumps({'status':'COMPLETE_OFFLINE_STUDY','comparisons':len(report['comparisons']),'p11a_baselines_checked':report['p11a_baselines_checked']}));return 0


if __name__=='__main__':raise SystemExit(main())
