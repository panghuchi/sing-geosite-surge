import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from geosite_dat import extract
from sources import load_sources
from merging import merge_category


def vi(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def field(number, value):
    if isinstance(value, int):
        return vi(number << 3) + vi(value)
    if isinstance(value, str):
        value = value.encode()
    return vi((number << 3) | 2) + vi(len(value)) + value


def domain(value, kind=2, attributes=()):
    return field(1, kind) + field(2, value) + b''.join(field(3, field(1, attr) + field(2, 1)) for attr in attributes)


def site(name, domains):
    # Deliberately put country_code last: protobuf field ordering is unspecified.
    return field(1, b''.join(field(2, d) for d in domains) + field(1, name))


class DatTests(unittest.TestCase):
    def test_attribute_filter_does_not_take_full_category(self):
        data = site('PAYPAL', [domain('paypal.com'), domain('paypal.com.cn', attributes=['cn'])])
        result = extract(data, ['paypal', 'paypal@cn', 'paypal@!cn'])
        self.assertEqual(result['paypal@cn']['rules'], [{'domain_suffix': ['paypal.com.cn']}])
        self.assertEqual(result['paypal@!cn']['rules'], [{'domain_suffix': ['paypal.com']}])
        self.assertEqual(len(result['paypal']['rules'][0]['domain_suffix']), 2)

    def test_explicit_attribute_category_precedes_attribute_filter(self):
        data = site('STEAM@CN', [domain('extra.cn')]) + site('STEAM', [domain('base.cn', attributes=['cn'])])
        self.assertEqual(extract(data, ['steam@cn'])['steam@cn']['rules'], [{'domain_suffix': ['extra.cn']}])

    def test_all_domain_types_and_unknown_fields(self):
        data = site('TEST', [domain('word', 0), domain('^a', 1), domain('a.com', 2), domain('exact.com', 3)]) + field(100, b'ignored')
        rule = extract(data, ['test'])['test']['rules'][0]
        self.assertEqual(set(rule), {'domain_keyword', 'domain_regex', 'domain_suffix', 'domain'})

    def test_malformed_and_unknown_type_fail(self):
        for data in (b'\x0a\x80', b'\x0a\x08x', b'\x00', site('test', [domain('a', 99)])):
            with self.subTest(data=data), self.assertRaises(ValueError):
                extract(data, ['test'])

    def test_missing_category_is_not_replaced_with_unrelated_category(self):
        self.assertEqual(extract(site('paypal', [domain('paypal.com')]), ['paypal@cn']), {})


class MergeTests(unittest.TestCase):
    def test_duplicate_normalization_and_shared_target(self):
        base = {'version': 1, 'rules': [{'domain': ['example.com'], 'domain_suffix': ['.example.com']}]}
        dat = site('TEST', [domain('example.com'), domain('extra.com')])
        configs = [{'id': 'first', 'format': 'v2ray-geosite', 'url': 'https://example.org/a'},
                   {'id': 'second', 'format': 'v2ray-geosite', 'url': 'https://example.org/b'}]
        sources = load_sources(configs, ['test'], lambda url: dat)
        merged, contributions = merge_category('test', [{'id': 'base', 'documents': {'test': base}, 'category_map': {'test': 'test'}}] + sources)
        self.assertEqual(merged['rules'], [{'domain_suffix': ['example.com', 'extra.com']}])
        self.assertEqual([x['added_entries'] for x in contributions], [1, 1, 0])
        self.assertTrue(all(s['sha256'] for s in sources))

    def test_category_mapping_and_missing_are_explicit(self):
        configs = [{'id': 'custom', 'format': 'v2ray-geosite', 'url': 'https://example.org/a', 'category_map': {'target': 'other@cn'}, 'on_missing': 'skip'}]
        dat = site('other', [domain('cn.example', attributes=['cn'])])
        loaded = load_sources(configs, ['target', 'missing'], lambda url: dat)
        self.assertEqual(loaded[0]['missing_categories'], ['missing'])
        base = {'version': 1, 'rules': [{'domain': ['a.com']}]}
        merged, report = merge_category('target', [{'id': 'base', 'documents': {'target': base}, 'category_map': {'target': 'target'}}] + loaded)
        self.assertIn('cn.example', merged['rules'][0]['domain_suffix'])
        self.assertEqual(report[1]['category'], 'other@cn')

    def test_duplicate_source_ids_and_unknown_adapters_fail(self):
        item = {'id': 'same', 'format': 'v2ray-geosite', 'url': 'https://example.org'}
        for configs in ([item, item], [dict(item, format='unknown')]):
            with self.assertRaises(ValueError):
                load_sources(configs, ['a'], lambda url: site('a', [domain('a.com')]))


class MissingSourceTests(unittest.TestCase):
    def test_missing_category_blocks_required_source(self):
        config = [{'id': 'required', 'format': 'v2ray-geosite', 'url': 'https://example.org'}]
        with self.assertRaisesRegex(ValueError, 'missing categories'):
            load_sources(config, ['paypal@cn'], lambda url: site('paypal', [domain('paypal.com')]))


class SrsAdapterTests(unittest.TestCase):
    def test_srs_template_and_fixed_geoip_share_adapter(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import sources
        configs = [
            {'id': 'domain-source', 'format': 'sing-box-srs', 'url_template': 'https://example.org/{category}.srs'},
            {'id': 'ip-source', 'format': 'sing-box-srs', 'url': 'https://example.org/ip.srs', 'targets': {'geoip-telegram': 'telegram'}}]
        def decode(command, **kwargs):
            ip = Path(command[3]).stem == 'telegram'
            rule = {'ip_cidr': ['1.2.3.0/24']} if ip else {'domain_suffix': ['example.cn']}
            Path(command[-1]).write_text(json.dumps({'version':1,'rules':[rule]}))
            return SimpleNamespace(stdout='')
        urls = []
        def fetch(url):
            urls.append(url)
            return b'fixture'
        with patch.object(sources.subprocess, 'run', side_effect=decode):
            loaded = load_sources(configs, ['apple@cn'], fetch, binary='fake')
        self.assertIn('https://example.org/apple%40cn.srs', urls)
        self.assertIn('apple@cn', loaded[0]['documents'])
        self.assertIn('geoip-telegram', loaded[1]['documents'])
        merged, report = merge_category('geoip-telegram', [loaded[1]])
        self.assertEqual(merged['rules'], [{'ip_cidr': ['1.2.3.0/24']}])
        self.assertTrue(report[0]['sha256'])


class SourceBoundaryTests(unittest.TestCase):
    def test_skip_missing_srs_category_does_not_swallow_other_errors(self):
        from urllib.error import HTTPError
        config = [{'id': 'optional', 'format': 'sing-box-srs', 'url': 'https://example.org/test.srs', 'on_missing': 'skip'}]
        def missing(url):
            raise HTTPError(url, 404, 'missing', None, None)
        self.assertEqual(load_sources(config, ['test'], missing)[0]['missing_categories'], ['test'])
        def failed(url):
            raise HTTPError(url, 500, 'failed', None, None)
        with self.assertRaises(HTTPError):
            load_sources(config, ['test'], failed)

    def test_empty_rule_cannot_disappear_during_union(self):
        source = {'id': 'bad', 'documents': {'test': {'version':1, 'rules':[{}]}}, 'category_map': {'test':'test'}}
        with self.assertRaisesRegex(ValueError, 'empty source rule'):
            merge_category('test', [source])
