"""Explicit read-only GitHub preparation probes using the user's existing gh login.

No auto login, permission changes, secret creation, posting or model calls.
"""
import json
import subprocess

REPOSITORY = 'sasumpi123/auto_trade'
ROUTES = {
    'repository': '',
    'master': '/branches/master',
    'protection': '/branches/master/protection',
    'rules': '/rules/branches/master',
    'actions_permissions': '/actions/permissions/workflow',
    'installed_apps': '/installation',
}


def inspect():
    results = []
    for name, suffix in ROUTES.items():
        try:
            response = subprocess.run(['gh', 'api', '--method', 'GET', f'repos/{REPOSITORY}{suffix}'],
                                      capture_output=True, text=True, timeout=15, check=False)
            if response.returncode:
                # Do not conflate an API/access error with a normal Issue-contract wait.
                results.append({'check': name, 'status': 'PENDING', 'reason': 'API_ACCESS_UNVERIFIED'})
                continue
            value = json.loads(response.stdout)
            if type(value) not in (dict, list):
                raise ValueError()
            row = {'check': name, 'status': 'PASS', 'reason': 'READ_ONLY_RESPONSE'}
            if name == 'repository':
                row['status'] = 'PASS' if value.get('private') is False and value.get('visibility') == 'public' else 'PENDING'
                row['reason'] = 'PUBLIC_STANDARD_RUNNER_ELIGIBILITY_ONLY_NOT_BILLING_PROOF'
            if name == 'master':
                row['status'] = 'PASS' if value.get('protected') is True else 'FAIL'
                row['reason'] = 'PROTECTED_FLAG_ONLY' if value.get('protected') is True else 'MASTER_PROTECTION_UNVERIFIED_OR_DISABLED'
            results.append(row)
        except (OSError, subprocess.SubprocessError, ValueError):
            results.append({'check': name, 'status': 'PENDING', 'reason': 'API_RESPONSE_UNVERIFIED'})
    for name in ('review_threads', 'codeowners', 'independent_reviewer', 'zero_cost_ci'):
        results.append({'check': name, 'status': 'PENDING', 'reason': 'EXACT_HEAD_AND_USER_CONFIGURATION_REQUIRED'})
    return {'status': 'PENDING', 'checks': results, 'execution_enabled': False, 'merge_enabled': False}


if __name__ == '__main__':
    print(json.dumps(inspect()))
    raise SystemExit(2)
