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
            source, output = Path(temp) / 'source', Path(temp) / 'dist'
            source.mkdir()
            for name in ('google', 'netflix'):
                (source / ('geosite-' + name + '.srs')).write_bytes(b'fixture')
            def decoder(command, **kwargs):
                if command[1] == 'version':
                    return SimpleNamespace(stdout='sing-box version test\n')
                pattern = r'^r+[0-9]+\.googlevideo\.com$' if 'google' in command[3] else r'(^|\.)apiproxy-.+\.amazonaws\.com$'
                Path(command[-1]).write_text(json.dumps({'version':1, 'rules':[{'domain_regex':pattern}]}))
                return SimpleNamespace(stdout='')
            with patch.object(convert.subprocess, 'run', side_effect=decoder):
                manifest = convert.build(source, output, 'fake-decoder', ['google', 'netflix'], strict=True,
                                         aliases={'geosite-google-alias': 'geosite-google'},
                                         supplemental_sources=[{
                                             'id': 'additional', 'url': 'https://example.org/geosite.dat', 'sha256': 'fixture',
                                             'documents': {'google': {'version': 1, 'rules': [{'domain_suffix': ['new.example']}]},
                                                           'netflix': {'version': 1, 'rules': [{'domain_suffix': ['netflix.com']}]}},
                                             'category_map': {'google': 'google', 'netflix': 'netflix'}, 'missing_categories': []}])
            self.assertEqual(manifest['unsupported'], 0)
            self.assertEqual(manifest['complete'], 3)
            google = manifest['categories']['geosite-google']
            netflix = manifest['categories']['geosite-netflix']
            self.assertNotIn('script', netflix)
            self.assertIn('DOMAIN-SUFFIX,new.example', (output / google['ruleset']).read_text())
            self.assertEqual(google['contributions'][1]['added_entries'], 1)
            self.assertEqual(len(manifest['geosite_sources']), 1)
            self.assertNotIn('documents', manifest['geosite_sources'][0])
            self.assertEqual(google['requires_module'], 'regex-support.sgmodule')
            module = (output / 'regex-support.sgmodule').read_text()
            self.assertEqual(module.count(' = type=rule,'), 1)
            self.assertIn(google['script_name'], module)
            self.assertIn('SCRIPT,' + google['script_name'], (output / google['ruleset']).read_text())
            self.assertEqual((output / google['ruleset']).read_text(), (output / 'ruleset/geosite-google-alias.list').read_text())
            self.assertTrue((output / google['script']).exists())
            self.assertNotIn('domainset', google)
