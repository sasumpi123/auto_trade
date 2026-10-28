"""Bounded unauthenticated Upbit GETs only. No account/order/client credentials."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from email.utils import parsedate_to_datetime
import math
import re
import time

import requests

MARKET = re.compile(r'KRW-[A-Z0-9]{1,20}')

PUBLIC_ERROR_CODES = frozenset({
    'PUBLIC_CANDLE_GAP',
    'PUBLIC_CANDLE_ORDER',
    'PUBLIC_CANDLE_RANGE',
    'PUBLIC_DEADLINE',
    'PUBLIC_DUPLICATE_JSON_KEY',
    'PUBLIC_DUPLICATE_OR_UNKNOWN_MARKET',
    'PUBLIC_ENDPOINT_FORBIDDEN',
    'PUBLIC_HTTP_ERROR',
    'PUBLIC_INCOMPLETE_CANDLES',
    'PUBLIC_INSUFFICIENT_MARKETS',
    'PUBLIC_INVALID_CANDLE',
    'PUBLIC_INVALID_CANDLE_TIME',
    'PUBLIC_INVALID_LIMIT',
    'PUBLIC_INVALID_MARKET',
    'PUBLIC_INVALID_NUMBER',
    'PUBLIC_INVALID_REGISTRY',
    'PUBLIC_INVALID_RESPONSE',
    'PUBLIC_INVALID_TICKER',
    'PUBLIC_MISSING_MARKET',
    'PUBLIC_NONFINITE_JSON',
    'PUBLIC_RATE_LIMIT',
    'PUBLIC_REQUEST_BUDGET',
    'PUBLIC_REQUEST_FAILED',
    'PUBLIC_RESPONSE_TOO_LARGE',
    'PUBLIC_STALE_CANDLE',
    'PUBLIC_STALE_TICKER',
    'PUBLIC_RESPONSE_CLOCK_UNVERIFIED',
    'PUBLIC_RANKING_EXPIRED',
    'PUBLIC_STOP_REQUESTED',
})


class PublicDataError(ValueError):
    """Fixed codes only; never includes URLs, response bodies or raw exceptions."""


def positive(value, *, zero=False):
    if type(value) not in (int, float, str, Decimal):
        raise PublicDataError('PUBLIC_INVALID_NUMBER')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or not math.isfinite(float(result)) or result < 0 or (result == 0 and not zero):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, OverflowError):
        raise PublicDataError('PUBLIC_INVALID_NUMBER') from None


class PublicOnlyAuth(requests.auth.AuthBase):
    def __call__(self, request):
        # Truthy AuthBase prevents requests' ambient netrc lookup. Preserve proxy routing.
        request.headers.pop('Authorization', None)
        request.headers.pop('Cookie', None)
        return request


class PublicMarketClient:
    def __init__(self, *, session=None, clock=time.time, monotonic=time.monotonic,
                 sleep=time.sleep, deadline=None, stopped=lambda:False, max_calls=None):
        self.session = session if session is not None else requests.Session()
        self.clock, self.monotonic, self.sleep = clock, monotonic, sleep
        self.deadline, self.stopped = deadline, stopped
        self.last_request = None
        self.calls = 0
        self.max_calls = max_calls

    def remaining(self):
        if self.stopped():raise PublicDataError('PUBLIC_STOP_REQUESTED')
        left = 5 if self.deadline is None else min(5, self.deadline-self.monotonic())
        if left <= 0:raise PublicDataError('PUBLIC_DEADLINE')
        return left

    def get(self, endpoint, params):
        if endpoint not in ('market/all','ticker','candles/minutes/1'):
            raise PublicDataError('PUBLIC_ENDPOINT_FORBIDDEN')
        try:
            if self.max_calls is not None and self.calls>=self.max_calls:
                raise PublicDataError('PUBLIC_REQUEST_BUDGET')
            if self.last_request is not None:
                delay = max(0, .2-(self.monotonic()-self.last_request))
                if delay:
                    self.remaining();self.sleep(min(delay,self.remaining()))
            timeout = self.remaining()
            self.last_request = self.monotonic();self.calls += 1
            response_started=self.monotonic();wall_started=self.clock()
            with self.session.get('https://api.upbit.com/v1/'+endpoint, params=params,
                    auth=PublicOnlyAuth(), timeout=timeout, allow_redirects=False, stream=True) as response:
                if response.status_code == 429:raise PublicDataError('PUBLIC_RATE_LIMIT')
                if response.status_code != 200:raise PublicDataError('PUBLIC_HTTP_ERROR')
                # HTTP response generation is distinct from a market's last update.
                # Never infer a fresh snapshot from one liquid market's timestamp.
                try:
                    server=parsedate_to_datetime(response.headers.get('Date',''))
                    if server.tzinfo is None or abs(self.clock()-server.timestamp())>5:
                        raise ValueError()
                    if endpoint=='candles/minutes/1' and 'to' in params:
                        boundary=datetime.fromisoformat(params['to'].replace('Z','+00:00'))
                        if boundary.tzinfo is None or boundary.timestamp()>server.timestamp():
                            raise ValueError()  # A small local lead must not cross the exchange's minute boundary.
                except Exception:
                    raise PublicDataError('PUBLIC_RESPONSE_CLOCK_UNVERIFIED') from None
                content=bytearray()
                for chunk in response.iter_content(chunk_size=16384):
                    self.remaining();content.extend(chunk)
                    if len(content)>2_000_000:raise PublicDataError('PUBLIC_RESPONSE_TOO_LARGE')
                def unique(pairs):
                    obj={}
                    for k,v in pairs:
                        if k in obj:raise PublicDataError('PUBLIC_DUPLICATE_JSON_KEY')
                        obj[k]=v
                    return obj
                result=json.loads(content,object_pairs_hook=unique,parse_constant=lambda _: (_ for _ in ()).throw(PublicDataError('PUBLIC_NONFINITE_JSON')))
            elapsed=self.monotonic()-response_started
            if elapsed<0 or elapsed>5 or abs((self.clock()-wall_started)-elapsed)>5 or abs(self.clock()-server.timestamp())>5:
                raise PublicDataError('PUBLIC_RESPONSE_CLOCK_UNVERIFIED')
            if not isinstance(result,list) or not 1<=len(result)<=2000:
                raise PublicDataError('PUBLIC_INVALID_RESPONSE')
            return result
        except PublicDataError:raise
        except Exception:raise PublicDataError('PUBLIC_REQUEST_FAILED') from None

    def close(self):self.session.close()

    def top_tickers(self, limit=10):
        if type(limit) is not int or not 1<=limit<=100:
            raise PublicDataError('PUBLIC_INVALID_LIMIT')
        registry=self.get('market/all',{'is_details':'false'})
        codes=[]
        for row in registry:
            code=row.get('market') if isinstance(row,dict) else None
            if not isinstance(code,str) or not re.fullmatch(r'[A-Z0-9]{1,10}-[A-Z0-9]{1,20}',code) or code in codes:
                raise PublicDataError('PUBLIC_INVALID_REGISTRY')
            codes.append(code)
        markets=[c for c in codes if MARKET.fullmatch(c)]
        if len(markets)<limit:raise PublicDataError('PUBLIC_INSUFFICIENT_MARKETS')
        ranks={}; stamps={}
        scan_started=self.monotonic(); wall_started=self.clock()
        for start in range(0,len(markets),100):
            batch=markets[start:start+100]
            rows=self.get('ticker',{'markets':','.join(batch)})
            seen=set()
            for row in rows:
                if not isinstance(row,dict):raise PublicDataError('PUBLIC_INVALID_TICKER')
                code=row.get('market')
                if not isinstance(code,str) or code not in batch or code in seen:
                    raise PublicDataError('PUBLIC_DUPLICATE_OR_UNKNOWN_MARKET')
                timestamp=row.get('timestamp')
                if type(timestamp) is not int or timestamp<=0 or timestamp>self.clock()*1000+5000:
                    raise PublicDataError('PUBLIC_STALE_TICKER')
                trade_stamp=row.get('trade_timestamp')
                if type(trade_stamp) is not int or trade_stamp<=0 or trade_stamp>self.clock()*1000+5000:
                    raise PublicDataError('PUBLIC_STALE_TICKER')
                stamps[code]=(timestamp,trade_stamp)
                positive(row.get('trade_price'))
                ranks[code]=positive(row.get('acc_trade_price_24h'),zero=True)
                seen.add(code)
            if seen!=set(batch):raise PublicDataError('PUBLIC_MISSING_MARKET')
        # Rank the COMPLETE universe before applying freshness; do not filter old
        # rows and silently substitute lower-volume markets for a stale winner.
        selected=sorted(ranks,key=lambda c:(-ranks[c],c))[:limit]
        elapsed=self.monotonic()-scan_started
        if elapsed<0 or elapsed>30 or abs((self.clock()-wall_started)-elapsed)>5:
            raise PublicDataError('PUBLIC_RANKING_EXPIRED')
        if any(not self.clock()*1000-300_000<=stamp<=self.clock()*1000+5000
               for c in selected for stamp in stamps[c]):
            raise PublicDataError('PUBLIC_STALE_TICKER')
        return selected

    def candles(self, market):
        if not isinstance(market,str) or not MARKET.fullmatch(market):
            raise PublicDataError('PUBLIC_INVALID_MARKET')
        import pandas as pd
        # Upbit candle timestamps are UTC opens; `to` excludes that instant.
        try:cutoff=pd.Timestamp(float(positive(self.clock())),unit='s',tz='UTC').floor('min')
        except Exception:raise PublicDataError('PUBLIC_RESPONSE_CLOCK_UNVERIFIED') from None
        rows=self.get('candles/minutes/1',{'market':market,'count':200,'to':cutoff.isoformat()})
        if len(rows)!=200:raise PublicDataError('PUBLIC_INCOMPLETE_CANDLES')
        values=[];timestamps=[]
        for row in rows:
            if not isinstance(row,dict) or row.get('market')!=market or row.get('unit')!=1:
                raise PublicDataError('PUBLIC_INVALID_CANDLE')
            try:t=pd.Timestamp(row['candle_date_time_utc'])
            except Exception:raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME') from None
            if pd.isna(t) or (t.tzinfo is not None and t.utcoffset().total_seconds()!=0):
                raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME')
            if t.tzinfo is not None:t=t.tz_convert('UTC').tz_localize(None)
            if t.tz_localize('UTC')>=cutoff:
                raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME')
            if t.second or t.microsecond or t.nanosecond or not math.isfinite(t.tz_localize('UTC').timestamp()):
                raise PublicDataError('PUBLIC_INVALID_CANDLE_TIME')
            if not self.clock()-86400<=t.tz_localize('UTC').timestamp()<=self.clock()+5:
                raise PublicDataError('PUBLIC_STALE_CANDLE')
            nums=[positive(row.get(k),zero=k in ('candle_acc_trade_volume','candle_acc_trade_price')) for k in
                ('opening_price','high_price','low_price','trade_price','candle_acc_trade_volume','candle_acc_trade_price')]
            o,h,l,c,volume,value=nums
            if h<max(o,l,c) or l>min(o,h,c):raise PublicDataError('PUBLIC_CANDLE_RANGE')
            timestamps.append(t);values.append([float(n) for n in nums])
        if len(set(timestamps))!=200 or any(a<=b for a,b in zip(timestamps,timestamps[1:])):
            raise PublicDataError('PUBLIC_CANDLE_ORDER')
        if any((a-b).total_seconds()!=60 for a,b in zip(timestamps,timestamps[1:])):
            raise PublicDataError('PUBLIC_CANDLE_GAP')
        if timestamps[0].tz_localize('UTC')!=cutoff-pd.Timedelta(minutes=1):
            raise PublicDataError('PUBLIC_CANDLE_GAP')
        if self.clock()-timestamps[0].tz_localize('UTC').timestamp()>180:
            raise PublicDataError('PUBLIC_STALE_CANDLE')
        # Preserve pyupbit's KST-naive indexing and existing indicator/strategy functions.
        index=pd.DatetimeIndex(timestamps[::-1]).tz_localize('UTC').tz_convert('Asia/Seoul').tz_localize(None)
        return pd.DataFrame(values[::-1],index=index,columns=['open','high','low','close','volume','value'])


def get_top_tickers(limit=10, *, client=None):
    owned=client is None
    client=client if client is not None else PublicMarketClient()
    try:return client.top_tickers(limit)
    finally:
        if owned:client.close()
