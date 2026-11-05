"""P30 supplemental audit of a saved, frozen P22 result: hypothetical exit + cash 100% / BH40.

Run from the repository root:
    python -m backtesting.forward_result_audit --p22-root P22_COLLECTION --results P22_RESULTS_JSON --output NEW_PRIVATE_DIRECTORY

POST_REGISTRATION_SUPPLEMENT_DESCRIPTIVE_ONLY: these metrics were added after the P22 period began.
They are not part of the pre-registered P22 protocol, never change its status or completion verdict,
and are not a retroactive success criterion. Read-only post-processing: the frozen P22 engine,
frozen(), collect/analyze and the strategy are never executed here; the unchanged p22_forward
input validator and pinned constants are reused on a bounded private snapshot. The only execution
is one disposable research P17 BUY per pair for the new BH40 supplement.
Before 2026-11-06T00:00:00Z it returns NOT_READY without reading inputs or writing anything.
"""
import argparse
import csv
import hashlib
import io
import json
import logging
import platform
import tempfile
import time
from contextlib import redirect_stdout,redirect_stderr
from decimal import Decimal
from pathlib import Path

import pandas as pd

from . import benchmark_audit as p29
from . import minute_study as minute
from . import p19_baseline as base
from . import p20_attribution as p20
from . import p21_entry_filter as p21
from . import p22_forward as p22
from . import terminal_exit_audit as p28
from .legacy_diagnostic import validate_candles,dataset_hash
from trading.execution import number

SCHEMA='P30_P22_SUPPLEMENT_AUDIT_V1'
SUPPLEMENT='POST_REGISTRATION_SUPPLEMENT_DESCRIPTIVE_ONLY'
TOOL_VERSION=1
MAX_SECONDS=60
P22_STATUSES=('VERIFIED','VERIFIED_PARTIAL')
PAIR_STATUSES=('TIME_SEPARATED_COMPARISON','NO_TRADES')
P22_INTERPRETATION='Short frozen forward window; no profitability proof or automatic adoption'
CONTEXT_KEYS={'data_sha256','input_candles','evaluation_first_open','evaluation_last_open','execution_first_open',
    'execution_last_open','conditions'}
INTERPRETATION=('POST_REGISTRATION_SUPPLEMENT_DESCRIPTIVE_ONLY: added after the frozen P22 period began; not a '
    'pre-registered metric or success criterion and never changes the original P22 status. Short time-separated '
    'sample; not profitability, statistical or risk-adjusted superiority. Cash 100% is a defined 0% baseline. '
    'BH40 is a NEW supplemental calculation (P22 stores none) at the P22 evaluation_context execution_first_open. '
    'Hypothetical exits are model values, not fills or liquidity lower bounds. No aggregation, ranking or adoption.')


def utc_now():return p22.utc_now()


def not_ready():
    return {'schema':SCHEMA,'status':'NOT_READY','supplement':SUPPLEMENT,'network_calls':0,'files_read':0,
        'files_written':0,'not_before_utc':p22.END.isoformat(),'cases':[],'excluded':[]}


class Deadline(p28.Deadline):
    def __init__(self,seconds=MAX_SECONDS,clock=time.monotonic):
        base.require(type(seconds) in (int,float) and 0<seconds<=MAX_SECONDS,'TIME_LIMIT')
        self.clock=clock;self.end=clock()+seconds
    def check(self):base.require(self.clock()<self.end,'P30_TIME_LIMIT')


def pins():
    """Static content pins only (no frozen(): it fails closed on the registered P25 drift by design)."""
    base.require(p22.sha(p22.PROTOCOL)==p22.PROTOCOL_SHA and p22.sha(p22.REGISTRATION)==p22.REGISTRATION_SHA,'P30_P22_PINS_CHANGED')
    return p22.runner_sha()


def snapshot(source,snap,deadline):
    """Path-checked bounded copy of the collection; the unchanged p22.inputs then validates the copy."""
    root=base.safe_path(source);base.require(root.is_dir(),'P29_INPUT_UNREADABLE')
    hashes={}
    for name in ('protocol.json','registration.json','collection.json'):
        hashes[name]=p29._copy(root/name,snap/name,minute.MAX_FILE_BYTES)
    base.require(not (root/'INCOMPLETE').exists(),'P30_COLLECTION_INCOMPLETE')
    _,record,digest=p22.inputs(snap);deadline.check()
    base.require(record['status'] in ('COLLECTED','PARTIAL_REAL_DATA'),'P30_COLLECTION_NOT_COMPLETED')
    for item in record['days']:
        day=item['day'];daily=root/'data'/day
        base.require(daily.is_dir() and not (daily/'INCOMPLETE').exists(),'P30_DAY_INCOMPLETE')
        for name in ('plan.json','manifest.json'):
            hashes[f'data/{day}/{name}']=p29._copy(daily/name,snap/'data'/day/name,minute.MAX_FILE_BYTES)
        manifest=json.loads((snap/'data'/day/'manifest.json').read_bytes())
        datasets=manifest.get('datasets') if type(manifest) is dict else None
        base.require(type(datasets) is dict and set(datasets)==set(p22.MARKETS),'P30_MANIFEST_UNVERIFIED')
        for market in p22.MARKETS:
            name=datasets[market].get('file') if type(datasets[market]) is dict else None
            base.require(name==market+'-1.csv','P30_MANIFEST_UNVERIFIED')
            hashes[f'data/{day}/{name}']=p29._copy(daily/name,snap/'data'/day/name,minute.MAX_FILE_BYTES)
        deadline.check()
    return root,record,digest,hashes


def check_structure(report,runner,collection_digest):
    """Original P22 result contract; nothing here is relaxed or re-interpreted."""
    base.require(report.get('schema')=='P22_ANALYSIS_V1','P30_SCHEMA_MISMATCH')
    base.require(report.get('status') in P22_STATUSES and 'failure_reason' not in report,'P30_P22_NOT_VERIFIED')
    base.require(report.get('protocol_sha256')==p22.PROTOCOL_SHA and report.get('registration_sha256')==p22.REGISTRATION_SHA
        and report.get('runner_sha256')==runner and report.get('freeze_commit')==p22.FREEZE_COMMIT,'P30_P22_REGISTRATION_MISMATCH')
    base.require(report.get('collection_sha256')==collection_digest,'P30_COLLECTION_LINK_MISMATCH')
    base.require(report.get('validation')=='TIME_SEPARATED_NOT_PROFITABILITY_PROOF' and report.get('maximum_cases')==12,'P30_SCHEMA_MISMATCH')
    base.require(report.get('input_issues')==[] and report.get('unfinished_policies',[])==[],'P30_P22_UNFINISHED')
    cases,done,excluded=report.get('cases'),report.get('completed_policies'),report.get('excluded')
    base.require(all(type(x) is list for x in (cases,done,excluded)),'P30_REPORT_FORMAT')
    base.require(len(cases)<=6 and len(done)<=12 and len(excluded)<=6,'P30_CASE_LIMIT')
    universe={(d,m) for d in p22.DAYS for m in p22.MARKETS};skipped=[];pairs=[]
    for e in excluded:
        base.require(type(e) is dict and set(e)=={'day','market','reason','quality'} and (e.get('day'),e.get('market')) in universe
            and e['reason']=='QUARANTINED_MISSING_CANDLES','P30_EXCLUDED_FORMAT')
        skipped.append((e['day'],e['market']))
    for row in cases:
        base.require(type(row) is dict and (row.get('day'),row.get('market')) in universe,'P30_REPORT_FORMAT')
        pairs.append((row['day'],row['market']))
    base.require(len(set(skipped))==len(skipped) and len(set(pairs))==len(pairs),'P30_DUPLICATE_CASE')
    base.require(not set(skipped)&set(pairs) and set(skipped)|set(pairs)==universe,'P30_CASE_COVERAGE_MISMATCH')
    base.require(bool(skipped)==(report['status']=='VERIFIED_PARTIAL') and pairs,'P30_STATUS_MISMATCH')
    keys=[]
    for r in done:
        base.require(type(r) is dict and set(r)=={'day','market','policy','result','data_sha256','evaluation_context'}
            and r['policy'] in p21.POLICIES and type(r['evaluation_context']) is dict
            and set(r['evaluation_context'])==CONTEXT_KEYS,'P30_POLICY_FORMAT')
        keys.append((r['day'],r['market'],r['policy']))
    base.require(len(set(keys))==len(keys),'P30_DUPLICATE_POLICY')
    base.require(set(keys)=={(d,m,p) for d,m in pairs for p in p21.POLICIES},'P30_POLICY_COVERAGE_MISMATCH')
    stored={(r['day'],r['market'],r['policy']):r for r in done};out={}
    for row in cases:
        a,b=(stored[(row['day'],row['market'],p)] for p in p21.POLICIES)
        base.require(a['result']==row.get('baseline') and b['result']==row.get('candidate'),'P30_PAIR_POLICY_CONFLICT')
        base.require(a['evaluation_context']==b['evaluation_context'] and a['data_sha256']==b['data_sha256'],'P30_CONTEXT_MISMATCH')
        expected=p21.comparison(row['day'],row['market'],a['result'],b['result'])
        if expected['status']!='NO_TRADES':expected['status']='TIME_SEPARATED_COMPARISON'
        expected['interpretation']=P22_INTERPRETATION
        base.require(set(row)==set(expected) and all(row[k]==expected[k] for k in ('status','delta_net_percentage_points','interpretation')),'P30_PAIR_MISMATCH')
        audits=(p28.exit_audit(a['result'],p21.POLICIES[0]),p28.exit_audit(b['result'],p21.POLICIES[1]))
        out[(row['day'],row['market'])]={'row':row,'context':a['evaluation_context'],'data_sha256':a['data_sha256'],'audits':audits}
    return out,{(e['day'],e['market']):e for e in excluded}


def evaluate(snap,record,report,pairs,excluded,deadline,work):
    """Exact p22.work() input checks on the snapshot, then the supplement per verified pair."""
    hashes=report.get('input_hashes');base.require(type(hashes) is dict and set(hashes)==set(p22.DAYS),'P30_INPUT_HASH_MISMATCH')
    out=[]
    for item in record['days']:
        day=item['day'];daily=base.safe_path(snap/'data'/day)
        plan,plan_hash=p20.metadata(daily/'plan.json');_,manifest_hash=p20.metadata(daily/'manifest.json')
        base.require(manifest_hash==item['manifest_sha256'],'P22_MANIFEST_CHANGED')
        base.require(plan['markets']==list(p22.MARKETS) and minute.utc(plan['start'])==p22.day_start(day)
            and minute.utc(plan['end_exclusive'])==p22.day_start(day)+pd.Timedelta(days=1),'P22_PERIOD_MISMATCH')
        meta,frames,quarantined=minute.load_partitioned_sample(daily,quarantine_gaps=True)
        base.require(minute.utc(meta['started_at'])>=p22.END and meta['network_calls']==item['network_calls'],'P22_DAILY_COLLECTION_MISMATCH')
        expected={'plan.json':plan_hash,'manifest.json':manifest_hash,**{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in p22.MARKETS}}
        base.require(hashes[day]==expected,'P30_INPUT_HASH_MISMATCH')
        for market in p22.MARKETS:
            deadline.check();key=(day,market)
            if market not in frames:
                base.require(key in excluded and excluded[key]['quality']==quarantined[market],'P30_EXCLUDED_MISMATCH');continue
            base.require(key in pairs,'P30_PAIR_SET_MISMATCH')
            pair=pairs[key];ctx=pair['context']
            base.require(pair['data_sha256']==meta['datasets'][market]['dataset_sha256'],'P30_WINDOW_MISMATCH')
            frame=validate_candles(frames[market],60)
            base.require(dataset_hash(frame)==ctx['data_sha256'] and len(frame)==ctx['input_candles'],'P30_WINDOW_MISMATCH')
            end=(frame.index[-1]+pd.Timedelta(seconds=60)).isoformat();mark=number(frame.close.iloc[-1])
            for name in ('baseline','candidate'):
                pos=pair['row'][name]['open_position']
                base.require(p28.num(pos['mark_price'],positive=True)==mark and pos['mark_at_utc']==end,'P30_MARK_MISMATCH')
            first=ctx['execution_first_open']
            bh=({'status':'NA_NO_COMMON_EXECUTION_OPEN'} if first is None else
                p29.buy_hold_40_core(frame,first,market,work/f'{day}-{market}',prefix='P30',
                    extra={'source':'NEW_SUPPLEMENTAL_CALCULATION_P22_STORES_NO_BH40'}))
            rows={p21.POLICIES[0]:p29.compare(pair['audits'][0],pair['row']['baseline'],bh),
                  p21.POLICIES[1]:p29.compare(pair['audits'][1],pair['row']['candidate'],bh)}
            out.append({'day':day,'market':market,'original_p22_status':pair['row']['status'],
                'original_delta_net_percentage_points':pair['row']['delta_net_percentage_points'],
                'window':{k:ctx[k] for k in ('execution_first_open','execution_last_open','evaluation_first_open','evaluation_last_open')}
                    |{'mark_at_utc':end,'mark_price':str(mark)},
                'cash_100':{'status':'DEFINED_BASELINE','marked_return_pct':'0','hypothetical_exit_return_pct':'0',
                    'fills':{'buys':0,'sells':0},'interest':'0','fee':'0'},
                'buy_hold_40':bh,'policies':rows,'flags':p29.flags(rows,bh),
                'exit_audit':{p21.POLICIES[0]:pair['audits'][0],p21.POLICIES[1]:pair['audits'][1]}})
    base.require({(c['day'],c['market']) for c in out}==set(pairs),'P30_PAIR_SET_MISMATCH')
    return out


def audit(p22_root,results,output,*,seconds=MAX_SECONDS,clock=time.monotonic):
    if utc_now()<p22.END:return None,not_ready()  # No path check, read, output or wait.
    deadline=Deadline(seconds,clock);runner=pins();out=base.fresh(output)
    report={'schema':SCHEMA,'status':'INCOMPLETE','supplement':SUPPLEMENT,'network_calls':0,'cases':[],'excluded':[],
        'original_p22':None,'retroactive_success_criterion':False,
        'tool':{'module':'backtesting.forward_result_audit','version':TOOL_VERSION,'git_commit':p28._git_commit(),
            'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'p29_source_sha256':hashlib.sha256(Path(p29.__file__).read_bytes()).hexdigest(),
            'p28_source_sha256':hashlib.sha256(Path(p28.__file__).read_bytes()).hexdigest(),
            'p22_runner_sha256':runner,'p22_frozen_verifier_executed':False},
        'environment':{'python':platform.python_version(),'pandas':pd.__version__,'system':platform.system()},
        'model':{'initial_cash':str(p28.INITIAL_CASH),'costs':p28.P21_COSTS,'cash_100':'0% by definition',
            'buy_hold_40':'NEW supplemental P17 research BUY of fee-inclusive 400,000 at P22 evaluation_context '
                'execution_first_open; formula-checked; marked at last close','hypothetical_exit':'terminal_exit_audit.exit_values',
            'tolerance':'services.notification_metrics.close'},
        'interpretation':INTERPRETATION}
    prior=logging.root.manager.disable
    try:
        base.write_json(out/'results.json',report)
        results_path,source,results_digest,size=p28.load(results);deadline.check()
        with tempfile.TemporaryDirectory(prefix='p30-') as temp:
            temp=Path(temp).resolve();snap=temp/'p22';work=temp/'engines';snap.mkdir(mode=0o700);work.mkdir(mode=0o700)
            logging.disable(logging.CRITICAL)
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                root,record,digest,hashes=snapshot(p22_root,snap,deadline)
                pairs,excluded=check_structure(source,runner,digest);deadline.check()
                cases=evaluate(snap,record,source,pairs,excluded,deadline,work)
        report['input']={'p22_results':{'schema':source['schema'],'status':source['status'],'sha256':results_digest,'bytes':size},
            'p22_collection':{'status':record['status'],'collection_sha256':digest,'files_sha256':hashes},
            'self_reported_flags_not_exchange_provenance':True}
        report['original_p22']={'status':source['status'],'pairs':len(source['cases']),'completed_policies':len(source['completed_policies']),
            'excluded':len(source['excluded']),'unfinished_policies':0,'input_issues':0,'unchanged':True}
        deadline.check();p29.unchanged(root,hashes)
        try:again=p28.read_bounded(results_path)
        except ValueError:raise ValueError('P30_INPUT_CHANGED') from None
        base.require(hashlib.sha256(again).hexdigest()==results_digest,'P30_INPUT_CHANGED')
        report.update(cases=cases,excluded=[dict(e) for e in excluded.values()],
            status='SUPPLEMENT_AUDITED_PARTIAL' if source['status']=='VERIFIED_PARTIAL' else 'SUPPLEMENT_AUDITED')
        write_outputs(out,report);deadline.check()
        (out/'INCOMPLETE').unlink()
    except (Exception,KeyboardInterrupt) as exc:
        code='P30_INTERRUPTED' if isinstance(exc,KeyboardInterrupt) else str(exc)
        if code=='P29_INPUT_CHANGED':code='P30_INPUT_CHANGED'
        report.update(status='INCOMPLETE',cases=[],failure_reason=code if p28._CODE.fullmatch(code) else 'P30_INPUT_UNVERIFIED')
        for name in ('REPORT.md','supplement.csv'):(out/name).unlink(missing_ok=True)
        base.write_json(out/'results.json',report);(out/'INCOMPLETE').touch()
    finally:logging.disable(prior)
    return out,report


def write_outputs(out,report):
    base.write_json(out/'results.json',report)
    def show(v):return 'N/A' if v is None else str(v)
    o=report['original_p22']
    lines=['# P30 supplemental audit of a frozen P22 result','',report['status'],'',
        f"Original P22 (unchanged): {o['status']}; pairs {o['pairs']}; completed policies {o['completed_policies']}; excluded {o['excluded']}.",
        '',SUPPLEMENT+' — '+INTERPRETATION]
    records=[]
    for c in report['cases']:
        bh=c['buy_hold_40']
        lines+=['',f"{c['day']} {c['market']} — original {c['original_p22_status']}",
            '|Policy|close %|exit %|close vs cash pp|close vs BH40 pp|exit vs cash pp|exit vs BH40 pp|buys/sells|','|---|---:|---:|---:|---:|---:|---:|---:|']
        for name,r in c['policies'].items():
            records.append({'day':c['day'],'market':c['market'],'policy':name,**{k:r[k] for k in p29.FIELDS},
                'buys':r['fills']['buys'],'sells':r['fills']['sells'],'supplement':SUPPLEMENT})
            lines.append('|'+name+'|'+'|'.join(show(r[k]) for k in p29.FIELDS)+f"|{r['fills']['buys']}/{r['fills']['sells']}|")
        for name,ret,exit_ret,fills in (('cash_100','0','0',(0,0)),
                ('buy_hold_40',bh.get('marked_return_pct'),bh.get('hypothetical_exit_return_pct'),(1,0) if bh['status']=='EVALUATED' else (0,0))):
            records.append({'day':c['day'],'market':c['market'],'policy':name,'marked_return_pct':ret,'exit_return_pct':exit_ret,
                **{k:None for k in p29.FIELDS[2:]},'buys':fills[0],'sells':fills[1],'supplement':SUPPLEMENT})
            lines.append(f'|{name}|{show(ret)}|{show(exit_ret)}|—|—|—|—|{fills[0]}/{fills[1]}|')
    lines+=['',f"Supplement pairs: {len(report['cases'])}; original quarantined inputs preserved: {len(report['excluded'])}.",
        'Full Decimal values, hashes, windows and environment: results.json.']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    with (out/'supplement.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=['day','market','policy',*p29.FIELDS,'buys','sells','supplement']);w.writeheader();w.writerows(records)


def summary(report,code):
    def pct(v):return 'N/A' if v is None else f'{Decimal(v):.2f}'
    o=report.get('original_p22') or {}
    head=f"P30 {report['status']} original={o.get('status','N/A')} pairs={len(report.get('cases',[]))} excluded={len(report.get('excluded',[]))} exit={code}"
    if 'failure_reason' in report:head+=f" reason={report['failure_reason']}"
    if report['status']=='NOT_READY':head+=f" not_before={p22.END.isoformat()} files_read=0"
    lines=[head]
    for c in report.get('cases',[])[:6]:
        a,b=c['policies'][p21.POLICIES[0]],c['policies'][p21.POLICIES[1]];bh=c['buy_hold_40']
        lines.append(f"{c['day']} {c['market']} close% L/44/BH={pct(a['marked_return_pct'])}/{pct(b['marked_return_pct'])}/{pct(bh.get('marked_return_pct'))} "
            f"exit% L/44/BH={pct(a['exit_return_pct'])}/{pct(b['exit_return_pct'])}/{pct(bh.get('hypothetical_exit_return_pct'))} cash=0")
    lines.append('Post-registration descriptive supplement; original P22 verdict unchanged; no profitability proof.')
    return lines[:8]


def main(argv=None):
    parser=argparse.ArgumentParser(description='P30 post-registration supplement for one frozen P22 result')
    parser.add_argument('--p22-root',required=True);parser.add_argument('--results',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:_,report=audit(args.p22_root,args.results,args.output)
    except Exception as exc:
        code=str(exc) if p28._CODE.fullmatch(str(exc)) else 'P30_OUTPUT_UNVERIFIED'
        report={'status':'INCOMPLETE','failure_reason':code,'cases':[],'excluded':[]}
    code=0 if report['status'] in ('SUPPLEMENT_AUDITED','SUPPLEMENT_AUDITED_PARTIAL') else 2
    for line in summary(report,code):print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
