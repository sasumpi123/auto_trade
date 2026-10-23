"""Offline exploratory overlay comparison on already disclosed development data only."""
import argparse
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pandas as pd

from strategies.signals import LegacyCompositeStrategy, research_strategies
from .collection import write_json
from .data import dataset_hash
from .development_data import load_development
from .engine import BacktestConfig, BacktestEngine, CostModel, serializable
from .historical import BuyHold, load_protocol
from .risk import OVERLAYS, RiskOverlay

ROOT = Path(__file__).resolve().parents[1]
RISK_HASH = 'd5086b966a1b5327f485983f548a45369ee631a25f1f80bd65c420e2f9316c2c'


def load_risk_protocol():
    raw = (Path(__file__).with_name('risk_protocol.json')).read_text(encoding='utf-8').encode()
    if hashlib.sha256(raw).hexdigest() != RISK_HASH:
        raise ValueError('Frozen overlay assumptions changed')
    return json.loads(raw), RISK_HASH


def run(data, output, *, archived_results=None):
    if archived_results is None:
        raise ValueError('Explicit archived baseline results required')
    protocol, protocol_hash = load_protocol()
    risk_protocol, risk_hash = load_risk_protocol()
    data, output = Path(data), Path(output)
    manifest = json.loads((data/'manifest.json').read_text(encoding='utf-8'))
    if manifest['protocol'] != protocol or manifest['protocol_sha256'] != protocol_hash or not manifest.get('completed_at'):
        raise ValueError('Snapshot protocol/completion mismatch')
    keys = {f'{m}-{u}' for m in protocol['markets'] for u in protocol['units_minutes']}
    if set(manifest['datasets']) != keys:
        raise ValueError('Incomplete dataset registry')
    archive = json.loads(Path(archived_results).read_text(encoding='utf-8')) if archived_results else None
    if archive is not None and (archive['protocol_sha256'] != protocol_hash or archive['holdout_evaluated'] is not False):
        raise ValueError('Archived baseline contract mismatch')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version':1, 'complete':False, 'research_only':True,
              'exploratory_previously_disclosed_data':True, 'independent_validation':False,
              'holdout_evaluated':False, 'holdout_rows_parsed':0, 'selection_performed':False,
              'multi_asset_portfolio_implemented':False, 'base_protocol':protocol,
              'base_protocol_sha256':protocol_hash,'risk_protocol':risk_protocol,'risk_protocol_sha256':risk_hash,
              'baseline_archive_checked': archive is not None,
              'runtime':{'python':__import__('platform').python_version(),'pandas':pd.__version__},
              'datasets':{}}
    rows = []
    for market in protocol['markets']:
        for unit in protocol['units_minutes']:
            key = f'{market}-{unit}'
            meta = manifest['datasets'][key]
            if meta['snapshot'] != f'{key}.csv.gz':
                raise ValueError('Unexpected snapshot path')
            frame = load_development(data/meta['snapshot'],protocol,unit,meta)
            dataset = {'development_sha256':dataset_hash(frame),'development_rows':len(frame),'results':[]}
            base = BacktestConfig(CostModel(5,5,protocol['full_spread_bps']),initial_cash=protocol['initial_cash'],
                position_fraction=protocol['position_fraction'],bar_seconds=unit*60,
                cooldown_seconds=protocol['cooldown_seconds'],min_volume_ratio=protocol['min_volume_ratio'])
            for split,end,start in (('train',protocol['train_end'],protocol['start']),
                                    ('validation',protocol['validation_end'],protocol['train_end'])):
                context = frame.loc[frame.index < pd.Timestamp(end)]
                part = context.loc[context.index >= pd.Timestamp(start)]
                prepared = BacktestEngine(base).prepare(context)
                candidates = [*research_strategies(),LegacyCompositeStrategy(),BuyHold(),BuyHold('buy_hold_100')]
                if [s.name for s in candidates] != protocol['strategies']:
                    raise ValueError('Strategy registry changed')
                for fee in protocol['fee_bps_per_side']:
                    for slip in protocol['slippage_bps_per_side']:
                        config = replace(base,costs=CostModel(fee,slip,protocol['full_spread_bps']))
                        for strategy in candidates:
                            passive = strategy.name.startswith('buy_hold_')
                            selected = replace(config,position_fraction='1') if strategy.name=='buy_hold_100' else config
                            if passive:selected=replace(selected,min_volume_ratio='0')
                            for overlay in ('signal_only',) if passive else OVERLAYS:
                                result = BacktestEngine(selected).run(prepared,strategy,evaluation_start=part.index[0],overlay=RiskOverlay(overlay))
                                if archive is not None and overlay=='signal_only':
                                    old = next(r['metrics'] for r in archive['datasets'][key]['results']
                                        if r['split']==split and r['strategy']==strategy.name and r['fee_bps']==fee and r['slippage_bps']==slip)
                                    for metric,value in old.items():
                                        new = result.metrics[metric]
                                        if (new is None) != (value is None) or (new is not None and abs(float(new)-float(value))>1e-8):
                                            raise ValueError('Archived baseline metric regression')
                                item={'dataset':key,'split':split,'strategy':strategy.name,'overlay':overlay,
                                    'fee_bps':fee,'slippage_bps':slip,'spread_bps':protocol['full_spread_bps'],
                                    'primary':fee==protocol['primary_fee_bps'] and slip==protocol['primary_slippage_bps'],
                                    'segment_sha256':dataset_hash(part) if overlay=='signal_only' and passive else meta['splits'][split]['sha256'],
                                    'context_sha256':prepared.data_hash,
                                    'unfilled_final_signal':result.unfilled_final_signal is not None,
                                    'fill_trace_sha256':hashlib.sha256(json.dumps(serializable([asdict(f) for f in result.fills]),sort_keys=True).encode()).hexdigest(),
                                    'metrics':serializable(result.metrics)}
                                dataset['results'].append(item)
                                rows.append({**{k:v for k,v in item.items() if k!='metrics'},**item['metrics']})
            report['datasets'][key]=dataset
            print(f'{key}: development only; {len(dataset["results"])} comparisons',flush=True)
    report['complete']=True
    write_json(output/'results.json',report)
    pd.DataFrame(rows).to_csv(output/'metrics.csv',index=False,lineterminator='\n')
    from .risk_report import summarize
    summarize(report, output)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'research/2026-10-21/data')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-results',type=Path,default=ROOT/'research/2026-10-21/results.json')
    args=parser.parse_args()
    run(args.data,args.output,archived_results=args.baseline_results)


if __name__=='__main__':main()
