"""Fixed official GraphQL READ queries only. POST is transport, never a mutation."""
import json
import re
import urllib.error
import urllib.request

from .github_read import VerificationPending, _NoRedirect, positive
from .events import digest

CORE = 'databaseId number state isDraft headRefOid baseRefOid reviewDecision mergeable mergeStateStatus mergeQueueEntry { id } autoMergeRequest { enabledAt }'
QUERIES = {
    'state': 'query P4State($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){databaseId pullRequest(number:$number){' + CORE + '}}}',
    'threads': '''query P4Threads($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){databaseId pullRequest(number:$number){headRefOid baseRefOid reviewThreads(first:100,after:$cursor){totalCount pageInfo{hasNextPage endCursor} nodes{id isResolved isOutdated}}}}}''',
    'requests': '''query P4Requests($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){databaseId pullRequest(number:$number){headRefOid baseRefOid reviewRequests(first:100,after:$cursor){totalCount pageInfo{hasNextPage endCursor} nodes{id asCodeOwner}}}}}''',
    'owners': '''query P4Owners($owner:String!,$name:String!,$githubPath:String!,$rootPath:String!,$docsPath:String!){repository(owner:$owner,name:$name){databaseId github:object(expression:$githubPath){__typename oid ... on Blob{byteSize isTruncated text}} root:object(expression:$rootPath){__typename oid ... on Blob{byteSize isTruncated text}} docs:object(expression:$docsPath){__typename oid ... on Blob{byteSize isTruncated text}}}}''',
    'user': 'query P4User($login:String!){user(login:$login){databaseId login}}',
}


class ReadOnlyGraphQL:
    MAX_REQUESTS = 40
    MAX_BYTES = 2_000_000

    def __init__(self, repository, token, *, send=None):
        if repository != 'sasumpi123/auto_trade' or type(token) is not str or not token or any(c.isspace() for c in token):
            raise VerificationPending('graphql_authentication_required')
        self.repository, self._token = repository, token
        self._send = send or self._https
        self.requests = 0

    def __repr__(self):
        return '<ReadOnlyGraphQL fixed query operations>'

    def _https(self, body, headers):
        request = urllib.request.Request('https://api.github.com/graphql', data=body, headers=headers, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, dict(response.headers), response.read(self.MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            status = error.code; error.close()
            return status, {}, b''
        except (OSError, ValueError):
            raise VerificationPending('graphql_network_unavailable') from None

    def read(self, operation, *, number=None, cursor=None, base=None, login=None):
        if operation not in QUERIES:
            raise VerificationPending('graphql_operation_forbidden')
        variables = dict(zip(('owner', 'name'), self.repository.split('/')))
        if operation in ('state', 'threads', 'requests'):
            variables['number'] = positive(number)
            if operation != 'state':
                if cursor is not None and (type(cursor) is not str or not re.fullmatch('[A-Za-z0-9_+=/-]{1,256}', cursor)):
                    raise VerificationPending('graphql_cursor_unverified')
                variables['cursor'] = cursor
        elif operation == 'owners':
            digest(base, 40)
            variables.update(githubPath=base+':.github/CODEOWNERS', rootPath=base+':CODEOWNERS', docsPath=base+':docs/CODEOWNERS')
        else:
            if type(login) is not str or not re.fullmatch('[A-Za-z0-9-]{1,39}', login):
                raise VerificationPending('codeowner_identity_unverified')
            variables = {'login': login}
        self.requests += 1
        if self.requests > self.MAX_REQUESTS:
            raise VerificationPending('graphql_request_limit')
        try:
            status, headers, body = self._send(json.dumps({'query': QUERIES[operation], 'variables': variables}).encode(),
                {'Authorization': 'Bearer '+self._token, 'Content-Type': 'application/json',
                 'Accept': 'application/json', 'User-Agent': 'auto-trade-readonly-gate'})
            headers = {k.lower(): v for k, v in headers.items()}
            if status in (401, 403):
                raise VerificationPending('graphql_access_unverified')
            if status == 429 or headers.get('x-ratelimit-remaining') == '0':
                raise VerificationPending('graphql_rate_limit')
            if status != 200 or 'application/json' not in headers.get('content-type', '') or len(body) > self.MAX_BYTES:
                raise VerificationPending('graphql_response_unverified')
            result = json.loads(body)
            if type(result) is not dict or result.get('errors') or type(result.get('data')) is not dict:
                raise VerificationPending('graphql_partial_or_error')
            return result['data']
        except VerificationPending:
            raise
        except Exception:
            raise VerificationPending('graphql_response_unverified') from None

    def connection(self, operation, number, head, base):
        field = {'threads': 'reviewThreads', 'requests': 'reviewRequests'}[operation]
        cursor, total, seen = None, None, set()
        result = []
        for _ in range(10):
            data = self.read(operation, number=number, cursor=cursor)['repository']
            pr = data['pullRequest']
            if data['databaseId'] != 888367411 or pr['headRefOid'] != head or pr['baseRefOid'] != base:
                raise VerificationPending('graphql_pr_changed')
            connection = pr[field]
            count, rows, info = connection['totalCount'], connection['nodes'], connection['pageInfo']
            if type(count) is not int or not 0 <= count <= 1000 or (total is not None and total != count) or type(rows) is not list or len(rows) > 100:
                raise VerificationPending('graphql_collection_unverified')
            total = count
            for row in rows:
                node_id = row['id']
                if type(node_id) is not str or not re.fullmatch('[A-Za-z0-9_=-]{1,128}', node_id) or node_id in seen:
                    raise VerificationPending('graphql_duplicate_or_invalid_node')
                seen.add(node_id); result.append(row)
            if info['hasNextPage'] is False:
                if total != len(result):
                    raise VerificationPending('graphql_collection_unverified')
                return result
            if info['hasNextPage'] is not True or not rows or info['endCursor'] == cursor:
                raise VerificationPending('graphql_pagination_unverified')
            cursor = info['endCursor']
        raise VerificationPending('graphql_pagination_limit')
