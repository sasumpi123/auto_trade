"""README stays consistent with the repository: links resolve, cited entry points exist, the tree is real."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
README=(ROOT/'README.md').read_text(encoding='utf-8')


class ReadmeTests(unittest.TestCase):
    def test_relative_links_point_to_existing_files(self):
        links=[t for t in re.findall(r'\]\(([^)\s]+)\)',README) if not re.match(r'[a-z]+://|#',t)]
        self.assertTrue(links)
        for target in links:
            with self.subTest(target=target):self.assertTrue((ROOT/target.split('#')[0]).exists())

    def test_cited_modules_scripts_and_tree_exist(self):
        modules=set(re.findall(r'python -m ([A-Za-z_][\w.]*)',README))-{'pip','venv'}  # Install tooling, not project entry points.
        self.assertTrue({'backtesting','backtesting.p36_history','ops.paper_bot','unittest'}<=modules)
        for module in modules:
            with self.subTest(module=module):self.assertIsNotNone(importlib.util.find_spec(module))
        for script in re.findall(r'\.venv/bin/python ([\w/]+\.py)',README)+re.findall(r'python ([\w/]+\.py)',README):
            with self.subTest(script=script):self.assertTrue((ROOT/script).is_file())
        tree=README.split('```\nauto_trade/',1)[1].split('```',1)[0]
        for name in re.findall(r'[├└]── ([\w.]+/?)',tree):
            with self.subTest(name=name):
                path=ROOT/name.rstrip('/');self.assertTrue(path.is_dir() if name.endswith('/') else path.is_file())

    def test_stated_facts_match_code(self):
        import config
        self.assertIn('Python 3.12',README);self.assertNotIn('3.8',README)
        self.assertIn('python-version: \'3.12\'',(ROOT/'.github/workflows/tests.yml').read_text(encoding='utf-8'))
        self.assertIs(config.REAL_TRADING,False);self.assertIs(config.SLACK_NOTIFICATIONS_ENABLED,False)
        self.assertEqual((config.MAX_COINS_AT_ONCE,config.CASH_USAGE_RATIO,config.MIN_TRADING_AMOUNT,config.STOP_LOSS),(2,0.4,5000,0.05))
        for stale in ('ACC_KEY=your','APP_TOKEN=your','목표수익률'):self.assertNotIn(stale,README)


if __name__=='__main__':unittest.main()
