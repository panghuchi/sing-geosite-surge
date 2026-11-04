"""Differential tests against source regexes, including real selected upstream patterns."""
import fnmatch
import json
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from regex_tools import native_rules, script_source, RegexUnsupported, parsed, C
from convert import convert


def witness(tree, rng):
    result = ''
    for op, arg in tree:
        if op == C.LITERAL:
            result += chr(arg)
        elif op == C.NOT_LITERAL:
            result += 'a' if arg != ord('a') else 'b'
        elif op == C.ANY:
            result += rng.choice('ab0.-')
        elif op == C.CATEGORY:
            result += rng.choice('0123456789') if arg == C.CATEGORY_DIGIT else 'x'
        elif op == C.IN:
            if arg[0][0] == C.NEGATE:
                result += 'a'
            else:
                child_op, child = rng.choice(list(arg))
                if child_op == C.LITERAL:
                    result += chr(child)
                elif child_op == C.RANGE:
                    result += chr(rng.randint(*child))
                else:
                    result += '1'
        elif op == C.SUBPATTERN:
            result += witness(arg[3], rng)
        elif op == C.BRANCH:
            result += witness(rng.choice(arg[1]), rng)
        elif op in (C.MAX_REPEAT, C.MIN_REPEAT):
            low, high, child = arg
            high = low + 3 if high == C.MAXREPEAT else min(high, low + 3)
            result += ''.join(witness(child, rng) for _ in range(rng.randint(low, high)))
    return result


def cases(pattern, rng):
    hosts = {'example.com', 'amazonaws.com', '', 'notnetflix.com', 'hses8.akamaized.net'}
    for _ in range(12):
        host = witness(parsed(pattern), rng)
        hosts.update([host, 'x.' + host, 'x' + host, host + '.invalid', host[1:], host[:-1], host.replace('.', '-'), host.replace('1', 'a')])
    return sorted(hosts)


class RegexTests(unittest.TestCase):
    def test_netflix_boundary_and_nonempty_middle(self):
        pattern = r'(^|\.)apiproxy-device-prod-nlb-.+\.amazonaws\.com$'
        rules = native_rules(pattern)
        for host, expected in [
            ('apiproxy-device-prod-nlb-a.amazonaws.com', True),
            ('x.apiproxy-device-prod-nlb-a.amazonaws.com', True),
            ('apiproxy-device-prod-nlb-.amazonaws.com', False),
            ('notapiproxy-device-prod-nlb-a.amazonaws.com', False),
            ('apiproxy-device-prod-nlb-a.amazonaws.com.evil', False),
        ]:
            self.assertEqual(any(fnmatch.fnmatchcase(host, r.split(',', 1)[1]) for r in rules), expected, host)

    def test_disney_optional_character_class(self):
        rules, domains = convert({'version': 1, 'rules': [{'domain_regex': r'^hses[1-7]?\.akamaized\.net$'}]})
        self.assertIsNone(domains)
        for host, expected in [('hses.akamaized.net', True), ('hses1.akamaized.net', True), ('hses7.akamaized.net', True), ('hses8.akamaized.net', False), ('hses11.akamaized.net', False), ('x.hses1.akamaized.net', False)]:
            self.assertEqual(any(fnmatch.fnmatchcase(host, r.split(',', 1)[1]) for r in rules), expected)

    def test_constrained_unbounded_repeat_is_not_broadened(self):
        with self.assertRaises(RegexUnsupported):
            native_rules(r'^cdn[0-9]+\.example\.com$')
        with self.assertRaises(RegexUnsupported):
            script_source(['(?i)example'])
        with self.assertRaises(RegexUnsupported):
            script_source([r'(a)\1'])

    def test_all_native_upstream_patterns_against_regex(self):
        patterns = json.loads((Path(__file__).parent / 'fixtures/regex-patterns.json').read_text())
        rng = random.Random(11)
        converted = 0
        for pattern in patterns:
            try:
                globs = [r.split(',', 1)[1] for r in native_rules(pattern)]
            except RegexUnsupported:
                continue
            converted += 1
            for host in cases(pattern, rng):
                expected = bool(re.search(pattern, host))
                self.assertEqual(any(fnmatch.fnmatchcase(host, g) for g in globs), expected, (pattern, host, globs))
        self.assertGreater(converted, 100)

    @unittest.skipUnless(shutil.which('node'), 'Node required for JavaScript differential tests')
    def test_all_upstream_regexes_in_javascript_and_generated_script(self):
        patterns = json.loads((Path(__file__).parent / 'fixtures/regex-patterns.json').read_text())
        rng = random.Random(29)
        checks = []
        for pattern in patterns:
            script_source([pattern])
            for host in cases(pattern, rng):
                checks.append([pattern, host, bool(re.search(pattern, host))])
        js = '''
const fs = require('fs'); const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
for (const [pattern, host, expected] of input.checks) {
  if (new RegExp(pattern).test(host) !== expected) throw Error(JSON.stringify([pattern,host,expected]));
}
for (const [host, expected] of [['cdn123.example.com', true], ['cdna.example.com', false], [null, false], ['', false]]) {
  let result; vm.runInNewContext(input.script, {$request:{hostname:host}, $done:r=>result=r});
  if (result.matched !== expected) throw Error('generated script result: '+host);
}
'''
        payload = {'checks': checks, 'script': script_source([r'^cdn[0-9]+\.example\.com$'])}
        result = subprocess.run(['node', '-e', js], input=json.dumps(payload), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
