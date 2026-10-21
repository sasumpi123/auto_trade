"""Bounded authenticated GET-only GitHub REST transport. No executor or write API."""
import json
import re
import urllib.error
import urllib.request


class VerificationPending(RuntimeError):
    """Fixed reason codes only; never attach response bodies, URLs or credentials."""


def positive(value):
    if type(value) is not int or value <= 0:
        raise VerificationPending('invalid_metadata')
    return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubREST:
    MAX_BYTES = 2_000_000
    MAX_REQUESTS = 120
    MAX_PAGES = 10

    def __init__(self, repository, token, *, send=None):
        if not isinstance(repository, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
            raise VerificationPending('invalid_repository')
        if not isinstance(token, str) or not token or any(c.isspace() for c in token):
            raise VerificationPending('authentication_required')
        self.repository = repository
        self._token = token
        self._send = send or self._https
        self.requests = 0

    def __repr__(self):
        return '<GitHubREST authenticated GET-only>'

    def _https(self, path, headers):
        # No environment proxy, redirects, remote Link URLs, or arbitrary origin.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        request = urllib.request.Request('https://api.github.com' + path, headers=headers, method='GET')
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read(self.MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            return status, {}, b''
        except (OSError, ValueError):
            raise VerificationPending('api_unavailable') from None

    def get(self, suffix=''):
        # Closed relative endpoint language; user-supplied URLs cannot receive the token.
        route, _, query = suffix.partition('?')
        patterns = (
            r'', r'/issues/[1-9][0-9]*(?:/comments)?',
            r'/pulls/[1-9][0-9]*(?:/(?:files|reviews))?',
            r'/branches/[A-Za-z0-9_.-]+(?:/protection)?',
            r'/rules/branches/[A-Za-z0-9_.-]+',
            r'/commits/[0-9a-f]{40}/check-runs',
            r'/actions/runs(?:/[1-9][0-9]*(?:/attempts/[1-9][0-9]*/jobs)?)?',
            r'/actions/workflows/[1-9][0-9]*',
            r'/compare/[0-9a-f]{40}\.\.\.[0-9a-f]{40}',
        )
        if not any(re.fullmatch(p, route) for p in patterns) or (query and not re.fullmatch(r'[A-Za-z0-9_=&.-]+', query)):
            raise VerificationPending('endpoint_not_allowed')
        self.requests += 1
        if self.requests > self.MAX_REQUESTS:
            raise VerificationPending('request_limit')
        try:
            status, headers, body = self._send('/repos/' + self.repository + suffix, {
                'Authorization': 'Bearer ' + self._token, 'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'auto-trade-readonly-observer'})
            headers = {k.lower(): v for k, v in headers.items()}
            if status in (401, 403):
                raise VerificationPending('permission_or_rate_limit')
            if status == 429 or headers.get('x-ratelimit-remaining') == '0':
                raise VerificationPending('rate_limit')
            if status != 200:
                raise VerificationPending('api_not_ok')
            if len(body) > self.MAX_BYTES or 'application/json' not in headers.get('content-type', ''):
                raise VerificationPending('invalid_response')
            value = json.loads(body)
            if type(value) not in (dict, list):
                raise VerificationPending('invalid_response')
            return value
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('invalid_response') from None

    def pages(self, suffix, key=None, *, identity='id'):
        result = []
        total = None
        for page in range(1, self.MAX_PAGES + 1):
            data = self.get(suffix + ('&' if '?' in suffix else '?') + f'per_page=100&page={page}')
            if key is not None:
                if type(data) is not dict or type(data.get('total_count')) is not int:
                    raise VerificationPending('incomplete_collection')
                if total is not None and total != data['total_count']:
                    raise VerificationPending('collection_changed')
                total = data['total_count']
                if total < 0 or total > self.MAX_PAGES * 100:
                    raise VerificationPending('collection_limit')
                items = data.get(key)
            else:
                items = data
            if type(items) is not list or len(items) > 100:
                raise VerificationPending('incomplete_collection')
            result.extend(items)
            if len(items) < 100:
                if total is not None and total != len(result):
                    raise VerificationPending('incomplete_collection')
                ids = [x[identity] for x in result]
                if len(ids) != len(set(ids)):
                    raise VerificationPending('duplicate_collection')
                return result
        raise VerificationPending('pagination_limit')
