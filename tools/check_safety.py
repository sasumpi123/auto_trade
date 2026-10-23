"""Check the tracked working tree without importing config or exposing secret values.

This is a bounded guard, not an exhaustive secret/history scanner.
"""
import json
import ast
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


TOKEN_PATTERNS = (
    re.compile(r'xox[baprs]-[A-Za-z0-9-]{20,}'),
    re.compile(r'gh[pousr]_[A-Za-z0-9]{30,}'),
    re.compile(r'AKIA[A-Z0-9]{16}'),
    re.compile(r'-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----'),
)
CREDENTIAL_NAMES = {'acc_key', 'sec_key', 'app_token', 'access_key', 'secret_key',
                    'upbit_access_key', 'upbit_secret_key', 'slack_app_token', 'slack_bot_token', 'api_key'}
CI_CREDENTIAL_NAMES = ('ACC_KEY', 'SEC_KEY', 'APP_TOKEN', 'UPBIT_ACCESS_KEY',
                       'UPBIT_SECRET_KEY', 'SLACK_APP_TOKEN', 'SLACK_BOT_TOKEN')


def is_placeholder(value):
    value = value.strip().lower()
    return not value or value.startswith(('fake', 'test', 'offline', 'your_', 'example',
                                          'placeholder', 'not-a-', '<'))


def audit_files(root, paths):
    issues = []
    if 'config.py' not in paths:
        issues.append('config.py: required tracked configuration is missing')
    for name in paths:
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            issues.append('Invalid tracked path')
            continue
        base = path.name.lower()
        sensitive = ((base == '.env' or base.startswith('.env.')) and base != '.env.example')
        sensitive |= path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx'}
        sensitive |= base.startswith(('credentials', 'secrets')) and path.suffix.lower() == '.json'
        if sensitive:
            issues.append(f'{name}: sensitive file must not be tracked')
            continue  # Do not read a tracked secret file to report its values.
        file = root / name
        if file.is_symlink():
            issues.append(f'{name}: tracked symlink requires manual review')
            continue
        try:
            source = file.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        if any(pattern.search(source) for pattern in TOKEN_PATTERNS):
            issues.append(f'{name}: possible credential material (value redacted)')
        if base == '.env.example':
            for line in source.splitlines():
                key, separator, value = line.strip().partition('=')
                if separator and key.lower() in CREDENTIAL_NAMES and not is_placeholder(value.strip('"\'')):
                    issues.append(f'{name}: example must contain placeholders only (value redacted)')
        if path.suffix != '.py':
            continue
        try:
            module = ast.parse(source)
        except SyntaxError:
            issues.append(f'{name}: invalid Python source')
            continue
        if name == 'config.py':
            mode = []
            for node in ast.walk(module):
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if any(isinstance(t, ast.Name) and t.id == 'REAL_TRADING' for t in targets):
                        mode.append(node.value)
            if len(mode) != 1 or not isinstance(mode[0], ast.Constant) or mode[0].value is not False:
                issues.append('config.py: REAL_TRADING must be the literal False')
        for node in ast.walk(module):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                field = target.id if isinstance(target, ast.Name) else (
                    target.attr if isinstance(target, ast.Attribute) else '')
                if field.lower() in CREDENTIAL_NAMES and not is_placeholder(node.value.value):
                    issues.append(f'{name}:{node.lineno}: hardcoded credential (value redacted)')
    return sorted(set(issues))


def audit_invariants(root):
    """Bounded literal policy checks; does not prove account billing or runtime safety."""
    issues = []
    try:
        policy = json.loads((root / '.github/automation/policy.json').read_text())
        cost = json.loads((root / '.github/automation/zero-cost.json').read_text())
        if policy.get('enabled') is not False:
            issues.append('Automation must remain disabled')
        expected = ('allow_api_billing', 'allow_credit_purchase', 'allow_usage_expansion',
                    'allow_paid_runner', 'allow_paid_hosting_or_database', 'allow_auth_export',
                    'allow_live_worker', 'allow_live_publish', 'allow_merge')
        if (cost.get('enabled') is not False or cost.get('mode') != 'fake-only'
            or type(cost.get('extra_spend_krw')) is not int or cost['extra_spend_krw'] != 0
            or any(cost.get(k) is not False for k in expected)
            or any(v is not False for k,v in cost.items() if k.startswith('allow_'))
            or cost.get('on_unproven_zero_cost') != 'halt'):
            issues.append('Zero-cost execution policy violation')
        module = ast.parse((root / 'config.py').read_text(encoding='utf-8'))
        values = [n.value for n in ast.walk(module) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'SLACK_NOTIFICATIONS_ENABLED' for t in n.targets)]
        if len(values)!=1 or not isinstance(values[0], ast.Constant) or values[0].value is not False:
            issues.append('Slack default must remain literal False')
    except (OSError, ValueError, SyntaxError, TypeError):
        issues.append('Required safety policy unavailable; failing closed')
    return issues


def check_repository(root):
    tracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '-z'],
                             capture_output=True, check=True).stdout.decode('utf-8').split('\0')
    paths = [name for name in tracked if name]
    issues = audit_files(root, paths) + audit_invariants(root)
    protected = ['.env', '.env.local', 'config/.env.production', 'server.key',
                 'server.pem', 'server.p12', 'server.pfx', 'credentials-local.json',
                 'secrets-local.json', '.venv/bin/python', 'runtime/paper-state.json']
    ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '--no-index', '-z', '--stdin'],
                             input=('\0'.join(protected) + '\0').encode('utf-8'),
                             capture_output=True, check=False)
    if ignored.returncode not in (0, 1):
        raise RuntimeError('Could not validate ignore rules')
    matches = set(ignored.stdout.decode('utf-8').split('\0'))
    issues.extend(f'{name}: required ignore rule missing' for name in protected if name not in matches)
    if os.getenv('CI', '').lower() == 'true' and any(os.getenv(k) for k in CI_CREDENTIAL_NAMES):
        issues.append('CI must not receive exchange/notification credentials (values redacted)')
    return sorted(set(issues)), len(paths)


def main():
    root = Path(__file__).resolve().parents[1]
    try:
        issues, count = check_repository(root)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        print('Safety check could not inspect the tracked tree; failing closed.', file=sys.stderr)
        return 1
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f'Safety check passed: {count} tracked files; paper mode; no detected credential material.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
