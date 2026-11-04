"""P26 offline summary of one private run.log/report.json pair: at most 8 short lines.

Run from the repository root as `python -m tools.p26_paper_summary`.
Read-only; no network, account write, raw line, price, path or credential output.
"""
import sys

from tools.p25_paper_summary import _get,_report,_rows,_v


def summarize(rows,report,run_exit=None,report_exit=None):
    terminal=[r for r in rows if r.get('status') in ('HALTED','STOPPED') and 'p26_account' in r]
    last=terminal[-1] if terminal else (rows[-1] if rows else {})
    final=rows[-1] if rows else {}
    a=last.get('p26_account') if isinstance(last.get('p26_account'),dict) else {}
    acct=report.get('p26_account') if isinstance(report.get('p26_account'),dict) else {}
    ev=report.get('regression_evidence') if isinstance(report.get('regression_evidence'),dict) else {}
    c=ev.get('combined',{}) if isinstance(ev,dict) else {}
    btc=((c.get('markets') or {}).get('KRW-BTC') or {}) if isinstance(c,dict) else {}
    s=report.get('summary') if isinstance(report.get('summary'),dict) else {}
    t=last.get('tick_timing') if isinstance(last.get('tick_timing'),dict) else {}
    cp=report.get('cost_policy') if isinstance(report.get('cost_policy'),dict) else {}
    positions=s.get('positions') if isinstance(s.get('positions'),dict) else None
    sells=s.get('cumulative_sells')
    return [
        f"1 exit run/report={_v(run_exit)}/{_v(report_exit)} session={_get(report,'session_state')} final={_v(final.get('status'))} status={_v(last.get('status'))} reason={_v(last.get('reason'))}",
        f"2 run={_get(a,'run_index')}/{_get(a,'max_runs')} session_s={_get(a,'session_seconds')} reserved_s={_get(acct,'reserved_seconds_total')} remaining_s={_get(acct,'remaining_seconds')} rest={_get(acct,'rest_reserved_total')}/{_get(acct,'max_rest_total')}",
        f"3 discards session/account={_get(a,'session_discards')}/{_get(acct,'account_discards_total')} remaining={_get(acct,'discards_remaining')} followup={_get(btc,'followup_newer_received')} awaiting={_get(c,'awaiting_followup')}",
        f"4 buys/sells={_get(s,'cumulative_buys')}/{_v(sells)} pending={_get(s,'pending_orders')} halted={_get(s,'halted')} positions={len(positions) if positions is not None else 'N/A'}",
        f"5 fees={_get(s,'fees')} net_realized={_get(s,'net_realized_pnl')} equity={_get(s,'equity')} return_pct={_get(s,'net_return_percent')}",
        f"6 timing recv/accept={_get(t,'received_total')}/{_get(t,'timing_accepted_total')} costs={_get(cp,'fee_bps')}/{_get(cp,'slippage_bps')}/{_get(cp,'full_spread_bps')} resume_allowed={_get(acct,'resume_allowed')}",
        f"7 public SELL settled={'YES' if type(sells) is int and sells>0 else 'NO_NOT_VERIFIED'} (no forced sell, strategy unchanged)",
        "8 PAPER only; not profitability or long-run stability proof; no restart/reset performed",
    ]


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if len(args) not in (2,4):
        print('Usage: python -m tools.p26_paper_summary RUN_LOG REPORT_JSON [RUN_EXIT REPORT_EXIT]');return 2
    try:rows=_rows(args[0])
    except (OSError,ValueError):
        print('1 run.log unreadable or too large; nothing else read');return 2
    codes=[int(x) if x.lstrip('-').isdigit() else None for x in args[2:4]] if len(args)==4 else [None,None]
    for line in summarize(rows,_report(args[1]),*codes):print(line)
    return 0


if __name__=='__main__':raise SystemExit(main())
