"""P35 common pre-registered evaluation runner: one protocol file + one fixed git commit.

    python -m backtesting.p35_registered_eval status --protocol REPO_RELATIVE_PROTOCOL_JSON
    python -m backtesting.p35_registered_eval analyze --protocol REPO_RELATIVE_PROTOCOL_JSON --input SNAPSHOT --output NEW_PRIVATE_DIR

A protocol (schema P35_EVAL_PROTOCOL_V1) fixes strategy/params, folds, markets, bar unit, costs, cash/sizing,
baselines, primary/secondary comparison, decision rule, `code_commit` and the input snapshot manifest SHA-256.
It registers only through a reviewed, committed anchor (`anchor.json` next to it) whose commit precedes the
protocol's registration deadline on the full remote-master history (the P33 gate method). Code is fixed by
checking `code_commit` out into a temporary `git worktree` and evaluating INSIDE it with an isolated
interpreter, so working-tree edits never reach a registered evaluation and no per-file hash list is needed.

The evaluation reuses reviewed paths only: P32 `strategy_case`, P21 `compact` (P9 reconciliation), P29 BH40
core, P28 exit audit, the P22/P33 daily input verification. No new engine, strategy or data access; network 0.
P22/P33 protocols, registrations, anchors, pinned sources and runners are not modified. No adoption.
"""
import argparse
import csv
import hashlib
import json
import logging
import os
import re
import signal
import subprocess
import sys
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

SCHEMA='P35_EVAL_PROTOCOL_V1'
ANCHOR_SCHEMA='P35_REGISTRATION_ANCHOR_V1'
SNAPSHOT_SCHEMA='P35_INPUT_SNAPSHOT_V1'
RESULT_SCHEMA='P35_ANALYSIS_V1'
CHECKOUT=minute.ROOT
REMOTE_MASTER='refs/remotes/origin/master'
UNITS=(1,15,60)
MAX_FOLDS=64
MAX_CASES_LIMIT=256
# Conditions the reviewed path (P32 strategy_case on legacy_diagnostic.simulate) actually implements. A protocol
# may only register values it can honestly run; anything else is refused instead of being silently ignored.
REVIEWED_PATH={'unit_minutes':1,'costs_bps':{'fee_bps':'5','slippage_bps':'5','spread_bps':'2'},'initial_cash':'1000000',
    'position_fraction':'0.4','warmup_candles':200}
BASELINES=['cash_100','buy_hold_40']
COMPARISON={'primary':'close_marked_return_pct','secondary':'p28_hypothetical_exit_return_pct',
    'equality':'unrounded Decimal; equal is TIE, never a win','no_trades':'never a win; explicit denominators',
    'aggregation':'none'}
DECISION_KIND='both_exceed_fraction_v1'
_CODE=p28._CODE
_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,39}')
BOOT=('import sys,runpy;sys.path.insert(0,sys.argv[1]);del sys.argv[1];'
      "runpy.run_module('backtesting.p35_registered_eval',run_name='__main__',alter_sys=True)")


def utc_now():return pd.Timestamp(datetime.now(timezone.utc))
def normalized(path):return Path(path).read_bytes().replace(b'\r\n',b'\n')
def sha(path):return hashlib.sha256(normalized(path)).hexdigest()
def raw_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------- protocol schema
def strategy(spec):
    """Registry of existing strategies only (no new strategy); unknown ids or params fail closed."""
    base.require(type(spec) is dict and set(spec)=={'id','label','params'},'P35_STRATEGY_NOT_REGISTERED')
    if spec['id']=='consensus' and type(spec['params']) is dict and set(spec['params'])=={'quorum'} \
            and spec['params']['quorum'] in (2,3) and type(spec['params']['quorum']) is int:
        s=ConsensusStrategy(quorum=spec['params']['quorum'])
        base.require(spec['label']==s.name,'P35_STRATEGY_LABEL_MISMATCH');return s
    raise ValueError('P35_STRATEGY_NOT_REGISTERED')


def decision_rule(rule):
    ok=(type(rule) is dict and rule.get('kind')==DECISION_KIND and set(rule)=={'kind','min_valid','min_trade_cases',
        'min_both_exceed_markets','both_exceed_fraction','scope'}
        and all(type(rule[k]) is int and 0<=rule[k]<=MAX_CASES_LIMIT for k in ('min_valid','min_trade_cases','min_both_exceed_markets'))
        and type(rule['both_exceed_fraction']) is str and re.fullmatch(r'(0(\.\d{1,4})?|1(\.0{1,4})?)',rule['both_exceed_fraction'])
        and type(rule['scope']) is str and rule['scope'])
    base.require(ok,'P35_DECISION_RULE_INVALID');return rule


def validate(p):
    """Schema check; returns the protocol. Conditions outside the reviewed path are refused, never ignored."""
    keys={'schema','id','question','strategy','folds','markets','unit_minutes','costs_bps','initial_cash','position_fraction',
        'warmup_candles','baselines','comparison','decision_rule','registration_deadline_exclusive','analysis_not_before_utc',
        'max_cases','analysis_seconds','code_commit','input_manifest_sha256','missing_candles','adopt_strategy'}
    base.require(type(p) is dict and p.get('schema')==SCHEMA and set(p)==keys,'P35_PROTOCOL_INVALID')
    base.require(type(p['id']) is str and _ID.fullmatch(p['id']) and type(p['question']) is str and p['question'],'P35_PROTOCOL_INVALID')
    strategy(p['strategy']);decision_rule(p['decision_rule'])
    folds=p['folds']
    base.require(type(folds) is list and 1<=len(folds)<=MAX_FOLDS and all(type(f) is dict and set(f)=={'id','start','end_exclusive'}
        and type(f['id']) is str and _ID.fullmatch(f['id']) for f in folds) and len({f['id'] for f in folds})==len(folds),'P35_FOLDS_INVALID')
    previous=None
    for f in folds:
        s,e=minute.utc(f['start']),minute.utc(f['end_exclusive'])
        base.require(s<e and (previous is None or previous<=s),'P35_FOLDS_INVALID');previous=e  # Ordered, non-overlapping.
    base.require(type(p['markets']) is list and 1<=len(p['markets'])<=8 and len(set(p['markets']))==len(p['markets'])
        and all(type(m) is str and re.fullmatch(r'KRW-[A-Z0-9]{2,10}',m) for m in p['markets']),'P35_MARKETS_INVALID')
    base.require(p['unit_minutes'] in UNITS and type(p['unit_minutes']) is int,'P35_UNIT_INVALID')
    base.require(p['baselines']==BASELINES and p['comparison']==COMPARISON and p['adopt_strategy'] is False
        and p['missing_candles']=='quarantine the fold/market (PARTIAL); no interpolation, re-collection or extension','P35_PROTOCOL_INVALID')
    base.require(type(p['max_cases']) is int and p['max_cases']==len(folds)*len(p['markets'])<=MAX_CASES_LIMIT,'P35_CASE_LIMIT_INVALID')
    base.require(type(p['analysis_seconds']) is int and 1<=p['analysis_seconds']<=600,'P35_TIME_LIMIT_INVALID')
    base.require(type(p['code_commit']) is str and re.fullmatch(r'[0-9a-f]{40}',p['code_commit']),'P35_CODE_COMMIT_INVALID')
    base.require(type(p['input_manifest_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}',p['input_manifest_sha256']),'P35_INPUT_MANIFEST_INVALID')
    deadline,ready=minute.utc(p['registration_deadline_exclusive']),minute.utc(p['analysis_not_before_utc'])
    base.require(deadline<=ready,'P35_PROTOCOL_INVALID')
    for k,v in REVIEWED_PATH.items():
        base.require(p[k]==v,'P35_UNIT_NOT_SUPPORTED_BY_REVIEWED_PATH' if k=='unit_minutes' else 'P35_CONDITION_NOT_SUPPORTED_BY_REVIEWED_PATH')
    return p


def load_protocol(path):
    try:p=json.loads(normalized(path))
    except (OSError,ValueError):raise ValueError('P35_PROTOCOL_INVALID') from None
    return validate(p)


# ---------------------------------------------------------------- registration gate (P33 method)
def _git(*args,root=None):
    try:return subprocess.run(['git','-C',str(root or CHECKOUT),*args],capture_output=True,text=True,timeout=15)
    except (OSError,subprocess.SubprocessError):raise ValueError('P35_CHECKOUT_UNVERIFIED') from None


def full_history():
    out=_git('rev-parse','--is-shallow-repository')
    # A shallow clone's tip would masquerade as the registration commit; never infer success or MISSED from it.
    base.require(out.returncode==0 and out.stdout.strip()=='false','P35_HISTORY_UNVERIFIED')
    remote=_git('rev-parse','--verify','--quiet',REMOTE_MASTER+'^{commit}')
    base.require(remote.returncode==0 and re.fullmatch(r'[0-9a-f]{40}',remote.stdout.strip()),'P35_HISTORY_UNVERIFIED')
    return remote.stdout.strip()


def on_master(commit):return _git('merge-base','--is-ancestor',commit,REMOTE_MASTER).returncode==0


def committed(path):
    """Tracked, unmodified, committed; returns (last commit SHA, commit time) for this file."""
    rel=str(Path(path).resolve().relative_to(Path(CHECKOUT).resolve()))
    base.require(_git('ls-files','--error-unmatch','--',rel).returncode==0,'P35_REGISTRATION_UNTRACKED')
    status=_git('status','--porcelain','--',rel)
    base.require(status.returncode==0 and status.stdout.strip()=='','P35_REGISTRATION_MODIFIED')
    log=_git('log','-1','--format=%H %cI','--',rel)
    base.require(log.returncode==0 and log.stdout.strip(),'P35_REGISTRATION_UNTRACKED')
    commit,stamp=log.stdout.split();return commit,minute.utc(stamp)


def protocol_path(value):
    path=(Path(CHECKOUT)/value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    base.require(path.is_relative_to(Path(CHECKOUT).resolve()) and path.is_file() and not Path(value).is_symlink(),'P35_PROTOCOL_NOT_IN_CHECKOUT')
    return path


def registration(value):
    """Gate opens only via the committed anchor on remote master before the deadline (P33 gate method)."""
    path=protocol_path(value);p=load_protocol(path);digest=sha(path);now=utc_now()
    deadline=minute.utc(p['registration_deadline_exclusive'])
    anchor_path=path.with_name('anchor.json')
    unregistered={'status':'MISSED_REGISTRATION' if now>=deadline else 'NOT_REGISTERED','protocol_sha256':digest,'protocol':p}
    if not anchor_path.is_file():return unregistered
    try:anchor=json.loads(normalized(anchor_path))
    except ValueError:raise ValueError('P35_REGISTRATION_UNVERIFIED') from None
    base.require(type(anchor) is dict and anchor.get('schema')==ANCHOR_SCHEMA and set(anchor)=={'schema','protocol_sha256','approval_reference'}
        and type(anchor['approval_reference']) is str and anchor['approval_reference'],'P35_REGISTRATION_UNVERIFIED')
    base.require(anchor['protocol_sha256']==digest,'P35_PROTOCOL_NOT_ANCHORED')
    remote=full_history()
    anchor_commit,anchor_time=committed(anchor_path);committed(path)
    base.require(on_master(anchor_commit),'P35_REGISTRATION_NOT_ON_MASTER')
    base.require(anchor_time<deadline,'P35_MISSED_REGISTRATION')
    code=p['code_commit']
    base.require(_git('cat-file','-e',code+'^{commit}').returncode==0,'P35_CODE_COMMIT_UNKNOWN')
    base.require(on_master(code),'P35_CODE_COMMIT_NOT_ON_MASTER')
    code_time=_git('log','-1','--format=%cI',code)
    base.require(code_time.returncode==0 and minute.utc(code_time.stdout.strip())<=anchor_time
        and _git('merge-base','--is-ancestor',code,anchor_commit).returncode==0,'P35_CODE_COMMIT_AFTER_REGISTRATION')
    return {'status':'REGISTERED','protocol_sha256':digest,'protocol':p,'registration_id':sha(anchor_path),
        'approval_reference':anchor['approval_reference'],'anchor_commit':anchor_commit,'remote_master':remote,
        'code_commit':code,'protocol_path':str(path.relative_to(Path(CHECKOUT).resolve()))}


def gate(value):
    reg=registration(value)
    if reg['status']!='REGISTERED':return reg,{'status':reg['status'],'network_calls':0,'files_written':0}
    if utc_now()<minute.utc(reg['protocol']['analysis_not_before_utc']):
        return reg,{'status':'NOT_READY','network_calls':0,'files_written':0,'not_before_utc':reg['protocol']['analysis_not_before_utc']}
    return reg,None


def status(value):
    reg,blocked=gate(value);p=reg['protocol']
    state=blocked['status'] if blocked else 'READY'
    return {'status':state,'registered':reg['status']=='REGISTERED','protocol_sha256':reg['protocol_sha256'],'id':p['id'],
        'code_commit':p['code_commit'],'register_before':p['registration_deadline_exclusive'],'analyze_after':p['analysis_not_before_utc']}


# ---------------------------------------------------------------- input snapshot
def snapshot_record(root,folds):
    """Snapshot manifest for fold directories data/<fold id>/ (P22/P33 daily layout). Helper for collectors/tests."""
    root=base.safe_path(root);items=[]
    for f in folds:
        _,plan=p20.metadata(root/'data'/f['id']/'plan.json');_,manifest=p20.metadata(root/'data'/f['id']/'manifest.json')
        items.append({'id':f['id'],'start':f['start'],'end_exclusive':f['end_exclusive'],'plan_sha256':plan,'manifest_sha256':manifest})
    return {'schema':SNAPSHOT_SCHEMA,'folds':items}


def snapshot(root,p):
    root=base.safe_path(root)
    record,digest=p20.metadata(root/'snapshot.json')
    base.require(digest==p['input_manifest_sha256'],'P35_INPUT_MANIFEST_MISMATCH')
    base.require(type(record) is dict and record.get('schema')==SNAPSHOT_SCHEMA and type(record.get('folds')) is list
        and [(f.get('id'),f.get('start'),f.get('end_exclusive')) for f in record['folds']]==[(f['id'],f['start'],f['end_exclusive']) for f in p['folds']],
        'P35_INPUT_MANIFEST_MISMATCH')
    return root,record,digest


# ---------------------------------------------------------------- evaluation (runs inside the code_commit worktree)
def case(frame,windows,market,folder,p):
    """Reviewed path only: P32 strategy_case, P21 compact (P9), P29 BH40 core, P28 exit (same as P33.case)."""
    r=p32.strategy_case(frame,windows,market,strategy(p['strategy']))
    value=p21.compact(r,r['attribution'],filtered=False)
    value['strategy']=p['strategy']['label'];value['entry_filter']={'enabled':False,'threshold_bps':None,'checks':0,'blocked':0,'allowed':0}
    first=r['execution_first_open']
    bh={'status':'NA_NO_COMMON_EXECUTION_OPEN'} if first is None else p29.buy_hold_40_core(frame,first,market,folder,prefix='P35')
    audit=p28.exit_audit(value,'legacy')
    return value,bh,audit,{k:r[k] for k in ('data_sha256','input_candles','evaluation_first_open','evaluation_last_open','execution_first_open','execution_last_open')}


def outcome(value,bench):
    """Unrounded Decimal comparison; ties are ties, never wins."""
    if value is None or bench is None:return None
    a,b=Decimal(value),Decimal(bench)
    return 'EXCEED' if a>b else 'TIE' if a==b else 'BELOW'


def row(fold,market,value,bh,audit,context):
    trades=value['status']=='TRADES'
    out={'fold':fold,'market':market,'status':value['status'],'result':value,'buy_hold_40':bh,'exit_audit':audit,'window':context,
        'net_return_pct':value['net_return_pct'],'fees':value['fees'],'mdd_pct':value['mdd_pct'],'fills':value['fills'],
        'open_position':value['open_position']}
    for basis,key in (('primary_close','marked_return_pct'),('secondary_exit','hypothetical_exit_return_pct')):
        mine=audit[key] if trades else None
        out[basis]={'return_pct':mine,'vs_cash':outcome(mine,'0') if trades else None,
            'vs_bh40':outcome(mine,bh.get(key)) if trades and bh['status']=='EVALUATED' else None}
    return out


def tally(cases,excluded,scheduled,rule):
    """Pre-registered counts and the bounded decision. Denominators are explicit; NO_TRADES and TIE never win."""
    valid=len(cases);trades=[c for c in cases if c['status']=='TRADES']
    def counts(basis):
        res={}
        for ref in ('vs_cash','vs_bh40'):
            vals=[c[basis][ref] for c in trades];res[ref]={k:sum(v==k for v in vals) for k in ('EXCEED','TIE','BELOW')}
            res[ref]['NA']=sum(v is None for v in vals)
        both=[c for c in trades if c[basis]['vs_cash']=='EXCEED' and c[basis]['vs_bh40']=='EXCEED']
        res['both_exceed']=len(both);res['both_exceed_markets']=sorted({c['market'] for c in both})
        res['denominator_trade_cases']=len(trades);return res
    primary,secondary=counts('primary_close'),counts('secondary_exit');n=len(trades)
    if valid<rule['min_valid'] or n<rule['min_trade_cases']:decision='INSUFFICIENT_SAMPLE_HOLD'
    elif len(primary['both_exceed_markets'])<rule['min_both_exceed_markets']:decision='CONCENTRATED_OR_ABSENT_HOLD'
    elif primary['both_exceed']>=ceil(Decimal(rule['both_exceed_fraction'])*n):decision='FURTHER_VALIDATION_WARRANTED'
    else:decision='NOT_SUPPORTED'
    return {'scheduled':scheduled,'valid':valid,'quarantined':len(excluded),'unfinished':scheduled-valid-len(excluded),
        'trade_cases':n,'no_trade_cases':valid-n,'primary_close':primary,'secondary_exit':secondary,'decision':decision,
        'decision_scope':rule['scope']}


def evaluate(p,source,out,*,protocol_sha256,code_commit,registration_id):
    """Fixed evaluation; writes results.json/cases.csv/REPORT.md into the existing output directory."""
    prior=logging.root.manager.disable
    report={'schema':RESULT_SCHEMA,'status':'INCOMPLETE','id':p['id'],'strategy':p['strategy']['label'],'network_calls':0,
        'protocol_sha256':protocol_sha256,'code_commit':code_commit,'registration_id':registration_id,
        'runner':{'source_root':str(minute.ROOT),'module':str(Path(__file__).resolve())},'cases':[],'excluded':[],
        'claim':'Pre-registered evaluation; not profitability proof; no adoption'}
    base.write_json(out/'results.json',report)
    try:
        logging.disable(logging.CRITICAL)
        with (out/'analysis.log').open('w') as log,redirect_stdout(log),redirect_stderr(log),tempfile.TemporaryDirectory(prefix='p35-') as temp:
            engines=Path(temp).resolve();(engines/'bh').mkdir(mode=0o700)
            root,record,digest=snapshot(source,p);report['input_manifest_sha256']=digest
            for fold,item in zip(p['folds'],record['folds']):
                path=base.safe_path(root/'data'/fold['id'])
                plan,plan_hash=p20.metadata(path/'plan.json');_,manifest_hash=p20.metadata(path/'manifest.json')
                base.require(plan_hash==item['plan_sha256'] and manifest_hash==item['manifest_sha256'],'P35_FOLD_MANIFEST_CHANGED')
                start,end=minute.utc(fold['start']),minute.utc(fold['end_exclusive'])
                base.require(plan['markets']==p['markets'] and minute.utc(plan['start'])==start and minute.utc(plan['end_exclusive'])==end,'P35_PERIOD_MISMATCH')
                meta,frames,quarantined=minute.load_partitioned_sample(path,quarantine_gaps=True)
                base.require(minute.utc(meta['started_at'])>=end,'P35_COLLECTED_BEFORE_FOLD_END')  # Complete folds only.
                report.setdefault('input_hashes',{})[fold['id']]={'plan.json':plan_hash,'manifest.json':manifest_hash,
                    **{m+'-1.csv':meta['datasets'][m]['file_sha256'] for m in p['markets']}}
                for market in p['markets']:
                    if market not in frames:
                        report['excluded'].append({'fold':fold['id'],'market':market,'reason':'QUARANTINED_MISSING_CANDLES','quality':quarantined[market]});continue
                    frame,windows=base.prepare(frames[market],60*p['unit_minutes'])
                    value,bh,audit,context=case(frame,windows,market,engines/'bh'/f"{fold['id']}-{market}",p)
                    report['cases'].append(row(fold['id'],market,value,bh,audit,context));base.write_json(out/'results.json',report)
            for fold,hashes in report.get('input_hashes',{}).items():
                for name,digest0 in hashes.items():
                    base.require(p32.rehash(root/'data'/fold/name,minute.MAX_FILE_BYTES)==digest0,'P35_INPUT_CHANGED')
            base.require(p32.rehash(root/'snapshot.json',minute.MAX_FILE_BYTES)==digest,'P35_INPUT_CHANGED')
            base.require(len(report['cases'])+len(report['excluded'])<=p['max_cases'],'P35_CASE_LIMIT')
            report['tally']=tally(report['cases'],report['excluded'],p['max_cases'],p['decision_rule'])
            report['status']=('VERIFIED_PARTIAL' if report['excluded'] else 'VERIFIED') if report['cases'] else 'INSUFFICIENT_REAL_DATA'
    except Exception as exc:
        code=str(exc);report.update(status='INCOMPLETE',failure_reason=code if _CODE.fullmatch(code) else 'P35_INPUT_OR_EXECUTION_UNVERIFIED')
    finally:logging.disable(prior)
    done={(c['fold'],c['market']) for c in report['cases']}|{(e['fold'],e['market']) for e in report['excluded']}
    report['unfinished']=[{'fold':f['id'],'market':m} for f in p['folds'] for m in p['markets'] if (f['id'],m) not in done]
    base.write_json(out/'results.json',report);write_tables(out,report)
    return report


def write_tables(out,report):
    fields=('net_return_pct','fees','mdd_pct')
    with (out/'cases.csv').open('w',newline='',encoding='utf-8') as fh:
        w=csv.DictWriter(fh,fieldnames=['fold','market','status',*fields,'buys','sells','close_vs_cash','close_vs_bh40','exit_vs_cash','exit_vs_bh40'])
        w.writeheader()
        for c in report.get('cases',[]):
            w.writerow({'fold':c['fold'],'market':c['market'],'status':c['status'],**{k:c[k] for k in fields},'buys':c['fills']['buys'],
                'sells':c['fills']['sells'],'close_vs_cash':c['primary_close']['vs_cash'],'close_vs_bh40':c['primary_close']['vs_bh40'],
                'exit_vs_cash':c['secondary_exit']['vs_cash'],'exit_vs_bh40':c['secondary_exit']['vs_bh40']})
    t=report.get('tally') or {}
    (out/'REPORT.md').write_text('\n'.join([f"# P35 registered evaluation {report.get('id')}",'',report['status'],'',
        f"Decision (bounded): {t.get('decision','N/A')}. Never adoption or live trading.",
        f"Scheduled {t.get('scheduled','N/A')}; valid {t.get('valid',0)}; quarantined {len(report.get('excluded',[]))}; unfinished {len(report.get('unfinished',[]))}.",
        f"code_commit {report.get('code_commit')}; protocol {report.get('protocol_sha256')}.",
        'Full values: results.json; per case: cases.csv. Returns are never summed.'])+'\n',encoding='utf-8')


def inner(argv):
    """Entry point executed by the isolated interpreter INSIDE the code_commit worktree."""
    a=argparse.ArgumentParser();[a.add_argument(k,required=True) for k in ('--protocol','--input','--output','--code-commit','--protocol-sha256','--registration-id')]
    a=a.parse_args(argv);out=Path(a.output)
    try:
        head=_git('rev-parse','HEAD',root=minute.ROOT);dirty=_git('status','--porcelain','--untracked-files=no',root=minute.ROOT)
        base.require(head.returncode==0 and head.stdout.strip()==a.code_commit and dirty.returncode==0 and dirty.stdout.strip()=='','P35_WORKTREE_NOT_AT_CODE_COMMIT')
        base.require(Path(__file__).resolve().is_relative_to(minute.ROOT.resolve()),'P35_WORKTREE_NOT_AT_CODE_COMMIT')
        base.require(sha(a.protocol)==a.protocol_sha256,'P35_PROTOCOL_CHANGED');p=load_protocol(a.protocol)
        base.require(p['code_commit']==a.code_commit,'P35_CODE_COMMIT_MISMATCH')
        report=evaluate(p,a.input,out,protocol_sha256=a.protocol_sha256,code_commit=a.code_commit,registration_id=a.registration_id)
    except Exception as exc:
        code=str(exc);report={'schema':RESULT_SCHEMA,'status':'INCOMPLETE','network_calls':0,'cases':[],'excluded':[],
            'failure_reason':code if _CODE.fullmatch(code) else 'P35_INPUT_OR_EXECUTION_UNVERIFIED'}
        base.write_json(out/'results.json',report)
    return 0 if report['status'] in ('VERIFIED','VERIFIED_PARTIAL') else 2


# ---------------------------------------------------------------- outer runner (current checkout)
def run_isolated(command,cwd,seconds):
    """Run in its own process group; on timeout or interrupt kill the whole group (no retry)."""
    env={k:v for k,v in os.environ.items() if not k.startswith('PYTHON')};env['PYTHON_DOTENV_DISABLED']='1'
    process=subprocess.Popen(command,cwd=cwd,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:return process.wait(timeout=seconds)
    except BaseException:
        try:os.killpg(process.pid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError):pass
        process.wait();return None


def worktree(code,parent):
    tree=Path(parent)/'code'
    out=_git('worktree','add','--detach','--quiet',str(tree),code)
    base.require(out.returncode==0,'P35_WORKTREE_UNAVAILABLE')
    head=_git('rev-parse','HEAD',root=tree)
    base.require(head.returncode==0 and head.stdout.strip()==code,'P35_WORKTREE_NOT_AT_CODE_COMMIT')
    return tree


def remove_worktree(tree):
    _git('worktree','remove','--force',str(tree));_git('worktree','prune')


def analyze(value,source,output,*,python=sys.executable):
    reg,blocked=gate(value)
    if blocked:return blocked
    p=reg['protocol'];p32.require_outside(source,output)
    snapshot(source,p)  # Cheap pre-check before any worktree; the frozen code re-checks everything itself.
    out=base.fresh(output);(out/'protocol.json').write_bytes(normalized(protocol_path(value)))
    parent=Path(tempfile.mkdtemp(prefix='p35-worktree-')).resolve();tree=None;started=time.monotonic()
    try:
        tree=worktree(reg['code_commit'],parent)
        code=run_isolated([python,'-I','-c',BOOT,str(tree),'_inner','--protocol',str(out/'protocol.json'),'--input',str(base.safe_path(source)),
            '--output',str(out),'--code-commit',reg['code_commit'],'--protocol-sha256',reg['protocol_sha256'],'--registration-id',reg['registration_id']],
            tree,p['analysis_seconds'])
    finally:
        if tree is not None:remove_worktree(tree)
        try:os.rmdir(parent)
        except OSError:pass
    try:report,_=p20.metadata(out/'results.json')
    except Exception:report={'status':'INCOMPLETE','cases':[],'excluded':[],'failure_reason':'P35_RESULT_MISSING'}
    if code is None:report.update(status='INCOMPLETE',failure_reason='P35_TIME_LIMIT')
    elif report.get('status') in ('VERIFIED','VERIFIED_PARTIAL'):
        bound=(report.get('protocol_sha256'),report.get('code_commit'),report.get('registration_id'))==(reg['protocol_sha256'],reg['code_commit'],reg['registration_id'])
        again=registration(value)
        if not bound:report.update(status='INCOMPLETE',failure_reason='P35_RESULT_BINDING_MISMATCH')
        elif again.get('registration_id')!=reg['registration_id']:report.update(status='INCOMPLETE',failure_reason='P35_REGISTRATION_CHANGED')
        elif p32.rehash(Path(base.safe_path(source))/'snapshot.json',minute.MAX_FILE_BYTES)!=p['input_manifest_sha256']:
            report.update(status='INCOMPLETE',failure_reason='P35_INPUT_CHANGED')
    report['elapsed_seconds']=round(time.monotonic()-started,3);report['exit_code']=code
    base.write_json(out/'results.json',report)
    if report['status'] in ('VERIFIED','VERIFIED_PARTIAL'):(out/'INCOMPLETE').unlink()
    return report


def summary(report):
    t=report.get('tally') or {}
    code=0 if report['status'] in ('VERIFIED','VERIFIED_PARTIAL') else 2
    lines=[f"P35 {report['status']} exit={code}"+(f" reason={report['failure_reason']}" if 'failure_reason' in report else '')]
    if t:
        lines.append(f"scheduled={t['scheduled']} valid={t['valid']} quarantined={t['quarantined']} unfinished={t['unfinished']} trades={t['trade_cases']} no_trades={t['no_trade_cases']}")
        for name,c in (('close(primary)',t['primary_close']),('exit(secondary)',t['secondary_exit'])):
            lines.append(f"{name} of {c['denominator_trade_cases']} trade cases: cash E/T/B={c['vs_cash']['EXCEED']}/{c['vs_cash']['TIE']}/{c['vs_cash']['BELOW']} "
                f"BH40 E/T/B/NA={c['vs_bh40']['EXCEED']}/{c['vs_bh40']['TIE']}/{c['vs_bh40']['BELOW']}/{c['vs_bh40']['NA']} both={c['both_exceed']}")
        lines.append(f"decision={t['decision']} (bounded; no adoption)")
    lines.append('NO_TRADES and TIE never win; returns are never summed; not profitability proof.')
    return lines[:8],code


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv[:1]==['_inner']:return inner(argv[1:])
    parser=argparse.ArgumentParser(description='P35 common pre-registered evaluation runner')
    parser.add_argument('command',choices=('status','analyze'));parser.add_argument('--protocol',required=True)
    parser.add_argument('--input');parser.add_argument('--output');a=parser.parse_args(argv)
    if a.command=='status' and (a.input or a.output):parser.error('status takes --protocol only')
    if a.command=='analyze' and not (a.input and a.output):parser.error('analyze needs --input and --output')
    try:
        if a.command=='status':
            s=status(a.protocol);code=0 if s['status'] in ('NOT_REGISTERED','NOT_READY','READY') else 2
            print(f"P35 {s['status']} exit={code} id={s['id']} registered={s['registered']}")
            print(f"protocol_sha256={s['protocol_sha256']} code_commit={s['code_commit']}")
            print(f"register_before={s['register_before']} analyze_after={s['analyze_after']}; no network or files.")
            return code
        report=analyze(a.protocol,a.input,a.output)
    except Exception as exc:
        print(f"P35 BLOCKED exit=2 reason={str(exc) if _CODE.fullmatch(str(exc)) else 'P35_UNVERIFIED'}");return 2
    lines,code=summary(report)
    for line in lines:print(line)
    return code


if __name__=='__main__':raise SystemExit(main())
