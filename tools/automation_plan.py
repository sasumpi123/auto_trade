"""Read-only event observation; intentionally no model invocation or GitHub writes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation.policy import plan


def main():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / '.github/automation/policy.json').read_text(encoding='utf-8'))
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
    # Never print issue/comment/review bodies, raw event payloads, tokens or model outputs.
    observation = {'event': os.getenv('GITHUB_EVENT_NAME'),
                   'issue': event.get('issue', {}).get('number'),
                   'pr': event.get('pull_request', {}).get('number'),
                   'review_id': event.get('review', {}).get('id'),
                   'read_only': True, 'model_calls': 0,
                   'plan': plan({}, policy)}
    print(json.dumps(observation))
    # Even editing enabled=true cannot turn this observer into an executor.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
