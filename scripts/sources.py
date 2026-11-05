"""Source adapters return source-format rule documents; they never write outputs."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import re
import time
from urllib.parse import urlparse, quote
from urllib.request import urlopen
from urllib.error import HTTPError
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


def load_dat(config, categories, fetch, binary):
    data = fetch(config['url'])
    return extract(data, categories), {'url': config['url'], 'sha256': hashlib.sha256(data).hexdigest()}


def load_srs(config, categories, fetch, binary):
    template = config.get('url_template')
    if template is not None and '{category}' not in template:
        raise ValueError('SRS template needs {category}')
    if template is None and len(set(categories)) != 1:
        raise ValueError('a fixed SRS URL must map to exactly one category')
    with tempfile.TemporaryDirectory(prefix='surge-source-srs-') as temp:
        def one(name):
            name = category_name(name)
            url = template.replace('{category}', quote(name, safe='')) if template else config['url']
            try:
                data = fetch(url)
            except HTTPError as error:
                if error.code != 404 or config.get('on_missing', 'error') != 'skip':
                    raise
                return name, None, {'url': url, 'status': 'missing'}
            source = Path(temp) / (name + '.srs')
            output = Path(temp) / (name + '.json')
            source.write_bytes(data)
            subprocess.run([binary, 'rule-set', 'decompile', str(source), '-o', str(output)],
                           check=True, capture_output=True, text=True, timeout=120)
            return name, json.loads(output.read_text()), {'url': url, 'sha256': hashlib.sha256(data).hexdigest()}
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(one, sorted(set(categories))))
    return {name: doc for name, doc, meta in rows if doc is not None}, {
        'url_template': template, 'files': {name: meta for name, doc, meta in rows}}


# New formats register a reader here. Readers only need to produce the same IR.
ADAPTERS = {'v2ray-geosite': load_dat, 'sing-box-srs': load_srs}


def category_name(name):
    name = name.removeprefix('geosite-').removesuffix('.srs').lower()
    if not re.fullmatch(r'[a-z0-9@!_-]+', name):
        raise ValueError('invalid category name: ' + name)
    return name


def load_sources(configs, categories, fetch=download, binary="sing-box"):
    categories = [category_name(name) for name in categories]
    result, ids = [], set()
    for config in configs:
        identity = config['id']
        if not re.fullmatch(r'[a-z0-9_-]+', identity) or identity in ids:
            raise ValueError('invalid or duplicate source id: ' + identity)
        ids.add(identity)
        adapter = ADAPTERS.get(config['format'])
        if adapter is None:
            raise ValueError('unknown source format: ' + config['format'])
        mapping = config.get('category_map', {})
        policy = config.get('on_missing', 'error')
        if policy not in ('error', 'skip'):
            raise ValueError('on_missing must be error or skip')
        selected = config.get('targets', {target: mapping.get(target, target) for target in categories})
        if not isinstance(selected, dict) or not selected or any(not re.fullmatch(r'[a-z0-9@!_-]+', target) for target in selected):
            raise ValueError('source targets must be a nonempty mapping')
        normalized = {category_name(target): name for target, name in selected.items()}
        if len(normalized) != len(selected):
            raise ValueError('duplicate normalized targets')
        selected = normalized
        if any(not isinstance(name, str) or not name for name in selected.values()):
            raise ValueError('invalid source category mapping')
        decoded, metadata = adapter(config, selected.values(), fetch, binary)
        missing = sorted(target for target, name in selected.items() if name not in decoded)
        if missing and policy == 'error':
            raise ValueError(identity + ': missing categories: ' + ', '.join(missing))
        result.append({'id': identity, **metadata,
                       'documents': {target: decoded[name] for target, name in selected.items() if name in decoded},
                       'category_map': selected,
                       'missing_categories': missing})
    return result
