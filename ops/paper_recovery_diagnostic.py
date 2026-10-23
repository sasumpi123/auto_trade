"""Offline, read-only HALTED PAPER evidence. Never opens an execution engine."""
import argparse
import ast
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path

from ops.local_preflight import ROOT, _private, _json, _literal, cold_lock
from services.notification_metrics import replay
from services.public_market import MARKET
from trading.execution import ExecutionEngine


def snapshot(path, initial_cash):
    p=_private(path,{'.json'})
    raw=p.read_bytes();saved=_json(raw.decode('utf-8'))
    ExecutionEngine(p,initial_cash,real_trading=False)._validate(saved)
    markets=set(saved['positions'])|set(saved['signals'])|{o['ticker'] for o in saved['orders'].values()}
    if any(not MARKET.fullmatch(t) for t in markets):raise ValueError()
    # Direct pure accounting replay deliberately accepts a HALTED snapshot. It does
    # not clear halt (even in memory) or confer permission to open/restart an engine.
    with localcontext() as ctx:
        ctx.prec=28
        accounting=replay(saved,Decimal(str(initial_cash)),as_of=datetime.now(timezone.utc),calendar='KST')
    if raw!=p.read_bytes():raise ValueError()
    return saved,accounting,hashlib.sha256(raw).hexdigest()


def diagnose(state, backup=None):
    result={'status':'HALTED','network_enabled':False,'state_writes':0,
            'recovery_authorized':False,'restart_authorized':False,
            'cause':'PENDING_EXTERNAL_SESSION_EVIDENCE_REQUIRED',
            'rollback_protection':'PENDING_NO_INDEPENDENT_WITNESS',
            'ledger':'UNVERIFIED','backup':'PENDING_NOT_PROVIDED'}
    try:
        p=_private(state,{'.json'})
        initial=_literal(ast.parse((ROOT/'config.py').read_text(encoding='utf-8')),'START_CASH')
        with cold_lock(p) as owner:
            result['ownership']=owner
            if owner!='READY_IDLE_AT_SNAPSHOT':return result
            saved,a,digest=snapshot(p,initial)
            result.update(ledger='RECONCILED_READ_ONLY',source_sha256=digest,
                halted=bool(saved.get('halt_reason')),pending_orders=0,
                confirmed_buys=a['buy_count'],confirmed_sells=a['sell_count'],cash=str(a['cash']),
                positions={t:{'quantity':str(v['quantity']),'engine_average_price':str(v['engine_avg']),
                              'fee_inclusive_cost':str(v['cost'])} for t,v in a['positions'].items()})
            result['halt_class']='SESSION_FAILURE_GENERIC' if saved.get('halt_reason')=='PAPER_SESSION_FAILURE' else 'OTHER_OR_ABSENT'
            if not saved.get('halt_reason'):result['status']='NO_HALT_TO_RECOVER'
            if backup is not None:
                bp=_private(backup,{'.json'})
                if bp==p:raise ValueError()
                # Caller supplies a cold, private backup. Hash equality is only a
                # content comparison, never proof of its age or rollback resistance.
                old,_,oldhash=snapshot(bp,initial)
                same=all(old.get(k)==saved.get(k) for k in ('cash','positions','orders','signals'))
                result.update(backup='RECONCILED_COPY_NOT_TRUSTED_WITNESS',backup_sha256=oldhash,
                              backup_bytes_equal=oldhash==digest,backup_accounting_equal=same)
            if hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError()
    except Exception:
        # No exception text, identifiers, order reasons, file content or credentials.
        result.update(status='HALTED',ledger='BLOCKED_INTEGRITY_PENDING_OR_INPUT_UNVERIFIED')
    return result


class SafeParser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'Invalid diagnostic arguments; no action performed.\n')


def main(argv=None):
    parser=SafeParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=ROOT/'runtime/paper-state.json')
    parser.add_argument('--backup',type=Path)
    args=parser.parse_args(argv)
    print(json.dumps(diagnose(args.state,args.backup),ensure_ascii=False,sort_keys=True))
    return 2  # Evidence is never an execution/recovery approval.


if __name__=='__main__':raise SystemExit(main())
