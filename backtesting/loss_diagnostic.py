"""P11-D offline loss attribution; explicit existing P11-C samples only, no collection."""
import argparse
import csv
import json
import logging
from decimal import Decimal
from pathlib import Path

from .collection import MARKETS,write_json
from .engine import CostModel,serializable
from .historical import load_protocol
from .legacy_diagnostic import prepare,simulate
from .minute_study import load_partitioned_sample,new_output,require,sha,canonical
from .strategy_comparison import candidates,display


def cost_cases(protocol):
    # Existing grid plus predeclared one-component removal and zero-cost diagnostics.
    # No data-dependent optimization or recommended operating parameters.
    cases=[(fee,slip,protocol['full_spread_bps']) for fee in protocol['fee_bps_per_side'] for slip in protocol['slippage_bps_per_side']]
    cases += [(0,5,2),(5,5,0),(0,0,0)]
    return [CostModel(*case) for case in cases]


def path_signature(row):
    # Order path deliberately excludes price/quantity, which can change even when actions match.
    return [(e['side'],e['executed_at_utc'],e['reason']) for e in row['attribution']['events']]


def sensitivity(rows):
    result=[]
    for row in rows:
        base=next(r for r in rows if r['market']==row['market'] and r['strategy']==row['strategy']
            and r['costs']=={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'})
        left,right=row['metrics']['net_return_pct'],base['metrics']['net_return_pct']
        result.append({'market':row['market'],'strategy':row['strategy'],**row['costs'],
            'net_return_pct':left,'delta_vs_primary_pp':None if left is None or right is None else Decimal(left)-Decimal(right),
            'same_action_times_and_reasons_as_primary':path_signature(row)==path_signature(base),
            'same_fill_prices_quantities_as_primary':[(e['side'],e['executed_at_utc'],e['quantity'],e['price']) for e in row['attribution']['events']]==[(e['side'],e['executed_at_utc'],e['quantity'],e['price']) for e in base['attribution']['events']],
            'interpretation':'RERUN_COUNTERFACTUAL_NOT_ADDITIVE_CAUSAL_ATTRIBUTION'})
    return serializable(result)


def csv_file(path,rows,fields):
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def markdown(report):
    lines=['# P11-D one-minute loss attribution','',f"Status: {report['status']}; source: {report['source']}",
        f"UTC: [{report['period_start']}, {report['period_end_exclusive']})",'',
        'Exploratory previously disclosed development data. No independent validation, optimization or causal strategy superiority claim.',
        'Missing candles are quarantined without interpolation; SHA/source/metadata corruption aborts the study.',
        'Amounts use P9 fee-inclusive moving-average allocation. Open-position valuation is separate from realized sales.',
        'Buy & Hold starts with 40% allocation at the same first executable open, holds to final close, and pays entry costs only.',
        'Exposure differs across strategies; relative returns are not risk-adjusted evidence of superiority. No terminal liquidation.',
        '', '|Market|Input status|Missing minutes|','|---|---|---:|']
    for r in report['datasets']:lines.append(f"|{r['market']}|{r['status']}|{r.get('missing','N/A')}|")
    lines+=['','## Primary fee5/slip5/full-spread2bps','',
        '|Market|Strategy|Buy/sell|Net %|Realized KRW|Unrealized KRW|B&H40 %|Held time %|Mean notional / initial %|Win mean P&L / sec|Loss mean P&L / sec|Expectancy KRW|Max loss streak|',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|']
    for r in report['comparisons']:
        if r['costs']!={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}:continue
        a=r['attribution'];m=r['metrics'];w=a['wins'];l=a['losses']
        lines.append(f"|{r['market']}|{r['strategy']}|{r['fills']['buys']}/{r['fills']['sells']}|{display(m['net_return_pct'])}|{display(a['net_realized_pnl'])}|{display(a['unrealized_pnl'])}|{display(m['buy_hold_40_net_pct'])}|{display(a['exposure']['position_held_time_pct'])}|{display(a['exposure']['mean_marked_notional_over_initial_pct'])}|{display(w['mean_net_pnl'])} / {display(w['mean_holding_seconds'])}|{display(l['mean_net_pnl'])} / {display(l['mean_holding_seconds'])}|{display(a['expectancy_per_sell'])}|{a['max_consecutive_losing_sells'] if a['max_consecutive_losing_sells'] is not None else 'N/A'}|")
    lines+=['','## Exit/risk attribution (primary costs)','',
        '|Market|Strategy|Exit reason|Sells|Net realized KRW|Mean holding sec|Averaging buys / subsequent sells|',
        '|---|---|---|---:|---:|---:|---|']
    for r in report['comparisons']:
        if r['costs']!={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}:continue
        a=r['attribution']
        for reason,g in a['exit_reasons'].items():
            lines.append(f"|{r['market']}|{r['strategy']}|{reason}|{g['sells']}|{display(g['net_realized_pnl'])}|{display(Decimal(g['holding_seconds_sum'])/g['sells'])}|{a['averaging']['buys']} / {a['averaging']['sells_after_averaging']}|")
    lines+=['','## Cost reruns','', '|Market|Strategy|Fee/slip/spread bps|Net %|Delta vs primary pp|Same action path|',
        '|---|---|---|---:|---:|---|']
    for c in report['cost_sensitivity']:
        lines.append(f"|{c['market']}|{c['strategy']}|{c['fee_bps']}/{c['slippage_bps']}/{c['spread_bps']}|{display(c['net_return_pct'])}|{display(c['delta_vs_primary_pp'])}|{c['same_action_times_and_reasons_as_primary']}|")
    lines+=['','## Per-sale detail (primary costs; all costs in closed_sales.csv / JSON)','',
        '|Market|Strategy|Entry first UTC|Entry signals (allocated quantities)|Exit UTC/reason|Quantity|Entry avg / exit price|Held weighted sec|Buy / sell fee|Gross reference P&L|Net realized KRW|',
        '|---|---|---|---|---|---:|---|---:|---|---:|---:|']
    for r in report['comparisons']:
        if r['costs']!={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}:continue
        for t in r['attribution']['closed_sales']:
            signals='; '.join(f"{e['reason']}:{','.join(e['signal_components']) or 'N/A'} ({display(e['allocated_quantity'])})" for e in t['entry_signals'])
            lines.append(f"|{r['market']}|{r['strategy']}|{t['entry_first_at_utc']}|{signals}|{t['exit_at_utc']} / {t['exit_reason']}|{t['quantity']}|{display(t['entry_average_price'])} / {display(t['exit_price'])}|{display(t['quantity_weighted_holding_seconds'])}|{display(t['allocated_buy_fee'])} / {display(t['sell_fee'])}|{display(t['gross_reference_price_pnl'])}|{display(t['net_realized_pnl'])}|")
    lines+=['','All fills, open positions and realized/unrealized amounts are in results.json / fills.csv / summary.csv.',
        'Quantity-weighted entry times allocate partial sells proportionally, not FIFO. Breakeven breaks a losing streak.',
        'Fixed-fill cost identities are bookkeeping. Cost reruns can change sizing, triggers, cooldown and action paths; their differences are not additive causal effects.',
        'Averaging-associated P&L is descriptive; no alternate risk-policy run proves its causal benefit/harm.',
        'Zero fills: performance N/A. Realized zero with only open buys is a ledger fact, not zero total return.',
        'No original Mac tick/evaluation inputs: this does not identify the historical live PAPER zero-trade cause.']
    return '\n'.join(lines)+'\n'


def run(data,output,*,allow_synthetic=False):
    require(1<=len(data)<=4,'INPUT_LIMIT')
    frames={};datasets={};sources=[];period=None;provenance=[]
    for path in data:
        m,good,bad=load_partitioned_sample(path,allow_synthetic=allow_synthetic,quarantine_gaps=True)
        spec=m['plan'];current=(spec['start'],spec['end_exclusive'])
        require(period is None or period==current,'UNPAIRED_PERIODS');period=current
        require(not (set(datasets)&set(spec['markets'])),'DUPLICATE_MARKET_INPUT')
        sources.append(m['source'])
        provenance.append({'manifest_canonical_sha256':sha(canonical(m)),'plan_sha256':m['plan_sha256'],
            'source':m['source'],'collection_started_at':m['started_at'],'collection_finished_at':m['finished_at']})
        for market,frame in good.items():
            meta=m['datasets'][market]
            datasets[market]={'market':market,'status':'VERIFIED_COMPLETE_CANDLES','rows':len(frame),'missing':0,
                'file_sha256':meta['file_sha256'],'dataset_sha256':meta['dataset_sha256']}
            frames[market]=frame
        for market,meta in bad.items():datasets[market]={'market':market,**meta}
    for market in MARKETS:datasets.setdefault(market,{'market':market,'status':'NOT_PROVIDED_UNVERIFIED'})
    p=new_output(output);protocol,ph=load_protocol();rows=[]
    for market,raw in frames.items():
        frame,windows=prepare(raw,60)
        for strategy in candidates():
            for costs in cost_cases(protocol):
                r=simulate(frame,windows,market=market,bar_seconds=60,costs=costs,evaluation_start=period[0],
                    strategy=strategy,initial_cash=protocol['initial_cash'],details=True)
                require(r['counts']['evaluations']>0,'INSUFFICIENT_EVALUABLE_MINUTES')
                r.pop('trace');r.update(strategy=strategy.name,source=m['source'] if len(set(sources))==1 else 'MIXED_DECLARED_AND_SYNTHETIC',
                    period_start=period[0],period_end_exclusive=period[1]);rows.append(r)
    status='COMPLETE_FOUR_MARKET_STUDY' if len(frames)==4 else 'PARTIAL_MARKET_STUDY' if frames else 'INSUFFICIENT_REAL_DATA'
    synthetic='SYNTHETIC_TEST_TRANSPORT' in sources
    if synthetic:status='SYNTHETIC_TEST_ONLY_'+status
    report={'schema':'P11D_LOSS_ATTRIBUTION_V1','status':status,'report_generation_complete':True,
        'all_four_markets_complete':len(frames)==4,'source':'SYNTHETIC_TEST_TRANSPORT' if synthetic else 'PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED',
        'period_start':period[0],'period_end_exclusive':period[1],'data_provenance':provenance,
        'research_only':True,'independent_validation':False,'previously_disclosed_development':True,
        'other_minute_collection_overlap':'UNKNOWN_NO_GLOBAL_REGISTRY','holdout_rows_parsed':0,'holdout_evaluated':False,
        'network_calls':0,'execution_enabled':False,'multi_asset_portfolio':False,'protocol_sha256':ph,
        'datasets':[datasets[k] for k in MARKETS],'comparisons':rows,'cost_sensitivity':sensitivity(rows)}
    write_json(p/'results.json',report)
    flat=lambda r:{'market':r['market'],'strategy':r['strategy'],**r['costs']}
    events=[{**flat(r),**e} for r in rows for e in r['attribution']['events']]
    sales=[{**flat(r),**e} for r in rows for e in r['attribution']['closed_sales']]
    summaries=[{**flat(r),**r['metrics'],**{'filled_'+k:v for k,v in r['fills'].items()},
        **{k:r['attribution'][k] for k in ('wins','losses','expectancy_per_sell','max_consecutive_losing_sells','averaging','exposure','cost_totals','open_position')}} for r in rows]
    for name,values in (('fills.csv',events),('closed_sales.csv',sales),('summary.csv',summaries),('cost_sensitivity.csv',report['cost_sensitivity'])):
        fields=list(values[0]) if values else ['market','strategy','status']
        csv_file(p/name,values,fields)
    (p/'REPORT.md').write_text(markdown(report),encoding='utf-8')
    (p/'INCOMPLETE').unlink();return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,action='append',required=True,help='Repeat for disjoint same-period P11-C collections; max 4')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);logging.disable(logging.CRITICAL)
    try:
        r=run(args.data,args.output)
        result={'status':r['status'],'network_calls':0,'execution_enabled':False,
            'all_four_markets_complete':r['all_four_markets_complete'],'comparisons':len(r['comparisons']),
            'datasets':[{'market':d['market'],'status':d['status']} for d in r['datasets']]}
    except Exception:
        result={'status':'INSUFFICIENT_REAL_DATA','reason':'INPUT_OUTPUT_OR_ACCOUNTING_UNVERIFIED',
            'network_calls':0,'execution_enabled':False}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 0 if result['status']=='COMPLETE_FOUR_MARKET_STUDY' else 3 if result['status']=='PARTIAL_MARKET_STUDY' else 2


if __name__=='__main__':raise SystemExit(main())
