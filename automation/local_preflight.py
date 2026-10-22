"""Read-only Mac diagnostics. Fixed output vocabulary; no login/model/launchctl."""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys

from .subscription_contract import SubscriptionContract, SubscriptionEvidence
from .worker_contracts import WorkerBlocked

ROOT = Path(__file__).resolve().parents[1]


def probe(arguments, cwd=None):
    try:
        result = subprocess.run(arguments, cwd=cwd, capture_output=True, text=True, timeout=10,
                                stdin=subprocess.DEVNULL, check=False)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None, ''


def diagnose(repository=ROOT, database=None):
    checks = []
    def add(name, status, detail):
        checks.append(dict(check=name, status=status, detail=detail))
    add('macos_arm64', 'PASS' if platform.system() == 'Darwin' and platform.machine() == 'arm64' else 'PENDING',
        'local_mac_required' if platform.system() != 'Darwin' else 'architecture_checked')
    add('python312', 'PASS' if sys.version_info[:2] == (3, 12) else 'FAIL', 'interpreter_checked')
    for tool in ('git', 'gh', 'codex'):
        if not shutil.which(tool):
            add(tool, 'USER_ACTION_REQUIRED', 'tool_missing')
            continue
        code, output = probe([tool, '--version'])
        # Do not reflect arbitrary output, paths, config or authentication details.
        version = re.search(r'\b\d+\.\d+(?:\.\d+)?\b', output[:256])
        add(tool, 'PASS' if code == 0 and version else 'PENDING', version.group() if code == 0 and version else 'version_unverified')
    if shutil.which('codex'):
        code, output = probe(['codex', 'login', 'status'])
        add('codex_login', 'PENDING', 'official_status_observed_account_and_cost_unverified' if code == 0 else 'official_status_unavailable')
    else:
        add('codex_login', 'USER_ACTION_REQUIRED', 'tool_missing')
    code, _ = probe(['git', 'rev-parse', '--show-toplevel'], repository)
    add('git_repository', 'PASS' if code == 0 else 'FAIL', 'repository_checked')
    code, branch = probe(['git', 'symbolic-ref', '--short', 'HEAD'], repository)
    add('master_checkout', 'PASS' if code == 0 and branch == 'master' else 'PENDING', 'branch_checked_no_fetch_or_reset')
    code, _ = probe(['git', 'diff', '--quiet', 'HEAD'], repository)
    add('tracked_clean', 'PASS' if code == 0 else 'PENDING', 'tracked_changes_checked')
    add('github_permissions', 'PENDING', 'authenticated_repository_api_and_scope_verification_required')
    if shutil.which('gh'):
        code, _ = probe(['gh', 'auth', 'status', '--hostname', 'github.com'])
        add('github_auth', 'PASS' if code == 0 else 'USER_ACTION_REQUIRED', 'cli_status_only_not_scope_proof')
    else:
        add('github_auth', 'USER_ACTION_REQUIRED', 'tool_missing')
    add('launchd', 'PASS' if platform.system() == 'Darwin' and shutil.which('launchctl') else 'PENDING', 'availability_only_not_registered')
    if database:
        path = Path(database)
        try:
            items = (path.parent, path)
            safe = all(not p.is_symlink() and p.stat().st_uid == os.getuid() and not stat.S_IMODE(p.stat().st_mode) & 0o077 for p in items)
            safe = safe and path.is_file() and path.parent.is_dir() and os.access(path, os.R_OK | os.W_OK) and os.access(path.parent, os.R_OK | os.W_OK | os.X_OK)
            add('sqlite_permissions', 'PASS' if safe else 'FAIL', 'metadata_only_integrity_not_checked')
        except (OSError, AttributeError):
            add('sqlite_permissions', 'USER_ACTION_REQUIRED', 'private_existing_local_storage_required')
    else:
        add('sqlite_permissions', 'USER_ACTION_REQUIRED', 'explicit_database_path_required')
    policy = json.loads((ROOT / '.github/automation/zero-cost.json').read_text())
    try:
        SubscriptionContract(policy).preflight(SubscriptionEvidence(), os.environ.keys())
    except WorkerBlocked:
        add('subscription_zero_cost', 'FAIL', 'HALTED_no_trusted_included_only_usage_evidence')
    return {'status': 'HALTED', 'checks': checks, 'execution_enabled': False, 'actual_model_calls': 0,
            'merge_enabled': False, 'launchd_registered': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', required=True)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    try:
        result = diagnose(database=args.database)
    except Exception:
        result = {'status': 'HALTED', 'reason': 'diagnostics_unavailable', 'execution_enabled': False}
    print(json.dumps(result))
    return 2  # Preparation report is never activation authorization.


if __name__ == '__main__':
    raise SystemExit(main())
