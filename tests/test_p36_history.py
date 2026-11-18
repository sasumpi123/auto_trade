"""P36 synthetic-only tests: fake HTTP, request cap, month boundaries, rejection, gaps, resume, sealed holdout.

No real network: every response comes from FakeSession; a socket guard fails the test on any connection.
"""
from contextlib import contextmanager
from email.utils import format_datetime
import gzip,hashlib,io,json,os,socket,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtesting import p36_history as p36

NOW=pd.Timestamp('2026-11-18T01:00:00Z')  # plan: end 2026-11-01, holdout from 2026-05-01.
ROOT=Path(__file__).resolve().parents[1]


def bars(unit,start,end,drop=()):
    index=pd.date_range(start,end,freq=f'{unit}min',inclusive='left',tz='UTC')
    return [t for t in index if t not in {pd.Timestamp(x) for x in drop}]


def default_data():
    """Small synthetic histories (listing -> last bar); some end before `end` like a halted market."""
    data={}
    for i,m in enumerate(p36.MARKETS):
        data[(m,60)]=bars(60,f'2026-0{1+i%2}-10T00:00:00Z','2026-06-03T00:00:00Z',drop=['2026-03-05T05:00:00Z','2026-03-05T06:00:00Z'])
        data[(m,15)]=bars(15,'2026-03-20T00:00:00Z','2026-05-04T00:00:00Z')
        data[(m,1)]=bars(1,'2026-04-29T12:00:00Z','2026-05-01T06:00:00Z',drop=['2026-04-30T10:00:00Z'])
    return data


def row(market,unit,t,k):
    price=1000+k%97
    return {'market':market,'unit':unit,'candle_date_time_utc':t.tz_localize(None).isoformat(),
        'opening_price':price,'high_price':price+2,'low_price':price-1,'trade_price':price+1,'candle_acc_trade_volume':0.5+k%3,
        'candle_date_time_kst':'ignored','timestamp':0}


class FakeResponse:
    def __init__(self,status,body,date):self.status_code=status;self.body=body;self.headers={'Date':date}
    def iter_content(self,chunk_size):
        for i in range(0,len(self.body),chunk_size):yield self.body[i:i+chunk_size]
    def __enter__(self):return self
    def __exit__(self,*a):return False


class FakeSession:
    def __init__(self,data=None,clock=lambda:NOW.timestamp(),mutate=None,fail_at=None):
        self.data=data or default_data();self.calls=[];self.clock=clock;self.mutate=mutate;self.fail_at=fail_at;self.closed=False
    def get(self,url,params,auth,timeout,allow_redirects,stream):
        self.calls.append((url,dict(params),type(auth).__name__,allow_redirects,stream))
        if self.fail_at is not None and len(self.calls)==self.fail_at[0]:
            if isinstance(self.fail_at[1],BaseException):raise self.fail_at[1]
            return FakeResponse(self.fail_at[1],b'[]',format_datetime(pd.Timestamp(self.clock(),unit='s',tz='UTC').to_pydatetime(),usegmt=True))
        unit=int(url.rsplit('/',1)[1]);market=params['market'];to=pd.Timestamp(params['to'])
        series=self.data.get((market,unit),[]);before=[t for t in series if t<to][-params['count']:]
        rows=[row(market,unit,t,series.index(t)) for t in reversed(before)]
        if self.mutate:rows=self.mutate(len(self.calls),rows)
        return FakeResponse(200,json.dumps(rows).encode(),format_datetime(pd.Timestamp(self.clock(),unit='s',tz='UTC').to_pydatetime(),usegmt=True))
    def close(self):self.closed=True


def transport(session):
    t={'mono':0.0,'slept':[]}
    def mono():return t['mono']
    def sleep(x):t['slept'].append(x);t['mono']+=x
    return p36.Transport(session=session,clock=lambda:NOW.timestamp(),monotonic=mono,sleep=sleep),t


def months_frame(root,manifest,key):
    frames=[]
    for month,record in sorted(manifest['series'][key]['months'].items()):
        frames.append(pd.read_csv(io.BytesIO(gzip.decompress((root/record['file']).read_bytes())),parse_dates=['timestamp']).set_index('timestamp'))
    return pd.concat(frames)


class Base(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
    def tearDown(self):self.guard.stop();self.t.cleanup()
    def collect(self,name='snap',session=None,**kw):
        session=session or FakeSession(**kw);tr,state=transport(session)
        return self.root/name,p36.collect(self.root/name,connect_public=True,transport=tr,clock=lambda:NOW.timestamp()),session,state


class PlanTests(Base):
    def test_plan_is_offline_and_fixes_end_and_holdout_month_boundaries(self):
        s=p36.plan(NOW);self.assertEqual((s['end_exclusive'],s['holdout_start']),('2026-11-01T00:00:00+00:00','2026-05-01T00:00:00+00:00'))
        self.assertFalse(s['automatic_retry_resume_schedule']);self.assertFalse(s['pre_registered_forward_evaluation'])
        r=p36.collect(self.root/'x',clock=lambda:NOW.timestamp())
        self.assertEqual((r['status'],r['network_calls'],r['files_written']),('OFFLINE_PLAN_ONLY',0,0));self.assertFalse((self.root/'x').exists())
        with patch('sys.stdout'):self.assertEqual(p36.main(['plan']),0)

    def test_output_must_be_new_private_and_outside_checkout(self):
        for bad,code in ((ROOT/'research'/'p36',"P36_OUTPUT_INSIDE_CHECKOUT"),(self.root/'.ssh'/'x','P36_OPERATIONAL_OR_SECRET_PATH')):
            with self.subTest(code=code),self.assertRaisesRegex(p36.P36Error,code):p36.output_path(bad,new=True)
        (self.root/'exists').mkdir()
        with self.assertRaisesRegex(p36.P36Error,'P36_OUTPUT_EXISTS_NO_OVERWRITE'):p36.output_path(self.root/'exists',new=True)

    def test_cli_arguments_are_fixed(self):
        for argv in (['plan','--output','x'],['collect'],['verify','--output','x','--connect-public'],['fetch']):
            with self.subTest(argv=argv),patch('sys.stderr'),self.assertRaises(SystemExit):p36.main(argv)


class CollectTests(Base):
    def test_full_collection_round_trips_every_bar_and_reaches_listing(self):
        root,m,session,state=self.collect()
        self.assertEqual(m['status'],'COMPLETE_EARLIEST_REACHED');self.assertEqual(len(m['series']),12)
        self.assertTrue(all(v['status']=='EARLIEST_REACHED' for v in m['series'].values()));self.assertTrue(session.closed)
        data=default_data()
        for (market,unit),series in data.items():
            got=months_frame(root,m,f'{market}-{unit}')
            self.assertEqual(list(got.index.tz_localize('UTC') if got.index.tz is None else got.index),series)  # No loss, no duplicate.
        self.assertEqual(m['requests'],len(session.calls))
        self.assertTrue(all(c[0]==f"https://api.upbit.com/v1/candles/minutes/{c[0].rsplit('/',1)[1]}" and c[2]=='PublicOnlyAuth'
            and c[3] is False and c[1]['count']==200 for c in session.calls))
        self.assertTrue(all(x>=0 for x in state['slept']) and abs(sum(state['slept'])-0.25*(len(session.calls)-1))<1e-6)  # 4 req/s floor.
        lines=p36.summary(m);self.assertLessEqual(len(lines),8);self.assertIn('series_full=12/12',lines[0])

    def test_holdout_is_stored_apart_with_only_hash_and_rows_and_no_statistics(self):
        root,m,_,_=self.collect()
        hold=[(k,mo,r) for k,v in m['series'].items() for mo,r in v['months'].items() if r['partition']=='holdout']
        self.assertTrue(hold)
        for key,month,record in hold:
            self.assertGreaterEqual(month,'2026-05');self.assertEqual(set(record),{'partition','file','file_sha256','rows'})
            self.assertTrue(record['file'].startswith('holdout/'))
            self.assertEqual(hashlib.sha256((root/record['file']).read_bytes()).hexdigest(),record['file_sha256'])
        dev=[r for v in m['series'].values() for mo,r in v['months'].items() if r['partition']=='development']
        self.assertTrue(dev and all(r['file'].startswith('development/') and 'quality' in r for r in dev))
        text=json.dumps({k:{mo:r for mo,r in v['months'].items() if r['partition']=='holdout'} for k,v in m['series'].items()})
        for word in ('first_bar','last_bar','missing','gap','close','return','price','dataset_sha256'):self.assertNotIn(word,text)
        printed='\n'.join(p36.summary(m))
        for word in ('price','return','close','first_bar','last_bar'):self.assertNotIn(word,printed)  # Only the fixed boundary date is shown.

    def test_missing_bars_are_recorded_not_filled_and_earliest_month_counts_from_listing(self):
        root,m,_,_=self.collect()
        march=m['series']['KRW-BTC-60']['months']['2026-03']['quality']
        self.assertEqual((march['missing_bars'],march['longest_gap_bars'],march['rows'],march['expected_rows']),(2,2,31*24-2,31*24))
        first=m['series']['KRW-BTC-60']['months']['2026-01'];self.assertTrue(first['earliest_month'])
        self.assertEqual(first['quality']['first_bar'],'2026-01-10T00:00:00+00:00');self.assertEqual(first['quality']['missing_bars'],0)
        april=m['series']['KRW-BTC-1']['months']['2026-04']['quality'];self.assertEqual(april['missing_bars'],1)
        self.assertEqual(april['expected_rows'],(pd.Timestamp('2026-05-01',tz='UTC')-pd.Timestamp('2026-04-29T12:00',tz='UTC'))//pd.Timedelta(minutes=1))

    def test_page_ending_exactly_on_a_month_start_waits_for_confirmation(self):
        data={k:[] for k in default_data()};data[('KRW-BTC',60)]=bars(60,'2026-03-01T00:00:00Z','2026-03-09T08:00:00Z')  # Exactly 200.
        self.assertEqual(len(data[('KRW-BTC',60)]),200)
        root,m,session,_=self.collect(data=data)
        btc=m['series']['KRW-BTC-60'];self.assertEqual(list(btc['months']),['2026-03']);self.assertTrue(btc['months']['2026-03']['earliest_month'])
        self.assertEqual(btc['months']['2026-03']['rows'],200);self.assertEqual([c[1]['to'] for c in session.calls[:2]],['2026-11-01T00:00:00+00:00','2026-03-01T00:00:00+00:00'])
        self.assertEqual(m['series']['KRW-ETH-60']['months'],{})  # Never listed: one empty page, nothing written.

    def test_month_boundary_rows_split_into_their_own_months(self):
        data={k:[] for k in default_data()};data[('KRW-XRP',15)]=bars(15,'2026-02-27T00:00:00Z','2026-03-02T12:00:00Z')
        root,m,_,_=self.collect(data=data);months=m['series']['KRW-XRP-15']['months']
        self.assertEqual(list(sorted(months)),['2026-02','2026-03'])
        self.assertEqual((months['2026-02']['rows'],months['2026-03']['rows']),(2*96,int(1.5*96)))
        self.assertEqual(months['2026-03']['quality']['first_bar'],'2026-03-01T00:00:00+00:00')


class RejectionTests(Base):
    def stopped(self,mutate=None,fail_at=None,name='bad'):
        root,m,session,_=self.collect(name=name,mutate=mutate,fail_at=fail_at);return root,m,session
    def test_duplicate_reversed_offgrid_or_foreign_rows_stop_without_repair(self):
        cases={'P36_DUPLICATE_CANDLE':lambda n,r:r[:-1]+[r[-2]] if n==3 else r,
            'P36_UNORDERED_PAGE':lambda n,r:list(reversed(r)) if n==3 else r,
            'P36_OUT_OF_WINDOW_OR_GRID':lambda n,r:[{**x,'candle_date_time_utc':'2026-04-01T00:30:00'} if i==0 else x for i,x in enumerate(r)] if n==3 else r,
            'P36_MARKET_OR_UNIT_MISMATCH':lambda n,r:[{**x,'market':'KRW-DOGE'} for x in r] if n==3 else r,
            'P36_INVALID_RANGE':lambda n,r:[{**x,'high_price':1} for x in r] if n==3 else r,
            'P36_INVALID_NUMBER':lambda n,r:[{**x,'trade_price':-1} for x in r] if n==3 else r,
            'P36_INVALID_PAGE':lambda n,r:r*2 if n==3 else r}
        for i,(code,mutate) in enumerate(cases.items()):
            with self.subTest(code=code):
                root,m,session=self.stopped(mutate=mutate,name=f'b{i}')
                self.assertEqual((m['status'],m['failure_reason']),('STOPPED_RESUME_MANUALLY',code));self.assertEqual(len(session.calls),3)
                for v in m['series'].values():
                    for r in v['months'].values():self.assertTrue((root/r['file']).is_file())
                self.assertFalse(list(root.rglob('*.pending')))

    def test_rate_limit_http_error_and_network_failure_stop_without_retry(self):
        for i,(fail,code) in enumerate(((429,'P36_RATE_LIMITED'),(503,'P36_HTTP_ERROR'),(OSError('down'),'P36_REQUEST_FAILED'))):
            with self.subTest(code=code):
                _,m,session=self.stopped(fail_at=(5,fail),name=f'h{i}')
                self.assertEqual((m['failure_reason'],len(session.calls)),(code,5))  # Exactly one attempt; no automatic retry.

    def test_server_date_must_match_local_clock(self):
        session=FakeSession(clock=lambda:NOW.timestamp()+30);tr,_=transport(session)
        m=p36.collect(self.root/'clock',connect_public=True,transport=tr,clock=lambda:NOW.timestamp())
        self.assertEqual(m['failure_reason'],'P36_CLOCK_UNVERIFIED')

    def test_request_cap_blocks_and_is_never_refilled_by_resume(self):
        with patch.object(p36,'MAX_REQUESTS',7):
            root,m,session,_=self.collect(name='cap')
            self.assertEqual((m['status'],m['failure_reason'],m['requests'],len(session.calls)),
                ('REQUEST_CAP_REACHED_FULL_HISTORY_NOT_CONFIRMED','P36_REQUEST_CAP_REACHED',7,7))
            self.assertIn('series_full=',p36.summary(m)[0])
            tr,_=transport(FakeSession())
            with self.assertRaisesRegex(p36.P36Error,'P36_REQUEST_CAP_REACHED'):p36.resume(root,connect_public=True,transport=tr)


class ResumeTests(Base):
    def test_manual_resume_after_interrupt_matches_uninterrupted_bytes(self):
        ref_root,ref,_,_=self.collect(name='ref')
        root,m,session,_=self.collect(name='cut',fail_at=(40,KeyboardInterrupt()))
        self.assertEqual((m['status'],m['failure_reason']),('INTERRUPTED_RESUME_MANUALLY','P36_INTERRUPTED'))
        r=p36.resume(root);self.assertEqual(r['status'],'VERIFIED_RESUMABLE_OFFLINE')  # Offline: verify only.
        tr,_=transport(FakeSession());done=p36.resume(root,connect_public=True,transport=tr,clock=lambda:NOW.timestamp())
        self.assertEqual((done['status'],done['runs']),('COMPLETE_EARLIEST_REACHED',2))
        for key,series in ref['series'].items():
            self.assertEqual({mo:r['file_sha256'] for mo,r in done['series'][key]['months'].items()},
                {mo:r['file_sha256'] for mo,r in series['months'].items()})
        self.assertEqual(p36.resume(root)['status'],'COMPLETE_EARLIEST_REACHED')

    def test_resume_blocks_on_hash_layout_or_plan_mismatch(self):
        root,m,_,_=self.collect(name='cut',fail_at=(40,KeyboardInterrupt()))
        record=next(r for v in m['series'].values() for r in v['months'].values())
        path=root/record['file'];saved=path.read_bytes();path.write_bytes(saved+b'x')
        with self.assertRaisesRegex(p36.P36Error,'P36_RESUME_HASH_MISMATCH'):p36.resume(root)
        path.write_bytes(saved);manifest=json.loads((root/'manifest.json').read_text())
        bad=json.loads(json.dumps(manifest));bad['plan']['holdout_start']='2026-04-01T00:00:00+00:00';p36.atomic_json(root/'manifest.json',bad)
        with self.assertRaisesRegex(p36.P36Error,'P36_RESUME_LAYOUT_MISMATCH|P36_PLAN_CHANGED'):p36.resume(root)
        bad=json.loads(json.dumps(manifest));bad['plan']['max_requests']=10**9;p36.atomic_json(root/'manifest.json',bad)
        with self.assertRaisesRegex(p36.P36Error,'P36_PLAN_CHANGED'):p36.resume(root)
        p36.atomic_json(root/'manifest.json',manifest);self.assertEqual(p36.resume(root)['status'],'VERIFIED_RESUMABLE_OFFLINE')


class BoundaryTests(unittest.TestCase):
    def test_frozen_p22_p33_sources_untouched_and_no_existing_snapshot_read(self):
        from backtesting import p33_forward as p33
        from tools import p22_isolated as iso
        p33.protocol();self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])
        source=(ROOT/'backtesting/p36_history.py').read_text(encoding='utf-8')
        for word in ('research/2026-10-21','load_csv(','protocol.json'):self.assertNotIn(word,source)


if __name__=='__main__':unittest.main()
