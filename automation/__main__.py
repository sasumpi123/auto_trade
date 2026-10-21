"""Local-only reproducible P1 demo and ledger inspection/backup utilities."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .events import LedgerError
from .fakes import FakeCodex, FakeGitHub, FakeReviewer
from .orchestrator import DryRunOrchestrator
from .store import SQLiteLedger

POLICY = Path(__file__).resolve().parents[1] / '.github/automation/policy.json'


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, 'Invalid local dry-run arguments; no execution.\n')


def demo(path, scenario):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    # The fixtures have no wall-clock market dependency; all timestamps are explicitly UTC.
    clock = lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    ledger = SQLiteLedger.create(path, policy, clock=clock)
    github, codex, reviewer = FakeGitHub(), FakeCodex(), FakeReviewer()
    github.add_issue(1)
    runner = DryRunOrchestrator(ledger, github, codex, reviewer)
    runner.approve(1)
    runner.queue(1)
    runner.claim(1)
    runner.build(1)
    runner.request_review(1)
    runner.gate(1)
    if scenario == 'revision':
        runner.review(1, changes=True)
        runner.claim(1)
        runner.build(1)
        runner.request_review(1)
        runner.gate(1)
    if scenario == 'failure':
        runner.ci(1, success=False)
        runner.retry(1)
        runner.claim(1)
        runner.build(1)
        runner.ci(1, success=False)
    else:
        runner.review(1)
        runner.ci(1)
    return {'mode': 'fake-only', 'paid_execution_allowed': False, 'actual_api_calls': 0,
            'scenario': scenario, 'issues': ledger.summary(),
            'states': [row['new_state'] for row in ledger.history(1)],
            'fake_codex_calls': len(codex.calls), 'integrity': ledger.integrity()}


def main(argv=None):
    parser = SafeParser(description='FAKE-ONLY: no GitHub/Codex/reviewer/order APIs or real merges')
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('demo', 'status', 'reconcile', 'backup'):
        item = sub.add_parser(name)
        item.add_argument('--database', required=True, type=Path)
        if name == 'demo':
            item.add_argument('--scenario', choices=('normal', 'revision', 'failure'), default='normal')
        if name == 'backup':
            item.add_argument('--output', required=True, type=Path)
    restore = sub.add_parser('restore')
    restore.add_argument('--backup', required=True, type=Path)
    restore.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        policy = json.loads(POLICY.read_text(encoding='utf-8'))
        if args.action == 'demo':
            result = demo(args.database, args.scenario)
        elif args.action == 'restore':
            ledger = SQLiteLedger.restore(args.backup, args.output, policy)
            result = {'mode': 'fake-only', 'quarantined': True, 'integrity': ledger.integrity()}
        else:
            ledger = SQLiteLedger(args.database, policy)
            if args.action == 'backup':
                result = {'mode': 'fake-only', 'backup_sha256': ledger.backup(args.output)}
            elif args.action == 'reconcile':
                result = {'mode': 'fake-only', 'halted_issues': ledger.reconcile()}
            else:
                result = {'mode': 'fake-only', 'issues': ledger.summary(), 'integrity': ledger.integrity()}
    except (LedgerError, ValueError, OSError):
        # Never include raw exception/event/SQL/credential contents in operator output.
        parser.exit(2, 'Local dry-run blocked: ledger/configuration invalid or reconciliation required.\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
