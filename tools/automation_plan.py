"""Read-only event observation; intentionally no model invocation or GitHub writes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.github_read import GitHubREST
from automation.github_verify import GitHubVerifier
from automation.github_observe import ObservationJournal, observe, route


def main():
    if not os.environ.get('GITHUB_REPOSITORY'):
        print('{"reason":"automation_disabled","execution_enabled":false,"read_only":true,"model_calls":0}')
        return 0
    try:
        root = Path(__file__).resolve().parents[1]
        policy = json.loads((root / '.github/automation/policy.json').read_text(encoding='utf-8'))
        if os.environ['GITHUB_REPOSITORY'] != policy['repository']:
            raise ValueError('Untrusted context')
        event_path = Path(os.environ['GITHUB_EVENT_PATH'])
        if event_path.stat().st_size > 2_000_000:
            raise ValueError('Event too large')
        event = json.loads(event_path.read_text(encoding='utf-8'))
        hint = route(os.environ['GITHUB_EVENT_NAME'], event, policy['repository'])
        api = GitHubREST(policy['repository'], os.environ.get('GITHUB_TOKEN', ''))
        verifier = GitHubVerifier(api, policy)
        # Runner-local only. No artifacts/caches or P1 execution ledger are loaded.
        path = root / 'runtime/github-observations.sqlite3'
        path.parent.mkdir(exist_ok=True)
        journal = ObservationJournal(path) if path.exists() else ObservationJournal.create(path)
        result = observe(verifier, journal,
                         f"actions:{os.environ['GITHUB_RUN_ID']}:{os.environ['GITHUB_RUN_ATTEMPT']}", hint)
        print(json.dumps({**result, 'read_only': True, 'model_calls': 0}))
        return 2 if result['decision'] == 'PENDING' else 0
    except Exception:
        # No exception string, traceback, payload, identifier or token.
        print('{"decision":"PENDING","reason":"observation_unverified","execution_enabled":false,"read_only":true,"model_calls":0}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
