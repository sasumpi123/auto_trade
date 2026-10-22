"""Closed diagnostic vocabulary. Never print arbitrary exception or API messages."""
from .github_read import VerificationPending

CODES = {
    'issue_link_required': 'ISSUE_CONTRACT_MISSING',
    'issue_link_mismatch': 'ISSUE_CONTRACT_MISMATCH',
    'permission_or_rate_limit': 'API_PERMISSION_OR_RATE_LIMIT',
    'rate_limit': 'API_RATE_LIMIT',
    'api_not_ok': 'API_HTTP_UNVERIFIED',
    'api_unavailable': 'API_NETWORK_UNAVAILABLE',
    'authentication_required': 'AUTHENTICATION_MISSING',
    'invalid_response': 'API_RESPONSE_UNVERIFIED',
    'missing_or_invalid_metadata': 'API_SCHEMA_UNVERIFIED',
    'state_changed_during_read': 'STATE_CHANGED_DURING_READ',
    'request_limit': 'API_REQUEST_LIMIT',
    'pagination_limit': 'API_PAGINATION_LIMIT',
    'invalid_event_hint': 'EVENT_HINT_UNVERIFIED',
    'unsupported_event': 'EVENT_UNSUPPORTED',
    'event_repository_mismatch': 'EVENT_REPOSITORY_MISMATCH',
    'run_pr_ambiguous': 'RUN_PR_RELATION_UNVERIFIED',
    'stale_run_event': 'STALE_RUN_EVENT',
    'observation_database_unverified': 'OBSERVATION_STORAGE_UNVERIFIED',
}


def diagnostic(error):
    if type(error) is VerificationPending:
        # Membership only; never reflect a server/user-provided string into logs.
        return CODES.get(error.args[0] if error.args and type(error.args[0]) is str else '', 'VERIFICATION_PENDING')
    return 'OBSERVER_PROGRAM_ERROR'
