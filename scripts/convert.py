#!/usr/bin/env python3
"""Convert sing-geosite SRS files using the official sing-box decoder (stdlib only)."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from regex_tools import native_rules, script_source, RegexUnsupported
from sources import download, load_sources, merge_documents
from urllib.parse import urlparse


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


def convert(document, regex_handler=None):
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
        unknown = set(rule) - {'type', 'invert', 'domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'ip_cidr'}
        if unknown or rule.get('type', 'default') != 'default' or rule.get('invert', False) is not False:
            raise Unsupported('unsupported conditions: ' + repr(rule))
        if not any(rule.get(k) for k in ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'ip_cidr')):
            raise Unsupported('empty rule would match all traffic')
        if rule.get('ip_cidr'):
            if any(rule.get(k) for k in ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex')):
                raise Unsupported('mixed IP/domain conditions need explicit logical conversion')
            for cidr in values(rule['ip_cidr']):
                try:
                    network = ipaddress.ip_network(cidr, strict=False)
                except (ValueError, TypeError) as error:
                    raise Unsupported('invalid IP prefix: ' + repr(cidr)) from error
                kind = 'IP-CIDR' if network.version == 4 else 'IP-CIDR6'
                lines.add(f'{kind},{network},no-resolve')
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
                    try:
                        lines.update(native_rules(value))
                    except RegexUnsupported:
                        if regex_handler is None:
                            errors.append(value)
                        else:
                            lines.add('SCRIPT,' + regex_handler(value))
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


def build(source, output, binary, include=None, workers=4, revision='local', strict=False, extra_files=None, aliases=None, base_url="https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules", supplemental_sources=None):
    base_url = base_url.rstrip("/")
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
    files.extend(Path(p) for p in (extra_files or {}))
    if len({p.stem for p in files}) != len(files):
        raise ValueError('duplicate source names')
    if not files:
        raise ValueError('no SRS files found')
    version = subprocess.run([binary, 'version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='surge-build-') as temp:
        root = Path(temp)
        def one(path):
            decoded = root / (path.stem + '.json')
            subprocess.run([binary, 'rule-set', 'decompile', str(path), '-o', str(decoded)],
                           check=True, capture_output=True, text=True, timeout=120)
            entry = {'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'source_url': (extra_files or {}).get(path, 'https://raw.githubusercontent.com/SagerNet/sing-geosite/' + revision + '/' + path.name)}
            document = json.loads(decoded.read_text(encoding='utf-8'))
            if path.stem.startswith('geosite-'):
                document, entry['contributions'] = merge_documents(document, path.stem.removeprefix('geosite-'), supplemental_sources or [])
            fallback_patterns = []
            script_name = 'srs-regex-' + hashlib.sha256(path.stem.encode()).hexdigest()[:16]
            def handle_regex(pattern):
                try:
                    script_source([pattern])
                except (RegexUnsupported, re.error) as error:
                    raise Unsupported(str(error)) from error
                fallback_patterns.append(pattern)
                return script_name
            try:
                lines, domains = convert(document, handle_regex)
            except Unsupported as error:
                entry.update(status='unsupported', reason=str(error))
                return path.stem, entry
            rulepath = 'ruleset/' + path.stem + '.list'
            write_lines(root / 'publish' / rulepath, lines)
            entry.update(status='complete', rules=len(lines), ruleset=rulepath)
            if fallback_patterns:
                scriptpath = 'scripts/' + script_name + '.js'
                write_lines(root / 'publish' / scriptpath, [script_source(fallback_patterns).rstrip()])
                entry.update(script=scriptpath, script_name=script_name,
                             requires_module='regex-support.sgmodule', regex_patterns=sorted(set(fallback_patterns)))
            if domains is not None:
                domainpath = 'domainset/' + path.stem + '.txt'
                write_lines(root / 'publish' / domainpath, domains)
                entry['domainset'] = domainpath
            return path.stem, entry
        with ThreadPoolExecutor(max_workers=workers) as pool:
            entries = dict(pool.map(one, files))
        for alias, original in (aliases or {}).items():
            if not re.fullmatch(r'geosite-[a-z0-9@!_-]+', alias) or original not in entries or alias in entries:
                raise ValueError('invalid category alias: ' + alias)
            entry = dict(entries[original], alias_of=original)
            if entry['status'] == 'complete':
                for key in ('ruleset', 'domainset'):
                    if key in entry:
                        old = entry[key]
                        new = old.replace(original, alias)
                        shutil.copyfile(root / 'publish' / old, root / 'publish' / new)
                        entry[key] = new
            entries[alias] = entry
        unsupported = {k: v['reason'] for k, v in entries.items() if v['status'] == 'unsupported'}
        complete = len(entries) - len(unsupported)
        if not complete or (strict and unsupported):
            raise Unsupported(f'{len(unsupported)} unsupported categories; publication blocked: ' + json.dumps(unsupported, ensure_ascii=False))
        module = ['#!name=Sing Geosite Regex Support',
                  '#!desc=Registers hostname regex matchers used by SRS RULE-SETs. Enable scripting. No routing, DNS, or MITM settings.',
                  '', '[Script]']
        registered = set()
        for entry in entries.values():
            if entry.get('script_name') and entry['script_name'] not in registered:
                registered.add(entry['script_name'])
                module.append(entry['script_name'] + ' = type=rule,script-path=' + base_url + '/' + entry['script'] + ',script-update-interval=86400,engine=jsc')
        if registered:
            write_lines(root / 'publish' / 'regex-support.sgmodule', module)
        manifest = {'upstream': 'https://github.com/SagerNet/sing-geosite/tree/rule-set',
                    'upstream_revision': revision, 'decoder': version,
                    'complete': complete, 'unsupported': len(unsupported),
                    'geosite_sources': [{key: value for key, value in item.items() if key not in ('documents', 'category_map')} for item in (supplemental_sources or [])],
                    'categories': entries}
        publish = root / 'publish'
        (publish / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        report = ['# Conversion report', '', f'Upstream commit: {revision}', '',
                  f'Complete: {complete}; unsupported (not published): {len(unsupported)}.', '',
                  'See manifest.json for category paths and reasons. Unsupported categories have no subscription file.', '',
                  '**For categories marked SCRIPT: install and enable [Regex Support](regex-support.sgmodule) and enable Surge scripting before using their RULE-SETs. Without the module these categories are incomplete.**' if registered else 'All published categories use native rules.', '']
        report.extend(['## Available subscriptions', '', '| Category | RULE-SET | DOMAIN-SET | Requires module |', '| --- | --- | --- | --- |'])
        for name, entry in entries.items():
            if entry['status'] == 'complete':
                domain_link = f"[Download]({entry['domainset']})" if 'domainset' in entry else '—'
                report.append(f"| {name} | [Download]({entry['ruleset']}) | {domain_link} | {'SCRIPT' if entry.get('script') else 'No'} |")
        report.extend(['', '## Source contributions', '',
                       'Counts are deduplicated domain atoms, not guaranteed newly covered hostnames. Source order: SagerNet, then configured supplements.', '',
                       '| Category | Source | Entries | Added entries |', '| --- | --- | --- | --- |'])
        for name, entry in entries.items():
            if entry.get('alias_of'):
                continue
            for item in entry.get('contributions', []):
                report.append(f"| {name} | {item['id']} | {item.get('entries', 'missing')} | {item.get('added_entries', '—')} |")
        report.append('')
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
        config = {}
        if args.config:
            config = json.loads(Path(args.config).read_text(encoding='utf-8'))
            include = config.get('categories')
            if not isinstance(include, list) or not include or not all(isinstance(x, str) and x for x in include):
                raise ValueError('categories is empty or invalid; configure the requested scope first')
        with tempfile.TemporaryDirectory(prefix='surge-extra-') as temp:
            extra_files = {}
            for entry in config.get('extra_sources', []):
                tag, url = entry['tag'], entry['url']
                if not re.fullmatch(r'geoip-[a-z0-9_-]+', tag) or urlparse(url).scheme != 'https':
                    raise ValueError('invalid extra source')
                path = Path(temp) / (tag + '.srs')
                if path in extra_files:
                    raise ValueError('duplicate extra source: ' + tag)
                path.write_bytes(download(url))
                extra_files[path] = url
            supplements = load_sources(config.get('geosite_sources', []), include or [p.stem.removeprefix('geosite-') for p in Path(args.source).glob('geosite-*.srs')])
            build(args.source, args.output, args.sing_box, include, args.workers, args.revision,
                  args.strict or config.get('strict', False), extra_files, config.get('aliases'), config.get('publish_base_url', 'https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules'), supplements)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Build failed (no publication): {error}\n')


if __name__ == '__main__':
    main()
