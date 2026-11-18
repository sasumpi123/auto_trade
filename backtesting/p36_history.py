"""P36 multi-year public minute-candle snapshot (KRW-BTC/ETH/XRP/SOL x 1/15/60 min) with a sealed holdout.

    python -m backtesting.p36_history plan
    python -m backtesting.p36_history collect --output NEW_PRIVATE_DIR --connect-public
    python -m backtesting.p36_history resume --output EXISTING_DIR --connect-public
    python -m backtesting.p36_history verify --output EXISTING_DIR

Unauthenticated GETs of https://api.upbit.com/v1/candles/minutes/{unit} only, paged backwards from a fixed
`end` (first day of the collection month, UTC) until the exchange returns no earlier candle. Each response is
validated (market/unit, naive-UTC grid timestamps, strictly descending unique, inside the cursor window,
finite positive OHLC with consistent range, non-negative volume); nothing is sorted, repaired, interpolated or
filled. Completed months are stored as gzip CSV (mtime 0) per market/unit/month with SHA-256 and quality.

Holdout: the 6 months before `end` are written under holdout/ and the manifest keeps ONLY their file SHA-256
and row count; no price, return, gap or timestamp statistic of those rows is recorded or printed.

Limits: fixed per-snapshot request cap, >=0.25 s between requests (4/s), stop on HTTP 429/5xx or any
validation error (no automatic retry, re-connection or schedule). A stopped snapshot is resumed only by the
user's explicit `resume`, which first re-verifies every stored month's SHA-256 and the fixed plan. This is a
data snapshot, not a pre-registered forward evaluation; no strategy is evaluated. Network 0 without
--connect-public. Existing research snapshots (including the 2026-10-21 sealed window) are never read.
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import time
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pandas as pd
import requests

from .collection import FIELDS,quality_report,utc
from .data import dataset_hash
from services.public_market import PublicOnlyAuth,PublicDataError,positive

SCHEMA='P36_HISTORY_SNAPSHOT_V1'
MARKETS=('KRW-BTC','KRW-ETH','KRW-XRP','KRW-SOL')
UNITS=(60,15,1)  # Cheapest series first.
HOLDOUT_MONTHS=6
MAX_REQUESTS=120_000  # Whole snapshot, across every manual resume; never refilled.
MIN_INTERVAL_SECONDS=0.25
PAGE=200
MAX_RESPONSE_BYTES=2_000_000
CLOCK_TOLERANCE_SECONDS=5
ROOT=Path(__file__).resolve().parents[1]
GAP_NOTE='A missing bar may be a minute without trades or an API omission; the two cannot be distinguished.'


class P36Error(ValueError):pass


def require(ok,code):
    if not ok:raise P36Error(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()
def month_key(stamp):return stamp.strftime('%Y-%m')
def month_start(stamp):return pd.Timestamp(year=stamp.year,month=stamp.month,day=1,tz='UTC')
def next_month(stamp):return month_start(stamp)+pd.offsets.MonthBegin(1)


def plan(now=None):
    """Fixed at the first run; a resume never moves `end` or the holdout boundary."""
    now=utc(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    end=month_start(now);holdout=end-pd.offsets.MonthBegin(HOLDOUT_MONTHS)
    return {'schema':SCHEMA,'markets':list(MARKETS),'units_minutes':list(UNITS),'end_exclusive':end.isoformat(),
        'holdout_start':holdout.isoformat(),'holdout_months':HOLDOUT_MONTHS,'max_requests':MAX_REQUESTS,
        'min_interval_seconds':MIN_INTERVAL_SECONDS,'page_count':PAGE,
        'endpoint':'https://api.upbit.com/v1/candles/minutes/{unit}','auth':'none; public GET only',
        'automatic_retry_resume_schedule':False,'pre_registered_forward_evaluation':False,
        'gap_note':GAP_NOTE}


def atomic_json(path,value):
    path=Path(path);temp=path.with_name(path.name+'.pending')
    with temp.open('w',encoding='utf-8') as fh:
        json.dump(value,fh,ensure_ascii=False,indent=2,allow_nan=False);fh.flush();os.fsync(fh.fileno())
    os.replace(temp,path)


def output_path(value,*,new):
    original=Path(value).expanduser().absolute()
    require(not any(p.is_symlink() for p in (original,*original.parents)),'P36_SYMLINK_FORBIDDEN')
    p=original.resolve()
    require(not p.is_relative_to(ROOT),'P36_OUTPUT_INSIDE_CHECKOUT')  # Large data never lands in the public repo.
    blocked={'.ssh','.aws','.codex','.git','keychains','runtime','paper-experiments','notifications'}
    require(not any(x.lower() in blocked for x in p.parts),'P36_OPERATIONAL_OR_SECRET_PATH')
    if new:
        require(not p.exists(),'P36_OUTPUT_EXISTS_NO_OVERWRITE');p.mkdir(parents=True,mode=0o700)
    else:require(p.is_dir() and (p/'manifest.json').is_file(),'P36_SNAPSHOT_NOT_FOUND')
    return p


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


def write_month(root,manifest,key,market,unit,month,rows,first_series_month):
    """Store one completed month; holdout months record only file SHA-256 and row count."""
    start=pd.Timestamp(month+'-01',tz='UTC');end=next_month(start)
    holdout=start>=utc(manifest['plan']['holdout_start'])
    frame=pd.DataFrame(sorted(rows,key=lambda r:r['timestamp'])).set_index('timestamp')
    if first_series_month:start=frame.index[0]  # Before the first listed bar there is nothing to expect.
    partition='holdout' if holdout else 'development'
    folder=root/partition/market/f'{unit}m';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    raw=gzip.compress(frame.to_csv(index_label='timestamp',float_format='%.17g',lineterminator='\n').encode(),mtime=0)
    quality=month_quality(frame,unit,start,end)  # Validates both partitions; only development keeps the values.
    path=folder/f'{month}.csv.gz';temp=path.with_name(path.name+'.pending');temp.write_bytes(raw);os.replace(temp,path)
    record={'partition':partition,'file':str(path.relative_to(root)),'file_sha256':sha(raw),'rows':len(frame)}
    if not holdout:record.update(quality=quality,earliest_month=first_series_month)
    manifest['series'][key]['months'][month]=record


def summary(manifest):
    s=manifest['series'];done=[k for k,v in s.items() if v['status']=='EARLIEST_REACHED']
    dev=[m for v in s.values() for m in v['months'].values() if m['partition']=='development']
    hold=[m for v in s.values() for m in v['months'].values() if m['partition']=='holdout']
    lines=[f"P36 {manifest['status']} requests={manifest['requests']}/{manifest['plan']['max_requests']} series_full={len(done)}/{len(s)}",
        f"end={manifest['plan']['end_exclusive']} holdout_start={manifest['plan']['holdout_start']} (holdout: sha256+rows only)"]
    for unit in UNITS:
        part=[(k,v) for k,v in s.items() if k.endswith(f'-{unit}')]
        first=[min(v['months']) for _,v in part if any(m['partition']=='development' for m in v['months'].values())]
        missing=sum(m['quality']['missing_bars'] for _,v in part for m in v['months'].values() if m['partition']=='development')
        lines.append(f"{unit}m: full={sum(v['status']=='EARLIEST_REACHED' for _,v in part)}/{len(part)} earliest_month={min(first) if first else '-'} "
            f"dev_months={sum(m['partition']=='development' for _,v in part for m in v['months'].values())} dev_missing_bars={missing}")
    lines.append(f"development months={len(dev)} rows={sum(m['rows'] for m in dev)}; holdout months={len(hold)} rows={sum(m['rows'] for m in hold)}")
    lines.append('Full history only where series_full; gaps are not repaired; '+('see failure_reason.' if manifest.get('failure_reason') else 'details in manifest.json.'))
    return lines[:8]


def run(root,manifest,transport,*,clock=time.time):
    """Collect until every series reaches its first listed candle, the cap, an error or an interrupt."""
    p=manifest['plan'];end=utc(p['end_exclusive'])
    try:
        for unit in UNITS:
            for market in MARKETS:
                key=f'{market}-{unit}';series=manifest['series'].setdefault(key,{'status':'IN_PROGRESS','months':{},'requests':0})
                if series['status']=='EARLIEST_REACHED':continue
                cursor=pd.Timestamp(min(series['months'])+'-01',tz='UTC') if series['months'] else end
                bucket={}
                while True:
                    require(manifest['requests']<p['max_requests'],'P36_REQUEST_CAP_REACHED')
                    params={'market':market,'count':PAGE,'to':cursor.isoformat()}
                    manifest['requests']+=1;series['requests']+=1
                    rows=parse_page(transport.get(unit,params),market,unit,cursor,PAGE)
                    for r in rows:bucket.setdefault(month_key(r['timestamp']),[]).append(r)
                    # Upbit skips minutes without trades, so a short (or empty) page means no earlier history exists.
                    exhausted=len(rows)<PAGE
                    if rows:cursor=rows[-1]['timestamp']
                    # A month is complete once strictly earlier data was seen (or history is exhausted); the month
                    # holding the cursor waits for the next page, so the earliest month is always flagged correctly.
                    for month in sorted(bucket,reverse=True):
                        if exhausted or pd.Timestamp(month+'-01',tz='UTC')>cursor:
                            write_month(root,manifest,key,market,unit,month,bucket.pop(month),exhausted and not bucket)
                    if exhausted:series['status']='EARLIEST_REACHED'
                    manifest['updated_at']=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat();atomic_json(root/'manifest.json',manifest)
                    if exhausted:break
        manifest['status']='COMPLETE_EARLIEST_REACHED';manifest.pop('failure_reason',None)
    except KeyboardInterrupt:manifest.update(status='INTERRUPTED_RESUME_MANUALLY',failure_reason='P36_INTERRUPTED')
    except Exception as exc:
        code=str(exc) if re.fullmatch(r'P36_[A-Z0-9_]{1,60}',str(exc)) else 'P36_COLLECTION_UNVERIFIED'
        manifest.update(status='REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED' if code=='P36_REQUEST_CAP_REACHED' else 'STOPPED_RESUME_MANUALLY',failure_reason=code)
    manifest['updated_at']=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat();atomic_json(root/'manifest.json',manifest)
    return manifest


def verify(root,manifest):
    """Every stored month must match its recorded SHA-256 and partition; anything else blocks a resume."""
    require(manifest.get('schema')==SCHEMA and manifest.get('plan',{}).get('schema')==SCHEMA,'P36_MANIFEST_INVALID')
    holdout=utc(manifest['plan']['holdout_start'])
    for key,series in manifest['series'].items():
        for month,record in series['months'].items():
            market,unit=key.rsplit('-',1);partition='holdout' if pd.Timestamp(month+'-01',tz='UTC')>=holdout else 'development'
            require(record['partition']==partition and record['file']==f'{partition}/{market}/{unit}m/{month}.csv.gz','P36_RESUME_LAYOUT_MISMATCH')
            path=root/record['file']
            require(path.is_file() and not path.is_symlink() and sha(path.read_bytes())==record['file_sha256'],'P36_RESUME_HASH_MISMATCH')
    return manifest


def collect(output,*,connect_public=False,transport=None,clock=time.time):
    spec=plan(pd.Timestamp(clock(),unit='s',tz='UTC'))
    if not connect_public:return {'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'plan':spec}
    root=output_path(output,new=True)
    manifest={'schema':SCHEMA,'plan':spec,'status':'IN_PROGRESS','requests':0,'series':{},
        'started_at':pd.Timestamp(clock(),unit='s',tz='UTC').isoformat(),'runs':1,
        'source':'SYNTHETIC_TEST_TRANSPORT' if transport is not None else 'PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED'}
    atomic_json(root/'manifest.json',manifest)
    transport=transport or Transport(clock=clock)
    try:return run(root,manifest,transport,clock=clock)
    finally:transport.close()


def resume(output,*,connect_public=False,transport=None,clock=time.time):
    root=output_path(output,new=False)
    try:manifest=json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    except ValueError:raise P36Error('P36_MANIFEST_INVALID') from None
    verify(root,manifest)
    require(manifest['plan']==plan(manifest['plan']['end_exclusive']),'P36_PLAN_CHANGED')  # Same code constants, same end.
    if manifest['status']=='COMPLETE_EARLIEST_REACHED':return manifest
    if not connect_public:return {**manifest,'status':'VERIFIED_RESUMABLE_OFFLINE'}
    require(manifest['requests']<manifest['plan']['max_requests'],'P36_REQUEST_CAP_REACHED')  # Never refilled.
    manifest.update(status='IN_PROGRESS',runs=manifest.get('runs',1)+1);manifest.pop('failure_reason',None)
    transport=transport or Transport(clock=clock)
    try:return run(root,manifest,transport,clock=clock)
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
            root=output_path(a.output,new=False);m=verify(root,json.loads((root/'manifest.json').read_text(encoding='utf-8')))
        elif a.command=='collect':m=collect(a.output,connect_public=a.connect_public)
        else:m=resume(a.output,connect_public=a.connect_public)
    except (P36Error,ValueError,OSError) as exc:
        print(f"P36 BLOCKED exit=2 reason={str(exc) if re.fullmatch(r'P36_[A-Z0-9_]{1,60}',str(exc)) else 'P36_UNVERIFIED'}");return 2
    if m['status']=='OFFLINE_PLAN_ONLY':
        print(f"P36 OFFLINE_PLAN_ONLY end={m['plan']['end_exclusive']} holdout_start={m['plan']['holdout_start']}; add --connect-public to collect.");return 0
    for line in summary(m):print(line)
    return 0 if m['status'] in ('COMPLETE_EARLIEST_REACHED','VERIFIED_RESUMABLE_OFFLINE','IN_PROGRESS') else 2


if __name__=='__main__':raise SystemExit(main())
