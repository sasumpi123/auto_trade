"""P25 offline summary of a private PAPER run.log/report.json: at most 8 short lines.

Read-only. No network, account write or raw line output; prices/paths/credentials are not printed.
"""
import json
import sys

MAX_BYTES=64*1024*1024


def _rows(path):
    with open(path,'rb') as h:raw=h.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('LOG_TOO_LARGE')
    out=[]
    for line in raw.decode('utf-8','replace').splitlines():
        line=line.strip()
        if not line.startswith('{'):continue
        try:v=json.loads(line)
        except ValueError:continue
        if isinstance(v,dict):out.append(v)
    return out


def _report(path):
    try:
        with open(path,'rb') as h:v=json.loads(h.read(MAX_BYTES))
        return v if isinstance(v,dict) else {}
    except (OSError,ValueError):return {}


def _v(value):
    if value is None or type(value) in (bool,int,float):return value
    if isinstance(value,str):return ''.join(c for c in value if c.isalnum() or c in '_.-:')[:64]
    return 'N/A'


def _get(d,*keys):
    for k in keys:d=d.get(k) if isinstance(d,dict) else None
    return _v(d)


def summarize(rows,report,run_exit=None,report_exit=None):
    terminal=[r for r in rows if r.get('status') in ('HALTED','STOPPED') and 'regression_evidence' in r]
    last=terminal[-1] if terminal else (rows[-1] if rows else {})
    final=rows[-1] if rows else {}
    ev=report.get('regression_evidence') if isinstance(report.get('regression_evidence'),dict) else last.get('regression_evidence',{})
    c=ev.get('combined',{}) if isinstance(ev,dict) else {}
    o=ev.get('observation',{}) if isinstance(ev,dict) else {}
    pol=c.get('policy',{}) if isinstance(c,dict) else {}
    mk=(c.get('markets') or {}) if isinstance(c,dict) else {}
    btc=mk.get('KRW-BTC',{}) if isinstance(mk,dict) else {}
    s=report.get('summary',{}) if isinstance(report.get('summary'),dict) else {}
    t=last.get('tick_timing',{}) if isinstance(last.get('tick_timing'),dict) else {}
    rd=t.get('regression_discard',{}) if isinstance(t.get('regression_discard'),dict) else {}
    equal=None
    if isinstance(t.get('markets'),dict):
        try:equal=sum(int(m.get('equal_timestamp',0)) for m in t['markets'].values() if isinstance(m,dict))
        except (TypeError,ValueError):equal=None
    cp=report.get('cost_policy') if isinstance(report.get('cost_policy'),dict) else {}
    return [
        f"1 exit run/report={_v(run_exit)}/{_v(report_exit)} session={_get(report,'session_state')} final={_v(final.get('status'))} status={_v(last.get('status'))} reason={_v(last.get('reason'))}",
        f"2 policy={_get(pol,'id')} market/session/seconds={_get(pol,'max_per_market')}/{_get(pol,'max_per_session')}/{_get(pol,'max_session_seconds')} width_limit={_get(pol,'max_width_ms')} release={_get(pol,'release')}",
        f"3 discarded obs/runtime/total={_get(o,'discarded')}/{_get(ev,'runtime_discarded')}/{_get(c,'discarded')} btc_width_ms={_get(btc,'width_ms')} followup={_get(btc,'followup_newer_received')} awaiting={_get(c,'awaiting_followup')}",
        f"4 buys/sells={_get(s,'cumulative_buys')}/{_get(s,'cumulative_sells')} pending={_get(s,'pending_orders')} halted={_get(s,'halted')} positions={len(s.get('positions') or {}) if isinstance(s.get('positions'),dict) else 'N/A'}",
        f"5 fees={_get(s,'fees')} net_realized={_get(s,'net_realized_pnl')} equity={_get(s,'equity')} return_pct={_get(s,'net_return_percent')}",
        f"6 timing recv/accept/equal={_get(t,'received_total')}/{_get(t,'timing_accepted_total')}/{_v(equal)} held={_get(rd,'quarantine_held')} quarantined={_v(','.join(rd.get('quarantined_markets') or []) if isinstance(rd.get('quarantined_markets'),list) else None)}",
        f"7 costs fee/slip/spread_bps={_get(cp,'fee_bps')}/{_get(cp,'slippage_bps')}/{_get(cp,'full_spread_bps')} restart={_v(last.get('automatic_restart'))}",
        "8 PAPER only; not profitability or long-run stability proof; no restart/reset performed",
    ]


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2,4):
        print('Usage: python tools/p25_paper_summary.py RUN_LOG REPORT_JSON [RUN_EXIT REPORT_EXIT]');return 2
    try:rows=_rows(args[0])
    except (OSError,ValueError):
        print('1 run.log unreadable or too large; nothing else read');return 2
    codes=[int(a) if a.lstrip('-').isdigit() else None for a in args[2:4]] if len(args)==4 else [None,None]
    for line in summarize(rows,_report(args[1]),*codes):print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
