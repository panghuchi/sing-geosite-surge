"""Source adapters return source-format rule documents; they never write outputs."""
import hashlib
import re
import time
from urllib.parse import urlparse
from urllib.request import urlopen
from geosite_dat import extract


def download(url, max_bytes=128 * 1024 * 1024):
    if urlparse(url).scheme != 'https':
        raise ValueError('source URL must use HTTPS')
    for attempt in range(3):
        try:
            with urlopen(url, timeout=60) as response:
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes or not data:
                raise ValueError('source is empty or exceeds size limit')
            return data
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2)


def load_dat(data, categories):
    return extract(data, categories)


# New formats register a reader here. Readers only need to produce the same IR.
ADAPTERS = {'v2ray-geosite': load_dat}
DOMAIN_FIELDS = ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex')


def category_name(name):
    name = name.removeprefix('geosite-').removesuffix('.srs').lower()
    if not re.fullmatch(r'[a-z0-9@!_-]+', name):
        raise ValueError('invalid category name: ' + name)
    return name


def load_sources(configs, categories, fetch=download):
    categories = [category_name(name) for name in categories]
    result, ids = [], set()
    for config in configs:
        identity = config['id']
        if not re.fullmatch(r'[a-z0-9_-]+', identity) or identity in ids or identity == 'sagernet':
            raise ValueError('invalid or duplicate source id: ' + identity)
        ids.add(identity)
        adapter = ADAPTERS.get(config['format'])
        if adapter is None:
            raise ValueError('unknown source format: ' + config['format'])
        mapping = config.get('category_map', {})
        policy = config.get('on_missing', 'error')
        if policy not in ('error', 'skip'):
            raise ValueError('on_missing must be error or skip')
        selected = {target: mapping.get(target, target) for target in categories}
        if any(not isinstance(name, str) or not name for name in selected.values()):
            raise ValueError('invalid source category mapping')
        data = fetch(config['url'])
        decoded = adapter(data, selected.values())
        missing = sorted(target for target, name in selected.items() if name not in decoded)
        if missing and policy == 'error':
            raise ValueError(identity + ': missing categories: ' + ', '.join(missing))
        result.append({'id': identity, 'url': config['url'],
                       'sha256': hashlib.sha256(data).hexdigest(),
                       'documents': {target: decoded[name] for target, name in selected.items() if name in decoded},
                       'category_map': selected,
                       'missing_categories': missing})
    return result


def domain_entries(document):
    """Canonical domain atoms for semantic union; dotted suffixes remain distinct."""
    entries = set()
    for rule in document['rules']:
        if set(rule) - set(DOMAIN_FIELDS) - {'type', 'invert'} or rule.get('type', 'default') != 'default' or rule.get('invert', False):
            raise ValueError('supplemental source contains unsupported conditions')
        for kind in DOMAIN_FIELDS:
            values = rule.get(kind, [])
            if isinstance(values, str):
                values = [values]
            for value in values:
                # DNS names/keywords are case insensitive; regex literals are not.
                entries.add((kind, value if kind == 'domain_regex' else value.lower()))
    # RootDomain and exact-apex+dotted-suffix describe the same set of hostnames.
    for kind, value in list(entries):
        if kind == 'domain_suffix' and value.startswith('.') and ('domain', value[1:]) in entries:
            entries.discard(('domain', value[1:]))
            entries.discard((kind, value))
            entries.add((kind, value[1:]))
    return entries


def merge_documents(base, target, supplements):
    """Union requested category only. Contributions are canonical atom counts."""
    atoms = domain_entries(base)
    provenance = [{'id': 'sagernet', 'entries': len(atoms), 'added_entries': len(atoms)}]
    for source in supplements:
        document = source['documents'].get(target)
        if document is None:
            provenance.append({'id': source['id'], 'status': 'missing',
                               'category': source['category_map'][target]})
            continue
        incoming = domain_entries(document)
        added = incoming - atoms
        provenance.append({'id': source['id'], 'category': source['category_map'][target],
                           'entries': len(incoming), 'added_entries': len(added)})
        atoms.update(incoming)
    rule = {}
    for kind, value in sorted(atoms):
        rule.setdefault(kind, []).append(value)
    return {'version': 1, 'rules': [rule]}, provenance
