import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import convert


class BuildTests(unittest.TestCase):
    def test_script_dependencies_aliases_and_native_categories(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'dist'
            loaded = [
                {'id': 'first', 'url': 'https://example.org/base', 'sha256': 'base',
                 'documents': {'google': {'version': 1, 'rules': [{'domain_regex': r'^r+[0-9]+\.googlevideo\.com$'}]},
                               'netflix': {'version': 1, 'rules': [{'domain_regex': r'(^|\.)apiproxy-.+\.amazonaws\.com$'}]}},
                 'category_map': {'google': 'google', 'netflix': 'netflix'}, 'missing_categories': []},
                {'id': 'additional', 'url': 'https://example.org/geosite.dat', 'sha256': 'fixture',
                 'documents': {'google': {'version': 1, 'rules': [{'domain_suffix': ['new.example']}]},
                               'netflix': {'version': 1, 'rules': [{'domain_suffix': ['netflix.com']}]}},
                 'category_map': {'google': 'google', 'netflix': 'netflix'}, 'missing_categories': []}]
            with patch.object(convert.subprocess, 'run', return_value=SimpleNamespace(stdout='sing-box version test\n')):
                manifest = convert.build(loaded, output, 'fake-decoder', strict=True,
                                         aliases={'geosite-google-alias': 'geosite-google'})
            self.assertEqual(manifest['unsupported'], 0)
            self.assertEqual(manifest['complete'], 3)
            google = manifest['categories']['geosite-google']
            netflix = manifest['categories']['geosite-netflix']
            self.assertNotIn('script', netflix)
            self.assertIn('DOMAIN-SUFFIX,new.example', (output / google['ruleset']).read_text())
            self.assertEqual(google['contributions'][1]['added_entries'], 1)
            self.assertEqual(len(manifest['sources']), 2)
            self.assertNotIn('documents', manifest['sources'][0])
            self.assertEqual(google['requires_module'], 'regex-support.sgmodule')
            module = (output / 'regex-support.sgmodule').read_text()
            self.assertEqual(module.count(' = type=rule,'), 1)
            self.assertIn(google['script_name'], module)
            self.assertIn('SCRIPT,' + google['script_name'], (output / google['ruleset']).read_text())
            self.assertEqual((output / google['ruleset']).read_text(), (output / 'ruleset/geosite-google-alias.list').read_text())
            self.assertTrue((output / google['script']).exists())
            self.assertNotIn('domainset', google)
