"""Read-only offline checks. Never opens MessageQueue/engine, config or credentials."""
import argparse
import ast
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import plistlib
import stat
import sys
import time

from utils.process_lock import lock_handle
from utils.slack_preflight import preflight as slack_preflight
from services.notification_metrics import report_metrics
from trading.execution import ExecutionEngine

ROOT = Path(__file__).resolve().parents[1]


def _json(text):
    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError('Duplicate JSON key')
            result[k] = v
        return result
    return json.loads(text, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def _private(path, suffix):
    p = Path(path).absolute()
    forbidden = {'.codex', '.git', '.ssh', '.aws', '.azure', 'keychains'}
    if (p.suffix not in suffix or p.name.lower().startswith(('auth', 'credentials', 'secrets', '.env'))
            or any(part.lower() in forbidden for part in p.resolve().parts) or p.is_symlink()):
        raise ValueError('Unsafe diagnostic input')
    p = p.parent.resolve() / p.name
    info, parent = p.stat(), p.parent.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 64_000_000:
        raise ValueError('Unsafe diagnostic file')
    if os.name == 'posix' and (info.st_uid != os.getuid() or parent.st_uid != os.getuid()
                               or info.st_mode & 0o077 or parent.st_mode & 0o077):
        raise ValueError('Private owner permissions required')
    if not os.access(p.parent, os.W_OK | os.X_OK) or not os.access(p, os.R_OK | os.W_OK):
        raise ValueError('Runtime access unverified')
    return p


@contextmanager
def cold_lock(path):
    """Probe existing lock without creating/writing it; hold across the read snapshot.

    Windows ownership works in MessageQueue, but this read-only probe is not certified.
    An absent lock is not proof of an idle writer. Never unlink a stale lock file.
    """
    if os.name != 'posix':
        yield 'BLOCKED_READ_ONLY_LOCK_PROBE_UNVERIFIED'
        return
    handle = None
    try:
        lock = Path(str(path) + '.lock')
        if lock.is_symlink():
            raise ValueError('Unsafe lock')
        fd = os.open(lock, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        handle = os.fdopen(fd, 'rb')
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError('Unsafe lock')
        lock_handle(handle, initialize=False)
    except FileNotFoundError:
        status = 'PENDING_LOCK_MISSING'
    except BlockingIOError:
        status = 'BLOCKED_OWNER_ACTIVE'
    except Exception:
        status = 'BLOCKED_LOCK_UNVERIFIED'
    else:
        status = 'READY_IDLE_AT_SNAPSHOT'
    try:
        yield status
    finally:
        if handle is not None:
            handle.close()


def _literal(tree, name):
    values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    if len(values) != 1:
        raise ValueError('Ambiguous setting')
    return ast.literal_eval(values[0])


def inspect(*, root=ROOT, state=None, outbox=None):
    root = Path(root)
    state = state if state is not None else root / 'runtime/paper-state.json'
    # Explicit future opt-in path; main.py currently wires no persistent Slack consumer.
    outbox = outbox if outbox is not None else root / 'runtime/notifications/outbox.sqlite3'
    result = {'kind': 'p10-offline-preflight', 'overall': 'PENDING',
              'network_calls': 0, 'state_writes': 0, 'model_calls': 0,
              'runtime_defaults': {'paper': 'runtime/paper-state.json',
                  'outbox': 'runtime/notifications/outbox.sqlite3 (not wired by main.py)'},
              'execution_authorized': False, 'slack_enabled_by_tool': False,
              'delivery_notice': 'enqueue는 로컬 수락이며 Slack 전달 성공이 아닙니다.',
              'rollback_resistance': 'UNPROVEN_UNKNOWN_NOT_RESET',
              'controller_recovery': 'PENDING_USE_EXISTING_P8_P9B_RECONCILIATION_NO_RESET',
              'checks': {}, 'next_action': '아래 보류·차단 항목을 확인한 뒤 비밀 없는 요약만 검토에 전달하세요.',
              'verification_commands': ['python tools/check_safety.py', 'python -m unittest discover -v',
                  'python -m ops.paper_rehearsal'],
              'user_checks': ['MacBook M3 실기기·디스크 여유 공간·ACL·로컬 파일시스템 확인',
                  'launchd 등록/자동 시작·기존 터미널 프로세스를 사용자 확인 (이 도구는 launchctl을 실행하지 않음)',
                  'Controller DB/checkpoint 및 P7 영수증/복구 요약의 별도 보관·최신성 확인; 공동 롤백 검증 불가',
                  'Slack Bot chat:write·채널 ID/멤버십·무료 조건은 별도 사용자 확인; 토큰을 제공하지 마세요',
                  '실제 시장 데이터·Slack 발송·자동 시작은 별도 승인 전 금지']}
    checks = result['checks']
    checks['python312'] = 'READY' if sys.version_info[:2] == (3, 12) else 'BLOCKED'
    checks['mac_apple_silicon'] = ('READY_ENVIRONMENT_ONLY' if platform.system() == 'Darwin'
        and platform.machine() == 'arm64' else 'PENDING_MACBOOK_NOT_TESTED')
    try:
        tree = ast.parse((root/'config.py').read_text(encoding='utf-8'))
        flags = [_literal(tree, k) for k in ('REAL_TRADING', 'SLACK_NOTIFICATIONS_ENABLED')]
        policy = _json((root/'.github/automation/policy.json').read_text(encoding='utf-8'))
        cost = _json((root/'.github/automation/zero-cost.json').read_text(encoding='utf-8'))
        checks['paper_slack_automation_off'] = ('READY' if all(v is False for v in flags)
            and policy['enabled'] is False and cost['enabled'] is False
            and cost['mode'] == 'fake-only' and cost['extra_spend_krw'] == 0
            and all(cost[k] is False for k in ('allow_api_billing','allow_credit_purchase',
                'allow_usage_expansion','allow_paid_runner','allow_paid_hosting_or_database',
                'allow_auth_export','allow_live_worker','allow_live_publish','allow_merge')) else 'BLOCKED')
        start_cash = _literal(tree, 'START_CASH')
    except Exception:
        checks['paper_slack_automation_off'] = 'BLOCKED_CONFIG_UNVERIFIED'
        start_cash = None
    try:
        plist = plistlib.loads((root/'ops/local/com.auto-trade.dry-run.plist.example').read_bytes())
        checks['launchd_example'] = ('READY_DISABLED_EXAMPLE_ONLY' if plist['Disabled'] is True
            and plist['RunAtLoad'] is False and plist['KeepAlive'] is False
            and not any(k in plist for k in ('StartInterval','StartCalendarInterval','WatchPaths','QueueDirectories')) else 'BLOCKED')
    except Exception:
        checks['launchd_example'] = 'BLOCKED_UNVERIFIED'
    checks['launchd_registration'] = 'USER_ACTION_REQUIRED_NOT_QUERIED'
    try:
        p = _private(state, {'.json'})
        with cold_lock(p) as ownership:
            checks['state_owner'] = ownership
            if not ownership.startswith('READY'):
                checks['paper_ledger'] = 'BLOCKED_NO_STABLE_SNAPSHOT'
            else:
                saved = _json(p.read_text(encoding='utf-8'))
                engine = ExecutionEngine(p, start_cash, real_trading=False)
                engine._validate(saved)  # validation only; never open/save/halt/reset engine
                pending = sum(o['status'] != 'settled' for o in saved['orders'].values())
                result['pending_orders'] = pending
                result['halted'] = bool(saved.get('halt_reason'))
                metrics = report_metrics(saved, start_cash, {}, now=time.monotonic(), verified=True)
                # No prices or private halt reasons/order contents leave this tool.
                available = type(metrics.get('거래 횟수')) is int
                checks['paper_ledger'] = ('READY_LOCAL_RECONCILIATION_ONLY' if not pending
                    and not result['halted'] and available else 'BLOCKED_PENDING_HALTED_OR_MISMATCH')
    except FileNotFoundError:
        checks['paper_ledger'] = 'PENDING_FILE_MISSING_NO_INITIALIZATION'
    except Exception:
        checks['paper_ledger'] = 'BLOCKED_UNVERIFIED'
    try:
        p = _private(outbox, {'.db', '.sqlite', '.sqlite3'})
        with cold_lock(p) as ownership:
            checks['outbox_owner'] = ownership
            # Refuse hot/WAL SQLite reads: mode=ro alone can create shared-memory files.
            sidecars = any(Path(str(p)+s).exists() for s in ('-wal','-shm','-journal'))
            if not ownership.startswith('READY') or sidecars:
                checks['outbox'] = 'BLOCKED_OWNER_OR_SQLITE_SIDECAR'
            else:
                slack = slack_preflight(p, root=root, immutable=True)
                result['outbox_counts'] = slack['counts']
                result['duplicate_events'] = slack.get('duplicate_events')
                checks['outbox'] = ('READY_METADATA_ONLY' if slack['outbox'] == 'VERIFIED_LOCAL_METADATA_ONLY'
                    and not any(slack['counts'][s] for s in ('SENDING','UNKNOWN')) else 'BLOCKED_UNCERTAIN_OR_UNVERIFIED')
    except FileNotFoundError:
        checks['outbox'] = 'PENDING_FILE_MISSING_NO_INITIALIZATION'
    except Exception:
        checks['outbox'] = 'BLOCKED_UNVERIFIED'
    if any(v.startswith('BLOCKED') for v in checks.values()):
        result['overall'] = 'BLOCKED'
    # Even a clean local snapshot is not rollback evidence, launchd proof or approval.
    return result


def main():
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            self.exit(2, 'Invalid offline arguments; no action performed.\n')
    parser = SafeParser(description=__doc__)
    parser.add_argument('--state')
    parser.add_argument('--outbox')
    parser.add_argument('--format', choices=('json', 'markdown'), default='markdown')
    args = parser.parse_args()
    result = inspect(state=args.state, outbox=args.outbox)
    if args.format == 'markdown':
        print('P10 오프라인 점검 — 실행 허가 아님 / MacBook 실기기·외부 서비스 별도 검증')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2  # No diagnostic report authorizes live operation or clears HALTED.


if __name__ == '__main__':
    raise SystemExit(main())
