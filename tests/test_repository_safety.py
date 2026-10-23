from pathlib import Path
import tempfile
import unittest
import shutil
import subprocess

from tools.check_safety import audit_files, check_repository


class RepositorySafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config.py').write_text('REAL_TRADING = False\n', encoding='utf-8')

    def audit(self, files):
        for name, content in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        return audit_files(self.root, list({'config.py', *files}))

    def test_paper_mode_and_empty_example_are_allowed(self):
        self.assertEqual(self.audit({'.env.example': 'ACC_KEY=\nSEC_KEY=\n'}), [])

    def test_example_cannot_bypass_credential_check(self):
        issues = self.audit({'.env.example': 'SEC_KEY=synthetic-sensitive-value'})
        self.assertTrue(any('placeholders only' in message for message in issues))
        self.assertNotIn('synthetic-sensitive-value', '\n'.join(issues))

    def test_live_or_dynamic_mode_is_rejected_without_importing_config(self):
        for source in ['REAL_TRADING=True', 'REAL_TRADING=bool(0)', 'REAL_TRADING=False\nREAL_TRADING=True']:
            with self.subTest(source=source):
                issues = self.audit({'config.py': source})
                self.assertTrue(any('REAL_TRADING' in message for message in issues))

    def test_env_variants_and_key_files_are_rejected_without_values(self):
        for name in ['.env', '.env.local', 'config/.env.production', 'server.key',
                     'server.pem', 'server.p12', 'server.pfx', 'credentials-local.json', 'secrets-local.json']:
            with self.subTest(name=name):
                issues = self.audit({name: 'synthetic-sensitive-value'})
                self.assertTrue(any(name in message for message in issues))
                self.assertNotIn('synthetic-sensitive-value', '\n'.join(issues))

    def test_hardcoded_credential_is_redacted(self):
        issues = self.audit({'settings.py': 'SEC_KEY = "synthetic-sensitive-value"'})
        self.assertTrue(any('hardcoded credential' in message for message in issues))
        self.assertNotIn('synthetic-sensitive-value', '\n'.join(issues))

    def test_fake_fixtures_and_environment_lookup_are_allowed(self):
        source = 'import os\nSEC_KEY = os.getenv("SEC_KEY")\nAPP_TOKEN = "fake-token"\n'
        self.assertEqual(self.audit({'settings.py': source}), [])

    def test_known_token_and_private_key_markers_are_rejected(self):
        values = ['gh' + 'p_' + 'A' * 36, 'xox' + 'b-' + 'A' * 30,
                  'AK' + 'IA' + 'A' * 16, '-----' + 'BEGIN PRIVATE KEY-----']
        for value in values:
            with self.subTest(marker=value[:4]):
                issues = self.audit({'fixture.txt': value})
                self.assertTrue(any('credential material' in message for message in issues))
                self.assertNotIn(value, '\n'.join(issues))

    @unittest.skipUnless(shutil.which('git'), 'Git is required for repository integration checks')
    def test_git_ignore_and_forced_tracked_env_are_checked(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        # Repository-level checks now require the disabled policy fixtures too.
        (self.root / 'config.py').write_text('REAL_TRADING=False\nSLACK_NOTIFICATIONS_ENABLED=False\n')
        policies = self.root / '.github/automation'
        policies.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1] / '.github/automation'
        for name in ('policy.json', 'zero-cost.json'):
            shutil.copyfile(source/name, policies/name)
        ignore = Path(__file__).resolve().parents[1] / '.gitignore'
        (self.root / '.gitignore').write_text(ignore.read_text(encoding='utf-8'), encoding='utf-8')
        subprocess.run(['git', '-C', str(self.root), 'add', 'config.py', '.gitignore'], check=True, capture_output=True)
        issues, count = check_repository(self.root)
        self.assertEqual(issues, [])
        self.assertEqual(count, 2)
        (self.root / '.env').write_text('SEC_KEY=fake-private-fixture', encoding='utf-8')
        subprocess.run(['git', '-C', str(self.root), 'add', '-f', '.env'], check=True, capture_output=True)
        issues, _ = check_repository(self.root)
        self.assertTrue(any('.env: sensitive file' in message for message in issues))
        self.assertNotIn('fake-private-fixture', '\n'.join(issues))


if __name__ == '__main__':
    unittest.main()
