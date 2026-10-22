"""Print-only future user commands; never invoke launchctl or copy a plist."""
import json


def plan():
    return {'registered': False, 'enabled': False, 'automatic_start': False,
            'commands_after_separate_user_approval': [
                'plutil -lint /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist',
                'launchctl bootstrap gui/$(id -u) /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist',
                'launchctl print gui/$(id -u)/com.auto-trade.dry-run',
                'launchctl bootout gui/$(id -u) /ABSOLUTE/REVIEWED/com.auto-trade.dry-run.plist'],
            'note': 'disabled example; no enable/kickstart/interval; activation requires separate review'}


if __name__ == '__main__':
    print(json.dumps(plan()))
