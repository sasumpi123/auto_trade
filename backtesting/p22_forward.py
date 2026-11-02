"""Frozen P22 forward window: explicit later collection, offline P17/P9 pairs."""
import argparse
import csv
import hashlib
import json
import logging
import multiprocessing as mp
import platform
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone
from pathlib import Path
import re
import time

import pandas as pd

from . import minute_study as minute
from . import p19_baseline as base
from . import p20_attribution as p20
from . import p21_entry_filter as p21

PROTOCOL=Path(__file__).with_name('p22_protocol.json')
REGISTRATION=Path(__file__).with_name('p22_registration.json')
PROTOCOL_SHA='4aa290d9077f0a134a376b01b8560f2eddaf65854f1bd329d6e73b9373266af4'
REGISTRATION_SHA='636ad56462e7ac5211d41ff29f9f72b103c885637ec5150c8d53b5108644a715'
FREEZE_COMMIT='31c24a717d6b26337e1306a3d1b4545ec0c05985'
DAYS=('2026-11-03','2026-11-04','2026-11-05')
MARKETS=('KRW-BTC','KRW-XRP')
START=pd.Timestamp('2026-11-03T00:00:00Z')
END=pd.Timestamp('2026-11-06T00:00:00Z')
POLICIES=p21.POLICIES


def utc_now():return pd.Timestamp(datetime.now(timezone.utc))
def normalized(path):return path.read_bytes().replace(b'\r\n',b'\n')
def sha(path):return hashlib.sha256(normalized(path)).hexdigest()
def day_start(day):return pd.Timestamp(day+'T00:00:00Z')
def runner_sha():return sha(Path(__file__))


def frozen():
    """Content pins survive squash merge; receipt is not a server-time attestation."""
    base.require(sha(PROTOCOL)==PROTOCOL_SHA,'P22_PROTOCOL_CHANGED')
    base.require(sha(REGISTRATION)==REGISTRATION_SHA,'P22_REGISTRATION_UNVERIFIED')
    p=json.loads(normalized(PROTOCOL));r=json.loads(normalized(REGISTRATION))
    base.require(p['schema']=='P22_FROZEN_FORWARD_V1' and p['days']==list(DAYS) and p['markets']==list(MARKETS)
        and minute.utc(p['start'])==START and minute.utc(p['end_exclusive'])==END
        and minute.utc(p['collection_not_before_utc'])==END and p['costs_bps']==p20.COST
        and p['policies']==list(POLICIES) and p['entry_block_if_less_than_bps']=='44'
        and p['initial_cash']=='1000000' and p['position_fraction']=='0.4'
        and p['warmup_candles']==200 and p['max_policy_cases']==12 and p['analysis_seconds']==600,'P22_FIXED_CONDITIONS_CHANGED')
    base.require(minute.utc(p['declared_at_utc'])<=minute.utc(r['observed_at_utc'])<START
        and minute.utc(p['protocol_push_deadline_exclusive'])==START
        and r['schema']=='P22_FREEZE_OBSERVATION_V1' and r['protocol_commit']==FREEZE_COMMIT
        and r['observed_remote_head']==FREEZE_COMMIT and r['protocol_sha256']==PROTOCOL_SHA
        and r['remote_repository']=='sasumpi123/auto_trade'
        and r['remote_ref']=='refs/heads/feat/p22-frozen-forward-validation','P22_MISSED_OR_UNVERIFIED_FREEZE')
    for name,digest in p['code_sha256'].items():
        base.require(sha(minute.ROOT/name)==digest,'P22_FROZEN_CODE_CHANGED')
    base.require(minute.MAX_CALLS==40 and minute.DEADLINE_SECONDS==120 and p21.THRESHOLD_BPS==44
        and p21.EntryFilterBot.entry_filter_enabled is True,'P22_FROZEN_LIMIT_CHANGED')
    return p


def not_ready():
    return {'status':'NOT_READY','network_calls':0,'files_written':0,'not_before_utc':END.isoformat(),
            'cases':[],'completed_policies':[],'excluded':[],'elapsed_seconds':0}


def collect(output,*,connect_public=False,clock=time.time,client_factory=None):
    frozen()
    instant=pd.Timestamp(clock(),unit='s',tz='UTC')
    if instant<END:return not_ready()
    if not connect_public:return {'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'days':DAYS,'markets':MARKETS}
    out=base.fresh(output);(out/'data').mkdir(mode=0o700)
    (out/'protocol.json').write_bytes(normalized(PROTOCOL));(out/'registration.json').write_bytes(normalized(REGISTRATION))
    report={'schema':'P22_COLLECTION_V1','status':'INCOMPLETE','protocol_sha256':PROTOCOL_SHA,
        'registration_sha256':REGISTRATION_SHA,'runner_sha256':runner_sha(),
        'started_at':instant.isoformat(),'days':[],'network_calls':0}
    base.write_json(out/'collection.json',report)
    try:
        for day in DAYS:
            base.require(pd.Timestamp(clock(),unit='s',tz='UTC')>=END,'P22_CLOCK_BEFORE_COLLECTION_WINDOW')
            start=day_start(day);path=out/'data'/day
            result=minute.collect(path,list(MARKETS),start.isoformat(),(start+pd.Timedelta(days=1)).isoformat(),
                connect_public=True,clock=clock,client=client_factory(day) if client_factory else None)
            calls=result['network_calls']
            base.require(type(calls) is int and 0<=calls<=40 and report['network_calls']+calls<=120,'P22_COLLECTION_LIMIT')
            manifest,digest=p20.metadata(path/'manifest.json')
            report['days'].append({'day':day,'status':result['status'],'complete':manifest.get('complete') is True,
                'network_calls':calls,'manifest_sha256':digest})
            report['network_calls']+=calls;base.write_json(out/'collection.json',report)
        frozen();base.require(report['runner_sha256']==runner_sha(),'P22_RUNNER_CHANGED')
        complete=all(d['complete'] for d in report['days'])
        good=all(d['status']=='COLLECTED_DEVELOPMENT_SAMPLE' for d in report['days'])
        report['status']='COLLECTED' if complete and good else 'PARTIAL_REAL_DATA' if complete else 'INCOMPLETE'
        if complete:(out/'INCOMPLETE').unlink()
    except Exception as exc:
        report.update(status='INCOMPLETE',failure_reason=error_code(exc))
    report['finished_at']=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat()
    base.write_json(out/'collection.json',report)
    return report


def error_code(exc):
    value=str(exc)
    return value if re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',value) else 'P22_INPUT_OR_EXECUTION_UNVERIFIED'


def inputs(source):
    root=base.safe_path(source)
    _,protocol_hash=p20.metadata(root/'protocol.json');_,registration_hash=p20.metadata(root/'registration.json')
    base.require(protocol_hash==PROTOCOL_SHA and registration_hash==REGISTRATION_SHA,'P22_INPUT_PROTOCOL_MISMATCH')
    record,digest=p20.metadata(root/'collection.json')
    base.require(record['schema']=='P22_COLLECTION_V1' and record['protocol_sha256']==PROTOCOL_SHA
        and record['registration_sha256']==REGISTRATION_SHA and record['runner_sha256']==runner_sha(), 'P22_INPUT_REGISTRATION_MISMATCH')
    base.require(END<=minute.utc(record['started_at'])<=minute.utc(record['finished_at'])<=utc_now(),'P22_COLLECTION_TIME_UNVERIFIED')
    days=record['days']
    base.require(type(days) is list and [d['day'] for d in days]==list(DAYS)[:len(days)] and len(days)<=3
        and all(type(d['network_calls']) is int and 0<=d['network_calls']<=40 and type(d['complete']) is bool for d in days)
        and type(record['network_calls']) is int and record['network_calls']==sum(d['network_calls'] for d in days)<=120
        and record['status'] in ('COLLECTED','PARTIAL_REAL_DATA','INCOMPLETE'),'P22_COLLECTION_REGISTRY_MISMATCH')
    if record['status']!='INCOMPLETE':
        base.require(len(days)==3 and all(d['complete'] for d in days),'P22_COLLECTION_REGISTRY_MISMATCH')
    return root,record,digest


def run_policy(frame,windows,market,policy):
    base.require(policy in POLICIES,'P22_UNKNOWN_POLICY')
    r=base.case(frame,windows,market,(5,5,2),details=True,
                bot_factory=p21.EntryFilterBot if policy==POLICIES[1] else None)
    common={k:r[k] for k in ('data_sha256','input_candles','evaluation_first_open','evaluation_last_open',
        'execution_first_open','execution_last_open','conditions')}
    return p21.compact(r,r['attribution'],filtered=policy==POLICIES[1]),common


def work(source,out,pipe):
    previous=logging.root.manager.disable
    report={'schema':'P22_ANALYSIS_V1','status':'INCOMPLETE','protocol_sha256':PROTOCOL_SHA,
        'registration_sha256':REGISTRATION_SHA,'runner_sha256':runner_sha(),'maximum_cases':12,
        'runtime':{'python':platform.python_version(),'pandas':pd.__version__},
        'validation':'TIME_SEPARATED_NOT_PROFITABILITY_PROOF','cases':[],'completed_policies':[],'excluded':[],'input_issues':[]}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            frozen();base.require(utc_now()>=END,'P22_NOT_READY')
            root,record,digest=inputs(source);report['collection_sha256']=digest
            report['freeze_commit']=FREEZE_COMMIT
            if record['status']=='INCOMPLETE':
                report['input_issues'].append({'reason':'COLLECTION_NOT_COMPLETED'})
            for day in DAYS:
                item=next((r for r in record['days'] if r['day']==day),None)
                if item is None or not item['complete']:
                    report['input_issues'].append({'day':day,'reason':'UNVERIFIED_OR_INCOMPLETE_COLLECTION'})
                    base.write_json(out/'results.json',report);continue
                path=base.safe_path(root/'data'/day)
                plan,plan_hash=p20.metadata(path/'plan.json');_,manifest_hash=p20.metadata(path/'manifest.json')
                base.require(manifest_hash==item['manifest_sha256'],'P22_MANIFEST_CHANGED')
                base.require(plan['markets']==list(MARKETS) and minute.utc(plan['start'])==day_start(day)
                    and minute.utc(plan['end_exclusive'])==day_start(day)+pd.Timedelta(days=1),'P22_PERIOD_MISMATCH')
                meta,frames,quarantined=minute.load_partitioned_sample(path,quarantine_gaps=True)
                base.require(minute.utc(meta['started_at'])>=END and meta['network_calls']==item['network_calls'],'P22_DAILY_COLLECTION_MISMATCH')
                report.setdefault('input_hashes',{})[day]={'plan.json':plan_hash,'manifest.json':manifest_hash,
                    **{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in MARKETS}}
                for market in MARKETS:
                    if market not in frames:
                        report['excluded'].append({'day':day,'market':market,'reason':'QUARANTINED_MISSING_CANDLES','quality':quarantined[market]})
                        base.write_json(out/'results.json',report);continue
                    frame,windows=base.prepare(frames[market],60);values=[];common=[]
                    for policy in POLICIES:
                        value,context=run_policy(frame,windows,market,policy);values.append(value);common.append(context)
                        if len(common)==2:base.require(common[0]==common[1],'P22_COMPARISON_WINDOW_MISMATCH')
                        report['completed_policies'].append({'day':day,'market':market,'policy':policy,'result':value,
                            'data_sha256':meta['datasets'][market]['dataset_sha256'],'evaluation_context':context})
                        base.write_json(out/'results.json',report)
                    pair=p21.comparison(day,market,*values)
                    if pair['status']!='NO_TRADES':pair['status']='TIME_SEPARATED_COMPARISON'
                    pair['interpretation']='Short frozen forward window; no profitability proof or automatic adoption'
                    report['cases'].append(pair);base.write_json(out/'results.json',report)
            for day,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in hashes.items():
                    base.require(hashlib.sha256((root/'data'/day/name).read_bytes()).hexdigest()==digest0,'P22_INPUT_CHANGED')
            base.require(p20.metadata(root/'collection.json')[1]==digest,'P22_INPUT_CHANGED')
            base.require(p20.metadata(root/'protocol.json')[1]==PROTOCOL_SHA
                and p20.metadata(root/'registration.json')[1]==REGISTRATION_SHA,'P22_INPUT_CHANGED')
            frozen();base.require(report['runner_sha256']==runner_sha(),'P22_RUNNER_CHANGED')
            base.require(len(report['completed_policies'])<=12,'P22_CASE_LIMIT')
            if not report['input_issues'] and report['cases']:
                report['status']='VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED'
            elif not report['input_issues']:report['status']='INSUFFICIENT_REAL_DATA'
            base.write_json(out/'results.json',report)
            if report['status'] in ('VERIFIED','VERIFIED_PARTIAL'):(out/'INCOMPLETE').unlink()
    except Exception as exc:
        report.update(status='INCOMPLETE',failure_reason=error_code(exc));base.write_json(out/'results.json',report)
    finally:logging.disable(previous);pipe.close()


def write_report(out,report):
    done={(r['day'],r['market'],r['policy']) for r in report.get('completed_policies',[])}
    excluded={(r['day'],r['market']) for r in report['excluded']}
    report['unfinished_policies']=[{'day':d,'market':m,'policy':p} for d in DAYS for m in MARKETS
        if (d,m) not in excluded for p in POLICIES if (d,m,p) not in done]
    base.write_json(out/'results.json',report)
    rows=[];lines=['# P22 frozen time-separated comparison','',report['status'],
        'UTC [2026-11-03, 2026-11-06). Short validation, not profitability proof or global unseen-data attestation.']
    fields=('net_return_pct','fees','mdd_pct','holding_fraction','time_held_pct','mean_capital_exposure_pct',
        'realized_pnl','unrealized_pnl','cash','total_pnl','p9_reconciled')
    for case in report['cases']:
        row={'day':case['day'],'market':case['market'],'status':case['status'],'delta_net_percentage_points':case['delta_net_percentage_points']}
        lines+=['',case['day']+' '+case['market']+' '+case['status'],'|Metric|Legacy|Frozen 44bps|','|---|---:|---:|']
        for field in fields:
            values=[case[k][field] for k in ('baseline','candidate')]
            for key,value in zip(('baseline','candidate'),values):row[key+'_'+field]=value
            lines.append('|'+field+'|'+'|'.join('N/A' if v is None else str(v) for v in values)+'|')
        for field,get in (('buys',lambda r:r['fills']['buys']),('sells',lambda r:r['fills']['sells']),
            ('filter_blocked',lambda r:r['entry_filter']['blocked']),('remaining_quantity',lambda r:r['open_position']['quantity']),
            ('remaining_cost',lambda r:r['open_position']['remaining_cost'])):
            values=[get(case[k]) for k in ('baseline','candidate')]
            for key,value in zip(('baseline','candidate'),values):row[key+'_'+field]=value
            lines.append('|'+field+'|'+'|'.join(map(str,values))+'|')
        rows.append(row)
    lines+=['',f"Completed policies: {len(done)}/12 maximum; quarantined inputs: {len(excluded)}; unfinished: {len(report['unfinished_policies'])}.",
        'NO_TRADES is not improvement. No sum of daily/market returns, no threshold change or automatic adoption.',
        'Frozen registration, hashes, completed policies and input issues: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'comparison.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['day','market','status']);writer.writeheader();writer.writerows(rows)


def analyze(source,output,*,seconds=600,worker=work):
    frozen()
    if utc_now()<END:return None,not_ready()
    base.require(type(seconds) is int and 1<=seconds<=600,'P22_TIME_LIMIT')
    source=base.safe_path(source);out=base.fresh(output);started=time.monotonic()
    ctx=mp.get_context('spawn');reader,writer=ctx.Pipe(duplex=False)
    child=ctx.Process(target=worker,args=(source,out,writer));child.start();writer.close();interrupted=False
    try:
        while child.is_alive() and time.monotonic()-started<seconds:child.join(.1)
    except KeyboardInterrupt:interrupted=True
    finally:
        alive=child.is_alive()
        if alive:child.terminate();child.join(2)
        if child.is_alive():child.kill();child.join(2)
        reader.close()
    file=out/'results.json'
    r=p20.read_results(file) if file.exists() else {'status':'INCOMPLETE','cases':[],'completed_policies':[],'excluded':[]}
    if alive or interrupted or child.exitcode!=0:r['status']='INCOMPLETE';(out/'INCOMPLETE').touch()
    r['elapsed_seconds']=round(time.monotonic()-started,3);write_report(out,r)
    return out,r


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('command',choices=('collect','analyze'))
    parser.add_argument('--output',required=True);parser.add_argument('--input');parser.add_argument('--connect-public',action='store_true')
    a=parser.parse_args(argv)
    if a.command=='analyze' and (not a.input or a.connect_public):parser.error('explicit offline P22 input required')
    if a.command=='collect' and a.input:parser.error('collection does not take input')
    try:
        if a.command=='collect':
            r=collect(a.output,connect_public=a.connect_public)
            print(r['status'],'requests:',r['network_calls'],'not before:',END.isoformat())
        else:
            _,r=analyze(a.input,a.output)
            print(r['status'],'pairs:',len(r['cases']),'policies:',len(r['completed_policies']),'quarantined:',len(r['excluded']))
            for case in r['cases']:
                def fmt(value):return 'N/A' if value is None else f'{p21.decimal(value,signed=True):.4f}'
                print(case['day'],case['market'],case['status'],'net%:',fmt(case['baseline']['net_return_pct']),
                    '→',fmt(case['candidate']['net_return_pct']),'delta pp:',fmt(case['delta_net_percentage_points']))
            print('Frozen short-period comparison; NO_TRADES is not improvement; no adoption.')
        return 0 if r['status'] in ('COLLECTED','PARTIAL_REAL_DATA','OFFLINE_PLAN_ONLY','VERIFIED','VERIFIED_PARTIAL') else 2
    except Exception as exc:print('BLOCKED:',error_code(exc));return 2


if __name__=='__main__':raise SystemExit(main())
