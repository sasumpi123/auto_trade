"""Regenerate the fixed development study into a NEW explicit directory, offline."""
import argparse
import csv
import hashlib
import json
import logging
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from backtesting.legacy_diagnostic import run,markdown


def write_study(reports,output):
    """Archive aggregates plus full-fill-trace hashes; CLI retains detailed traces."""
    public=[];rows=[]
    for report in reports:
        for item in report['results']:
            reduced={k:v for k,v in item.items() if k!='trace'}
            reduced.update(split=report['split'],period_start=report['period_start'],period_end_exclusive=report['period_end_exclusive'],
                trace_count=len(item['trace']),trace_sha256=hashlib.sha256(json.dumps(item['trace'],sort_keys=True).encode()).hexdigest())
            public.append(reduced)
            rows.append({'market':item['market'],'minutes':item['bar_seconds']//60,'split':report['split'],**item['costs'],
                **item['counts'],**{'filled_'+k:v for k,v in item['fills'].items()},**item['metrics'],
                **{k:item[k] for k in ('fees','slippage_cost','half_spread_cost','na_reason')}})
    envelope={k:v for k,v in reports[0].items() if k not in ('results','split','period_start','period_end_exclusive')}
    envelope['comparisons']=public
    # One comparison per line keeps generated diffs reviewable without hiding data.
    (output/'results.json').write_text(json.dumps({k:v for k,v in envelope.items() if k!='comparisons'},ensure_ascii=False)[:-1]+',\n"comparisons":[\n'+',\n'.join(json.dumps(r,ensure_ascii=False,allow_nan=False) for r in public)+'\n]}\n',encoding='utf-8')
    (output/'REPORT.md').write_text('\n\n'.join(markdown(r) for r in reports),encoding='utf-8')
    with (output/'metrics.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=False);logging.disable(logging.CRITICAL)
    reports=[]
    for market in ('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL'):
        for unit in (15,60):
            for split in ('train','validation'):
                reports.append(run(args.data,market,unit,split));print(f'{market}-{unit}-{split}',flush=True)
    write_study(reports,args.output)


if __name__=='__main__':main()
