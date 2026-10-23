"""Run the existing PAPER/Mock Slack regression rehearsal in temporary directories."""
import io
import json
import logging
import socket
from contextlib import redirect_stdout, redirect_stderr
import unittest
from unittest.mock import patch


def main():
    # Never expose raw unittest logs, local paths, exceptions or credentials in summaries.
    output = io.StringIO()
    old_level = logging.root.manager.disable
    old_handlers = logging.root.handlers[:]
    logging.root.handlers = [logging.NullHandler()]
    logging.disable(logging.NOTSET)
    try:
        with patch.dict('os.environ', {'PYTHON_DOTENV_DISABLED': '1'}), \
             patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline rehearsal')), \
             redirect_stdout(output), redirect_stderr(output):
            suite = unittest.defaultTestLoader.loadTestsFromNames([
                'tests.test_p9b_paper_slack_e2e', 'tests.test_p10_outbox_ownership',
                'tests.test_p10_paper_rehearsal'])
            result = unittest.TextTestRunner(stream=output, verbosity=0).run(suite)
    finally:
        logging.root.handlers = old_handlers
        logging.disable(old_level)
    print(json.dumps({'kind': 'p10-paper-offline-rehearsal', 'tests_run': result.testsRun,
        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'success': result.wasSuccessful(), 'real_orders': False, 'real_slack': False,
        'uses_isolated_fake_data_only': True, 'enqueue_is_delivery': False,
        'operation_authorized': False}, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 2


if __name__ == '__main__':
    raise SystemExit(main())
