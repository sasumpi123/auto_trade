"""Generate a synthetic P22 collection + analysis with the UNCHANGED frozen P22 code.

Run only through tools.p22_isolated.run([...], module='tests.p30_fixture_gen'), which builds the
registered tree and verifies all pins; frozen() is executed for real. Exactly like the original P22
tests, only the wall clock is moved (future collection) and a fake REST transport is injected.
Never real market data; never used by any CLI.
"""
import json
import os
import socket
import sys
import time
from pathlib import Path
from unittest.mock import Mock,patch

import pandas as pd

from backtesting import p22_forward as p
from backtesting.data import demo_candles
from tests.test_p22_forward import FuturePandas


def factory_for(complete):
    def factory(day):
        frame=demo_candles(1440);frame.index=pd.date_range(p.day_start(day),periods=1440,freq='min')
        class FakeRest:
            def get(self,endpoint,params):
                f=frame if (day,params['market']) in complete else frame.drop(frame.index[100])
                f=f.loc[f.index<pd.Timestamp(params['to'])].iloc[-params['count']:]
                return [{'market':params['market'],'unit':1,'candle_date_time_utc':t.tz_localize(None).isoformat(),
                    'opening_price':float(r.open),'high_price':float(r.high),'low_price':float(r.low),
                    'trade_price':float(r.close),'candle_acc_trade_volume':float(r.volume)} for t,r in f.iloc[::-1].iterrows()]
            def close(self):pass
        return FakeRest()
    return factory


def main(argv):
    if os.environ.get('P22_ISOLATED_TREE')!='1' or len(argv)!=1:
        print('ISOLATED_TREE_REQUIRED');return 2
    out=Path(argv[0]).resolve()
    p.frozen()  # Real verification of the registered tree; never patched.
    started=time.monotonic();future=p.END+pd.Timedelta(hours=1)
    def clock():return future.timestamp()+time.monotonic()-started
    loader=p.minute.load_partitioned_sample
    def synthetic(path,**kwargs):return loader(path,allow_synthetic=True,**kwargs)
    with patch.object(socket.socket,'connect',side_effect=AssertionError('No actual network')):
        collection=p.collect(out/'collection',connect_public=True,clock=clock,
            client_factory=factory_for({(p.DAYS[0],'KRW-XRP')}))
        with patch.object(p,'utc_now',return_value=future+pd.Timedelta(hours=1)),patch.object(p.minute,'pd',FuturePandas()),\
                patch.object(p.minute,'load_partitioned_sample',side_effect=synthetic):
            analysis=p.base.fresh(out/'analysis');p.work(out/'collection',analysis,Mock())
            report=p.p20.read_results(analysis/'results.json');report['elapsed_seconds']=0.0;p.write_report(analysis,report)
    print(json.dumps({'collection':collection['status'],'analysis':report['status'],'pairs':len(report['cases'])}))
    return 0 if report['status']=='VERIFIED_PARTIAL' else 2


if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
