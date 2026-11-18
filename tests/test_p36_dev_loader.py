"""P36 development loader: synthetic snapshots only (fake HTTP collector), no network, no writes, no holdout reads."""
import builtins,gzip,io,json,os,pathlib,socket,unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from backtesting import p36_dev_loader as loader
from backtesting import p36_history as p36
from tests import test_p36_history as t36

START,END='2026-01-01T00:00:00+00:00','2026-05-01T00:00:00+00:00'  # Development partition of the synthetic plan.


def tree(root):
    return {str(p.relative_to(root)):(p.read_bytes(),p.stat().st_mtime_ns) for p in sorted(Path(root).rglob('*')) if p.is_file()}


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.root=Path(self.t.name).resolve()
        self.guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No network'));self.guard.start()
    def tearDown(self):self.guard.stop();self.t.cleanup()
    def snap(self,name='snap',data=None,**kw):
        session=t36.FakeSession(data=data,**kw);tr,_=t36.transport(session)
        m=p36.collect(self.root/name,connect_public=True,transport=tr,clock=lambda:t36.NOW.timestamp())
        return self.root/name,m
    def only(self,**series):
        data={k:[] for k in t36.default_data()};data.update(series);return data
    def manifest_edit(self,root,change):
        m=json.loads((root/'manifest.json').read_text());change(m);(root/'manifest.json').write_text(json.dumps(m))


class LoadTests(Snapshot):
    def test_returns_exactly_the_stored_development_window_in_ascending_order(self):
        root,m=self.snap();self.assertEqual(m['status'],'COMPLETE_API_BOUNDARY_REACHED')
        frame,report=loader.load_development(root,'KRW-BTC',60,START,END)
        source=[t for t in t36.default_data()[('KRW-BTC',60)] if pd.Timestamp(START)<=t<pd.Timestamp(END)]
        self.assertEqual(list(frame.index),source);self.assertTrue(frame.index.is_monotonic_increasing)
        self.assertEqual(list(frame.columns),['open','high','low','close','volume'])
        self.assertEqual((report['status'],report['rows'],report['missing_bars'],report['holdout_read']),('LOADED',len(source),2,False))
        self.assertEqual(report['first_observed_bar'],'2026-01-10T00:00:00+00:00');self.assertTrue(report['before_first_observed'])
        self.assertIn('not a data-quality pass',report['note'])

    def test_month_boundaries_and_partial_windows(self):
        root,_=self.snap()
        frame,report=loader.load_development(root,'KRW-ETH',60,'2026-02-28T22:00:00+00:00','2026-03-01T02:00:00+00:00')
        self.assertEqual([t.isoformat() for t in frame.index],['2026-02-28T22:00:00+00:00','2026-02-28T23:00:00+00:00',
            '2026-03-01T00:00:00+00:00','2026-03-01T01:00:00+00:00'])
        self.assertEqual((report['missing_bars'],report['empty_months']),(0,[]))
        frame,_=loader.load_development(root,'KRW-BTC',1,'2026-04-30T09:58:00+00:00','2026-04-30T10:03:00+00:00')
        self.assertNotIn(pd.Timestamp('2026-04-30T10:00:00Z'),frame.index);self.assertEqual(len(frame),4)  # One missing minute, not filled.

    def test_missing_months_and_bars_are_reported_never_filled(self):
        data=self.only();data[('KRW-BTC',60)]=t36.bars(60,'2026-02-10T00:00:00Z','2026-02-10T05:00:00Z')+t36.bars(60,'2026-04-05T00:00:00Z','2026-04-05T03:00:00Z')
        root,m=self.snap(data=data);self.assertEqual(m['status'],'COMPLETE_API_BOUNDARY_REACHED')
        frame,report=loader.load_development(root,'KRW-BTC',60,START,END)
        self.assertEqual(len(frame),8);self.assertEqual(report['empty_months'],['2026-03'])
        self.assertFalse(((frame.index>=pd.Timestamp('2026-03-01',tz='UTC'))&(frame.index<pd.Timestamp('2026-04-01',tz='UTC'))).any())
        self.assertEqual(report['missing_bars'],report['expected_rows_from_first_observed']-8)
        self.assertEqual(report['first_observed_bar'],'2026-02-10T00:00:00+00:00')

    def test_empty_series_is_not_data_or_continuity(self):
        root,_=self.snap(data=self.only())
        frame,report=loader.load_development(root,'KRW-SOL',15,START,END)
        self.assertEqual((len(frame),report['status'],report['series_status'],report['rows']),(0,'NO_DEVELOPMENT_ROWS','NO_ROWS_RETURNED',0))
        data=self.only();data[('KRW-SOL',60)]=t36.bars(60,'2026-07-02T00:00:00Z','2026-07-03T00:00:00Z')  # Holdout-only series.
        root,_=self.snap(name='hold-only',data=data)
        frame,report=loader.load_development(root,'KRW-SOL',60,START,END);self.assertEqual((len(frame),report['status']),(0,'NO_DEVELOPMENT_ROWS'))


class HoldoutIsolationTests(Snapshot):
    def watch(self):
        seen=[];real={'open':builtins.open,'read_bytes':pathlib.Path.read_bytes,'gzip':gzip.open,'scandir':os.scandir,'listdir':os.listdir,'osopen':os.open}
        def note(p):seen.append(str(p))
        def b_open(file,*a,**k):note(file);return real['open'](file,*a,**k)
        def rb(self):note(self);return real['read_bytes'](self)
        def g(file,*a,**k):note(file);return real['gzip'](file,*a,**k)
        def sd(path='.'):note(path);return real['scandir'](path)
        def ld(path='.'):note(path);return real['listdir'](path)
        def oo(path,*a,**k):note(path);return real['osopen'](path,*a,**k)
        ps=[patch('builtins.open',b_open),patch.object(pathlib.Path,'read_bytes',rb),patch('gzip.open',g),patch('os.scandir',sd),patch('os.listdir',ld),patch('os.open',oo)]
        for x in ps:x.start()
        self.addCleanup(lambda:[x.stop() for x in reversed(ps)])
        return seen

    def test_holdout_files_are_never_opened_listed_or_hashed(self):
        root,_=self.snap();self.assertTrue(list((root/'holdout').rglob('*.csv.gz')))
        before=tree(root);seen=self.watch()
        for market in p36.MARKETS:
            for unit in p36.UNITS:loader.load_development(root,market,unit,START,END)  # Up to the boundary.
        for end in ('2026-05-01T01:00:00+00:00','2026-11-01T00:00:00+00:00'):
            with self.subTest(end=end),self.assertRaisesRegex(loader.LoaderError,'P36D_HOLDOUT_OVERLAP'):
                loader.load_development(root,'KRW-BTC',60,'2026-04-30T00:00:00+00:00',end)  # Crossing the boundary.
        with self.assertRaisesRegex(loader.LoaderError,'P36D_HOLDOUT_OVERLAP'):loader.load_development(root,'KRW-BTC',60,'2026-06-01T00:00:00+00:00','2026-07-01T00:00:00+00:00')
        self.assertTrue(any('/development/' in s for s in seen))
        self.assertFalse([s for s in seen if 'holdout' in s])
        self.assertEqual(tree(root),before)  # No write, lock or temp file in the snapshot.

    def test_overlap_is_refused_before_any_candle_file_is_opened(self):
        root,_=self.snap();seen=self.watch()
        with self.assertRaisesRegex(loader.LoaderError,'P36D_HOLDOUT_OVERLAP'):loader.load_development(root,'KRW-BTC',60,START,'2026-05-02T00:00:00+00:00')
        self.assertFalse([s for s in seen if s.endswith('.csv.gz')])


class RejectionTests(Snapshot):
    def rewrite(self,root,month,text,key='KRW-BTC-60',unit=60,market='KRW-BTC',sync=True):
        """Replace one month file with crafted CSV text; optionally re-sync the recorded SHA so content checks run."""
        path=root/p36.month_file(market,unit,month,'development');raw=gzip.compress(text.encode(),mtime=0);path.write_bytes(raw)
        if sync:self.manifest_edit(root,lambda m:m['series'][key]['months'][month].update(file_sha256=p36.sha(raw)))
        return path
    def text(self,root,month='2026-03'):
        return gzip.decompress((root/p36.month_file('KRW-BTC',60,month,'development')).read_bytes()).decode()

    def test_tampered_file_hash_row_count_header_ohlcv_order_and_grid_are_refused(self):
        root,_=self.snap();good=self.text(root);lines=good.strip('\n').split('\n')
        def case(code,text=None,edit=None,sync=True):
            with self.subTest(code=code):
                path=root/p36.month_file('KRW-BTC',60,'2026-03','development');saved=path.read_bytes();manifest=(root/'manifest.json').read_bytes()
                if text is not None:self.rewrite(root,'2026-03',text,sync=sync)
                if edit:self.manifest_edit(root,edit)
                with self.assertRaisesRegex(loader.LoaderError,code):loader.load_development(root,'KRW-BTC',60,START,END)
                path.write_bytes(saved);(root/'manifest.json').write_bytes(manifest)
        case('P36D_HASH_MISMATCH',text=good+'\n',sync=False)
        case('P36D_ROW_COUNT_MISMATCH',edit=lambda m:m['series']['KRW-BTC-60']['months']['2026-03'].update(rows=10))
        case('P36D_HEADER_INVALID',text=good.replace('timestamp,open,high,low,close,volume','timestamp,close,high,low,open,volume',1))
        bad=lines[1].split(',');bad[2]=str(float(bad[4])-10)  # high below low.
        case('P36D_OHLCV_INVALID',text='\n'.join([lines[0],','.join(bad),*lines[2:]])+'\n')
        case('P36D_DUPLICATE_OR_UNORDERED',text='\n'.join([lines[0],*reversed(lines[1:])])+'\n')
        case('P36D_DUPLICATE_OR_UNORDERED',text='\n'.join([lines[0],lines[1],lines[1],*lines[3:]])+'\n')
        off=lines[1].replace(':00:00+00:00',':30:00+00:00',1)
        case('P36D_GRID_OR_MONTH_INVALID',text='\n'.join([lines[0],off,*lines[2:]])+'\n')
        case('P36D_TIMESTAMP_INVALID',text='\n'.join([lines[0],lines[1].replace('+00:00','',1),*lines[2:]])+'\n')
        case('P36D_QUALITY_MISMATCH',edit=lambda m:m['series']['KRW-BTC-60']['months']['2026-03']['quality'].update(first_bar='2026-03-02T00:00:00+00:00'))
        case('P36D_RECORD_INVALID',edit=lambda m:m['series']['KRW-BTC-60']['months']['2026-03'].update(file='development/KRW-BTC/60m/../../holdout/x.csv.gz'))
        self.assertEqual(loader.load_development(root,'KRW-BTC',60,START,END)[1]['status'],'LOADED')  # Restored.

    def test_incomplete_or_inconsistent_snapshots_are_refused(self):
        root,m=self.snap(name='cut',fail_at=(30,KeyboardInterrupt()));self.assertEqual(m['status'],'INTERRUPTED_RESUME_MANUALLY')
        with self.assertRaisesRegex(loader.LoaderError,'P36D_SNAPSHOT_NOT_COMPLETE'):loader.load_development(root,'KRW-BTC',60,START,END)
        root,_=self.snap()
        for change,code in ((lambda m:m['plan'].update(holdout_start='2026-08-01T00:00:00+00:00'),'P36D_PLAN_INVALID'),
                (lambda m:m.update(requests=m['requests']+1),'P36D_COMPLETION_UNVERIFIED'),
                (lambda m:m['series'].pop('KRW-SOL-1'),'P36D_MANIFEST_INVALID'),
                (lambda m:m['series']['KRW-BTC-60'].update(responses=0),'P36D_COMPLETION_UNVERIFIED'),
                (lambda m:m.update(schema='OTHER'),'P36D_PLAN_INVALID')):
            saved=(root/'manifest.json').read_bytes();self.manifest_edit(root,change)
            with self.subTest(code=code),self.assertRaisesRegex(loader.LoaderError,code):loader.load_development(root,'KRW-BTC',60,START,END)
            (root/'manifest.json').write_bytes(saved)

    def test_invalid_window_series_paths_and_symlinks_are_refused(self):
        root,_=self.snap()
        for args,code in ((('KRW-DOGE',60,START,END),'P36D_SERIES_INVALID'),(('KRW-BTC',5,START,END),'P36D_SERIES_INVALID'),
                (('KRW-BTC',60,END,START),'P36D_WINDOW_INVALID'),(('KRW-BTC',60,'2026-01-01T00:30:00+00:00',END),'P36D_WINDOW_INVALID'),
                (('KRW-BTC',60,'2026-01-01T00:00:00',END),'P36D_WINDOW_INVALID')):
            with self.subTest(args=args),self.assertRaisesRegex(loader.LoaderError,code):loader.load_development(root,*args)
        outside=self.root/'outside';outside.mkdir();(outside/'paper-state.json').write_text('{"keep":1}');keep=tree(outside)
        with self.assertRaisesRegex(loader.LoaderError,'P36D_ROOT_FORBIDDEN'):loader.load_development(outside,'KRW-BTC',60,START,END)
        link=self.root/'snap-link';link.symlink_to(root,target_is_directory=True)
        with self.assertRaisesRegex(loader.LoaderError,'P36D_ROOT_FORBIDDEN'):loader.load_development(link,'KRW-BTC',60,START,END)
        real=root/'development'/'KRW-BTC';moved=self.root/'moved-btc';real.rename(moved);real.symlink_to(moved,target_is_directory=True)
        with self.assertRaisesRegex(loader.LoaderError,'P36D_PATH_FORBIDDEN'):loader.load_development(root,'KRW-BTC',60,START,END)
        self.assertEqual(tree(outside),keep)


class ReviewRegressionTests(Snapshot):
    """Review 5951924433 plus the same-class gaps found while fixing it."""
    def test_budget_above_the_plan_cap_is_refused_even_when_both_records_agree(self):
        root,m=self.snap(data={**self.only(),('KRW-BTC',60):t36.bars(60,'2026-03-01T00:00:00Z','2026-03-01T03:00:00Z')})
        over=p36.MAX_REQUESTS+1;extra=over-m['requests']
        self.manifest_edit(root,lambda x:x.update(requests=over,unconfirmed_requests=x['unconfirmed_requests']+extra))
        (root/'budget.json').write_text(json.dumps({'schema':p36.SCHEMA,'requests':over}))
        with self.assertRaisesRegex(loader.LoaderError,'P36D_BUDGET_INVALID'):loader.load_development(root,'KRW-BTC',60,START,END)
        (root/'budget.json').write_text(json.dumps({'schema':p36.SCHEMA,'requests':p36.MAX_REQUESTS}))
        self.manifest_edit(root,lambda x:x.update(requests=p36.MAX_REQUESTS,unconfirmed_requests=x['unconfirmed_requests']-1))
        self.assertEqual(loader.load_development(root,'KRW-BTC',60,START,END)[1]['status'],'LOADED')  # Exactly at the cap: allowed.

    def test_unverified_first_observed_bar_outside_the_window_is_refused(self):
        data={**self.only(),('KRW-BTC',60):[pd.Timestamp('2026-01-15T00:00:00Z'),pd.Timestamp('2026-03-01T00:00:00Z')]}
        root,m=self.snap(data=data);self.assertEqual(m['status'],'COMPLETE_API_BOUNDARY_REACHED')
        frame,report=loader.load_development(root,'KRW-BTC',60,'2026-03-01T00:00:00+00:00','2026-03-01T01:00:00+00:00')
        self.assertEqual((report['rows'],report['missing_bars'],report['first_observed_bar']),(1,0,'2026-01-15T00:00:00+00:00'))
        def forge(x):  # The reviewer's case: CSV untouched, only the out-of-window record and boundary rewritten.
            x['series']['KRW-BTC-60']['months']['2026-01']['quality']['first_bar']='2026-04-01T00:00:00+00:00'
            x['series']['KRW-BTC-60']['boundary_to']='2026-04-01T00:00:00+00:00'
        self.manifest_edit(root,forge)
        with self.assertRaisesRegex(loader.LoaderError,'P36D_QUALITY_MISMATCH'):
            loader.load_development(root,'KRW-BTC',60,'2026-03-01T00:00:00+00:00','2026-03-01T01:00:00+00:00')

    def test_every_recorded_quality_value_is_recomputed_even_with_a_valid_hash(self):
        root,_=self.snap()
        for field,value in (('expected_rows',1),('missing_bars',0),('longest_gap_bars',0),('dataset_sha256','0'*64),('rows',1)):
            saved=(root/'manifest.json').read_bytes()
            self.manifest_edit(root,lambda x:x['series']['KRW-BTC-60']['months']['2026-03']['quality'].update({field:value}))
            with self.subTest(field=field),self.assertRaisesRegex(loader.LoaderError,'P36D_QUALITY_MISMATCH'):
                loader.load_development(root,'KRW-BTC',60,START,END)
            (root/'manifest.json').write_bytes(saved)
        self.manifest_edit(root,lambda x:x['series']['KRW-BTC-60']['months']['2026-03']['quality'].update(expected_rows=1,missing_bars=0,longest_gap_bars=0))
        with self.assertRaisesRegex(loader.LoaderError,'P36D_QUALITY_MISMATCH'):loader.load_development(root,'KRW-BTC',60,START,END)

    def test_earliest_flag_boundary_and_month_keys_must_be_consistent(self):
        root,_=self.snap()
        for change,code in ((lambda x:x['series']['KRW-BTC-60']['months']['2026-03'].update(earliest_observed_month=True),'P36D_RECORD_INVALID'),
                (lambda x:x['series']['KRW-BTC-60']['months'].update({'2026-13':x['series']['KRW-BTC-60']['months']['2026-03']}),'P36D_RECORD_INVALID'),
                (lambda x:x['series']['KRW-BTC-60'].update(boundary_to='2026-01-10T01:00:00+00:00'),'P36D_COMPLETION_UNVERIFIED|P36D_RECORD_INVALID')):
            saved=(root/'manifest.json').read_bytes();self.manifest_edit(root,change)
            with self.subTest(code=code),self.assertRaisesRegex(loader.LoaderError,code):loader.load_development(root,'KRW-BTC',60,START,END)
            (root/'manifest.json').write_bytes(saved)
        self.assertEqual(loader.load_development(root,'KRW-BTC',60,START,END)[1]['missing_bars'],2)

class BoundaryTests(unittest.TestCase):
    def test_loader_never_calls_full_verify_or_writers(self):
        source=(Path(loader.__file__)).read_text(encoding='utf-8')
        for name in ('verify(','atomic_json','write_new','Lock(','Budget(','collect(','resume(','Transport('):
            self.assertNotIn(name,source.split('"""',2)[2])
        from tools import p22_isolated as iso
        from backtesting import p33_forward as p33
        p33.protocol();self.assertEqual(iso.working_tree_drift(),['ops/paper_experiment.py'])


if __name__=='__main__':unittest.main()
