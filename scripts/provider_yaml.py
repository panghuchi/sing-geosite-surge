"""Adapter for a YAML provider catalog in a tar archive; never emits Surge syntax."""
import fnmatch
import hashlib
import io
import re
import tarfile
from pathlib import PurePosixPath
import yaml

FIELDS = {'domain':'domain', 'domainSuffix':'domain_suffix', 'domainKeyword':'domain_keyword',
          'domainRegex':'domain_regex', 'processName':'process_name', 'ipCidr':'ip_cidr',
          'ipCidr6':'ip_cidr', 'asn':'ip_asn'}


def parse_provider(data, exclude_groups=()):
    record = yaml.safe_load(data)
    if not isinstance(record, dict) or not isinstance(record.get('provider'), str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]*', record['provider']):
        raise ValueError('provider catalog item must declare provider')
    scope = record.get('scope', 'global')
    if scope not in ('global', 'cn'):
        raise ValueError('unknown provider scope: ' + str(scope))
    groups = record.get('groups', [])
    if record.get('rules'):
        groups = [{'name': 'Default', 'rules': record['rules']}] + groups
    rule_fields = {}
    for group in groups:
        if group['name'] in exclude_groups:
            continue
        for field, values in group['rules'].items():
            if field not in FIELDS or not isinstance(values, list):
                raise ValueError('unsupported provider rule field: ' + field)
            target = FIELDS[field]
            for value in values:
                if field == 'asn':
                    if isinstance(value, bool) or not str(value).isdigit() or not 0 < int(value) <= 4294967295:
                        raise ValueError('invalid ASN')
                    value = str(value)
                if not isinstance(value, str) or not value:
                    raise ValueError('invalid provider rule value')
                rule_fields.setdefault(target, set()).add(value)
    if not rule_fields:
        raise ValueError('empty provider rules: ' + record['provider'])
    document = {'version':1, 'rules': [{field: sorted(values)} for field, values in sorted(rule_fields.items())]}
    labels = {'provider': record['provider'], 'scope': scope, 'name': record.get('name', record['provider'])}
    return record['provider'], document, labels


def load_provider_archive(config, categories, fetch, binary):
    data = fetch(config['url'])
    documents, labels, files, notices = {}, {}, {}, {}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        for member in archive:
            if not member.isfile():
                continue
            path = PurePosixPath(member.name)
            relative = str(PurePosixPath(*path.parts[1:]))
            if not fnmatch.fnmatchcase(relative, config.get('members_glob', 'data/providers/*.yaml')):
                if relative in config.get('notice_paths', []):
                    if member.size > 1024 * 1024:
                        raise ValueError('oversize upstream notice')
                    notices[relative] = archive.extractfile(member).read().decode('utf-8')
                continue
            if member.size > 1024 * 1024:
                raise ValueError('oversize provider YAML')
            content = archive.extractfile(member).read()
            name, document, metadata = parse_provider(content, config.get('exclude_groups', []))
            if name in documents:
                raise ValueError('duplicate provider: ' + name)
            documents[name], labels[name] = document, metadata
            files[name] = {'archive_member': relative, 'sha256': hashlib.sha256(content).hexdigest()}
    if not documents:
        raise ValueError('archive has no provider files')
    if set(config.get('notice_paths', [])) - notices.keys():
        raise ValueError('upstream notice missing')
    return documents, {'url': config['url'], 'sha256': hashlib.sha256(data).hexdigest(),
                       'labels': labels, 'files': files, 'notices': notices}
