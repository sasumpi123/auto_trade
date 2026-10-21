"""Unauthenticated, GET-only KRW minute candle acquisition and audit trail."""
import hashlib
import json
from pathlib import Path
import time

import pandas as pd
import requests

from .data import dataset_hash, validate_candles

MARKETS = ('KRW-BTC', 'KRW-ETH', 'KRW-XRP', 'KRW-SOL')
UNITS = (15, 60)
FIELDS = {'opening_price': 'open', 'high_price': 'high', 'low_price': 'low',
          'trade_price': 'close', 'candle_acc_trade_volume': 'volume'}


def utc(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None or pd.isna(timestamp):
        raise ValueError('Explicit timezone required')
    return timestamp.tz_convert('UTC')


class PublicCandles:
    """No credentials, account endpoints, POSTs or implicit environment auth."""
    def __init__(self, session=None, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.sleep = sleep
        self.audit = []

    def page(self, market, unit, before, count=200):
        if market not in MARKETS or unit not in UNITS or not 1 <= count <= 200:
            raise ValueError('Unsupported market/unit/count')
        before = utc(before)
        url = f'https://api.upbit.com/v1/candles/minutes/{unit}'
        params = {'market': market, 'count': count, 'to': before.isoformat()}
        for attempt in range(5):
            self.sleep(0.26)  # Below documented candle group limit; shared-IP limits may be lower.
            try:
                response = self.session.get(url, params=params, timeout=30, allow_redirects=False)
            except requests.RequestException:
                if attempt == 4:
                    raise
                self.sleep(2 ** attempt)
                continue
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == 4:
                    response.raise_for_status()
                self.sleep(min(60, max(2 ** attempt, float(response.headers.get('Retry-After', 0)))))
                continue
            if response.status_code != 200:
                raise ValueError(f'Public candles HTTP {response.status_code}; stop acquisition')
            rows = response.json()
            if not isinstance(rows, list) or len(rows) > count:
                raise ValueError('Invalid candle response')
            stamps = []
            for row in rows:
                if row.get('market') != market or row.get('unit') != unit:
                    raise ValueError('Response market/unit mismatch')
                timestamp = pd.Timestamp(row['candle_date_time_utc']).tz_localize('UTC')
                if timestamp >= before or timestamp.value % pd.Timedelta(minutes=unit).value:
                    raise ValueError('Response cursor/grid mismatch')
                stamps.append(timestamp)
            if len(set(stamps)) != len(stamps) or stamps != sorted(stamps, reverse=True):
                raise ValueError('Response candles must be unique descending')
            canonical = json.dumps(rows, sort_keys=True, separators=(',', ':'), allow_nan=False)
            self.audit.append({'url': url, 'params': params, 'server_date': response.headers.get('Date'),
                               'rows': len(rows), 'response_sha256': hashlib.sha256(canonical.encode()).hexdigest(),
                               'fetched_at': pd.Timestamp.now(tz='UTC').isoformat()})
            return rows
        raise RuntimeError('Retries exhausted')

    def availability(self, market, unit, cutoff):
        """Find first obtainable candle to a one-day bracket, then exact candle.

        This probes existence before UTC days, not completeness of the entire history.
        """
        cutoff = utc(cutoff).floor('D')
        lower = pd.Timestamp('2017-01-01', tz='UTC')
        if self.page(market, unit, lower, 1):
            raise ValueError('Historical lower bound is not empty')
        latest = self.page(market, unit, cutoff, 1)
        if not latest:
            raise ValueError('No available history')
        upper = cutoff
        while upper - lower > pd.Timedelta(days=1):
            middle = lower + pd.Timedelta(days=(upper - lower).days // 2)
            if self.page(market, unit, middle, 1):
                upper = middle
            else:
                lower = middle
        rows = self.page(market, unit, upper)
        first = min(row['candle_date_time_utc'] for row in rows)
        if pd.Timestamp(first).tz_localize('UTC') < lower:
            raise ValueError('Availability bracket inconsistent')
        # At 15/60 minutes a single day has <=96 candles, below the 200 limit.
        return {'earliest_observed': first + '+00:00',
                'latest_completed_observed': latest[0]['candle_date_time_utc'] + '+00:00',
                'empty_before': lower.isoformat(), 'nonempty_before': upper.isoformat(),
                'full_history_continuity_verified': False}

    def collect(self, market, unit, start, end, *, now=None):
        start, end = utc(start), utc(end)
        now = utc(now) if now is not None else pd.Timestamp.now(tz='UTC')
        step = pd.Timedelta(minutes=unit)
        if start >= end or end > now.floor(f'{unit}min') or start.value % step.value or end.value % step.value:
            raise ValueError('Aligned, closed historical interval [start,end) required')
        rows, cursor = [], end
        while cursor > start:
            page = self.page(market, unit, cursor)
            if not page:
                break
            oldest = pd.Timestamp(page[-1]['candle_date_time_utc']).tz_localize('UTC')
            if oldest >= cursor:
                raise ValueError('Pagination did not advance')
            rows.extend(page)
            cursor = oldest
        frame = pd.DataFrame([{**{new: row[old] for old, new in FIELDS.items()},
                               'timestamp': pd.Timestamp(row['candle_date_time_utc']).tz_localize('UTC')}
                              for row in rows])
        if frame.empty:
            raise ValueError('Empty historical window')
        frame = frame.set_index('timestamp').sort_index()
        frame = frame.loc[(frame.index >= start) & (frame.index < end)]
        quality = quality_report(frame, unit, start, end, strict=False)
        return frame, quality


def quality_report(frame, unit, start, end, *, strict=True):
    start, end = utc(start), utc(end)
    expected = pd.date_range(start, end, freq=f'{unit}min', inclusive='left')
    if not frame.index.isin(expected).all():
        raise ValueError('Off-grid or out-of-window snapshot timestamps')
    missing = expected.difference(frame.index)
    if len(missing) and not strict:
        if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
            raise ValueError('Duplicate/unordered snapshot')
        # Validate values without concealing the original timestamps or claiming gap repair.
        values = frame.copy()
        values.index = pd.date_range(start, periods=len(values), freq=f'{unit}min')
        validate_candles(values, unit * 60)
        return {'passed': False, 'rows': len(frame), 'expected_rows': len(expected),
                'missing': len(missing), 'missing_examples': [x.isoformat() for x in missing[:20]],
                'reason': 'Missing bars; dataset quarantined, never silently filled',
                'dataset_sha256': dataset_hash(frame)}
    # Duplicates, nonfinite prices, OHLC bounds, zero/negative prices and gaps fail closed.
    validated = validate_candles(frame, unit * 60)
    missing = expected.difference(validated.index)
    extra = validated.index.difference(expected)
    if len(missing) or len(extra) or len(frame) != len(expected):
        raise ValueError(f'Incomplete research window: missing={len(missing)}, extra={len(extra)}')
    return {'passed': True, 'rows': len(frame), 'expected_rows': len(expected),
            'missing': 0, 'duplicates': 0, 'nonfinite': 0, 'invalid_ohlc': 0,
            'zero_volume_bars': int((validated.volume == 0).sum()),
            'large_close_moves_gt20pct': int((validated.close.pct_change().abs() > .2).sum()),
            'first_open': validated.index[0].isoformat(), 'end_exclusive': end.isoformat(),
            'dataset_sha256': dataset_hash(validated),
            'gap_policy': 'reject; no sorting repairs, deduplication, resampling or future filling'}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
