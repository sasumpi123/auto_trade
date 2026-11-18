"""P36 multi-year public minute-candle snapshot (KRW-BTC/ETH/XRP/SOL x 1/15/60 min) with a sealed holdout.

    python -m backtesting.p36_history plan
    python -m backtesting.p36_history collect --output NEW_PRIVATE_DIR --connect-public
    python -m backtesting.p36_history resume --output EXISTING_DIR --connect-public
    python -m backtesting.p36_history verify --output EXISTING_DIR

Unauthenticated GETs of https://api.upbit.com/v1/candles/minutes/{unit} only, paged backwards from a fixed
`end` (first day of the collection month, UTC). Every non-empty page moves the exclusive cursor to its oldest
candle, whatever its length; only an EMPTY response ends a series, and it is recorded as the API query boundary,
never as proof of the listing time or of complete history. Each response is validated (market/unit, naive-UTC
grid timestamps, strictly descending unique, inside the cursor window, finite positive OHLC with a consistent
range, non-negative volume); nothing is sorted, repaired, interpolated or filled. Completed months are stored
as gzip CSV (mtime 0) per market/unit/month with SHA-256; development months also keep quality, and the
development coverage report lists empty months and cross-month gaps.

Holdout: the 6 months before `end` are written under holdout/ and the manifest keeps ONLY their file SHA-256
and row count; no price, return, gap or timestamp statistic of those rows is recorded or printed.

Limits: one writer per snapshot (lock), a fixed request cap debited on disk BEFORE every call and never
refunded (a hard kill leaves it spent), >=0.25 s between requests (4/s, below Upbit's documented 10/s per-IP
candle group limit), stop on HTTP 429/5xx or any validation error (no automatic retry, re-connection or
schedule). A stopped snapshot is resumed only by the user's explicit `resume`, which first verifies the plan,
series, request accounting, completion evidence, every month's SHA-256 and the exact file set. This is a data
snapshot, not a pre-registered forward evaluation; no strategy is evaluated. Network 0 without
--connect-public. Existing research snapshots (including the 2026-10-21 sealed window) are never read.
"""
import argparse
import fcntl
import gzip
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from . import p19_baseline as base
from .collection import FIELDS,quality_report,utc
from services.public_market import PublicOnlyAuth,PublicDataError,positive

SCHEMA='P36_HISTORY_SNAPSHOT_V2'
MARKETS=('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL')
UNITS=(60,15,1)  # Cheapest series first.
SERIES=tuple(f'{m}-{u}' for u in UNITS for m in MARKETS)
HOLDOUT_MONTHS=6
MAX_REQUESTS=120_000  # Whole snapshot, across every manual resume; debited on disk BEFORE each call, never refunded.
MIN_INTERVAL_SECONDS=0.25  # 4/s, below Upbit's documented 10/s per-IP candle group limit.
PAGE=200
MAX_RESPONSE_BYTES=2_000_000
CLOCK_TOLERANCE_SECONDS=5
TERMINAL=('API_BOUNDARY_REACHED','NO_ROWS_RETURNED')
STATUSES=('IN_PROGRESS',*TERMINAL)
RUN_STATUSES=('IN_PROGRESS','COMPLETE_API_BOUNDARY_REACHED','INTERRUPTED_RESUME_MANUALLY','STOPPED_RESUME_MANUALLY',
    'REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED','QUERY_FINISHED_COMPLETION_UNVERIFIED')
SUCCESS='COMPLETE_API_BOUNDARY_REACHED'
SEALED='SEALED_IN_HOLDOUT'  # The boundary cursor of a holdout-only series is a holdout timestamp: never recorded.
ROOT=Path(__file__).resolve().parents[1]
GAP_NOTE='A missing bar may be a minute without trades or an API omission; the two cannot be distinguished.'
BOUNDARY_NOTE=('An empty response is only the API query boundary; it does not prove the listing time or that the '
    'whole exchange history was obtained.')
SNAPSHOT_MARKERS=('manifest.json','budget.json','snapshot.json','protocol.json','results.json','collection.json')


class P36Error(ValueError):pass


def require(ok,code):
    if not ok:raise P36Error(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def month_key(stamp):return stamp.strftime('%Y-%m')
def month_start(stamp):return pd.Timestamp(year=stamp.year,month=stamp.month,day=1,tz='UTC')
def next_month(stamp):return month_start(stamp)+pd.offsets.MonthBegin(1)
def now_iso(clock):return pd.Timestamp(clock(),unit='s',tz='UTC').isoformat()


def plan(now=None):
    """Fixed at the first run; a resume never moves `end` or the holdout boundary."""
    now=utc(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    end=month_start(now);holdout=end-pd.offsets.MonthBegin(HOLDOUT_MONTHS)
    return {'schema':SCHEMA,'markets':list(MARKETS),'units_minutes':list(UNITS),'end_exclusive':end.isoformat(),
        'holdout_start':holdout.isoformat(),'holdout_months':HOLDOUT_MONTHS,'max_requests':MAX_REQUESTS,
        'min_interval_seconds':MIN_INTERVAL_SECONDS,'page_count':PAGE,
        'endpoint':'https://api.upbit.com/v1/candles/minutes/{unit}','auth':'none; public GET only',
        'automatic_retry_resume_schedule':False,'pre_registered_forward_evaluation':False,
        'gap_note':GAP_NOTE,'boundary_note':BOUNDARY_NOTE}


# ---------------------------------------------------------------- paths (review 5949862643)
def root_path(value,*,new):
    """The snapshot root: existing P19 account/holdout/symlink rules, outside the checkout, not inside another snapshot."""
    try:p=base.safe_path(value)
    except ValueError as exc:raise P36Error('P36_SYMLINK_FORBIDDEN' if str(exc)=='SYMLINK_FORBIDDEN'
        else 'P36_OUTPUT_INSIDE_CHECKOUT' if str(exc)=='PRIVATE_OUTPUT_OUTSIDE_CHECKOUT_REQUIRED' else 'P36_OPERATIONAL_OR_ACCOUNT_PATH') from None
    blocked={'.ssh','.aws','.codex','.git','keychains','runtime','paper-experiments','notifications'}
    require(not any(x.lower() in blocked for x in p.parts),'P36_OPERATIONAL_OR_ACCOUNT_PATH')
    require(not any((x/n).exists() for x in p.parents for n in SNAPSHOT_MARKERS),'P36_INSIDE_EXISTING_SNAPSHOT')
    if new:
        require(not p.exists(),'P36_OUTPUT_EXISTS_NO_OVERWRITE');p.mkdir(parents=True,mode=0o700)
    else:require(p.is_dir() and (p/'manifest.json').is_file() and not (p/'manifest.json').is_symlink(),'P36_SNAPSHOT_NOT_FOUND')
    return p


def inside(root,rel,*,make=False):
    """Every component below the root is a real (non-symlink) directory/file inside the root."""
    rel=Path(rel);require(not rel.is_absolute() and '..' not in rel.parts,'P36_PATH_ESCAPE')
    current=root
    for i,part in enumerate(rel.parts):
        current=current/part
        if current.is_symlink():raise P36Error('P36_SYMLINK_FORBIDDEN')
        if i<len(rel.parts)-1:
            if make and not current.exists():current.mkdir(mode=0o700)
            require(not current.exists() or (current.is_dir() and not current.is_symlink()),'P36_SYMLINK_FORBIDDEN')
    require(current.resolve().is_relative_to(root),'P36_PATH_ESCAPE')
    return current


def write_new(root,rel,raw):
    """Exclusive temp file in the same real directory, fsync, then a no-overwrite publish."""
    target=inside(root,rel,make=True);require(not target.exists(),'P36_MONTH_EXISTS')
    fd,temp=tempfile.mkstemp(prefix='.p36-',suffix='.tmp',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as fh:fh.write(raw);fh.flush();os.fsync(fh.fileno())
        os.link(temp,target)  # Fails if the target appeared meanwhile; never overwrites.
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return target


def atomic_json(root,name,value):
    target=inside(root,name);fd,temp=tempfile.mkstemp(prefix='.p36-',suffix='.tmp',dir=root)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as fh:
            json.dump(value,fh,ensure_ascii=False,indent=2,allow_nan=False);fh.flush();os.fsync(fh.fileno())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def read_json(root,name):
    path=inside(root,name);require(path.is_file(),'P36_MANIFEST_INVALID')
    try:return json.loads(path.read_text(encoding='utf-8'))
    except ValueError:raise P36Error('P36_MANIFEST_INVALID') from None


class Lock:
    """One writer per snapshot; a second collect/resume is refused before any network or file write."""
    def __init__(self,root):self.root=root;self.fd=None
    def __enter__(self):
        path=inside(self.root,'.lock');self.fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
        try:fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:os.close(self.fd);self.fd=None;raise P36Error('P36_SNAPSHOT_LOCKED') from None
        return self
    def __exit__(self,*exc):
        if self.fd is not None:fcntl.flock(self.fd,fcntl.LOCK_UN);os.close(self.fd)


class Budget:
    """Disk-backed request counter: each call is debited and fsynced BEFORE it is sent; never refunded."""
    def __init__(self,root,cap):self.root=root;self.cap=cap;self.used=read_json(root,'budget.json')['requests']
    def debit(self):
        require(type(self.used) is int and self.used<self.cap,'P36_REQUEST_CAP_REACHED')
        self.used+=1;atomic_json(self.root,'budget.json',{'schema':SCHEMA,'requests':self.used})


class Transport:
    """GET-only candle client: fixed endpoint/params, rate floor, size cap, Date check, no retry."""
    def __init__(self,*,session=None,clock=time.time,monotonic=time.monotonic,sleep=time.sleep):
        self.session=session if session is not None else requests.Session()
        self.clock,self.monotonic,self.sleep=clock,monotonic,sleep;self.last=None

    def get(self,unit,params):
        require(unit in UNITS and set(params)=={'market','count','to'} and params['market'] in MARKETS
            and type(params['count']) is int and 1<=params['count']<=PAGE,'P36_REQUEST_INVALID')
        if self.last is not None:
            wait=MIN_INTERVAL_SECONDS-(self.monotonic()-self.last)
            if wait>0:self.sleep(wait)
        self.last=self.monotonic()
        try:
            with self.session.get(f'https://api.upbit.com/v1/candles/minutes/{unit}',params=params,auth=PublicOnlyAuth(),
                    timeout=10,allow_redirects=False,stream=True) as response:
                if response.status_code==429:raise P36Error('P36_RATE_LIMITED')
                if response.status_code!=200:raise P36Error('P36_HTTP_ERROR')
                try:
                    server=parsedate_to_datetime(response.headers.get('Date',''))
                    require(server.tzinfo is not None and abs(self.clock()-server.timestamp())<=CLOCK_TOLERANCE_SECONDS,'x')
                    require(utc(params['to']).timestamp()<=server.timestamp(),'x')  # Completed bars only.
                except Exception:raise P36Error('P36_CLOCK_UNVERIFIED') from None
                body=bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    body.extend(chunk);require(len(body)<=MAX_RESPONSE_BYTES,'P36_RESPONSE_TOO_LARGE')
            def unique(pairs):
                obj={}
                for k,v in pairs:
                    require(k not in obj,'P36_DUPLICATE_JSON_KEY');obj[k]=v
                return obj
            def nonfinite(_):raise P36Error('P36_NONFINITE_JSON')
            return json.loads(bytes(body),object_pairs_hook=unique,parse_constant=nonfinite)
        except P36Error:raise
        except Exception:raise P36Error('P36_REQUEST_FAILED') from None

    def close(self):self.session.close()


def parse_page(rows,market,unit,cursor,count):
    """Reject (never repair) anything but completed, unique, strictly descending grid candles before cursor."""
    require(isinstance(rows,list) and len(rows)<=count,'P36_INVALID_PAGE')
    step=pd.Timedelta(minutes=unit);out=[];stamps=[]
    for row in rows:
        require(isinstance(row,dict) and row.get('market')==market and type(row.get('unit')) is int and row['unit']==unit,'P36_MARKET_OR_UNIT_MISMATCH')
        try:stamp=pd.Timestamp(row['candle_date_time_utc'])
        except Exception:raise P36Error('P36_INVALID_TIME') from None
        require(not pd.isna(stamp) and stamp.tzinfo is None,'P36_INVALID_TIME')
        stamp=stamp.tz_localize('UTC')
        require(stamp.value%step.value==0 and stamp<cursor,'P36_OUT_OF_WINDOW_OR_GRID')
        try:values={name:float(positive(row[key],zero=name=='volume')) for key,name in FIELDS.items()}
        except (PublicDataError,KeyError):raise P36Error('P36_INVALID_NUMBER') from None
        require(values['low']<=min(values['open'],values['close']) and values['high']>=max(values['open'],values['close'],values['low']),'P36_INVALID_RANGE')
        out.append({'timestamp':stamp,**values});stamps.append(stamp)
    require(len(set(stamps))==len(stamps),'P36_DUPLICATE_CANDLE')
    require(stamps==sorted(stamps,reverse=True),'P36_UNORDERED_PAGE')
    return out


def month_quality(frame,unit,start,end):
    """Development months only: counts and the longest gap; validated by the existing quality_report."""
    report=quality_report(frame,unit,start.isoformat(),end.isoformat(),strict=False)
    step=pd.Timedelta(minutes=unit);edges=[start-step,*frame.index,end]
    longest=max((b-a)//step-1 for a,b in zip(edges,edges[1:]))
    return {'rows':len(frame),'expected_rows':report['expected_rows'],'missing_bars':report['missing'],
        'longest_gap_bars':int(longest),'first_bar':frame.index[0].isoformat(),'last_bar':frame.index[-1].isoformat(),
        'dataset_sha256':report['dataset_sha256']}


def month_file(market,unit,month,partition):return f'{partition}/{market}/{unit}m/{month}.csv.gz'


def write_month(root,manifest,key,market,unit,month,rows,earliest):
    """Store one completed month; holdout months record only file SHA-256 and row count."""
    start=pd.Timestamp(month+'-01',tz='UTC');end=next_month(start)
    holdout=start>=utc(manifest['plan']['holdout_start']);partition='holdout' if holdout else 'development'
    frame=pd.DataFrame(sorted(rows,key=lambda r:r['timestamp'])).set_index('timestamp')
    if earliest:start=frame.index[0]  # Before the first observed bar nothing is expected.
    raw=gzip.compress(frame.to_csv(index_label='timestamp',float_format='%.17g',lineterminator='\n').encode(),mtime=0)
    quality=month_quality(frame,unit,start,end)  # Validates both partitions; only development keeps the values.
    write_new(root,month_file(market,unit,month,partition),raw)
    record={'partition':partition,'file':month_file(market,unit,month,partition),'file_sha256':sha(raw),'rows':len(frame)}
    if not holdout:record.update(quality=quality,earliest_observed_month=earliest)
    manifest['series'][key]['months'][month]=record


def development_coverage(series,plan_,unit):
    """Development-only coverage over [first observed bar, holdout start): empty months and cross-month gaps
    are visible; nothing is interpolated. No holdout value enters this report."""
    dev=sorted((m,r) for m,r in series['months'].items() if r['partition']=='development')
    if not dev:return {'development_months':0,'rows':0,'expected_rows':0,'missing_bars':0,'longest_gap_bars':0,'empty_development_months':[]}
    step=pd.Timedelta(minutes=unit);holdout=utc(plan_['holdout_start'])
    first=utc(dev[0][1]['quality']['first_bar'])
    # Unfinished series: the span starts at the oldest completed month (a later resume extends it).
    if series['status'] not in TERMINAL and not dev[0][1].get('earliest_observed_month'):first=pd.Timestamp(dev[0][0]+'-01',tz='UTC')
    have={m for m,_ in dev};months=[];cursor=month_start(first)
    while cursor<holdout:months.append(month_key(cursor));cursor=next_month(cursor)
    longest=max(r['quality']['longest_gap_bars'] for _,r in dev);previous=first-step
    for _,r in dev:
        longest=max(longest,(utc(r['quality']['first_bar'])-previous)//step-1);previous=utc(r['quality']['last_bar'])
    longest=max(longest,(holdout-previous)//step-1)
    expected=(holdout-first)//step;rows=sum(r['rows'] for _,r in dev)
    return {'development_months':len(dev),'rows':rows,'expected_rows':int(expected),'missing_bars':int(expected-rows),
        'longest_gap_bars':int(longest),'empty_development_months':[m for m in months if m not in have]}


def summary(manifest):
    s=manifest['series'];bounded=[k for k,v in s.items() if v['status']=='API_BOUNDARY_REACHED']
    empty=[k for k,v in s.items() if v['status']=='NO_ROWS_RETURNED']
    hold=[m for v in s.values() for m in v['months'].values() if m['partition']=='holdout']
    lines=[f"P36 {manifest['status']} requests={manifest['requests']}/{manifest['plan']['max_requests']} "
        f"unconfirmed={manifest.get('unconfirmed_requests',0)} bounded={len(bounded)}/{len(SERIES)} no_rows={len(empty)}",
        f"end={manifest['plan']['end_exclusive']} holdout_start={manifest['plan']['holdout_start']} (holdout: sha256+rows only)"]
    for unit in UNITS:
        part=[(k,v) for k,v in s.items() if k.endswith(f'-{unit}')];cov=[development_coverage(v,manifest['plan'],unit) for _,v in part]
        first=[min(m for m,r in v['months'].items() if r['partition']=='development') for _,v in part
            if any(r['partition']=='development' for r in v['months'].values())]
        lines.append(f"{unit}m: bounded={sum(v['status']=='API_BOUNDARY_REACHED' for _,v in part)}/{len(part)} earliest_observed_month={min(first) if first else '-'} "
            f"dev_months={sum(c['development_months'] for c in cov)} dev_missing_bars={sum(c['missing_bars'] for c in cov)} "
            f"empty_dev_months={sum(len(c['empty_development_months']) for c in cov)}")
    lines.append(f"holdout months={len(hold)} rows={sum(m['rows'] for m in hold)}")
    lines.append('API query boundary only, not proof of listing or full history; gaps are not repaired; '
        +('see failure_reason.' if manifest.get('failure_reason') else 'details in manifest.json.'))
    return lines[:8]


def run(root,manifest,budget,transport,*,clock=time.time):
    """Collect until every series gets an empty response, the cap, an error or an interrupt."""
    p=manifest['plan'];end=utc(p['end_exclusive'])
    def save():
        manifest['updated_at']=now_iso(clock);atomic_json(root,'manifest.json',manifest)
    try:
        for unit in UNITS:
            for market in MARKETS:
                key=f'{market}-{unit}';series=manifest['series'].setdefault(key,{'status':'IN_PROGRESS','months':{},'requests':0,'responses':0})
                if series['status'] in TERMINAL:continue
                cursor=pd.Timestamp(min(series['months'])+'-01',tz='UTC') if series['months'] else end
                bucket={}
                while True:
                    budget.debit()  # On disk before the request leaves; a crash keeps the debit.
                    manifest['requests']+=1;series['requests']+=1
                    params={'market':market,'count':PAGE,'to':cursor.isoformat()}
                    rows=parse_page(transport.get(unit,params),market,unit,cursor,PAGE)
                    series['responses']+=1  # A validated response; failed or killed attempts only count as requests.
                    for r in rows:bucket.setdefault(month_key(r['timestamp']),[]).append(r)
                    # Only an EMPTY response ends a series (review 5949862643): a short page is not proof of the end.
                    boundary=not rows
                    if rows:cursor=rows[-1]['timestamp']
                    for month in sorted(bucket,reverse=True):
                        if boundary or pd.Timestamp(month+'-01',tz='UTC')>cursor:
                            write_month(root,manifest,key,market,unit,month,bucket.pop(month),boundary and not bucket)
                    if boundary:
                        # Terminal evidence (review 5950578943): the exclusive `to` of the empty response, which for an
                        # observed series equals its oldest stored bar (or is sealed if that bar is in the holdout).
                        series['status']='API_BOUNDARY_REACHED' if series['months'] else 'NO_ROWS_RETURNED'
                        oldest=series['months'][min(series['months'])] if series['months'] else None
                        series['boundary_to']=(params['to'] if oldest is None or oldest['partition']=='development' else SEALED)
                    save()
                    if boundary:break
        # Review 5950882054: the writer uses the verifier's own completion rule, so a saved success always re-verifies.
        try:completion(manifest,budget.used);manifest['status']=SUCCESS;manifest.pop('failure_reason',None)
        except P36Error as exc:manifest.update(status='QUERY_FINISHED_COMPLETION_UNVERIFIED',failure_reason=str(exc))
    except KeyboardInterrupt:manifest.update(status='INTERRUPTED_RESUME_MANUALLY',failure_reason='P36_INTERRUPTED')
    except Exception as exc:
        code=str(exc) if re.fullmatch(r'P36_[A-Z0-9_]{1,60}',str(exc)) else 'P36_COLLECTION_UNVERIFIED'
        manifest.update(status='REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED' if code=='P36_REQUEST_CAP_REACHED' else 'STOPPED_RESUME_MANUALLY',failure_reason=code)
    save()
    if manifest['status']==SUCCESS:  # Final self-check of the stored copy with the same verifier the CLI uses.
        try:verify(root,read_json(root,'manifest.json'))
        except P36Error as exc:manifest.update(status='QUERY_FINISHED_COMPLETION_UNVERIFIED',failure_reason=str(exc));save()
    return manifest


def stored_files(root):
    found=set()
    for partition in ('development','holdout'):
        top=root/partition
        if top.is_symlink():raise P36Error('P36_SYMLINK_FORBIDDEN')
        if not top.exists():continue
        for path in top.rglob('*'):
            if path.is_symlink():raise P36Error('P36_SYMLINK_FORBIDDEN')
            if path.is_file():found.add(str(path.relative_to(root)))
    return found


def verify(root,manifest):
    """Plan, series set, statuses, request accounting, completion evidence and the exact file set (review 5949862643)."""
    require(type(manifest) is dict and manifest.get('schema')==SCHEMA and type(manifest.get('plan')) is dict
        and manifest['plan']==plan(manifest['plan'].get('end_exclusive')),'P36_PLAN_CHANGED')
    p=manifest['plan'];end=utc(p['end_exclusive']);holdout=utc(p['holdout_start'])
    budget=read_json(root,'budget.json')
    require(budget.get('schema')==SCHEMA and type(budget.get('requests')) is int and 0<=budget['requests']<=p['max_requests'],'P36_BUDGET_INVALID')
    require(manifest.get('status') in RUN_STATUSES,'P36_STATUS_INVALID')
    unconfirmed=manifest.get('unconfirmed_requests')
    require(type(unconfirmed) is int and unconfirmed>=0,'P36_REQUEST_COUNT_MISMATCH')
    series=manifest.get('series');require(type(series) is dict and set(series)<=set(SERIES),'P36_SERIES_INVALID')
    counted=sum(v.get('requests',-1) for v in series.values())
    require(type(manifest.get('requests')) is int and counted==manifest['requests']-manifest.get('unconfirmed_requests',0)
        and manifest['requests']<=budget['requests'] and all(type(v.get('requests')) is int and v['requests']>=0 for v in series.values()),'P36_REQUEST_COUNT_MISMATCH')
    expected=set()
    for key,s in series.items():
        market,unit=key.rsplit('-',1);unit=int(unit)
        require(s.get('status') in STATUSES and type(s.get('months')) is dict,'P36_SERIES_INVALID')
        require(type(s.get('responses')) is int and 0<=s['responses']<=s['requests'],'P36_REQUEST_COUNT_MISMATCH')
        if s['status'] in TERMINAL:terminal_evidence(s,end)
        for month,record in s['months'].items():
            require(re.fullmatch(r'\d{4}-\d{2}',month) and pd.Timestamp(month+'-01',tz='UTC')<end,'P36_RESUME_LAYOUT_MISMATCH')
            partition='holdout' if pd.Timestamp(month+'-01',tz='UTC')>=holdout else 'development'
            keys={'partition','file','file_sha256','rows'}|({'quality','earliest_observed_month'} if partition=='development' else set())
            require(type(record) is dict and set(record)==keys and record['partition']==partition
                and record['file']==month_file(market,unit,month,partition),'P36_RESUME_LAYOUT_MISMATCH')
            path=inside(root,record['file'])
            require(path.is_file() and sha(path.read_bytes())==record['file_sha256'],'P36_RESUME_HASH_MISMATCH')
            expected.add(record['file'])
    require(stored_files(root)==expected,'P36_UNREGISTERED_OR_MISSING_FILE')
    if manifest['status']==SUCCESS:completion(manifest,budget['requests'])
    return manifest


def terminal_evidence(s,end):
    """Shared by writer and verifier. Failed or killed attempts may precede the boundary, so the evidence is the
    count of VALIDATED responses: exactly one empty response for NO_ROWS_RETURNED, at least one page with rows
    plus the empty response for API_BOUNDARY_REACHED. A terminal state with no validated response never passes."""
    if s['status']=='NO_ROWS_RETURNED':
        require(not s['months'] and s.get('responses')==1 and s['requests']>=1 and s.get('boundary_to')==end.isoformat(),'P36_COMPLETION_UNVERIFIED')
    else:
        require(s['months'] and type(s.get('responses')) is int and s['responses']>=2 and s['requests']>=s['responses'],'P36_COMPLETION_UNVERIFIED')
        oldest=s['months'][min(s['months'])]
        if oldest.get('partition')=='development':
            require(oldest.get('earliest_observed_month') is True and type(oldest.get('quality')) is dict
                and s.get('boundary_to')==oldest['quality'].get('first_bar'),'P36_COMPLETION_UNVERIFIED')
        else:require(s.get('boundary_to')==SEALED,'P36_COMPLETION_UNVERIFIED')


def completion(manifest,budget_used):
    """The single success rule: all 12 series terminal with evidence and every debited request accounted for
    (recorded or reported as unconfirmed; never refunded)."""
    end=utc(manifest['plan']['end_exclusive']);series=manifest['series']
    require(set(series)==set(SERIES) and all(v['status'] in TERMINAL for v in series.values()),'P36_COMPLETION_UNVERIFIED')
    for v in series.values():terminal_evidence(v,end)
    require(manifest['requests']==budget_used and sum(v['requests'] for v in series.values())+manifest['unconfirmed_requests']==budget_used,'P36_COMPLETION_UNVERIFIED')


def reconcile(manifest,used):
    """Report requests debited on disk but never recorded (hard kill) as spent and unconfirmed; never refunded."""
    out=json.loads(json.dumps(manifest));extra=used-out['requests']
    require(extra>=0,'P36_REQUEST_COUNT_MISMATCH')
    out['unconfirmed_requests']+=extra;out['requests']=used
    if used>=out['plan']['max_requests'] and out['status'] not in (SUCCESS,'QUERY_FINISHED_COMPLETION_UNVERIFIED'):
        out.update(status='REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED',failure_reason='P36_REQUEST_CAP_REACHED')
    return out


def collect(output,*,connect_public=False,transport=None,clock=time.time):
    spec=plan(pd.Timestamp(clock(),unit='s',tz='UTC'))
    if not connect_public:return {'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'plan':spec}
    root=root_path(output,new=True)
    with Lock(root):
        manifest={'schema':SCHEMA,'plan':spec,'status':'IN_PROGRESS','requests':0,'unconfirmed_requests':0,'series':{},
            'started_at':now_iso(clock),'runs':1,
            'source':'SYNTHETIC_TEST_TRANSPORT' if transport is not None else 'PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED'}
        atomic_json(root,'budget.json',{'schema':SCHEMA,'requests':0});atomic_json(root,'manifest.json',manifest)
        transport=transport or Transport(clock=clock)
        try:return run(root,manifest,Budget(root,spec['max_requests']),transport,clock=clock)
        finally:transport.close()


def resume(output,*,connect_public=False,transport=None,clock=time.time):
    root=root_path(output,new=False)
    with Lock(root):
        manifest=verify(root,read_json(root,'manifest.json'))
        if manifest['status']==SUCCESS:return manifest
        if manifest['status']=='QUERY_FINISHED_COMPLETION_UNVERIFIED':
            # Every series already ended but the stored evidence fails the success rule: resuming cannot add evidence,
            # so this stays a distinct non-success state (no transport is opened, nothing is rewritten).
            return reconcile(manifest,Budget(root,manifest['plan']['max_requests']).used)
        budget=Budget(root,manifest['plan']['max_requests'])
        manifest=reconcile(manifest,budget.used)  # Review 5950578943: disk budget is always what is reported.
        exhausted=budget.used>=budget.cap
        if not connect_public:return manifest if exhausted else {**manifest,'status':'VERIFIED_RESUMABLE_OFFLINE'}
        if exhausted:  # Record the reconciled counts; no transport is opened and nothing is requested.
            manifest['updated_at']=now_iso(clock);atomic_json(root,'manifest.json',manifest);return manifest
        manifest.update(status='IN_PROGRESS',runs=manifest.get('runs',1)+1);manifest.pop('failure_reason',None)
        transport=transport or Transport(clock=clock)
        try:return run(root,manifest,budget,transport,clock=clock)
        finally:transport.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description='P36 public multi-year minute snapshot (manual, sealed holdout)')
    parser.add_argument('command',choices=('plan','collect','resume','verify'))
    parser.add_argument('--output');parser.add_argument('--connect-public',action='store_true');a=parser.parse_args(argv)
    if a.command=='plan' and (a.output or a.connect_public):parser.error('plan takes no arguments')
    if a.command in ('collect','resume','verify') and not a.output:parser.error('--output is required')
    if a.command=='verify' and a.connect_public:parser.error('verify is offline')
    try:
        if a.command=='plan':
            s=plan();print(f"P36 PLAN end={s['end_exclusive']} holdout_start={s['holdout_start']} max_requests={s['max_requests']} interval={s['min_interval_seconds']}s; no network or files.");return 0
        if a.command=='verify':
            root=root_path(a.output,new=False)
            with Lock(root):
                m=verify(root,read_json(root,'manifest.json'));m=reconcile(m,Budget(root,m['plan']['max_requests']).used)
        elif a.command=='collect':m=collect(a.output,connect_public=a.connect_public)
        else:m=resume(a.output,connect_public=a.connect_public)
    except (P36Error,ValueError,OSError) as exc:
        print(f"P36 BLOCKED exit=2 reason={str(exc) if re.fullmatch(r'P36_[A-Z0-9_]{1,60}',str(exc)) else 'P36_UNVERIFIED'}");return 2
    if m['status']=='OFFLINE_PLAN_ONLY':
        print(f"P36 OFFLINE_PLAN_ONLY end={m['plan']['end_exclusive']} holdout_start={m['plan']['holdout_start']}; add --connect-public to collect.");return 0
    for line in summary(m):print(line)
    return 0 if m['status'] in ('COMPLETE_API_BOUNDARY_REACHED','VERIFIED_RESUMABLE_OFFLINE') else 2


if __name__=='__main__':raise SystemExit(main())
