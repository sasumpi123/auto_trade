"""Local dry-run entry point. No live CLI, GitHub writer, login or scheduler installation."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from .fake_pipeline import Coordinator, FakeAuthority, FakePipeline, FakePublisher
from .patch_guard import TreeFile, blob_sha
from .subscription_contract import SubscriptionContract
from .worker_contracts import Change, FakeWorker, FakeIndependentReviewer
from .worker_store import FakeWitness, SQLiteTaskStore

ROOT = Path(__file__).resolve().parents[1]


def demo(database, scenario):
    policy = json.loads((ROOT / '.github/automation/policy.json').read_text())
    SubscriptionContract(json.loads((ROOT / '.github/automation/zero-cost.json').read_text()))
    witness = FakeWitness()
    store = SQLiteTaskStore.create(database, policy, witness=witness)
    authority = FakeAuthority(); base = 'a' * 40
    authority.approve(42, 'Add a short research note.', base)
    coordinator = Coordinator(store, authority, policy, clock=lambda: datetime(2026, 10, 21, tzinfo=timezone.utc))
    publisher = FakePublisher(base, {'docs/note.md': TreeFile(b'initial\n')})
    pipeline = FakePipeline(coordinator, publisher)
    owner = str(uuid4())
    task = coordinator.claim(42, owner)
    patch = pipeline.run(task, FakeWorker([Change('docs/note.md', blob_sha(b'initial\n'), b'updated\n')]))
    pipeline.publish(patch, owner)
    item = coordinator.state(42)
    if scenario == 'revision':
        authority.review(FakeIndependentReviewer().review(42, item['pr'], item['head'], item['head_version'], 1, state='CHANGES_REQUESTED', text='Add a second sentence.'))
        pipeline.review(42)
        task = coordinator.claim(42, owner, expected_version=coordinator.state(42)['version'])
        patch = pipeline.run(task, FakeWorker([Change('docs/note.md', blob_sha(b'updated\n'), b'updated\nsecond sentence\n')]))
        pipeline.publish(patch, owner)
        item = coordinator.state(42)
    authority.review(FakeIndependentReviewer().review(42, item['pr'], item['head'], item['head_version'], 2))
    pipeline.review(42); pipeline.ci(42)
    item = coordinator.state(42)
    return {'mode': 'fake-only', 'status': item['status'], 'attempts': item['attempts'],
            'reserved_cents': item['reserved'], 'fake_pushes': publisher.pushes, 'fake_prs': publisher.creations,
            'extra_spend_krw': 0, 'actual_model_calls': 0, 'execution_enabled': False, 'merge_enabled': False}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError('Invalid arguments')


def main():
    try:
        parser = Parser()
        parser.add_argument('--dry-run', action='store_true', required=True)
        parser.add_argument('--database', type=Path)
        parser.add_argument('--scenario', choices=('normal', 'revision'))
        args = parser.parse_args()
        SubscriptionContract(json.loads((ROOT / '.github/automation/zero-cost.json').read_text()))
        if args.scenario:
            if args.database is None:
                raise ValueError('Explicit new fake database required')
            result = demo(args.database, args.scenario)
        else:
            result = {'mode': 'fake-only', 'status': 'DISABLED', 'execution_enabled': False,
                      'extra_spend_krw': 0, 'actual_model_calls': 0, 'reason': 'account_and_zero_cost_unverified'}
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"status":"HALTED","execution_enabled":false,"extra_spend_krw":0,"reason":"dry_run_unverified"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
