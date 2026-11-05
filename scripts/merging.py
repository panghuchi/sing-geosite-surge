"""Source-independent normalized-rule union and provenance. No I/O or Surge syntax."""
import ipaddress

DOMAIN_FIELDS = ('domain', 'domain_suffix', 'domain_keyword', 'domain_regex')
RULE_FIELDS = DOMAIN_FIELDS + ('ip_cidr', 'process_name', 'ip_asn')


def rule_entries(document):
    if document.get('version') not in (1, 2, 3, 4) or set(document) - {'version', 'rules'}:
        raise ValueError('unknown source document version or fields')
    if not isinstance(document.get('rules'), list) or not document['rules']:
        raise ValueError('empty source document')
    """Canonical domain atoms for semantic union; dotted suffixes remain distinct."""
    entries = set()
    for rule in document['rules']:
        if set(rule) - set(RULE_FIELDS) - {'type', 'invert'} or rule.get('type', 'default') != 'default' or rule.get('invert', False):
            raise ValueError('supplemental source contains unsupported conditions')
        if not any(rule.get(kind) for kind in RULE_FIELDS):
            raise ValueError('empty source rule would match all traffic')
        groups = [any(rule.get(k) for k in DOMAIN_FIELDS)] + [bool(rule.get(k)) for k in ('ip_cidr', 'process_name', 'ip_asn')]
        if sum(groups) > 1:
            raise ValueError('mixed IP/domain rule requires explicit logical conversion')
        for kind in RULE_FIELDS:
            values = rule.get(kind, [])
            if isinstance(values, str):
                values = [values]
            for value in values:
                # DNS names/keywords are case insensitive; regex literals are not.
                if not isinstance(value, str) or not value:
                    raise ValueError('invalid normalized rule value')
                if kind == 'ip_cidr':
                    value = str(ipaddress.ip_network(value, strict=False))
                entries.add((kind, value if kind in ('domain_regex', 'process_name') else value.lower()))
    # RootDomain and exact-apex+dotted-suffix describe the same set of hostnames.
    for kind, value in list(entries):
        if kind == 'domain_suffix' and value.startswith('.') and ('domain', value[1:]) in entries:
            entries.discard(('domain', value[1:]))
            entries.discard((kind, value))
            entries.add((kind, value[1:]))
    return entries


def merge_category(target, sources):
    """Union one target across all configured sources, in configuration order."""
    atoms, provenance = set(), []
    for source in sources:
        document = source['documents'].get(target)
        category = source['category_map'][target]
        if document is None:
            provenance.append({'id': source['id'], 'status': 'missing', 'category': category})
            continue
        incoming = rule_entries(document)
        added = incoming - atoms
        item = {'id': source['id'], 'category': category,
                'entries': len(incoming), 'added_entries': len(added)}
        if category in source.get('files', {}):
            item.update(source['files'][category])
        if target in source.get('members', {}):
            item['members'] = source['members'][target]
        provenance.append(item)
        atoms.update(incoming)
    if not atoms:
        raise ValueError('no source provides category: ' + target)
    domains, ips, processes, asns = {}, {}, {}, {}
    for kind, value in sorted(atoms):
        rule = {'ip_cidr': ips, 'process_name': processes, 'ip_asn': asns}.get(kind, domains)
        rule.setdefault(kind, []).append(value)
    return {'version': 1, 'rules': [rule for rule in (domains, ips, processes, asns) if rule]}, provenance
