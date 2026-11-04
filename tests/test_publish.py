import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('publish', Path(__file__).resolve().parents[1] / 'scripts/publish.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PublishTests(unittest.TestCase):
    def test_regression_leaves_old_publication_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.mkdir(); target.mkdir()
            (target / '.git').write_text('gitdir: placeholder')
            old = {'categories': {'google': {'status': 'complete'}}}
            (target / 'manifest.json').write_text(json.dumps(old))
            (target / 'old.list').write_text('DOMAIN,google.com\n')
            (source / 'manifest.json').write_text(json.dumps({'categories': {'google': {'status': 'unsupported'}}}))
            with self.assertRaises(ValueError):
                m.prepare(source, target)
            self.assertEqual((target / 'old.list').read_text(), 'DOMAIN,google.com\n')
            self.assertEqual(json.loads((target / 'manifest.json').read_text()), old)

    def test_success_removes_obsolete_outputs_preserving_git(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'target'
            source.mkdir(); target.mkdir()
            (target / '.git').write_text('gitdir: placeholder')
            (target / 'obsolete.txt').write_text('old')
            current = {'categories': {}, 'upstream_revision': 'abc', 'complete': 1, 'unsupported': 0}
            (source / 'manifest.json').write_text(json.dumps(current))
            m.prepare(source, target)
            self.assertTrue((target / '.git').exists())
            self.assertFalse((target / 'obsolete.txt').exists())
            self.assertEqual(json.loads((target / 'status.json').read_text())['upstream_revision'], 'abc')
