"""Explicit bounded public minute collection; strictly offline PAPER analysis."""
import argparse
import hashlib
import json
import logging
import re
from pathlib import Path
import time

import pandas as pd

from .collection import MARKETS,FIELDS,utc,quality_report,write_json
from .data import load_csv,dataset_hash
from .historical import load_protocol
from .legacy_diagnostic import prepare,simulate
from .engine import CostModel
from .strategy_comparison import candidates,summarize,write_report,display
from services.public_market import PublicMarketClient,PublicDataError,positive,PUBLIC_ERROR_CODES

ROOT=Path(__file__).resolve().parents[1]
ENDPOINT='https://api.upbit.com/v1/candles/minutes/1'
MAX_MINUTES=1440
MAX_CALLS=40
DEADLINE_SECONDS=120
MAX_FILE_BYTES=2_000_000
SCHEMA='P11C_MINUTE_DEVELOPMENT_V1'


class MinuteError(ValueError):pass


def require(condition,code):
    if not condition:raise MinuteError(code)


def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()


def plan(markets,start,end,*,now=None):
    protocol,_=load_protocol();start,end=utc(start),utc(end)
    now=utc(now) if now is not None else pd.Timestamp.now(tz='UTC')
    require(1<=len(markets)<=4 and len(set(markets))==len(markets) and all(m in MARKETS for m in markets),'MARKET_LIMIT')
    require(start==start.floor('min') and end==end.floor('min') and 201<=(end-start).total_seconds()/60<=MAX_MINUTES,'WINDOW_LIMIT')
    # Never repurpose old disclosed development OR sealed holdout as new evidence.
    require(start>=utc(protocol['end']),'ARCHIVED_OR_SEALED_WINDOW_FORBIDDEN')
    require(end<=now.floor('min') and end>=now-pd.Timedelta(days=30),'FUTURE_OR_OLD_WINDOW')
    return {'schema':SCHEMA,'markets':list(markets),'unit_minutes':1,'start':start.isoformat(),'end_exclusive':end.isoformat(),
        'endpoint':ENDPOINT,'max_requests':MAX_CALLS,'deadline_seconds':DEADLINE_SECONDS,
        'max_minutes':MAX_MINUTES,'archived_development_overlap':False,'sealed_holdout_overlap':False,
        'independent_validation':False,'other_minute_collection_overlap':'UNKNOWN_NO_GLOBAL_REGISTRY'}


def new_output(value,*,collection=False):
    original=Path(value).expanduser().absolute()
    require(not any(p.is_symlink() for p in (original,*original.parents)),'SYMLINK_OUTPUT_FORBIDDEN')
    p=original.resolve()
    blocked={'.ssh','.aws','.codex','.git','keychains','runtime','paper-experiments','notifications'}
    require(not any(x.lower() in blocked for x in p.parts),'OPERATIONAL_OR_SECRET_PATH_FORBIDDEN')
    if collection:require(not p.is_relative_to(ROOT),'COLLECTION_OUTSIDE_CHECKOUT_REQUIRED')
    elif p.is_relative_to(ROOT):require(p.is_relative_to(ROOT/'research'),'RESEARCH_OUTPUT_REQUIRED')
    require(not p.exists(),'OUTPUT_EXISTS_NO_OVERWRITE')
    p.mkdir(parents=True,mode=0o700)
    (p/'INCOMPLETE').write_text('Research acquisition/analysis incomplete. Not an operational ledger.\n',encoding='utf-8')
    return p


def parse_page(rows,market,cursor,start):
    require(isinstance(rows,list) and 1<=len(rows)<=200,'INVALID_PAGE')
    result=[];stamps=[]
    for row in rows:
        require(isinstance(row,dict) and row.get('market')==market and type(row.get('unit')) is int and row['unit']==1,'INVALID_MARKET_OR_UNIT')
        try:
            stamp=pd.Timestamp(row['candle_date_time_utc'])
            require(stamp.tzinfo is None,'INVALID_UTC_CANDLE')
            stamp=stamp.tz_localize('UTC')
        except (ValueError,KeyError,TypeError):raise MinuteError('INVALID_UTC_CANDLE') from None
        require(not pd.isna(stamp) and stamp==stamp.floor('min') and start<=stamp<cursor,'OUT_OF_WINDOW_OR_GAP_UNVERIFIED')
        values={name:float(positive(row[key],zero=name=='volume')) for key,name in FIELDS.items()}
        result.append({'timestamp':stamp,**values});stamps.append(stamp)
    require(len(set(stamps))==len(stamps),'DUPLICATE_CANDLE')
    require(stamps==sorted(stamps,reverse=True),'UNORDERED_PAGE')
    return result


def collect(output,markets,start,end,*,connect_public=False,client=None,clock=time.time,monotonic=time.monotonic):
    spec=plan(markets,start,end,now=pd.Timestamp(clock(),unit='s',tz='UTC'))
    if not connect_public:
        return {'status':'OFFLINE_PLAN_ONLY','network_calls':0,'files_written':0,'plan':spec}
    require(client is None or connect_public,'EXPLICIT_CONNECTION_REQUIRED')
    p=new_output(output,collection=True);started=clock();mono=monotonic();deadline=mono+DEADLINE_SECONDS
    synthetic=client is not None
    transport=client if synthetic else PublicMarketClient(clock=clock,monotonic=monotonic,deadline=deadline,max_calls=MAX_CALLS)
    manifest={'schema':SCHEMA,'plan':spec,'plan_sha256':sha(canonical(spec)),
        'source':'SYNTHETIC_TEST_TRANSPORT' if synthetic else 'PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED',
        'started_at':pd.Timestamp(started,unit='s',tz='UTC').isoformat(),'complete':False,'requests':[],'datasets':{},
        'provenance_limit':'SHA checks integrity, not independent proof of exchange origin',
        'http_date_policy':'NOT_APPLICABLE_SYNTHETIC' if synthetic else 'PUBLIC_CLIENT_ENFORCES_5_SECOND_SERVER_DATE_AND_LOCAL_CLOCK_CHECK'}
    write_json(p/'plan.json',spec);write_json(p/'manifest.json',manifest)
    calls=0;closed=False
    try:
        for market in markets:
            cursor=utc(end);lower=utc(start);entries=[];seen=set()
            while cursor>lower:
                require(calls<MAX_CALLS and monotonic()<deadline,'REQUEST_OR_DEADLINE_LIMIT')
                count=min(200,int((cursor-lower).total_seconds()/60))
                params={'market':market,'count':count,'to':cursor.isoformat()};calls+=1
                audit={'endpoint':ENDPOINT,'params':params,'request_number':calls,
                    'requested_at':pd.Timestamp(clock(),unit='s',tz='UTC').isoformat()}
                manifest['requests'].append(audit)
                rows=transport.get('candles/minutes/1',params)
                require(monotonic()<=deadline and abs((clock()-started)-(monotonic()-mono))<=5,'COLLECTION_CLOCK_OR_DEADLINE')
                require(len(rows)<=count,'INVALID_PAGE')
                audit.update(rows=len(rows),response_sha256=sha(canonical(rows)),fetched_at=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat())
                batch=parse_page(rows,market,cursor,lower)
                for row in batch:
                    require(row['timestamp'] not in seen,'DUPLICATE_CANDLE');seen.add(row['timestamp'])
                entries.extend(batch);cursor=batch[-1]['timestamp']
                # A gap is not proof of API failure; do not request earlier-than-plan history.
                if len(batch)<count:break
            frame=pd.DataFrame(list(reversed(entries))).set_index('timestamp')
            quality=quality_report(frame,1,start,end,strict=False)
            name=market+'-1.csv';raw=frame.to_csv(index_label='timestamp',float_format='%.17g',lineterminator='\n').encode()
            (p/name).write_bytes(raw)
            manifest['datasets'][market]={'file':name,'file_sha256':sha(raw),'dataset_sha256':dataset_hash(frame),'quality':quality}
            write_json(p/'manifest.json',manifest)
        transport.close();closed=True
        require(monotonic()<=deadline,'REQUEST_OR_DEADLINE_LIMIT')
        manifest.update(complete=True,finished_at=pd.Timestamp(clock(),unit='s',tz='UTC').isoformat(),network_calls=calls)
        write_json(p/'manifest.json',manifest);(p/'INCOMPLETE').unlink()
        passed=all(x['quality']['passed'] for x in manifest['datasets'].values())
        return {'status':'COLLECTED_DEVELOPMENT_SAMPLE' if passed else 'INSUFFICIENT_REAL_DATA',
            'network_calls':calls,'quality_passed':passed,'source':manifest['source'],'period':spec,'datasets':manifest['datasets']}
    except Exception as exc:
        allowed={'INVALID_PAGE','INVALID_MARKET_OR_UNIT','INVALID_UTC_CANDLE','OUT_OF_WINDOW_OR_GAP_UNVERIFIED',
            'DUPLICATE_CANDLE','UNORDERED_PAGE','REQUEST_OR_DEADLINE_LIMIT','COLLECTION_CLOCK_OR_DEADLINE'}|set(PUBLIC_ERROR_CODES)
        code=str(exc) if str(exc) in allowed else 'COLLECTION_UNVERIFIED'
        manifest.update(failure=code,network_calls=calls);write_json(p/'manifest.json',manifest)
        return {'status':'INSUFFICIENT_REAL_DATA','reason':code,'network_calls':calls,'complete':False,'source':manifest['source']}
    finally:
        if not closed:
            try:transport.close()
            except Exception:pass  # Existing failure/INCOMPLETE remains; never claim success.


def read_json(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size<=MAX_FILE_BYTES,'UNVERIFIED_METADATA')
    return json.loads(path.read_text(encoding='utf-8'))


def load_sample(data,*,allow_synthetic=False):
    p=Path(data).expanduser().absolute()
    require(not any(q.is_symlink() for q in (p,*p.parents)),'SYMLINK_INPUT_FORBIDDEN')
    require(not any(x.lower() in {'.ssh','.aws','.codex','.git','keychains','runtime','paper-experiments','notifications'} for x in p.parts),'OPERATIONAL_OR_SECRET_PATH_FORBIDDEN')
    require(not (p/'INCOMPLETE').exists(),'INCOMPLETE_COLLECTION')
    m=read_json(p/'manifest.json');spec=read_json(p/'plan.json')
    require(m.get('schema')==SCHEMA and m.get('complete') is True and m['plan']==spec and m['plan_sha256']==sha(canonical(spec)),'MANIFEST_MISMATCH')
    started,finished=utc(m['started_at']),utc(m['finished_at'])
    require(started<=finished<=pd.Timestamp.now(tz='UTC') and (finished-started).total_seconds()<=DEADLINE_SECONDS+5,'COLLECTION_TIME_UNVERIFIED')
    require(spec==plan(spec['markets'],spec['start'],spec['end_exclusive'],now=started),'PLAN_MISMATCH')
    synthetic=m.get('source')=='SYNTHETIC_TEST_TRANSPORT'
    require((synthetic and allow_synthetic) or m.get('source')=='PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED','REAL_SOURCE_UNVERIFIED')
    require(set(m['datasets'])==set(spec['markets']) and 1<=len(m['requests'])<=MAX_CALLS,'REGISTRY_OR_AUDIT_MISMATCH')
    require(type(m.get('network_calls')) is int and m['network_calls']==len(m['requests']),'AUDIT_COUNT_MISMATCH')
    audited=set()
    for n,audit in enumerate(m['requests'],1):
        params=audit['params'];before=utc(params['to'])
        require(audit['endpoint']==ENDPOINT and type(audit['request_number']) is int and audit['request_number']==n
            and set(params)=={'market','count','to'} and params['market'] in spec['markets']
            and type(params['count']) is int and 1<=params['count']<=200
            and utc(spec['start'])<before<=utc(spec['end_exclusive']) and before==before.floor('min')
            and type(audit['rows']) is int and 1<=audit['rows']<=params['count']
            and bool(re.fullmatch(r'[0-9a-f]{64}',audit['response_sha256']))
            and started<=utc(audit['requested_at'])<=utc(audit['fetched_at'])<=finished,'AUDIT_UNVERIFIED')
        audited.add(params['market'])
    require(audited==set(spec['markets']),'AUDIT_MARKET_MISMATCH')
    frames={}
    for market in spec['markets']:
        meta=m['datasets'][market];name=market+'-1.csv'
        require(meta['file']==name,'SNAPSHOT_PATH_MISMATCH')
        f=p/name;require(f.is_file() and not f.is_symlink() and f.stat().st_size<=MAX_FILE_BYTES,'SNAPSHOT_UNVERIFIED')
        require(sha(f.read_bytes())==meta['file_sha256'],'FILE_SHA_MISMATCH')
        frame=load_csv(f)
        require(list(frame.columns)==['open','high','low','close','volume'],'SNAPSHOT_SCHEMA_MISMATCH')
        quality=quality_report(frame,1,spec['start'],spec['end_exclusive'])
        require(quality==meta['quality'] and dataset_hash(frame)==meta['dataset_sha256'],'QUALITY_OR_SHA_MISMATCH')
        require(len(frame)>=201,'INSUFFICIENT_REAL_DATA')
        frames[market]=frame
    return m,frames


def minute_markdown(report):
    lines=['# One-minute offline strategy diagnostic','',f"Source: {report['source']}",
        f"Period UTC: [{report['period_start']}, {report['period_end_exclusive']})",
        f"Collected: {report['collection_started_at']} → {report['collection_finished_at']}",
        'Exploratory development sample; not independent validation or evidence of the old Mac zero-trade cause.',
        '', '|Strategy|Market|Fee/slip bps|Evaluations|Raw BUY/SELL/HOLD|Effective BUY/SELL/HOLD|Volume blocked|Cooldown actionable/all|Held BUY/empty SELL|Rejected buy/sell|Cash/minimum blocked|Fills buy/sell|Net %|MDD %|Win %|Expectancy KRW|Fees KRW|',
        '|---|---|---|---:|---|---|---:|---|---|---|---|---|---:|---:|---:|---:|---:|']
    for r in report['comparisons']:
        c=r['counts'];m=r['metrics']
        lines.append(f"|{r['strategy']}|{r['market']}|{r['costs']['fee_bps']}/{r['costs']['slippage_bps']}|{c['evaluations']}|{c['raw_BUY']}/{c['raw_SELL']}/{c['raw_HOLD']}|{c['effective_BUY']}/{c['effective_SELL']}/{c['effective_HOLD']}|{c['volume_blocked']}|{c['cooldown_blocked_actionable']}/{c['cooldown_blocked']}|{c['buy_while_held']}/{c['sell_without_position']}|{c['buy_rejected']}/{c['sell_rejected']}|{c['reject_cash_below_minimum']}/{c['reject_minimum_order']}|{r['fills']['buys']}/{r['fills']['sells']}|{display(m['net_return_pct'])}|{display(m['mdd_pct'])}|{display(m['win_rate_pct'])}|{display(m['expectancy_per_sell'])}|{display(r['fees'])}|")
    lines+=['','Full rejection reasons, slippage/spread, fee-inclusive realized/unrealized P&L, Buy & Hold and trace hashes are in JSON/CSV.',
        'No confirmed trades → profitability N/A, not invented zero return. New collection time is not proof of an unseen dataset.',
        'SHA verifies local integrity, not independent exchange attestation. No intrabar fills/partial-fill claims/live activation.']
    return '\n'.join(lines)+'\n'


def analyze(data,output,*,allow_synthetic=False):
    manifest,frames=load_sample(data,allow_synthetic=allow_synthetic);spec=manifest['plan']
    p=new_output(output);protocol,ph=load_protocol();rows=[]
    for market,raw in frames.items():
        frame,windows=prepare(raw,60)
        for strategy in candidates():
            for fee in protocol['fee_bps_per_side']:
                for slip in protocol['slippage_bps_per_side']:
                    r=simulate(frame,windows,market=market,bar_seconds=60,costs=CostModel(fee,slip,protocol['full_spread_bps']),
                        evaluation_start=spec['start'],strategy=strategy,initial_cash=protocol['initial_cash'])
                    require(r['counts']['evaluations']>0,'INSUFFICIENT_EVALUABLE_MINUTES')
                    trace=r.pop('trace');r.update(strategy=strategy.name,split='new_minute_development',
                        period_start=spec['start'],period_end_exclusive=spec['end_exclusive'],source=manifest['source'],
                        trace_count=len(trace),trace_sha256=sha(json.dumps(trace,sort_keys=True).encode()))
                    rows.append(r)
    values,summary,sensitivity=summarize(rows)
    report={'schema_version':1,'complete':True,'source':manifest['source'],'research_only':True,'independent_validation':False,
        'holdout_evaluated':False,'holdout_rows_parsed':0,'production_minute1_status':'SYNTHETIC_ONLY_REAL_DATA_INSUFFICIENT' if allow_synthetic else 'AVAILABLE_DEVELOPMENT_SAMPLE_NOT_LIVE_VALIDATION',
        'selection_performed':False,'multi_asset_portfolio':False,'period_start':spec['start'],'period_end_exclusive':spec['end_exclusive'],
        'collection_started_at':manifest['started_at'],'collection_finished_at':manifest['finished_at'],
        'archived_development_overlap':False,'sealed_holdout_overlap':False,'other_minute_collection_overlap':'UNKNOWN_NO_GLOBAL_REGISTRY',
        'protocol_sha256':ph,'plan_sha256':manifest['plan_sha256'],'common_risk_policy':'legacy_5pct_take_profit_stop_average_once_at_next_open',
        'comparisons':values,'summaries':summary,'leave_one_market_out':sensitivity}
    write_report(report,p,render=minute_markdown);(p/'INCOMPLETE').unlink();return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    c=commands.add_parser('collect');c.add_argument('--output',type=Path,required=True);c.add_argument('--markets',nargs='+',required=True)
    c.add_argument('--start',required=True);c.add_argument('--end',required=True);c.add_argument('--connect-public',action='store_true')
    a=commands.add_parser('analyze');a.add_argument('--data',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);logging.disable(logging.CRITICAL)
    try:
        if args.command=='collect':result=collect(args.output,args.markets,args.start,args.end,connect_public=args.connect_public)
        else:
            r=analyze(args.data,args.output)
            result={'status':'COMPLETE_OFFLINE_MINUTE_STUDY','source':r['source'],'comparisons':len(r['comparisons']),
                'period_start':r['period_start'],'period_end_exclusive':r['period_end_exclusive'],'independent_validation':False}
    except Exception:result={'status':'INSUFFICIENT_REAL_DATA','reason':'INPUT_OR_OUTPUT_UNVERIFIED',
        'network_calls':'UNKNOWN' if args.command=='collect' and args.connect_public else 0}
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
    return 2 if result['status']=='INSUFFICIENT_REAL_DATA' else 0


if __name__=='__main__':raise SystemExit(main())
