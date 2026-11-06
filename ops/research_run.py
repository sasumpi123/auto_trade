"""P31 single manual research command: P22 collect -> isolated frozen analyze -> P30 supplement.

Run from the repository root:
    python -m ops.research_run p22 --directory NEW_PRIVATE_ROOT --connect-public

A convenience layer over already reviewed tools; no new strategy, metric or observer.
- Before 2026-11-06T00:00:00Z (09:00 KST): NOT_READY, exit 2, no path access, output, network or child.
- Without --connect-public: OFFLINE_PLAN_ONLY (plan and limits only), nothing created or started.
- With it: Safety + isolation preflight, then exactly once each, in this order, with fixed argv:
    1. python tools/p22_isolated.py collect --connect-public --output ROOT/collection
    2. python tools/p22_isolated.py analyze --input ROOT/collection --output ROOT/analysis
    3. python -m backtesting.forward_result_audit --p22-root ROOT/collection
           --results ROOT/analysis/results.json --output ROOT/supplement
       (only when the frozen analysis is VERIFIED/VERIFIED_PARTIAL; otherwise SKIPPED_WITH_REASON)
No retry, resume, re-collection, new date, extension, schedule, daemon, model, Slack or launchd.
run.json is a private research run record, not a PAPER ledger or approval/recovery evidence.
"""
import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

from backtesting import p19_baseline as base
from backtesting import p22_forward as p22
from backtesting import terminal_exit_audit as p28

ROOT=Path(__file__).resolve().parents[1]
SCHEMA='P31_RESEARCH_RUN_V1'
ISOLATED='tools/p22_isolated.py'
SUPPLEMENT_MODULE='backtesting.forward_result_audit'
SOURCES=('ops/research_run.py',ISOLATED,'backtesting/p22_forward.py','backtesting/forward_result_audit.py',
    'backtesting/benchmark_audit.py','backtesting/terminal_exit_audit.py')
# Existing limits: collection 3 days x 120 s (+ isolated-tree build), analysis 600 s (+ its own cleanup),
# supplement 60 s. These are outer kill limits for the direct child process group only.
STEP_SECONDS={'collect':3*120+60,'analyze':600+60,'supplement':60+30}
TOTAL_SECONDS=sum(STEP_SECONDS.values())
MAX_LOG_BYTES=4*1024*1024
MAX_RECORD_BYTES=16*1024*1024


def now():return p22.utc_now()


def sha(rel):return hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()


def plan():
    return {'schema':SCHEMA,'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'children_started':0,
        'days':list(p22.DAYS),'markets':list(p22.MARKETS),'not_before_utc':p22.END.isoformat(),
        'steps':['collect (isolated, once, <=40 requests/day, <=120 total)','analyze (isolated frozen P22, once, <=600 s)',
            'supplement (P30, once, only if VERIFIED/VERIFIED_PARTIAL, <=60 s)'],'outer_limit_seconds':TOTAL_SECONDS}


def not_ready():
    return {'schema':SCHEMA,'status':'NOT_READY','network_calls':0,'files_written':0,'children_started':0,
        'not_before_utc':p22.END.isoformat()}


def preflight():
    """Existing Safety check and isolation readiness, read-only (no tree build, no child)."""
    from tools import check_safety,p22_isolated as iso
    issues,_=check_safety.check_repository(ROOT)
    base.require(not issues,'P31_SAFETY_FAILED')
    drift=set(iso.working_tree_drift(ROOT))
    base.require(drift<=set(iso.PINNED_COPIES),'P31_UNEXPECTED_PINNED_DRIFT')
    pinned=iso.pinned_hashes(ROOT)
    for name,pin in iso.PINNED_COPIES.items():
        base.require(iso.sha(ROOT/pin)==pinned[name],'P31_PINNED_COPY_UNVERIFIED')
    base.require(iso.sha(ROOT/iso.RUNNER)==iso.RUNNER_SHA and iso.sha(ROOT/iso.REGISTRATION)==iso.REGISTRATION_SHA,'P31_P22_PINS_CHANGED')
    return sorted(drift)


def argv_for(step,root):
    """Fixed interpreter, script/module and list arguments only; never a shell string."""
    if step=='collect':return [sys.executable,str(ROOT/ISOLATED),'collect','--connect-public','--output',str(root/'collection')]
    if step=='analyze':return [sys.executable,str(ROOT/ISOLATED),'analyze','--input',str(root/'collection'),'--output',str(root/'analysis')]
    if step=='supplement':return [sys.executable,'-m',SUPPLEMENT_MODULE,'--p22-root',str(root/'collection'),
        '--results',str(root/'analysis'/'results.json'),'--output',str(root/'supplement')]
    raise ValueError('P31_UNKNOWN_STEP')


def child_env():
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','PYTHONSTARTUP')}
    env['PYTHON_DOTENV_DISABLED']='1'
    return env


TERM_GRACE_SECONDS=10
KILL_REAP_SECONDS=5


def _leader_exited(pid):
    """Non-reaping check: the exited leader stays a zombie, so its PGID cannot be reused meanwhile."""
    try:return os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT) is not None
    except ChildProcessError:return True


def stop_group(child):
    """Bounded cleanup of the ONE process group this process created (never another group).

    SIGTERM to the group, wait up to TERM_GRACE_SECONDS for the leader without reaping it, then
    ALWAYS SIGKILL the same group (members that ignore SIGTERM or outlive the leader), and only then
    reap the leader. Leader exit and group cleanup are recorded separately.
    """
    pgid=child.pid;record={'pgid_is_own_session':True,'sigterm_sent':False,'sigkill_sent':False,'leader_exit':None}
    try:previous=signal.signal(signal.SIGINT,signal.SIG_IGN)  # Finish the bounded cleanup on a 2nd Ctrl-C.
    except ValueError:previous=None
    try:
        try:os.killpg(pgid,signal.SIGTERM);record['sigterm_sent']=True
        except ProcessLookupError:pass
        deadline=time.monotonic()+TERM_GRACE_SECONDS
        while not _leader_exited(pgid) and time.monotonic()<deadline:time.sleep(0.1)
        record['leader_exited_after_term']=_leader_exited(pgid)
        try:os.killpg(pgid,signal.SIGKILL);record['sigkill_sent']=True
        except ProcessLookupError:pass
        try:record['leader_exit']=child.wait(timeout=KILL_REAP_SECONDS)
        except subprocess.TimeoutExpired:record['leader_exit']=None
    finally:
        if previous is not None:signal.signal(signal.SIGINT,previous)
    return record


def spawn(argv,log,timeout):
    """Run ONE direct child in its own process group; on timeout/Ctrl-C clean up that whole group only."""
    cleanup=None
    with open(log,'wb') as out:
        child=subprocess.Popen(argv,cwd=ROOT,env=child_env(),stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            code=child.wait(timeout=timeout);outcome='EXITED'
        except subprocess.TimeoutExpired:outcome='TIMEOUT'
        except KeyboardInterrupt:outcome='INTERRUPTED'
        if outcome!='EXITED':
            cleanup=stop_group(child);code=cleanup['leader_exit']
    size=Path(log).stat().st_size
    if size>MAX_LOG_BYTES:  # Keep the bounded tail; never echo it.
        with open(log,'rb') as fh:fh.seek(size-MAX_LOG_BYTES);tail=fh.read()
        Path(log).write_bytes(b'[truncated]\n'+tail)
    return outcome,code,cleanup


def read_json(path):
    """Bounded read of a child's result; None when missing or not a JSON object."""
    try:
        raw=p28.read_bounded(base.safe_path(path),MAX_RECORD_BYTES)
        value=json.loads(raw.decode('utf-8'),parse_constant=lambda name:None)
        return (value if type(value) is dict else None),hashlib.sha256(raw).hexdigest()
    except (OSError,ValueError,UnicodeDecodeError):return None,None


def known(value,allowed):return value if value in allowed else 'UNKNOWN'


def describe(step,root):
    if step=='collect':
        r,h=read_json(root/'collection'/'collection.json')
        if r is None:return {'result':None}
        calls=r.get('network_calls')
        return {'result':known(r.get('status'),('COLLECTED','PARTIAL_REAL_DATA','INCOMPLETE')),'sha256':h,
            'requests':calls if type(calls) is int else None}
    if step=='analyze':
        r,h=read_json(root/'analysis'/'results.json')
        if r is None:return {'result':None}
        def n(key):return len(r[key]) if type(r.get(key)) is list else None
        return {'result':known(r.get('status'),('VERIFIED','VERIFIED_PARTIAL','INSUFFICIENT_REAL_DATA','INCOMPLETE')),'sha256':h,
            'pairs':n('cases'),'completed_policies':n('completed_policies'),'excluded':n('excluded'),
            'unfinished':n('unfinished_policies'),'input_issues':n('input_issues')}
    r,h=read_json(root/'supplement'/'results.json')
    if r is None:return {'result':None}
    return {'result':known(r.get('status'),('SUPPLEMENT_AUDITED','SUPPLEMENT_AUDITED_PARTIAL','INCOMPLETE','NOT_READY')),'sha256':h,
        'pairs':len(r['cases']) if type(r.get('cases')) is list else None}


def run(directory,*,connect_public=False,executor=None,clock=time.monotonic):
    """executor(step, argv, log, timeout) -> (outcome, exit_code[, cleanup]); tests inject a fake, the CLI never can."""
    if now()<p22.END:return None,not_ready()
    if not connect_public:return None,plan()
    executor=executor or (lambda step,argv,log,timeout:spawn(argv,log,timeout))  # -> (outcome, code, cleanup)
    drift=preflight()
    previous=os.umask(0o077)
    try:
        root=base.fresh(directory);(root/'logs').mkdir(mode=0o700)
        started=clock()
        record={'schema':SCHEMA,'status':'RUNNING','kind':'RESEARCH_RUN_RECORD_NOT_PAPER_LEDGER_OR_APPROVAL','network_calls_by_tool':None,
            'python':platform.python_version(),'started_utc':now().isoformat(),'registered_drift_replaced_in_isolation':drift,
            'sources_sha256':{rel:sha(rel) for rel in SOURCES},'outer_limit_seconds':TOTAL_SECONDS,'steps':[]}
        def save():base.write_json(root/'run.json',record)
        save()
        def step(name,reason=None):
            entry={'step':name,'argv':argv_for(name,root)}
            if reason:
                entry.update(status='SKIPPED_WITH_REASON',reason=reason);record['steps'].append(entry);save();return entry
            elapsed=clock()-started;remaining=TOTAL_SECONDS-elapsed
            if remaining<=0:
                entry.update(status='SKIPPED_WITH_REASON',reason='P31_TOTAL_BUDGET_EXCEEDED');record['steps'].append(entry);save();return entry
            log=root/'logs'/f'{name}.log';t0=clock();entry['status']='RUNNING';record['steps'].append(entry);save()
            try:result=executor(name,entry['argv'],log,min(STEP_SECONDS[name],remaining))
            except KeyboardInterrupt:result=('INTERRUPTED',None)  # spawn already stopped its own group.
            outcome,code=result[0],result[1]
            if len(result)>2 and result[2] is not None:entry['cleanup']=result[2]
            entry.update(status=outcome,exit_code=code,elapsed_seconds=round(clock()-t0,3),**describe(name,root))
            save();return entry
        collect=step('collect')
        if collect['status']=='INTERRUPTED':return finish(root,record,'INTERRUPTED')
        has_collection=(root/'collection'/'collection.json').is_file()
        analyze=step('analyze',None if has_collection else 'P31_NO_COLLECTION_TO_ANALYZE')
        if analyze['status']=='INTERRUPTED':return finish(root,record,'INTERRUPTED')
        verified=analyze['status']=='EXITED' and analyze.get('exit_code')==0 and analyze.get('result') in ('VERIFIED','VERIFIED_PARTIAL')
        supplement=step('supplement',None if verified else 'P31_ANALYSIS_NOT_VERIFIED')
        if supplement['status']=='INTERRUPTED':return finish(root,record,'INTERRUPTED')
        ok_collect=collect['status']=='EXITED' and collect.get('exit_code')==0 and collect.get('result') in ('COLLECTED','PARTIAL_REAL_DATA')
        ok_supp=supplement['status']=='EXITED' and supplement.get('exit_code')==0 and supplement.get('result') in ('SUPPLEMENT_AUDITED','SUPPLEMENT_AUDITED_PARTIAL')
        if any(s['status']=='TIMEOUT' for s in record['steps']):status='TIMEOUT'
        elif any(s.get('reason')=='P31_TOTAL_BUDGET_EXCEEDED' for s in record['steps']):status='BUDGET_EXCEEDED'
        elif ok_collect and verified and ok_supp:
            full=collect['result']=='COLLECTED' and analyze['result']=='VERIFIED' and supplement['result']=='SUPPLEMENT_AUDITED'
            status='COMPLETED' if full else 'COMPLETED_PARTIAL_DATA'
        else:status='FAILED'
        return finish(root,record,status)
    finally:os.umask(previous)


def finish(root,record,status):
    record.update(status=status,finished_utc=now().isoformat());base.write_json(root/'run.json',record)
    if status in ('COMPLETED','COMPLETED_PARTIAL_DATA'):(root/'INCOMPLETE').unlink(missing_ok=True)
    return root,record


def code_of(record):return 0 if record['status'] in ('COMPLETED','COMPLETED_PARTIAL_DATA','OFFLINE_PLAN_ONLY') else 2


def summary(record,root=None):
    code=code_of(record);status=record['status']
    if status in ('NOT_READY','OFFLINE_PLAN_ONLY'):
        return [f"P31 {status} exit={code} not_before={record['not_before_utc']} network=0 files=0 children=0",
            'Plan: isolated collect once -> isolated frozen analyze once -> P30 supplement once if verified.',
            'Manual one-shot only; no retry, schedule, model, Slack, launchd or trading.']
    steps={s['step']:s for s in record['steps']}
    def c(name):
        s=steps.get(name,{});return f"{name}={s.get('status','N/A')}/{s.get('exit_code','-')}/{s.get('result') or s.get('reason','-')}"
    a=steps.get('analyze',{})
    lines=[f"P31 {status} exit={code} {c('collect')} {c('analyze')} {c('supplement')} pairs={a.get('pairs','N/A')} "
        f"excluded={a.get('excluded','N/A')} unfinished={a.get('unfinished','N/A')} requests={steps.get('collect',{}).get('requests','N/A')}"]
    def pct(v):
        try:return f'{Decimal(v):.2f}'
        except Exception:return 'N/A'
    supp=orig=None
    if root is not None and steps.get('supplement',{}).get('result') in ('SUPPLEMENT_AUDITED','SUPPLEMENT_AUDITED_PARTIAL'):
        supp,_=read_json(root/'supplement'/'results.json')
    elif root is not None and a.get('result') in ('VERIFIED','VERIFIED_PARTIAL'):
        orig,_=read_json(root/'analysis'/'results.json')
    try:
        for x in ((supp or {}).get('cases') or [])[:6]:
            L,F=x['policies']['legacy'],x['policies']['entry_bandwidth_44bps'];bh=x['buy_hold_40']
            lines.append(f"{x['day']} {x['market']} close% L/44/BH={pct(L['marked_return_pct'])}/{pct(F['marked_return_pct'])}/{pct(bh.get('marked_return_pct'))} "
                f"exit% L/44/BH={pct(L['exit_return_pct'])}/{pct(F['exit_return_pct'])}/{pct(bh.get('hypothetical_exit_return_pct'))}")
        for x in ((orig or {}).get('cases') or [])[:6]:
            lines.append(f"{x['day']} {x['market']} {x['status']} original net% L->44={pct(x['baseline']['net_return_pct'])}->"
                f"{pct(x['candidate']['net_return_pct'])} (no supplement)")
    except (KeyError,TypeError):lines=lines[:1]+['pair summary unavailable (see private files)']
    lines.append('Original frozen P22 verdict first; P30 is a post-registration descriptive supplement; short sample, no profitability proof.')
    return lines[:1]+lines[1:-1][:6]+lines[-1:]


def main(argv=None):
    parser=argparse.ArgumentParser(description='P31 single manual P22 research run (no scheduling or retries)')
    parser.add_argument('target',choices=('p22',));parser.add_argument('--directory',required=True)
    parser.add_argument('--connect-public',action='store_true')
    a=parser.parse_args(argv)
    try:root,record=run(a.directory,connect_public=a.connect_public)
    except Exception as exc:
        reason=str(exc) if p28._CODE.fullmatch(str(exc)) else 'P31_PREFLIGHT_OR_PATH_UNVERIFIED'
        print(f'P31 BLOCKED exit=2 reason={reason} network=0');return 2
    for line in summary(record,root):print(line)
    return code_of(record)


if __name__=='__main__':raise SystemExit(main())
