"""Read only the fixed development prefix of a combined gzip snapshot.

No holdout row is requested/parsed. gzip may buffer compressed tail bytes internally;
this is logical row isolation, not a byte-level vault or full gzip/CRC audit.
"""
import gzip
from io import StringIO
from pathlib import Path

import pandas as pd

from .collection import quality_report, utc
from .data import dataset_hash


def load_development(path, protocol, unit, metadata):
    if unit not in protocol['units_minutes']:
        raise ValueError('Unknown interval')
    start, train_end, end = (utc(protocol[k]) for k in ('start','train_end','validation_end'))
    count = int((end-start).total_seconds()) // (unit*60)
    expected_train = int((train_end-start).total_seconds()) // (unit*60)
    if (metadata['splits']['train']['rows'] != expected_train
            or metadata['splits']['validation']['rows'] != count-expected_train):
        raise ValueError('Development row counts differ from frozen calendar')
    lines = []
    with gzip.open(Path(path), 'rt', encoding='utf-8', newline='') as source:
        for _ in range(count+1):  # Header plus exactly development count; never peek at the next row.
            line = source.readline()
            if not line:
                raise ValueError('Truncated development prefix')
            lines.append(line)
    if lines[0].rstrip('\r\n') != 'timestamp,open,high,low,close,volume':
        raise ValueError('Unexpected snapshot columns')
    # Check timestamps before allowing any row into indicators or performance.
    for line in lines[1:]:
        timestamp = utc(line.split(',',1)[0])
        if not start <= timestamp < end:
            raise ValueError('Nondevelopment row in prefix; no evaluation allowed')
    frame = pd.read_csv(StringIO(''.join(lines)), float_precision='round_trip')
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop('timestamp'), utc=True), name='timestamp')
    quality_report(frame, unit, protocol['start'], protocol['validation_end'])
    for split, part in (('train',frame.iloc[:expected_train]),('validation',frame.iloc[expected_train:])):
        if dataset_hash(part) != metadata['splits'][split]['sha256']:
            raise ValueError('Development segment hash mismatch')
    return frame
