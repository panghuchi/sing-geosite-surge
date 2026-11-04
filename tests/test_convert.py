import importlib.util
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import unittest

spec = importlib.util.spec_from_file_location('convert', Path(__file__).resolve().parents[1] / 'scripts/convert.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def doc(**rule):
    return {'version': 1, 'rules': [rule]}


class ConversionTests(unittest.TestCase):
    def test_exact_suffix_deduplication(self):
        lines, domains = m.convert(doc(domain=['exact.test', 'exact.test'], domain_suffix='example.com'))
        self.assertEqual(lines, ['DOMAIN,exact.test', 'DOMAIN-SUFFIX,example.com'])
        self.assertEqual(domains, ['.example.com', 'exact.test'])

    def test_dotted_suffix_excludes_apex(self):
        self.assertEqual(m.convert(doc(domain_suffix='.example.com')), (['DOMAIN-WILDCARD,*.example.com'], None))

    def test_apex_plus_dotted_suffix_can_be_domainset(self):
        self.assertEqual(m.convert(doc(domain='example.com', domain_suffix='.example.com')),
                         (['DOMAIN-SUFFIX,example.com'], ['.example.com']))

    def test_ipv4_ipv6_and_host_addresses(self):
        lines, domains = m.convert(doc(ip_cidr=['1.2.3.4/24', '2001:db8::/32', '8.8.8.8']))
        self.assertEqual(lines, ['IP-CIDR,1.2.3.0/24,no-resolve', 'IP-CIDR,8.8.8.8/32,no-resolve', 'IP-CIDR6,2001:db8::/32,no-resolve'])
        self.assertIsNone(domains)

    def test_invalid_ip_and_mixed_conditions_are_rejected(self):
        for rule in ({'ip_cidr': 'invalid'}, {'ip_cidr': '10.0.0.0/8', 'domain': 'a.com'}):
            with self.subTest(rule=rule), self.assertRaises(m.Unsupported):
                m.convert(doc(**rule))

    def test_keyword_requires_ruleset(self):
        self.assertEqual(m.convert(doc(domain_keyword='google')), (['DOMAIN-KEYWORD,google'], None))

    def test_regex_rejects_entire_category(self):
        with self.assertRaises(m.Unsupported):
            m.convert(doc(domain='example.com', domain_regex=r'^cdn[0-9]+\.example.com$'))

    def test_logic_unknown_conditions_and_empty_fail(self):
        for rule in ({'type': 'logical', 'mode': 'and', 'rules': []},
                     {'domain': 'test.com', 'invert': True},
                     {'domain': 'test.com', 'port': 443}, {}, {'domain': 'test.com\nFINAL,DIRECT'}):
            with self.subTest(rule=rule), self.assertRaises(m.Unsupported):
                m.convert(doc(**rule))

    def test_multiple_rules_are_union(self):
        lines, domains = m.convert({'version': 1, 'rules': [{'domain': 'a.com'}, {'domain': 'b.com'}]})
        self.assertEqual(domains, ['a.com', 'b.com'])


if __name__ == '__main__':
    unittest.main()
