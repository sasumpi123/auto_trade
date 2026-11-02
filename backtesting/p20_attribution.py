"""Offline attribution of verified P19 primary cases through actual P17 execution."""
import argparse
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
from decimal import Decimal
import csv,hashlib,json,logging,multiprocessing as mp,re
from pathlib import Path
import time

from . import p19_baseline as base
from . import minute_study as minute

COST={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'}


def metadata(path):
    path=base.safe_path(path)
    base.require(path.is_file() and path.stat().st_size<=minute.MAX_FILE_BYTES,'UNVERIFIED_METADATA')
    raw=path.read_bytes()
    base.require(len(raw)<=minute.MAX_FILE_BYTES,'UNVERIFIED_METADATA')
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def inputs(value):
    root=base.safe_path(value)
    if root.name=='analysis' and (root/'results.json').is_file():root=base.safe_path(root.parent)
    report,digest=metadata(root/'analysis/results.json')
    base.require(report.get('protocol')==base.protocol() and report.get('protocol_sha256')==base.protocol_hash(),'P19_PROTOCOL_MISMATCH')
    base.require(report.get('status') in ('COMPLETE','PARTIAL_REAL_DATA'),'P19_NOT_FINALIZED')
    cases=report.get('cases');base.require(type(cases) is list and 0<len(cases)<=18,'P19_CASE_LIMIT')
    keys=set();primary={}
    for r in cases:
        c=r['costs'];cost=tuple(c[k] for k in ('fee_bps','slippage_bps','spread_bps'))
        key=(r['day'],r['market'],cost)
        base.require(r['day'] in base.DAYS and r['market'] in base.MARKETS and cost in tuple(tuple(map(str,c)) for c in base.COSTS)
                     and key not in keys and r['status']=='EVALUATED' and r.get('accounting_verified') is True,'P19_CASE_UNVERIFIED')
        keys.add(key)
        if c==COST:primary[(r['day'],r['market'])]=r
    groups={(r['day'],r['market']) for r in cases}
    base.require(all(sum(x['day']==d and x['market']==m for x in cases)==3 for d,m in groups) and primary,'P19_MISSING_COST_CASES')
    base.require(len(cases)==18 if report['status']=='COMPLETE' else len(cases)<18,'P19_COMPLETION_MISMATCH')
    excluded=set()
    for item in report.get('excluded',[]):
        markets=item.get('markets',[item.get('market')])
        base.require(item.get('day') in base.DAYS and type(markets) is list and markets
                     and item.get('reason') in ('INVALID_OR_MISSING_DAILY_INPUT','MISSING_CANDLES'),'P19_EXCLUSION_UNVERIFIED')
        for market in markets:
            key=(item['day'],market)
            base.require(market in base.MARKETS and key not in excluded,'P19_EXCLUSION_UNVERIFIED')
            excluded.add(key)
    base.require(excluded=={(d,m) for d in base.DAYS for m in base.MARKETS}-groups,'P19_EXCLUSION_MISMATCH')
    return root,report,primary,digest


def summary(attribution):
    result=deepcopy(attribution)
    events={e['sequence']:e for e in result['events']}
    for sale in result['closed_sales']:
        sale['exit_signals']=events[sale['sequence']]['signal_components']
        gross=Decimal(sale['gross_reference_price_pnl']);net=Decimal(sale['net_realized_pnl'])
        sale['price_loss']=gross<0;sale['positive_price_pnl_cost_loss']=gross>0 and net<0
    sales=result['closed_sales']
    result['loss_counts']={'price_loss':sum(s['price_loss'] for s in sales),
        'positive_price_pnl_cost_loss':sum(s['positive_price_pnl_cost_loss'] for s in sales),
        'zero_price_pnl_cost_loss':sum(Decimal(s['gross_reference_price_pnl'])==0 and Decimal(s['net_realized_pnl'])<0 for s in sales)}
    for row in result['exit_reasons'].values():
        row['mean_holding_seconds']=str(Decimal(row['holding_seconds_sum'])/row['sells'])
    result['interpretation']='Accounting decomposition at identical quantities; NOT policy causal effect'
    return result


def matched_case(frame,windows,old):
    rerun=base.case(frame,windows,old['market'],(5,5,2),details=True)
    detail=rerun.pop('attribution');rerun.pop('trace',None)
    rerun.update(day=old['day'],input_quality=old['input_quality'])
    base.require(minute.canonical(rerun)==minute.canonical(old),'P19_REPLAY_MISMATCH')
    result=summary(detail)
    initial=Decimal(rerun['initial_cash']);equity=Decimal(rerun['final_equity'])
    position=result['open_position'];marked=Decimal(position['quantity'])*Decimal(position['mark_price'])
    net=Decimal(result['net_realized_pnl']);unreal=Decimal(result['unrealized_pnl'])
    base.require(base.close(net+unreal,equity-initial),'P20_TOTAL_PNL_MISMATCH')
    result['p9_reconciliation']={'verified':True,'initial_cash':str(initial),'final_equity':str(equity),
        'final_cash':str(equity-marked),'total_pnl':str(net+unreal),'remaining_cost':position['remaining_cost'],
        'fees':rerun['fees'],'slippage_cost':rerun['slippage_cost'],'half_spread_cost':rerun['half_spread_cost']}
    return result


def work(source,out,pipe):
    prior=logging.root.manager.disable
    report={'status':'INCOMPLETE','cases':[],'excluded':[],'maximum_cases':6}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            root,old,primary,digest=inputs(source);report['p19_results_sha256']=digest
            report['protocol_sha256']=base.protocol_hash()
            for day in base.DAYS:
                selected=[m for m in base.MARKETS if (day,m) in primary]
                for market in base.MARKETS:
                    if market not in selected:report['excluded'].append({'day':day,'market':market,'reason':'P19_BASELINE_EXCLUDED'})
                if not selected:continue
                daily=base.safe_path(root/'data'/day);plan,plan_hash=metadata(daily/'plan.json')
                _,manifest_hash=metadata(daily/'manifest.json')
                base.require(plan['markets']==list(base.MARKETS) and minute.utc(plan['start'])==base.day_start(day)
                    and minute.utc(plan['end_exclusive'])==base.day_start(day)+minute.pd.Timedelta(days=1),'P19_PERIOD_MISMATCH')
                meta,frames,missing=minute.load_partitioned_sample(daily,quarantine_gaps=True)
                report.setdefault('input_hashes',{})[day]={'manifest':manifest_hash,'plan':plan_hash,
                    'files':{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in selected}}
                for market in selected:
                    base.require(market in frames and primary[(day,market)]['input_quality']==meta['datasets'][market],'P19_INPUT_MISMATCH')
                    frame,windows=base.prepare(frames[market],60)
                    value=matched_case(frame,windows,primary[(day,market)])
                    report['cases'].append({'day':day,'market':market,'data_sha256':meta['datasets'][market]['dataset_sha256'],
                                            'attribution':value})
                    base.write_json(out/'results.json',report)
            for day,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in {'manifest.json':hashes['manifest'],'plan.json':hashes['plan'],**hashes['files']}.items():
                    base.require(hashlib.sha256((root/'data'/day/name).read_bytes()).hexdigest()==digest0,'P19_INPUT_CHANGED')
            base.require(hashlib.sha256((root/'analysis/results.json').read_bytes()).hexdigest()==digest,'P19_INPUT_CHANGED')
            report['status']='VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED'
            base.write_json(out/'results.json',report);(out/'INCOMPLETE').unlink()
    except Exception as exc:
        report['status']='INCOMPLETE';report['failure_type']=type(exc).__name__
        code=str(exc)
        report['failure_reason']=code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',code) else 'INPUT_OR_REPLAY_UNVERIFIED'
        # No raw exception, account path or payload in terminal output.
        base.write_json(out/'results.json',report)
    finally:logging.disable(prior);pipe.close()


def read_results(path):
    base.require(path.is_file() and not path.is_symlink() and path.stat().st_size<=64*1024*1024,'ATTRIBUTION_OUTPUT_UNVERIFIED')
    return json.loads(path.read_text())


def write_report(out,report):
    """Compact private exports; results.json remains the complete atomic evidence."""
    fields=('day','market','sequence','entry_first_at_utc','entry_last_at_utc','exit_at_utc','exit_reason',
        'quantity','quantity_weighted_holding_seconds','gross_reference_price_pnl','allocated_buy_fee','sell_fee',
        'allocated_buy_slippage','sell_slippage','allocated_buy_half_spread','sell_half_spread','net_realized_pnl',
        'price_loss','positive_price_pnl_cost_loss')
    lines=['# P20 P17 trade attribution','',report['status'],
        'Accounting decomposition, not a policy causal effect. Development data, not independent validation.','',
        '|UTC day|Market|Sells|Price losses|Positive price, cost loss|Realized KRW|Unrealized KRW|Remaining cost KRW|',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    with (out/'sales.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for case in report['cases']:
            a=case['attribution'];loss=a['loss_counts']
            for sale in a['closed_sales']:
                writer.writerow({**{k:sale[k] for k in fields if k in sale},'day':case['day'],'market':case['market']})
            lines.append(f"|{case['day']}|{case['market']}|{len(a['closed_sales'])}|{loss['price_loss']}|{loss['positive_price_pnl_cost_loss']}|"
                f"{a['net_realized_pnl']}|{a['unrealized_pnl']}|{a['open_position']['remaining_cost']}|")
    lines+=['',f"Excluded: {len(report['excluded'])}; unfinished: {len(report['unfinished'])}.",
        'Full entry/exit indicator tags, reason groups, P9 reconciliation and receipt observations: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def analyze(source,output,*,seconds=600,worker=work):
    base.require(type(seconds) is int and 1<=seconds<=600,'TIME_LIMIT')
    source=base.safe_path(source);out=base.fresh(output);started=time.monotonic()
    ctx=mp.get_context('spawn');reader,writer=ctx.Pipe(duplex=False)
    process=ctx.Process(target=worker,args=(source,out,writer));process.start();writer.close()
    interrupted=False
    try:
        while process.is_alive() and time.monotonic()-started<seconds:process.join(.1)
    except KeyboardInterrupt:interrupted=True
    finally:
        alive=process.is_alive()
        if alive:process.terminate();process.join(2)
        if process.is_alive():process.kill();process.join(2)
        reader.close()
    path=out/'results.json';report=read_results(path) if path.exists() else {'status':'INCOMPLETE','cases':[],'excluded':[]}
    if alive or interrupted or process.exitcode!=0:report['status']='INCOMPLETE';(out/'INCOMPLETE').touch()
    report['elapsed_seconds']=round(time.monotonic()-started,3)
    done={(r['day'],r['market']) for r in report['cases']};excluded={(r['day'],r['market']) for r in report['excluded']}
    report['unfinished']=[{'day':d,'market':m} for d in base.DAYS for m in base.MARKETS if (d,m) not in done|excluded]
    base.write_json(path,report)
    write_report(out,report)
    return out,report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--input',required=True);parser.add_argument('--output',required=True)
    a=parser.parse_args(argv)
    try:
        out,r=analyze(a.input,a.output)
        cases=r['cases'];counts=[x['attribution']['loss_counts'] for x in cases]
        print('Status:',r['status'],'Cases:',len(cases),'Excluded:',len(r['excluded']))
        print('Price-loss sales:',sum(x['price_loss'] for x in counts))
        print('Positive-price cost-loss sales:',sum(x['positive_price_pnl_cost_loss'] for x in counts))
        print('Elapsed seconds:',r['elapsed_seconds'])
        print('Source:',base.safe_path(a.input))
        return 0 if r['status'] in ('VERIFIED','VERIFIED_PARTIAL') else 2
    except Exception:print('INCOMPLETE: input or replay unverified');return 2


if __name__=='__main__':raise SystemExit(main())
