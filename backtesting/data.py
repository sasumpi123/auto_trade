"""Strict, causal OHLCV preparation. Timestamps identify candle OPEN times."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.signals import Snapshot

OHLCV = ['open', 'high', 'low', 'close', 'volume']
INDICATORS = ['rsi', 'macd', 'macd_signal', 'bb_lower', 'bb_middle', 'bb_upper', 'volume_average']


def validate_candles(data, bar_seconds):
    if type(bar_seconds) is not int or bar_seconds <= 0:
        raise ValueError('bar_seconds must be a positive integer')
    if not isinstance(data, pd.DataFrame) or data.empty:
        raise ValueError('Non-empty OHLCV DataFrame required')
    if not isinstance(data.index, pd.DatetimeIndex) or data.index.tz is None:
        raise ValueError('Timezone-aware candle OPEN timestamps required')
    if data.index.hasnans or data.index.has_duplicates or not data.index.is_monotonic_increasing:
        raise ValueError('Candle timestamps must be unique and ordered')
    if data.columns.has_duplicates or not set(OHLCV).issubset(data.columns):
        raise ValueError('Missing OHLCV columns')
    frame = data[OHLCV].astype(float).copy()
    frame.index = frame.index.tz_convert('UTC')
    if not np.isfinite(frame.to_numpy()).all():
        raise ValueError('Non-finite OHLCV')
    if (frame[['open', 'high', 'low', 'close']] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError('Prices must be positive and volume non-negative')
    if (frame.high < frame[['open', 'close', 'low']].max(axis=1)).any() or (
            frame.low > frame[['open', 'close', 'high']].min(axis=1)).any():
        raise ValueError('Invalid candle range')
    if len(frame) > 1 and not (frame.index.to_series().diff().iloc[1:] == pd.Timedelta(seconds=bar_seconds)).all():
        raise ValueError('Irregular/gapped candles; resampling must be an explicit research decision')
    return frame


def load_csv(path):
    frame = pd.read_csv(Path(path), float_precision='round_trip')
    if 'timestamp' not in frame:
        raise ValueError('CSV requires a timestamp column')
    timestamps = [pd.Timestamp(value) for value in frame.pop('timestamp')]
    if any(t.tzinfo is None for t in timestamps):
        raise ValueError('CSV timestamps must include a timezone')
    frame.index = pd.DatetimeIndex(pd.to_datetime(timestamps, utc=True), name='timestamp')
    return frame


def dataset_hash(frame):
    normalized = frame[OHLCV].copy()
    normalized.index = pd.Index([timestamp.isoformat() for timestamp in frame.index], name='timestamp')
    canonical = normalized.to_csv(float_format='%.17g', lineterminator='\n')
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def indicators(frame):
    """No bfill/ffill: unavailable warm-up rows are never traded."""
    result = frame.copy()
    delta = result.close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    result['rsi'] = 100 - 100 / (1 + gain / loss)
    result.loc[(gain == 0) & (loss == 0), 'rsi'] = 50
    result['macd'] = result.close.ewm(span=12, adjust=False, min_periods=12).mean() - result.close.ewm(span=26, adjust=False, min_periods=26).mean()
    result['macd_signal'] = result.macd.ewm(span=9, adjust=False, min_periods=9).mean()
    result['bb_middle'] = result.close.rolling(20).mean()
    deviation = result.close.rolling(20).std()
    result['bb_lower'] = result.bb_middle - 2 * deviation
    result['bb_upper'] = result.bb_middle + 2 * deviation
    result['volume_average'] = result.volume.rolling(20).mean()
    return result


def snapshot_at(features, index):
    if index < 1:
        return None
    current = features.iloc[index]
    previous = features.iloc[index - 1]
    if not np.isfinite(current[INDICATORS].to_numpy(dtype=float)).all() or not np.isfinite(previous[INDICATORS].to_numpy(dtype=float)).all():
        return None
    return Snapshot(float(current.close), float(current.rsi), float(current.rsi - previous.rsi),
                    float(current.macd), float(current.macd_signal), float(current.macd - previous.macd),
                    float(current.bb_lower), float(current.bb_middle), float(current.bb_upper))


def demo_candles(count=360):
    """Deterministic SYNTHETIC fixture; never evidence of market profitability."""
    x = np.arange(count, dtype=float)
    close = 10000 + 900 * np.sin(x / 18) + 220 * np.sin(x / 3)
    opening = np.r_[close[0], close[:-1]]
    return pd.DataFrame({'open': opening, 'high': np.maximum(opening, close) + 40,
                         'low': np.minimum(opening, close) - 40, 'close': close,
                         'volume': 100 + 20 * np.cos(x / 5)},
                        index=pd.date_range('2025-01-01', periods=count, freq='min', tz='UTC', name='timestamp'))
