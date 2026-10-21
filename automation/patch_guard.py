"""Strict patch subset, validated from bytes. Never applies an untrusted filesystem tree."""
import ast
import difflib
import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field

from .policy import allowed_paths
from .worker_contracts import Change, Patch, WorkInput, fingerprint, patch_head, require

RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
SECRET = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{16,}|'
                    r'AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|-----BEGIN .*PRIVATE KEY-----|'
                    r'(?:api[_-]?key|access[_-]?key|secret|password|token|authorization)\s*[:=])', re.I)
BYPASS = re.compile(r'\[skip[ -]ci\]|\[ci[ -]skip\]|skip-checks|pytest|unittest|xfail|skipif|'
                    r'noqa|nosec|pragma:\s*no\s*cover|real_trading|automation[._/]policy', re.I)
PURE_NODES = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Expr, ast.Constant,
              ast.Name, ast.Load, ast.Store, ast.Assign, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
              ast.If, ast.IfExp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
              ast.UAdd, ast.USub, ast.Not, ast.And, ast.Or, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def path_key(path):
    require(type(path) is str and 1 <= len(path) <= 180 and re.fullmatch(r'[A-Za-z0-9_./-]+', path), 'unsafe_path')
    parts = path.split('/')
    require(all(p and p not in ('.', '..') and not p.startswith('.') and not p.endswith('.')
                and p.split('.')[0].casefold() not in RESERVED for p in parts), 'unsafe_path')
    return path.casefold()


def blob_sha(content):
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


@dataclass(frozen=True)
class TreeFile:
    content: bytes = field(repr=False)
    mode: str = '100644'


@dataclass(frozen=True)
class ValidatedPatch:
    patch: Patch = field(repr=False)
    digest: str
    changed_lines: int
    bytes_count: int


class PatchGuard:
    def __init__(self, policy):
        self.policy = dict(policy)
        require(policy.get('enabled') is False, 'disabled_policy_required')

    def validate(self, request, patch, tree):
        require(type(request) is WorkInput and type(patch) is Patch, 'typed_patch_required')
        request.validate()
        require((patch.task, patch.issue, patch.attempt, patch.spec_version, patch.spec_sha256, patch.approved_base, patch.parent)
                == (request.task, request.issue, request.attempt, request.spec_version, request.spec_sha256, request.approved_base, request.checkout), 'patch_binding_mismatch')
        require(type(tree) is dict and len(tree) <= 10000, 'invalid_base_tree')
        seen = set()
        base_bytes = 0
        for path, entry in tree.items():
            key = path_key(path)
            require(key not in seen and type(entry) is TreeFile and entry.mode == '100644' and type(entry.content) is bytes, 'unsafe_base_tree')
            seen.add(key)
            require(len(entry.content) <= 262144, 'base_file_size_limit')
            base_bytes += len(entry.content)
        require(base_bytes <= 8388608, 'base_tree_size_limit')
        require(type(patch.changes) is tuple and 1 <= len(patch.changes) <= min(20, self.policy['max_changed_files']), 'file_count_limit')
        require(all(type(c) is Change for c in patch.changes), 'typed_change_required')
        paths = [c.path for c in patch.changes]
        require(allowed_paths(paths, self.policy), 'protected_path')
        keys = [path_key(p) for p in paths]
        require(len(set(keys)) == len(keys), 'duplicate_or_case_collision')
        require(not any(a.startswith(b + '/') for a in keys for b in keys if a != b), 'file_directory_collision')
        total = lines = 0
        for change in patch.changes:
            path, content = change.path, change.content
            key = path_key(path)
            require(not any(k == key and p != path for p, k in ((p, path_key(p)) for p in tree)), 'case_collision')
            require(not any(p.startswith(path + '/') or path.startswith(p + '/') for p in tree if p != path), 'file_directory_collision')
            require(change.mode == '100644' and change.operation == 'write', 'unsupported_tree_operation')
            # Deliberately stricter than the general policy. Do not weaken the trusted test oracle.
            require(not path.startswith('tests/') and not path.split('/')[-1].startswith(('test_', 'conftest', '__')),
                    'test_or_import_hook_change')
            require(path.endswith(('.py', '.md')) and type(content) is bytes and 0 < len(content) <= 65536, 'file_size_or_type')
            before = tree.get(path)
            require(change.before == (blob_sha(before.content) if before else ''), 'preimage_mismatch')
            old = before.content if before else b''
            require(content != old, 'empty_change')
            try:
                text, previous = content.decode('utf-8'), old.decode('utf-8')
            except UnicodeError:
                require(False, 'binary_content')
            require(all(unicodedata.category(c) not in ('Cc', 'Cf', 'Cs') or c in '\n\t\r' for c in text), 'hidden_control_character')
            require(not SECRET.search(text), 'credential_material')
            require(not BYPASS.search(text), 'test_or_policy_bypass')
            for token in re.findall(r'[A-Za-z0-9_+/=-]{32,}', text):
                entropy = -sum((token.count(c)/len(token)) * math.log2(token.count(c)/len(token)) for c in set(token))
                require(entropy < 4.5, 'opaque_high_entropy_material')
            if path.endswith('.py'):
                self._pure_python(text)
            require(len(text.splitlines()) <= 1000 and len(previous.splitlines()) <= 1000, 'line_count_limit')
            changed = sum(1 for row in difflib.ndiff(previous.splitlines(), text.splitlines()) if row.startswith(('+ ', '- ')))
            lines += changed; total += len(content)
        require(lines <= 400 and total <= min(262144, request.isolation.max_output_bytes), 'change_size_limit')
        require(patch.head == patch_head(patch.task, patch.parent, patch.changes), 'head_mismatch')
        return ValidatedPatch(patch, fingerprint([patch.task, patch.parent, patch.head]), lines, total)

    def _pure_python(self, source):
        try:
            module = ast.parse(source)
        except SyntaxError:
            require(False, 'invalid_python')
        require(module.body and all(type(n) is ast.FunctionDef for n in module.body), 'unsupported_python_subset')
        for node in ast.walk(module):
            require(isinstance(node, PURE_NODES), 'unsupported_python_subset')
            if isinstance(node, ast.FunctionDef):
                require(not node.decorator_list and node.returns is None and re.fullmatch('[a-z][a-z0-9_]*', node.name), 'unsupported_python_subset')
            if isinstance(node, ast.arg):
                require(node.annotation is None and re.fullmatch('[a-z][a-z0-9_]*', node.arg), 'unsupported_python_subset')
            if isinstance(node, ast.arguments):
                require(not node.defaults and not node.kw_defaults and node.vararg is None and node.kwarg is None, 'unsupported_python_subset')
            if isinstance(node, ast.Name):
                require(re.fullmatch('[a-z][a-z0-9_]*', node.id), 'unsupported_python_subset')
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                require(len(node.value) <= 128, 'opaque_string_literal')
