"""Offline-only controller lifecycle. No real snapshot to Fake authority conversion."""
from contextlib import contextmanager
import os
from pathlib import Path

from .local_checkpoint import LocalCheckpoint
from .worker_contracts import require
from .worker_store import SQLiteTaskStore, ACTIVE


@contextmanager
def single_writer(directory):
    """Kernel releases macOS flock after death; lock file is never unlinked."""
    require(os.name == 'posix', 'local_writer_platform_unsupported')
    import fcntl
    directory = Path(directory)
    LocalCheckpoint._secure(directory, directory=True)
    descriptor = os.open(directory / 'controller.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        LocalCheckpoint._secure(directory / 'controller.lock')
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            require(False, 'local_writer_busy')
        yield
    finally:
        os.close(descriptor)


class LocalController:
    """Open existing fake ledger only. No reset/create/dispatch/publish/merge method."""
    def __init__(self, database, checkpoint, policy):
        self.database, self.checkpoint, self.policy = Path(database), Path(checkpoint), policy

    def inspect(self):
        with single_writer(self.database.parent):
            LocalCheckpoint._secure(self.database)
            store = SQLiteTaskStore(self.database, self.policy, witness=LocalCheckpoint(self.checkpoint))
            state = store.read()
            interrupted = any(t['status'] in (*ACTIVE, 'UNKNOWN') for t in state['tasks'].values())
            intent = any(row['status'] in ('MERGE_INTENT', 'HALTED') for row in state.get('merge', {}).values())
            return {'status': 'HALTED' if interrupted or intent else 'PENDING',
                    'reason': 'manual_reconciliation_required' if interrupted or intent else 'live_integration_disabled',
                    'revision': state['revision'], 'audit_entries': len(state['audit']),
                    'execution_enabled': False, 'merge_enabled': False, 'actual_model_calls': 0}


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    args = parser.parse_args()
    try:
        root = Path(__file__).resolve().parents[1]
        policy = json.loads((root / '.github/automation/policy.json').read_text())
        result = LocalController(args.database, args.checkpoint, policy).inspect()
    except Exception:
        result = {'status': 'HALTED', 'reason': 'local_ledger_unverified', 'execution_enabled': False}
    print(json.dumps(result))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
