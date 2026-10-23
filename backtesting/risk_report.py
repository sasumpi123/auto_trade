"""Descriptive paired summaries, never a multi-asset portfolio or selection rule."""
from decimal import Decimal
from pathlib import Path
import argparse
import json

import pandas as pd

from .risk import OVERLAYS


def summarize(report, output):
    if (not report['complete'] or report['holdout_evaluated'] or report['independent_validation']
            or not report['baseline_archive_checked']):
        raise ValueError('Only complete exploratory development reports are supported')
    rows=[r for d in report['datasets'].values() for r in d['results']]
    if len(rows)!=3072 or {r['split'] for r in rows}!={'train','validation'}:
        raise ValueError('Incomplete fixed comparison grid')
    output=Path(output)
    baselines={(r['dataset'],r['split'],r['strategy'],r['fee_bps'],r['slippage_bps']):r for r in rows if r['overlay']=='signal_only'}
    paired=[]
    for r in rows:
        if r['overlay']=='signal_only':continue
        base=baselines[(r['dataset'],r['split'],r['strategy'],r['fee_bps'],r['slippage_bps'])]
        pair={k:r[k] for k in ('dataset','split','strategy','overlay','fee_bps','slippage_bps','primary')}
        for metric in ('net_return_pct','max_drawdown_pct','total_fees','fill_count','closed_trades',
                       'mean_market_exposure_pct','max_market_exposure_pct','invested_bar_pct'):
            pair['delta_'+metric]=str(Decimal(str(r['metrics'][metric]))-Decimal(str(base['metrics'][metric])))
        paired.append(pair)
    pd.DataFrame(paired).to_csv(output/'paired_deltas.csv',index=False,lineterminator='\n')
    def num(r,k):return Decimal(str(r['metrics'][k]))
    def fmt(value):return 'N/A' if value is None else f'{value:.3f}'
    lines=['# P11-A — fixed risk overlay comparison', '',
        'Exploratory reuse of disclosed train/validation, not independent validation. Holdout rows were not parsed or evaluated.',
        '3,072 single-asset runs; 768 signal-only baselines checked against the archived metrics (absolute tolerance 1e-8). No selection or optimization.',
        'Full results: metrics.csv/results.json; 2,304 matched nonbaseline differences: paired_deltas.csv. Spread fixed at 2bps; fee 5/10bps × slippage 0/5/10bps.',
        'These arithmetic cross-dataset summaries are NOT portfolio returns. The 8 overlapping market/interval samples are correlated; no significance claim.', '',
        'Primary cost: fee 5bps / slippage 5bps / full spread 2bps. Equal-weight summaries across 8 separate initial-cash simulation accounts.',
        'Worst return=min net return; worst MDD=max MDD. Win mean excludes datasets without a closed trade (count shown); it is not a pooled portfolio win rate.',
        'Fills/closed are summed descriptive counts across simulations. Exposure is mean close-marked invested asset/equity percentage; idle/warm-up bars count.', '']
    for split in ('train','validation'):
        lines += [f'## {split}', '', '|Strategy|Overlay|Mean net %|Worst net %|Worst MDD %|Mean fees KRW|Fills/closed|Mean win % (n/8)|Mean exposure %|',
                  '|---|---|---:|---:|---:|---:|---:|---:|---:|']
        for strategy in report['base_protocol']['strategies']:
            for overlay in ('signal_only',) if strategy.startswith('buy_hold_') else OVERLAYS:
                group=[r for r in rows if r['primary'] and r['split']==split and r['strategy']==strategy and r['overlay']==overlay]
                if len(group)!=8:raise ValueError('Missing matched primary cell')
                wins=[num(r,'win_rate_pct') for r in group if r['metrics']['win_rate_pct'] is not None]
                lines.append('|'+ '|'.join((strategy,overlay,
                    fmt(sum(num(r,'net_return_pct') for r in group)/8),fmt(min(num(r,'net_return_pct') for r in group)),
                    fmt(max(num(r,'max_drawdown_pct') for r in group)),fmt(sum(num(r,'total_fees') for r in group)/8),
                    f"{sum(int(num(r,'fill_count')) for r in group)}/{sum(int(num(r,'closed_trades')) for r in group)}",
                    f'{fmt(sum(wins)/len(wins) if wins else None)} ({len(wins)}/8)',
                    fmt(sum(num(r,'mean_market_exposure_pct') for r in group)/8)))+'|')
        lines += ['']
    lines += ['## Legacy composite: validation baseline → take-profit + one averaging', '',
              '|Dataset|Net %|MDD %|Fees KRW|Fills/closed|Win %|Mean exposure %|Average fills|',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for key in report['datasets']:
        group=[r for r in rows if r['dataset']==key and r['split']=='validation' and r['primary'] and r['strategy']=='legacy_composite']
        a=next(r for r in group if r['overlay']=='signal_only')
        b=next(r for r in group if r['overlay']=='take_profit_average_once_5')
        def change(k):return fmt(num(a,k) if a['metrics'][k] is not None else None)+' → '+fmt(num(b,k) if b['metrics'][k] is not None else None)
        counts=' → '.join(f"{r['metrics']['fill_count']}/{r['metrics']['closed_trades']}" for r in (a,b))
        lines.append('|'+ '|'.join((key,change('net_return_pct'),change('max_drawdown_pct'),change('total_fees'),counts,
            change('win_rate_pct'),change('mean_market_exposure_pct'),str(b['metrics']['averaging_fill_count'])))+'|')
    lines += ['', '## Cost sensitivity (validation, legacy composite, 8-account descriptive mean)', '',
              '|Fee bps|Slippage bps|Overlay|Mean net %|Worst MDD %|Mean fees KRW|',
              '|---:|---:|---|---:|---:|---:|']
    for fee in report['base_protocol']['fee_bps_per_side']:
        for slip in report['base_protocol']['slippage_bps_per_side']:
            for overlay in OVERLAYS:
                g=[r for r in rows if r['split']=='validation' and r['strategy']=='legacy_composite' and r['fee_bps']==fee and r['slippage_bps']==slip and r['overlay']==overlay]
                lines.append(f'|{fee}|{slip}|{overlay}|{fmt(sum(num(r,"net_return_pct") for r in g)/8)}|{fmt(max(num(r,"max_drawdown_pct") for r in g))}|{fmt(sum(num(r,"total_fees") for r in g)/8)}|')
    lines += ['', '## Limits', '',
        '- Closed-bar decisions execute at next open with fixed costs and assumed full fills. No intrabar path, liquidity/partial-fill/cancel/timeout simulation; a stop is not a guaranteed loss cap.',
        '- Final holdings are marked to close without liquidation costs. Close-only MDD misses intrabar risk. Short, correlated samples and many comparisons do not establish future profitability.',
        '- Existing combined gzip archives are streamed for exactly development rows only. Buffered compressed bytes may include tail data; no byte-level isolation/CRC or whole-archive revalidation claim.',
        '- Operating strategy, orders, PAPER journals, Slack, automatic workers and deployment are unchanged/OFF. Details: ../../docs/P11_STRATEGY_RISK_RESEARCH.md.', '']
    (output/'README.md').write_text('\n'.join(lines),encoding='utf-8')
    return paired


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    summarize(json.loads((args.directory/'results.json').read_text(encoding='utf-8')),args.directory)


if __name__=='__main__':main()
