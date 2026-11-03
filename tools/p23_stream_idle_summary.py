"""P23 offline summary of a private PAPER run.log/report.json: at most 8 short lines.

Read-only. No network, no account/ledger access, no raw line, price, path or credential output.
"""
import json
import sys

MAX_BYTES=64*1024*1024
MAX_LINES=8


def _load_lines(path):
    with open(path,'rb') as handle:raw=handle.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('LOG_TOO_LARGE')
    rows=[]
    for line in raw.decode('utf-8','replace').splitlines():
        line=line.strip()
        if not line.startswith('{'):continue
        try:value=json.loads(line)
        except ValueError:continue
        if isinstance(value,dict):rows.append(value)
    return rows


def _report(path):
    try:
        with open(path,'rb') as handle:raw=handle.read(MAX_BYTES+1)
        value=json.loads(raw) if len(raw)<=MAX_BYTES else None
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def _scalar(value):
    if value is None or type(value) in (bool,int,float):return value
    if isinstance(value,str):
        text=''.join(c for c in value if c.isalnum() or c in '_.-:')
        return text[:64]
    return 'N/A'


def summarize(rows,report,run_exit=None,report_exit=None):
    terminal=[r for r in rows if 'stream_idle_diagnostics' in r]
    last=terminal[-1] if terminal else {}
    final=rows[-1] if rows else {}
    d=last.get('stream_idle_diagnostics') if isinstance(last.get('stream_idle_diagnostics'),dict) else {}
    s=report.get('summary') if isinstance(report.get('summary'),dict) else {}
    timing=last.get('tick_timing') if isinstance(last.get('tick_timing'),dict) else {}
    equal=None
    if isinstance(timing.get('markets'),dict):
        try:equal=sum(int(m.get('equal_timestamp',0)) for m in timing['markets'].values() if isinstance(m,dict))
        except (TypeError,ValueError):equal=None
    discard=timing.get('regression_discard') if isinstance(timing.get('regression_discard'),dict) else {}
    c=d.get('counters') if isinstance(d.get('counters'),dict) else {}
    v=_scalar
    lines=[
        f"1 exit run/report={v(run_exit)}/{v(report_exit)} session={v(report.get('session_state'))} final={v(final.get('status'))}",
        f"2 status={v(last.get('status'))} reason={v(last.get('reason'))} supervisor={v(last.get('supervisor_reason'))} restart={v(last.get('automatic_restart'))}",
        f"3 buys/sells={v(s.get('cumulative_buys'))}/{v(s.get('cumulative_sells'))} pending={v(s.get('pending_orders'))} halted={v(s.get('halted'))} equity={v(s.get('equity'))}",
        f"4 timing recv/accept/equal/discard={v(timing.get('received_total'))}/{v(timing.get('timing_accepted_total'))}/{v(equal)}/{v(discard.get('discarded_total'))}",
        f"5 diag={v(d.get('status'))} path={v(d.get('trigger_path'))} kind={v(d.get('message_kind'))} msgs={v(c.get('stream_messages'))} ignored={v(c.get('ignored_non_strategy'))}",
        f"6 idle_ms={v(d.get('idle_ms'))} since_msg_ms={v(d.get('since_last_message_ms'))} since_advance_ms={v(d.get('since_last_advance_ms'))}",
        f"7 conn={v(d.get('connection_before'))}->{v(d.get('connection_after'))} pong={v(d.get('pong'))} probe={v(d.get('probe_reason'))} probe_ms={v(d.get('probe_elapsed_ms'))}",
        "8 diagnostic only: cause unconfirmed; PAPER only; no restart/reset performed",
    ]
    return lines[:MAX_LINES]


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2,4):
        print('Usage: python tools/p23_stream_idle_summary.py RUN_LOG REPORT_JSON [RUN_EXIT REPORT_EXIT]');return 2
    try:rows=_load_lines(args[0])
    except (OSError,ValueError):
        print('1 run.log unreadable or too large; nothing else read');return 2
    codes=[int(a) if a.lstrip('-').isdigit() else None for a in args[2:4]] if len(args)==4 else [None,None]
    for line in summarize(rows,_report(args[1]),*codes):print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
