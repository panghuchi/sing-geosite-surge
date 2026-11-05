#!/usr/bin/env python3
"""Load configured sources, merge normalized rules, and assemble Surge outputs."""
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
from sources import load_sources
from merging import merge_category
from aggregation import project_sources


class Unsupported(ValueError):
    pass


def values(value):
    return value if isinstance(value, list) else [value]


def safe_value(value, spaces=False):
    if not isinstance(value, str) or not value or any((c.isspace() and not (spaces and c == ' ')) or c in ',#;' for c in value):
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
        unknown = set(rule) - {'type', 'invert', 'domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'ip_cidr', 'process_name', 'ip_asn'}
        if unknown or rule.get('type', 'default') != 'default' or rule.get('invert', False) is not False:
            raise Unsupported('unsupported conditions: ' + repr(rule))
        if not any(rule.get(k) for k in ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex', 'ip_cidr', 'process_name', 'ip_asn')):
            raise Unsupported('empty rule would match all traffic')
        for process in values(rule.get('process_name', [])):
            lines.add('PROCESS-NAME,' + safe_value(process, spaces=True))
        for asn in values(rule.get('ip_asn', [])):
            if not str(asn).isdigit() or not 0 < int(asn) <= 4294967295:
                raise Unsupported('invalid ASN')
            lines.add('IP-ASN,' + str(int(asn)) + ',no-resolve')
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


def build(loaded_sources, output, binary='sing-box', workers=4, strict=True, aliases=None,
          base_url='https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules', output_paths=None):
    """Merge normalized sources, then assemble exactly one output per target."""
    base_url = base_url.rstrip('/')
    output_paths = output_paths or {}
    if any(not re.fullmatch(r'[a-z0-9@!_-]+(?:/[a-z0-9@!_-]+)*', path) for path in output_paths.values()):
        raise ValueError('invalid output path')
    if len(set(output_paths.values())) != len(output_paths):
        raise ValueError('duplicate output paths')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('output must be a new directory; existing output is never overwritten')
    targets = sorted({target for source in loaded_sources for target in source['category_map']})
    if not targets:
        raise ValueError('no configured targets')
    source_metadata = [{key: value for key, value in source.items() if key not in ('documents', 'category_map')}
                       for source in loaded_sources]
    revision = hashlib.sha256(json.dumps(source_metadata, sort_keys=True).encode()).hexdigest()
    version = subprocess.run([binary, 'version'], check=True, capture_output=True, text=True).stdout.splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='surge-build-') as temp:
        root = Path(temp)
        def one(target):
            tag = target if target in output_paths or target.startswith(('geosite-', 'geoip-')) else 'geosite-' + target
            document, contributions = merge_category(target, [source for source in loaded_sources if target in source['category_map']])
            entry = {'contributions': contributions}
            fallback_patterns = []
            script_name = 'srs-regex-' + hashlib.sha256(tag.encode()).hexdigest()[:16]
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
                return tag, entry
            rulepath = 'ruleset/' + output_paths.get(target, tag) + '.list'
            write_lines(root / 'publish' / rulepath, lines)
            entry.update(status='complete', rules=len(lines), ruleset=rulepath)
            if fallback_patterns:
                scriptpath = 'scripts/' + script_name + '.js'
                write_lines(root / 'publish' / scriptpath, [script_source(fallback_patterns).rstrip()])
                entry.update(script=scriptpath, script_name=script_name,
                             requires_module='regex-support.sgmodule', regex_patterns=sorted(set(fallback_patterns)))
            if domains is not None:
                domainpath = 'domainset/' + output_paths.get(target, tag) + '.txt'
                write_lines(root / 'publish' / domainpath, domains)
                entry['domainset'] = domainpath
            return tag, entry
        with ThreadPoolExecutor(max_workers=workers) as pool:
            entries = dict(pool.map(one, targets))
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
        manifest = {'upstream': 'config.json',
                    'upstream_revision': revision, 'decoder': version,
                    'complete': complete, 'unsupported': len(unsupported),
                    'sources': [{key: value for key, value in item.items() if key not in ('documents', 'category_map')} for item in loaded_sources],
                    'categories': entries}
        publish = root / 'publish'
        for source in loaded_sources:
            for name, content in source.get('notices', {}).items():
                notice = publish / 'licenses' / source['id'] / Path(name).name
                write_lines(notice, [content.rstrip()])
        (publish / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        report = ['# Conversion report', '', f'Source fingerprint: {revision}', '',
                  f'Complete: {complete}; unsupported (not published): {len(unsupported)}.', '',
                  'See manifest.json for category paths and reasons. Unsupported categories have no subscription file.', '',
                  '**For categories marked SCRIPT: install and enable [Regex Support](regex-support.sgmodule) and enable Surge scripting before using their RULE-SETs. Without the module these categories are incomplete.**' if registered else 'All published categories use native rules.', '']
        report.extend(['## Available subscriptions', '', '| Category | RULE-SET | DOMAIN-SET | Requires module |', '| --- | --- | --- | --- |'])
        for name, entry in entries.items():
            if entry['status'] == 'complete':
                domain_link = f"[Download]({entry['domainset']})" if 'domainset' in entry else '—'
                report.append(f"| {name} | [Download]({entry['ruleset']}) | {domain_link} | {'SCRIPT' if entry.get('script') else 'No'} |")
        report.extend(['', '## Source contributions', '',
                       'Counts are deduplicated rule atoms, not guaranteed newly covered hostnames. Sources are processed in configuration order.', '',
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
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--output', default='dist')
    parser.add_argument('--sing-box', default='sing-box')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text(encoding='utf-8'))
        categories = config.get('categories')
        if not isinstance(categories, list) or not categories or not all(isinstance(name, str) for name in categories):
            raise ValueError('configure a nonempty categories array')
        sources = load_sources(config['sources'], categories, binary=args.sing_box)
        sources = project_sources(sources, config.get('aggregations', []))
        build(sources, args.output, args.sing_box, args.workers,
              args.strict or config.get('strict', True), config.get('aliases'),
              config.get('publish_base_url', 'https://raw.githubusercontent.com/panghuchi/sing-geosite-surge/rules'), config.get('output_paths'))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Build failed (no publication): {error}\n')


if __name__ == '__main__':
    main()
