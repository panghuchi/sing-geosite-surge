#!/usr/bin/env python3
"""Convert sing-geosite SRS files using the official sing-box decoder (stdlib only)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


class Unsupported(ValueError):
    pass


def values(value):
    return value if isinstance(value, list) else [value]


def safe_value(value):
    if not isinstance(value, str) or not value or any(c.isspace() or c in ',#;' for c in value):
        raise Unsupported('invalid rule value: ' + repr(value))
    return value


def hostname(value):
    value = safe_value(value).lower()
    if not re.fullmatch(r'[a-z0-9_-]+(?:\.[a-z0-9_-]+)*', value):
        raise Unsupported('unsupported hostname: ' + repr(value))
    return value


def convert(document):
    if document.get('version') not in (1, 2, 3, 4):
        raise Unsupported('unknown rule-set version')
    if set(document) - {'version', 'rules'}:
        raise Unsupported('unknown top-level fields')
    rules = document.get('rules')
    if not isinstance(rules, list) or not rules:
        raise Unsupported('empty or invalid rule-set')
    lines, domains, errors = set(), set(), []
    pure = True
    for rule in rules:
        if not isinstance(rule, dict):
            raise Unsupported('invalid rule object')
        unknown = set(rule) - {'type', 'invert', 'domain', 'domain_suffix', 'domain_keyword', 'domain_regex'}
        if unknown or rule.get('type', 'default') != 'default' or rule.get('invert', False) is not False:
            raise Unsupported('unsupported conditions: ' + repr(rule))
        if not any(rule.get(k) for k in ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex')):
            raise Unsupported('empty rule would match all traffic')
        for key in ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex'):
            for value in values(rule.get(key, [])):
                if key == 'domain':
                    value = hostname(value)
                    lines.add('DOMAIN,' + value)
                    domains.add(value)
                elif key == 'domain_suffix':
                    # sing-box: .example.com excludes the apex; example.com includes it.
                    leading_dot = isinstance(value, str) and value.startswith('.')
                    value = hostname(value[1:] if leading_dot else value)
                    if leading_dot:
                        lines.add('DOMAIN-WILDCARD,*.' + value)
                        pure = False
                    else:
                        lines.add('DOMAIN-SUFFIX,' + value)
                        domains.add('.' + value)
                elif key == 'domain_keyword':
                    lines.add('DOMAIN-KEYWORD,' + safe_value(value))
                    pure = False
                else:
                    errors.append(value)
    if errors:
        raise Unsupported('domain_regex has no general equivalent in Surge: ' + json.dumps(errors, ensure_ascii=False))
    # An explicit apex plus a dotted suffix is equivalent to Surge DOMAIN-SUFFIX.
    apexes = {line[7:] for line in lines if line.startswith('DOMAIN,')}
    for apex in apexes:
        wildcard = 'DOMAIN-WILDCARD,*.' + apex
        if wildcard in lines:
            lines.remove(wildcard)
            lines.discard('DOMAIN,' + apex)
            lines.add('DOMAIN-SUFFIX,' + apex)
            domains.discard(apex)
            domains.add('.' + apex)
    pure = all(line.startswith(('DOMAIN,', 'DOMAIN-SUFFIX,')) for line in lines)
    if not lines:
        raise Unsupported('empty conversion')
    return sorted(lines), sorted(domains) if pure else None


def write_lines(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def build(source, output, binary, include=None, workers=4, revision='local', strict=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output == source or output in source.parents or source in output.parents:
        raise ValueError('source and output must not contain each other')
    if output.exists():
        raise ValueError('output must be a new directory; existing output is never overwritten')
    files = sorted(source.glob('geosite-*.srs'))
    if include:
        requested = {'geosite-' + name.removeprefix('geosite-').removesuffix('.srs') + '.srs' for name in include}
        missing = requested - {p.name for p in files}
        if missing:
            raise ValueError('missing categories: ' + ', '.join(sorted(missing)))
        files = [p for p in files if p.name in requested]
    if not files:
        raise ValueError('no SRS files found')
    version = subprocess.run([binary, 'version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='surge-build-') as temp:
        root = Path(temp)
        def one(path):
            decoded = root / (path.stem + '.json')
            subprocess.run([binary, 'rule-set', 'decompile', str(path), '-o', str(decoded)],
                           check=True, capture_output=True, text=True, timeout=120)
            entry = {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            try:
                lines, domains = convert(json.loads(decoded.read_text(encoding='utf-8')))
            except Unsupported as error:
                entry.update(status='unsupported', reason=str(error))
                return path.stem, entry
            rulepath = 'ruleset/' + path.stem + '.list'
            write_lines(root / 'publish' / rulepath, lines)
            entry.update(status='complete', rules=len(lines), ruleset=rulepath)
            if domains is not None:
                domainpath = 'domainset/' + path.stem + '.txt'
                write_lines(root / 'publish' / domainpath, domains)
                entry['domainset'] = domainpath
            return path.stem, entry
        with ThreadPoolExecutor(max_workers=workers) as pool:
            entries = dict(pool.map(one, files))
        unsupported = {k: v['reason'] for k, v in entries.items() if v['status'] == 'unsupported'}
        complete = len(entries) - len(unsupported)
        if not complete or (strict and unsupported):
            raise Unsupported(f'{len(unsupported)} unsupported categories; publication blocked: ' + json.dumps(unsupported, ensure_ascii=False))
        manifest = {'upstream': 'https://github.com/SagerNet/sing-geosite/tree/rule-set',
                    'upstream_revision': revision, 'decoder': version,
                    'complete': complete, 'unsupported': len(unsupported), 'categories': entries}
        publish = root / 'publish'
        (publish / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        report = ['# Conversion report', '', f'Upstream commit: {revision}', '',
                  f'Complete: {complete}; unsupported (not published): {len(unsupported)}.', '',
                  'See manifest.json for category paths and reasons. Unsupported categories have no subscription file.', '']
        for name, reason in unsupported.items():
            report.extend([f'## {name}', '', '```text', reason, '```', ''])
        (publish / 'README.md').write_text('\n'.join(report), encoding='utf-8')
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(publish, output)
    print(f'Complete: {complete}; unsupported: {len(unsupported)}; output: {output}')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', default='dist')
    parser.add_argument('--sing-box', default='sing-box')
    parser.add_argument('--include', nargs='+', help='category names')
    parser.add_argument('--config', help='JSON file containing a nonempty categories array')
    parser.add_argument('--all', action='store_true', help='explicitly convert every category')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--revision', default='local')
    parser.add_argument('--strict', action='store_true', help='block all publication if any category is unsupported')
    args = parser.parse_args()
    try:
        if sum(bool(x) for x in (args.include, args.config, args.all)) != 1:
            raise ValueError('choose exactly one of --include, --config, or --all')
        include = args.include
        if args.config:
            include = json.loads(Path(args.config).read_text(encoding='utf-8')).get('categories')
            if not isinstance(include, list) or not include or not all(isinstance(x, str) and x for x in include):
                raise ValueError('categories is empty or invalid; configure the requested scope first')
        build(args.source, args.output, args.sing_box, include, args.workers, args.revision, args.strict)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Build failed (no publication): {error}\n')


if __name__ == '__main__':
    main()
