"""Subscription-only launch contract. Produces NO executable/live invocation."""
from dataclasses import dataclass
from .worker_contracts import WorkerBlocked, require


@dataclass(frozen=True)
class SubscriptionEvidence:
    auth_mode: str = 'unknown'
    plan: str = 'unknown'
    included_usage_available: bool = False
    quota_fresh: bool = False
    authentication_valid: bool = False
    purchased_credits_disabled: bool = False
    zero_cost_proven: bool = False


class SubscriptionContract:
    def __init__(self, policy):
        require(policy.get('enabled') is False and policy.get('extra_spend_krw') == 0
                and type(policy.get('extra_spend_krw')) is int and policy.get('mode') == 'fake-only', 'zero_cost_policy_required')
        for name in ('allow_api_billing', 'allow_credit_purchase', 'allow_usage_expansion', 'allow_paid_runner',
                     'allow_paid_hosting_or_database', 'allow_auth_export', 'allow_live_worker', 'allow_live_publish', 'allow_merge'):
            require(policy.get(name) is False, 'zero_cost_policy_required')

    def preflight(self, evidence, environment_names=()):
        # Names only, never read auth.json, Keychain, tokens or environment values.
        require(type(evidence) is SubscriptionEvidence, 'subscription_evidence_required')
        forbidden = {'OPENAI_API_KEY', 'CODEX_API_KEY', 'AZURE_OPENAI_API_KEY', 'CODEX_ACCESS_TOKEN',
                     'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'ANTHROPIC_API_KEY'}
        require(not forbidden.intersection(environment_names), 'api_or_override_environment_forbidden')
        require(evidence.auth_mode == 'chatgpt' and evidence.plan == 'plus', 'plus_login_unverified')
        require(evidence.authentication_valid, 'authentication_unavailable')
        require(evidence.included_usage_available and evidence.quota_fresh, 'included_quota_unavailable')
        require(evidence.purchased_credits_disabled and evidence.zero_cost_proven, 'zero_cost_unproven')
        return {'execution_enabled': False, 'reason': 'live_subscription_adapter_not_implemented'}

    def execute(self, *args, **kwargs):
        raise WorkerBlocked('live_subscription_execution_disabled')
