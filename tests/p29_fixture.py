"""Synthetic-only P19 collection + real P21 work() output for P29 tests (no network, no user data)."""
import json
import socket
from pathlib import Path
from unittest.mock import Mock,patch

from backtesting import p19_baseline as base
from backtesting import p20_attribution as p20
from backtesting import p21_entry_filter as p21
from backtesting.collection import quality_report
from backtesting.data import dataset_hash,demo_candles


def synthetic_loader():
    loader=base.minute.load_partitioned_sample
    def load(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
    return load


def build(root,*,day_index=0,seed_shift=0,source_label='SYNTHETIC_TEST_TRANSPORT'):
    """P19 root with one fully collected day (KRW-BTC has a missing candle -> excluded) and its P21 report.

    source_label may be the public label ONLY for the unpatched subprocess CLI test inside a temp dir;
    the candles are always synthetic demo_candles and are never presented as real market data.
    """
    root=Path(root);m=base.minute
    day=base.DAYS[day_index];start=base.day_start(day);end=start+m.pd.Timedelta(days=1);collected=end+m.pd.Timedelta(hours=1)
    source=root/'p19';daily=source/'data'/day;daily.mkdir(parents=True);(source/'analysis').mkdir()
    plan=m.plan(list(base.MARKETS),start,end,now=collected)
    manifest={'schema':m.SCHEMA,'complete':True,'plan':plan,'plan_sha256':m.sha(m.canonical(plan)),'source':source_label,
        'started_at':collected.isoformat(),'finished_at':collected.isoformat(),'network_calls':2,'requests':[],'datasets':{}}
    for n,market in enumerate(base.MARKETS,1):
        frame=demo_candles(1440+seed_shift).iloc[seed_shift:];frame.index=m.pd.date_range(start,periods=1440,freq='min')
        if market=='KRW-BTC':frame=frame.drop(frame.index[50])
        file=daily/(market+'-1.csv');frame.to_csv(file,index_label='timestamp')
        manifest['datasets'][market]={'file':file.name,'file_sha256':m.sha(file.read_bytes()),
            'dataset_sha256':dataset_hash(frame),'quality':quality_report(frame,1,start,end,strict=False)}
        manifest['requests'].append({'endpoint':m.ENDPOINT,'request_number':n,'params':{'market':market,'count':200,'to':end.isoformat()},
            'rows':200,'response_sha256':'0'*64,'requested_at':collected.isoformat(),'fetched_at':collected.isoformat()})
    base.write_json(daily/'plan.json',plan);base.write_json(daily/'manifest.json',manifest)
    load=synthetic_loader() if source_label=='SYNTHETIC_TEST_TRANSPORT' else base.minute.load_partitioned_sample
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')):
        meta,frames,_=load(daily,quarantine_gaps=True)
        frame,windows=base.prepare(frames['KRW-XRP'],60);rows=[]
        for costs in base.COSTS:
            r=base.case(frame,windows,'KRW-XRP',costs);r.pop('trace');r.update(day=day,input_quality=meta['datasets']['KRW-XRP']);rows.append(r)
        excluded=[{'day':day,'market':'KRW-BTC','reason':'MISSING_CANDLES'}]+[
            {'day':d,'markets':list(base.MARKETS),'reason':'INVALID_OR_MISSING_DAILY_INPUT'} for d in base.DAYS if d!=day]
        base.write_json(source/'analysis/results.json',{'protocol':base.protocol(),'protocol_sha256':base.protocol_hash(),
            'status':'PARTIAL_REAL_DATA','cases':rows,'excluded':excluded})
        with patch.object(base.minute,'load_partitioned_sample',side_effect=load):
            out=base.fresh(root/'p21');p21.work(source,out,Mock())
        report=p20.read_results(out/'results.json')
        report.update(unfinished=[],elapsed_seconds=1.0);p21.write_report(out,report)
    return source,out/'results.json'


def relabel_public(p19,p21_results):
    """Test-only copy for the unpatched CLI subprocess: same synthetic candles, consistent hashes."""
    import hashlib
    day=next(p.name for p in (Path(p19)/'data').iterdir())
    path=Path(p19)/'data'/day/'manifest.json';manifest=json.loads(path.read_text())
    manifest['source']='PUBLIC_REST_DECLARED_SOURCE_NOT_INDEPENDENTLY_ATTESTED';base.write_json(path,manifest)
    report=json.loads(Path(p21_results).read_text())
    report['input_hashes'][day]['manifest']=hashlib.sha256(path.read_bytes()).hexdigest()
    base.write_json(p21_results,report)
