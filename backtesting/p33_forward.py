"""P33 PROPOSED forward evaluation of the existing consensus_2of3 (not registered; review first).

    python -m backtesting.p33_forward status
    python -m backtesting.p33_forward collect --output NEW_PRIVATE_COLLECTION --connect-public
    python -m backtesting.p33_forward analyze --input COLLECTION --output NEW_PRIVATE_ANALYSIS

The proposal is backtesting/p33_protocol.json. It counts as a forward evaluation only if, BEFORE
2026-11-08T00:00Z, it is reviewed/approved and a registration receipt backtesting/p33_registration.json
records its SHA and a GitHub-observable commit. Without that, collect/analyze are blocked forever for
this window (no silent date move, no retroactive registration). Collection is never allowed before
2026-11-15T00:00Z. One strategy only (existing ConsensusStrategy(2) via the reviewed P32 strategy_case,
P17/P9, P28 exit, P29 BH40 core); no Legacy/44bps/parameter runs. No operational adoption.
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
from contextlib import redirect_stdout,redirect_stderr
from datetime import datetime,timezone
from decimal import Decimal
from math import ceil
from pathlib import Path

import pandas as pd

from . import benchmark_audit as p29
from . import minute_study as minute
from . import p19_baseline as base
from . import p20_attribution as p20
from . import p21_entry_filter as p21
from . import p32_consensus_cost_comparison as p32
from . import terminal_exit_audit as p28
from strategies.signals import ConsensusStrategy

PROTOCOL=Path(__file__).with_name('p33_protocol.json')
REGISTRATION=Path(__file__).with_name('p33_registration.json')
ANCHOR=Path(__file__).with_name('p33_registration_anchor.json')
CHECKOUT=minute.ROOT
DAYS=tuple((pd.Timestamp('2026-11-08')+pd.Timedelta(days=i)).strftime('%Y-%m-%d') for i in range(7))
MARKETS=('KRW-BTC','KRW-XRP')
START=pd.Timestamp('2026-11-08T00:00:00Z')
END=pd.Timestamp('2026-11-15T00:00:00Z')
POLICY='consensus_2of3'
MAX_CALLS_PER_DAY=40
MAX_CALLS_TOTAL=280
MAX_CASES=14


def utc_now():return pd.Timestamp(datetime.now(timezone.utc))
def normalized(path):return Path(path).read_bytes().replace(b'\r\n',b'\n')
def sha(path):return hashlib.sha256(normalized(path)).hexdigest()
def day_start(day):return pd.Timestamp(day+'T00:00:00Z')


def protocol():
    """The fixed proposal and its pinned evaluation sources (content pins survive squash merge)."""
    p=json.loads(normalized(PROTOCOL))
    base.require(p.get('schema')=='P33_FORWARD_PROTOCOL_V1' and p['days']==list(DAYS) and p['markets']==list(MARKETS)
        and minute.utc(p['start'])==START and minute.utc(p['end_exclusive'])==END
        and minute.utc(p['registration_deadline_exclusive'])==START and minute.utc(p['collection_not_before_utc'])==END
        and p['strategy']=={'name':POLICY,'class':'strategies.signals.ConsensusStrategy','quorum':2}
        and p['costs_bps']=={'fee_bps':'5','slippage_bps':'5','spread_bps':'2'} and p['initial_cash']=='1000000'
        and p['position_fraction']=='0.4' and p['warmup_candles']==200 and p['max_cases']==MAX_CASES
        and p['collection_per_day_requests']==MAX_CALLS_PER_DAY and p['collection_total_requests']==MAX_CALLS_TOTAL
        and p['analysis_seconds']==600,'P33_PROTOCOL_CONDITIONS_CHANGED')
    for name,digest in p['code_sha256'].items():
        base.require(sha(minute.ROOT/name)==digest,'P33_FROZEN_CODE_CHANGED')
    base.require(minute.MAX_CALLS==MAX_CALLS_PER_DAY and minute.DEADLINE_SECONDS==120,'P33_COLLECTION_LIMIT_CHANGED')
    return p


def _git(*args):
    try:out=subprocess.run(['git','-C',str(CHECKOUT),*args],capture_output=True,text=True,timeout=15)
    except (OSError,subprocess.SubprocessError):raise ValueError('P33_CHECKOUT_UNVERIFIED') from None
    return out


def committed(path):
    """Tracked, unmodified and committed in this checkout; returns the last commit time touching it."""
    rel=str(Path(path).resolve().relative_to(Path(CHECKOUT).resolve()))
    shallow=_git('rev-parse','--is-shallow-repository')
    # A shallow clone's tip commit would masquerade as the anchor commit; never infer MISSED from it.
    base.require(shallow.returncode==0 and shallow.stdout.strip()=='false','P33_HISTORY_UNVERIFIED')
    base.require(_git('ls-files','--error-unmatch','--',rel).returncode==0,'P33_REGISTRATION_UNTRACKED')
    status=_git('status','--porcelain','--',rel)
    base.require(status.returncode==0 and status.stdout.strip()=='','P33_REGISTRATION_MODIFIED')
    log=_git('log','-1','--format=%cI','--',rel)
    base.require(log.returncode==0 and log.stdout.strip(),'P33_REGISTRATION_UNTRACKED')
    return minute.utc(log.stdout.strip())


def registration():
    """Gate opens ONLY via the reviewed anchor: a receipt file alone can never register.

    The anchor (separately reviewed registration PR, merged before START) pins the receipt digest and
    approval reference. Receipt and anchor must be tracked and unmodified; the anchor's last commit and
    the receipt's observed time must both precede START, and the observed time cannot be in the future.
    """
    p=protocol();now=utc_now();missed='MISSED_REGISTRATION' if now>=START else 'NOT_REGISTERED'
    anchor=json.loads(normalized(ANCHOR))
    base.require(anchor.get('schema')=='P33_REGISTRATION_ANCHOR_V1','P33_REGISTRATION_UNVERIFIED')
    if anchor.get('receipt_sha256') is None:
        return {'status':missed,'protocol_sha256':sha(PROTOCOL),'receipt_present_but_unanchored':REGISTRATION.exists()}
    base.require(re.fullmatch(r'[0-9a-f]{64}',str(anchor['receipt_sha256'])) and type(anchor.get('approval_reference')) is str
        and anchor['approval_reference'],'P33_REGISTRATION_UNVERIFIED')
    base.require(REGISTRATION.is_file() and sha(REGISTRATION)==anchor['receipt_sha256'],'P33_RECEIPT_NOT_ANCHORED')
    base.require(committed(ANCHOR)<START,'P33_MISSED_REGISTRATION')
    committed(REGISTRATION)
    r=json.loads(normalized(REGISTRATION))
    base.require(r.get('schema')=='P33_REGISTRATION_V1' and r.get('protocol_sha256')==sha(PROTOCOL)
        and r.get('approved') is True and r.get('approval_reference')==anchor['approval_reference']
        and re.fullmatch(r'[0-9a-f]{40}',str(r.get('protocol_commit'))) and r.get('observed_remote_head')==r.get('protocol_commit')
        and r.get('remote_repository')=='sasumpi123/auto_trade','P33_REGISTRATION_UNVERIFIED')
    observed=minute.utc(r['observed_at_utc'])
    base.require(observed<START,'P33_MISSED_REGISTRATION');base.require(observed<=now,'P33_REGISTRATION_FROM_FUTURE')
    return {'status':'REGISTERED','protocol_sha256':r['protocol_sha256'],'protocol_commit':r['protocol_commit'],
        'registration_id':anchor['receipt_sha256'],'protocol':p}


def not_ready(reg):return {'status':'NOT_READY','registration':reg['status'],'network_calls':0,'files_written':0,
    'not_before_utc':END.isoformat(),'cases':[],'excluded':[]}


def gate():
    reg=registration()
    if reg['status']!='REGISTERED':return reg,{'status':reg['status'],'network_calls':0,'files_written':0,'cases':[],'excluded':[]}
    if utc_now()<END:return reg,not_ready(reg)
    return reg,None


def collect(output,*,connect_public=False,clock=time.time,client_factory=None):
    reg,blocked=gate()
    if blocked:return blocked
    if not connect_public:return {'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'days':DAYS,'markets':MARKETS}
    out=base.fresh(output);(out/'data').mkdir(mode=0o700)
    (out/'protocol.json').write_bytes(normalized(PROTOCOL))
    record={'schema':'P33_COLLECTION_V1','status':'INCOMPLETE','protocol_sha256':reg['protocol_sha256'],'registration_id':reg['registration_id'],
        'started_at':pd.Timestamp(clock(),unit='s',tz='UTC').isoformat(),'days':[],'network_calls':0}
    base.write_json(out/'collection.json',record)
    try:
        for day in DAYS:
            base.require(pd.Timestamp(clock(),unit='s',tz='UTC')>=END,'P33_CLOCK_BEFORE_COLLECTION_WINDOW')
            start=day_start(day);path=out/'data'/day
            result=minute.collect(path,list(MARKETS),start.isoformat(),(start+pd.Timedelta(days=1)).isoformat(),
                connect_public=True,clock=clock,client=client_factory(day) if client_factory else None)
            calls=result['network_calls']
            base.require(type(calls) is int and 0<=calls<=MAX_CALLS_PER_DAY and record['network_calls']+calls<=MAX_CALLS_TOTAL,'P33_COLLECTION_LIMIT')
            manifest,digest=p20.metadata(path/'manifest.json')
            record['days'].append({'day':day,'status':result['status'],'complete':manifest.get('complete') is True,
                'network_calls':calls,'manifest_sha256':digest})
            record['network_calls']+=calls;base.write_json(out/'collection.json',record)
        complete=all(d['complete'] for d in record['days'])
        record['status']='COLLECTED' if complete and all(d['status']=='COLLECTED_DEVELOPMENT_SAMPLE' for d in record['days']) else 'PARTIAL_REAL_DATA' if complete else 'INCOMPLETE'
        if complete:(out/'INCOMPLETE').unlink()
    except Exception as exc:
        code=str(exc);record.update(status='INCOMPLETE',failure_reason=code if p28._CODE.fullmatch(code) else 'P33_COLLECTION_UNVERIFIED')
    record['finished_at']=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat();base.write_json(out/'collection.json',record)
    return record


def inputs(source,reg):
    root=base.safe_path(source)
    _,proto=p20.metadata(root/'protocol.json');base.require(proto==reg['protocol_sha256'],'P33_INPUT_PROTOCOL_MISMATCH')
    record,digest=p20.metadata(root/'collection.json')
    base.require(record.get('schema')=='P33_COLLECTION_V1' and record.get('protocol_sha256')==reg['protocol_sha256']
        and record.get('registration_id')==reg['registration_id'],'P33_INPUT_REGISTRATION_MISMATCH')  # Never reinterpret under another approval.
    base.require(END<=minute.utc(record['started_at'])<=minute.utc(record['finished_at'])<=utc_now(),'P33_COLLECTION_TIME_UNVERIFIED')
    days=record['days']
    base.require(type(days) is list and [d['day'] for d in days]==list(DAYS) and record['status'] in ('COLLECTED','PARTIAL_REAL_DATA')
        and all(d['complete'] is True and type(d['network_calls']) is int and 0<=d['network_calls']<=MAX_CALLS_PER_DAY for d in days)
        and record['network_calls']==sum(d['network_calls'] for d in days)<=MAX_CALLS_TOTAL,'P33_COLLECTION_REGISTRY_MISMATCH')
    return root,record,digest


def case(frame,windows,market,folder):
    """Reviewed P32 path only: ConsensusStrategy(2) through strategy_case, P21 compact (P9), P28 exit, P29 BH40 core."""
    r=p32.strategy_case(frame,windows,market,ConsensusStrategy(quorum=2))
    value=p21.compact(r,r['attribution'],filtered=False)
    value['strategy']=POLICY;value['entry_filter']={'enabled':False,'threshold_bps':None,'checks':0,'blocked':0,'allowed':0}
    first=r['execution_first_open']
    bh={'status':'NA_NO_COMMON_EXECUTION_OPEN'} if first is None else p29.buy_hold_40_core(frame,first,market,folder,prefix='P33')
    audit=p28.exit_audit(value,'legacy')
    return value,bh,audit,{k:r[k] for k in ('data_sha256','input_candles','evaluation_first_open','evaluation_last_open','execution_first_open','execution_last_open')}


def outcome(value,bench):
    """Unrounded Decimal comparison; ties are ties, never wins."""
    if value is None or bench is None:return None
    a,b=Decimal(value),Decimal(bench)
    return 'EXCEED' if a>b else 'TIE' if a==b else 'BELOW'


def row(day,market,value,bh,audit,context):
    trades=value['status']=='TRADES'
    out={'day':day,'market':market,'status':value['status'],'result':value,'buy_hold_40':bh,'exit_audit':audit,'window':context,
        'net_return_pct':value['net_return_pct'],'fees':value['fees'],'mdd_pct':value['mdd_pct'],'fills':value['fills'],
        'open_position':value['open_position']}
    for basis,key,bkey in (('primary_close','marked_return_pct','marked_return_pct'),('secondary_exit','hypothetical_exit_return_pct','hypothetical_exit_return_pct')):
        mine=audit[key] if trades else None
        out[basis]={'return_pct':mine,'vs_cash':outcome(mine,'0') if trades else None,
            'vs_bh40':outcome(mine,bh.get(bkey)) if trades and bh['status']=='EVALUATED' else None}
    return out


def tally(cases,excluded,scheduled=MAX_CASES):
    """Pre-declared counts and the bounded decision (further validation or not; never adoption)."""
    valid=len(cases);trades=[c for c in cases if c['status']=='TRADES']
    def counts(basis):
        res={}
        for ref in ('vs_cash','vs_bh40'):
            vals=[c[basis][ref] for c in trades];res[ref]={k:sum(v==k for v in vals) for k in ('EXCEED','TIE','BELOW')}
            res[ref]['NA']=sum(v is None for v in vals)
        res['both_exceed']=sum(c[basis]['vs_cash']=='EXCEED' and c[basis]['vs_bh40']=='EXCEED' for c in trades)
        res['both_exceed_markets']=sorted({c['market'] for c in trades if c[basis]['vs_cash']=='EXCEED' and c[basis]['vs_bh40']=='EXCEED'})
        res['denominator_trade_cases']=len(trades);return res
    primary,secondary=counts('primary_close'),counts('secondary_exit')
    n=len(trades)
    if valid<10 or n<6:decision='INSUFFICIENT_SAMPLE_HOLD'
    elif len(primary['both_exceed_markets'])<2:decision='CONCENTRATED_OR_ABSENT_HOLD'
    elif primary['both_exceed']>=ceil(0.6*n):decision='FURTHER_VALIDATION_WARRANTED'
    else:decision='NOT_SUPPORTED'
    return {'scheduled':scheduled,'valid':valid,'quarantined':len(excluded),'unfinished':scheduled-valid-len(excluded),
        'trade_cases':n,'no_trade_cases':valid-n,'primary_close':primary,'secondary_exit':secondary,'decision':decision,
        'decision_scope':'Whether further pre-registered validation is warranted; never PAPER default or live trading'}


def work(source,out,pipe):
    prior=logging.root.manager.disable
    report={'schema':'P33_ANALYSIS_V1','status':'INCOMPLETE','policy':POLICY,'network_calls':0,'cases':[],'excluded':[],
        'input_issues':[],'claim':'Forward window only if registered before start; not profitability proof; no adoption'}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log),tempfile.TemporaryDirectory(prefix='p33-') as temp:
            reg,blocked=gate();base.require(blocked is None,'P33_'+(blocked['status'] if blocked else 'OK'))
            engines=Path(temp).resolve();(engines/'bh').mkdir(mode=0o700)
            root,record,digest=inputs(source,reg);report.update(collection_sha256=digest,protocol_sha256=reg['protocol_sha256'],protocol_commit=reg['protocol_commit'],registration_id=reg['registration_id'])
            for item in record['days']:
                day=item['day'];path=base.safe_path(root/'data'/day)
                plan,plan_hash=p20.metadata(path/'plan.json');_,manifest_hash=p20.metadata(path/'manifest.json')
                base.require(manifest_hash==item['manifest_sha256'],'P33_MANIFEST_CHANGED')
                base.require(plan['markets']==list(MARKETS) and minute.utc(plan['start'])==day_start(day)
                    and minute.utc(plan['end_exclusive'])==day_start(day)+pd.Timedelta(days=1),'P33_PERIOD_MISMATCH')
                meta,frames,quarantined=minute.load_partitioned_sample(path,quarantine_gaps=True)
                base.require(minute.utc(meta['started_at'])>=END and meta['network_calls']==item['network_calls'],'P33_DAILY_COLLECTION_MISMATCH')
                report.setdefault('input_hashes',{})[day]={'plan.json':plan_hash,'manifest.json':manifest_hash,
                    **{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in MARKETS}}
                for market in MARKETS:
                    if market not in frames:
                        report['excluded'].append({'day':day,'market':market,'reason':'QUARANTINED_MISSING_CANDLES','quality':quarantined[market]});continue
                    frame,windows=base.prepare(frames[market],60)
                    value,bh,audit,context=case(frame,windows,market,engines/'bh'/f'{day}-{market}')
                    report['cases'].append(row(day,market,value,bh,audit,context));base.write_json(out/'results.json',report)
            for day,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in hashes.items():
                    base.require(p32.rehash(root/'data'/day/name,minute.MAX_FILE_BYTES)==digest0,'P33_INPUT_CHANGED')
            base.require(p32.rehash(root/'collection.json',minute.MAX_FILE_BYTES)==digest,'P33_INPUT_CHANGED')
            base.require(len(report['cases'])<=MAX_CASES,'P33_CASE_LIMIT')
            base.require(registration().get('registration_id')==reg['registration_id'],'P33_REGISTRATION_CHANGED')
            report['tally']=tally(report['cases'],report['excluded'])
            report['status']=('VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED') if report['cases'] else 'INSUFFICIENT_REAL_DATA'
            base.write_json(out/'results.json',report)
            if report['status'] in ('VERIFIED','VERIFIED_PARTIAL'):(out/'INCOMPLETE').unlink()
    except Exception as exc:
        code=str(exc);report.update(status='INCOMPLETE',failure_reason=code if p28._CODE.fullmatch(code) else 'P33_INPUT_OR_EXECUTION_UNVERIFIED')
        base.write_json(out/'results.json',report)
    finally:logging.disable(prior);pipe.close()


def write_report(out,report):
    done={(c['day'],c['market']) for c in report.get('cases',[])};skipped={(e['day'],e['market']) for e in report.get('excluded',[])}
    report['unfinished']=[{'day':d,'market':m} for d in DAYS for m in MARKETS if (d,m) not in done|skipped]  # P33 universe, not P19.
    base.write_json(out/'results.json',report)
    fields=('net_return_pct','fees','mdd_pct')
    with (out/'cases.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=['day','market','status',*fields,'buys','sells','close_vs_cash','close_vs_bh40','exit_vs_cash','exit_vs_bh40'])
        w.writeheader()
        for c in report.get('cases',[]):
            w.writerow({'day':c['day'],'market':c['market'],'status':c['status'],**{k:c[k] for k in fields},'buys':c['fills']['buys'],'sells':c['fills']['sells'],
                'close_vs_cash':c['primary_close']['vs_cash'],'close_vs_bh40':c['primary_close']['vs_bh40'],
                'exit_vs_cash':c['secondary_exit']['vs_cash'],'exit_vs_bh40':c['secondary_exit']['vs_bh40']})
    t=report.get('tally') or {}
    (out/'REPORT.md').write_text('\n'.join(['# P33 consensus_2of3 forward evaluation','',report['status'],'',
        f"Decision (bounded): {t.get('decision','N/A')}. Never adoption or live trading.",
        f"Scheduled {t.get('scheduled',MAX_CASES)}; valid {t.get('valid',0)}; quarantined {t.get('quarantined',len(skipped))}; unfinished {len(report['unfinished'])}.",
        'Full values: results.json; per case: cases.csv. Returns are never summed.'])+'\n',encoding='utf-8')


def analyze(source,output,*,seconds=600,worker=work):
    reg,blocked=gate()
    if blocked:return None,blocked
    p32.require_outside(source,output)
    return p20.analyze(source,output,seconds=seconds,worker=worker,report_writer=write_report)


def summary(report):
    t=report.get('tally') or {};p=t.get('primary_close') or {};s=t.get('secondary_exit') or {}
    code=0 if report['status'] in ('VERIFIED','VERIFIED_PARTIAL','OFFLINE_PLAN_ONLY','COLLECTED','PARTIAL_REAL_DATA','PROPOSED_NOT_REGISTERED','REGISTERED') else 2
    lines=[f"P33 {report['status']} exit={code}"+(f" reason={report['failure_reason']}" if 'failure_reason' in report else '')
        +(f" not_before={report['not_before_utc']}" if 'not_before_utc' in report else '')]
    if t:
        lines.append(f"scheduled={t['scheduled']} valid={t['valid']} quarantined={t['quarantined']} unfinished={t['unfinished']} trades={t['trade_cases']} no_trades={t['no_trade_cases']}")
        for name,c in (('close(primary)',p),('exit(secondary)',s)):
            lines.append(f"{name} of {c['denominator_trade_cases']} trade cases: cash E/T/B={c['vs_cash']['EXCEED']}/{c['vs_cash']['TIE']}/{c['vs_cash']['BELOW']} "
                f"BH40 E/T/B/NA={c['vs_bh40']['EXCEED']}/{c['vs_bh40']['TIE']}/{c['vs_bh40']['BELOW']}/{c['vs_bh40']['NA']} both={c['both_exceed']}")
        lines.append(f"decision={t['decision']} (further validation only; no adoption)")
    lines.append('NO_TRADES never counts as a win; returns are never summed; not profitability proof.')
    return lines[:8],code


def status():
    p=protocol();reg=registration()
    state='REGISTERED' if reg['status']=='REGISTERED' else 'PROPOSED_NOT_REGISTERED' if reg['status']=='NOT_REGISTERED' else reg['status']
    return {'status':state,'protocol_sha256':reg['protocol_sha256'],'window':f"[{START.isoformat()},{END.isoformat()})",
        'registration_deadline_exclusive':p['registration_deadline_exclusive'],'collection_not_before_utc':p['collection_not_before_utc']}


def main(argv=None):
    parser=argparse.ArgumentParser(description='P33 proposed consensus_2of3 forward evaluation')
    parser.add_argument('command',choices=('status','collect','analyze'))
    parser.add_argument('--output');parser.add_argument('--input');parser.add_argument('--connect-public',action='store_true')
    a=parser.parse_args(argv)
    if a.command=='status' and (a.output or a.input or a.connect_public):parser.error('status takes no arguments')
    if a.command=='collect' and (a.input or not a.output):parser.error('collect needs --output only')
    if a.command=='analyze' and (not a.input or not a.output or a.connect_public):parser.error('analyze needs --input and --output')
    try:
        if a.command=='status':
            s=status();print(f"P33 {s['status']} protocol_sha256={s['protocol_sha256']}")
            print(f"window={s['window']} register_before={s['registration_deadline_exclusive']} collect_after={s['collection_not_before_utc']}")
            print('Registered via the reviewed anchor; collect only after the window ends; no network or files.' if s['status']=='REGISTERED'
                else 'Proposal only until reviewed/approved and anchored before the start; no network or files.')
            return 0 if s['status'] in ('PROPOSED_NOT_REGISTERED','REGISTERED') else 2
        r=collect(a.output,connect_public=a.connect_public) if a.command=='collect' else analyze(a.input,a.output)[1]
    except Exception as exc:
        print(f"P33 BLOCKED exit=2 reason={str(exc) if p28._CODE.fullmatch(str(exc)) else 'P33_UNVERIFIED'}");return 2
    lines,code=summary(r)
    for line in lines:print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
