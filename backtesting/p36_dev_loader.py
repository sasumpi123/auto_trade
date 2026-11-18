"""Read-only development-period loader for a completed P36 snapshot (P36_HISTORY_SNAPSHOT_V2).

    frame, report = load_development(SNAPSHOT_ROOT, 'KRW-BTC', 60, '2024-01-01T00:00:00+00:00', '2025-01-01T00:00:00+00:00')

Returns the candles of [start, end) in ascending UTC order exactly as stored, plus a report of what the window
does NOT contain (missing months, missing bars, the span before the first observed bar). Nothing is filled,
interpolated, resampled or re-selected. A successful load is a read interface only: neither it nor the
snapshot's COMPLETE status is a data-quality pass, a split decision or evidence of full exchange history.

Fail-closed rules:
- Metadata first (no candle bytes): schema, the fixed plan, run status COMPLETE with the P36 completion rule
  and budget accounting, allowed market/unit, canonical development month records.
- A window that reaches the holdout (end > holdout_start) is refused before any candle file is opened.
- Only development/<MARKET>/<unit>m/YYYY-MM.csv.gz below a P19-safe root is ever opened, through the P36
  symlink-free path check; holdout/ is never listed, opened or hashed, and the full P36 verify() (which reads
  holdout bytes) is not called.
- Every opened month must match its recorded SHA-256, row count, first/last bar and dataset hash, the exact
  header, the unit grid inside its month, strictly increasing unique timestamps and valid OHLCV.
- Nothing is written (no lock, no temp file) and there is no network access.
"""
import gzip
import io
import re

import pandas as pd

from . import p36_history as p36
from .collection import quality_report,utc

SCHEMA='P36_DEV_LOADER_V1'
HEADER=b'timestamp,open,high,low,close,volume'
MAX_MONTH_BYTES=64_000_000  # 1-minute month ~ 44,640 rows; far below this compressed size.
NOTE=('Read interface only: not a data-quality pass, a split decision or proof of full history. Missing bars '
    'may be minutes without trades or API omissions; they are reported, never filled.')


class LoaderError(ValueError):pass


def require(ok,code):
    if not ok:raise LoaderError(code)


def metadata(root):
    """Manifest + budget checks that read no candle bytes (and nothing under holdout/)."""
    try:manifest=p36.read_json(root,'manifest.json');budget=p36.read_json(root,'budget.json')
    except p36.P36Error as exc:raise LoaderError('P36D_MANIFEST_INVALID') from exc
    require(type(manifest) is dict and manifest.get('schema')==p36.SCHEMA and type(manifest.get('plan')) is dict
        and manifest['plan']==p36.plan(manifest['plan'].get('end_exclusive')),'P36D_PLAN_INVALID')
    require(manifest.get('status')==p36.SUCCESS,'P36D_SNAPSHOT_NOT_COMPLETE')
    # Review 5951924433: the same bound the P36 verifier enforces; completion() alone only checks the sums.
    require(type(budget) is dict and budget.get('schema')==p36.SCHEMA and type(budget.get('requests')) is int
        and 0<=budget['requests']<=manifest['plan']['max_requests'],'P36D_BUDGET_INVALID')
    series=manifest.get('series')
    require(type(series) is dict and set(series)==set(p36.SERIES) and type(manifest.get('requests')) is int
        and type(manifest.get('unconfirmed_requests')) is int and manifest['unconfirmed_requests']>=0
        and all(type(v) is dict and type(v.get('requests')) is int and type(v.get('responses')) is int
            and 0<=v['responses']<=v['requests'] and type(v.get('months')) is dict for v in series.values()),'P36D_MANIFEST_INVALID')
    try:p36.completion(manifest,budget['requests'])  # The same success rule the writer and verifier use.
    except (p36.P36Error,KeyError,TypeError) as exc:raise LoaderError('P36D_COMPLETION_UNVERIFIED') from exc
    return manifest


def window(manifest,unit,start,end):
    try:start,end=utc(start),utc(end)
    except Exception:raise LoaderError('P36D_WINDOW_INVALID') from None
    step=pd.Timedelta(minutes=unit)
    require(start<end and start.value%step.value==0 and end.value%step.value==0,'P36D_WINDOW_INVALID')
    # Refused BEFORE any candle file is touched: the holdout is never read through this loader.
    require(end<=utc(manifest['plan']['holdout_start']),'P36D_HOLDOUT_OVERLAP')
    return start,end


def read_month(root,market,unit,month,record,earliest):
    """Open ONE canonical development month and check it against its record without repairing anything."""
    rel=p36.month_file(market,unit,month,'development')
    keys={'partition','file','file_sha256','rows','quality','earliest_observed_month'}
    require(type(record) is dict and set(record)==keys and record['partition']=='development' and record['file']==rel
        and record['earliest_observed_month'] is earliest
        and type(record['rows']) is int and record['rows']>0 and type(record['quality']) is dict
        and re.fullmatch(r'[0-9a-f]{64}',str(record['file_sha256'])),'P36D_RECORD_INVALID')
    try:path=p36.inside(root,rel)
    except p36.P36Error as exc:raise LoaderError('P36D_PATH_FORBIDDEN') from exc
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=MAX_MONTH_BYTES,'P36D_FILE_MISSING')
    raw=path.read_bytes()
    require(p36.sha(raw)==record['file_sha256'],'P36D_HASH_MISMATCH')
    try:text=gzip.decompress(raw)
    except Exception:raise LoaderError('P36D_FILE_UNREADABLE') from None
    require(text.split(b'\n',1)[0]==HEADER,'P36D_HEADER_INVALID')
    try:
        frame=pd.read_csv(io.BytesIO(text),float_precision='round_trip',dtype={'timestamp':str})
        stamps=[pd.Timestamp(t) for t in frame.pop('timestamp')]
    except Exception:raise LoaderError('P36D_FILE_UNREADABLE') from None
    require(all(t.tzinfo is not None and t.utcoffset()==pd.Timedelta(0) for t in stamps),'P36D_TIMESTAMP_INVALID')
    frame.index=pd.DatetimeIndex(stamps).tz_convert('UTC');frame.index.name='timestamp'
    require(len(frame)==record['rows'],'P36D_ROW_COUNT_MISMATCH')
    require(frame.index.is_unique and frame.index.is_monotonic_increasing,'P36D_DUPLICATE_OR_UNORDERED')
    first=pd.Timestamp(month+'-01',tz='UTC');last=p36.next_month(first);step=pd.Timedelta(minutes=unit)
    require(((frame.index-first)%step==pd.Timedelta(0)).all() and frame.index[0]>=first and frame.index[-1]<last,'P36D_GRID_OR_MONTH_INVALID')
    try:quality_report(frame,unit,first.isoformat(),last.isoformat(),strict=False)  # Finite, positive, OHLC range.
    except ValueError:raise LoaderError('P36D_OHLCV_INVALID') from None
    # Review 5951924433: recompute the WHOLE recorded quality with the writer's own function (the earliest month
    # counts from its first real bar, any other month from the 1st 00:00 UTC) instead of trusting stored values.
    recomputed=p36.month_quality(frame,unit,frame.index[0] if earliest else first,last)
    require(recomputed==record['quality'],'P36D_QUALITY_MISMATCH')
    return frame


def load_development(root,market,unit,start,end):
    """[start, end) candles of one series from the development partition only; see the module docstring."""
    require(market in p36.MARKETS and unit in p36.UNITS and type(unit) is int,'P36D_SERIES_INVALID')
    try:root=p36.root_path(root,new=False)
    except p36.P36Error as exc:raise LoaderError('P36D_ROOT_FORBIDDEN') from exc
    manifest=metadata(root);start,end=window(manifest,unit,start,end)
    series=manifest['series'][f'{market}-{unit}'];step=pd.Timedelta(minutes=unit)
    report={'schema':SCHEMA,'market':market,'unit_minutes':unit,'start':start.isoformat(),'end_exclusive':end.isoformat(),
        'series_status':series['status'],'note':NOTE,'holdout_read':False}
    require(all(type(m) is str and re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',m) for m in series['months']),'P36D_RECORD_INVALID')  # Before parsing.
    dev={m:r for m,r in series['months'].items() if pd.Timestamp(m+'-01',tz='UTC')<utc(manifest['plan']['holdout_start'])}
    require(all(type(r) is dict and r.get('partition')=='development' for r in dev.values()),'P36D_RECORD_INVALID')
    if series['status']=='NO_ROWS_RETURNED' or not dev:
        # Not data and not continuity: the API returned nothing for this series in the development partition.
        report.update(status='NO_DEVELOPMENT_ROWS',rows=0,first_observed_bar=None,expected_rows_from_first_observed=0,
            missing_bars=0,empty_months=[],before_first_observed=True)
        return pd.DataFrame(columns=['open','high','low','close','volume'],index=pd.DatetimeIndex([],tz='UTC',name='timestamp')),report
    # Review 5951924433: the first observed bar is taken from the earliest development month's OWN verified file
    # (opened even when it lies before the window; still development only), never from an unchecked record.
    earliest=min(dev);opened={earliest:read_month(root,market,unit,earliest,dev[earliest],True)}
    first_bar=opened[earliest].index[0]
    require(series.get('boundary_to')==first_bar.isoformat(),'P36D_RECORD_INVALID')  # Same bar the terminal evidence names.
    frames=[];empty=[];cursor=p36.month_start(start)
    while cursor<end:
        month=p36.month_key(cursor)
        if month in dev:frames.append(opened[month] if month in opened else read_month(root,market,unit,month,dev[month],False))
        elif cursor>=p36.month_start(first_bar):empty.append(month)  # Inside the observed span: reported, never filled.
        cursor=p36.next_month(cursor)
    frame=pd.concat(frames) if frames else pd.DataFrame(columns=['open','high','low','close','volume'],index=pd.DatetimeIndex([],tz='UTC'))
    frame=frame.loc[(frame.index>=start)&(frame.index<end)];frame.index.name='timestamp'
    observed_from=max(start,first_bar);expected=max(0,(end-observed_from)//step)
    require(0<=len(frame)<=expected,'P36D_COUNT_INCONSISTENT')  # Never report a negative missing count.
    report.update(status='LOADED',rows=len(frame),first_observed_bar=first_bar.isoformat(),
        expected_rows_from_first_observed=int(expected),missing_bars=int(expected-len(frame)),empty_months=empty,
        before_first_observed=start<first_bar,
        first_bar=frame.index[0].isoformat() if len(frame) else None,last_bar=frame.index[-1].isoformat() if len(frame) else None)
    return frame,report
