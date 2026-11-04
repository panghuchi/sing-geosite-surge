"""Read V2Ray GeoSiteList using the official routercommon protobuf wire schema.

Only requested categories are decoded. Unknown wire fields are skipped; malformed
messages and unknown domain types fail rather than silently dropping rules.
"""
DOMAIN_FIELDS = {0: 'domain_keyword', 1: 'domain_regex', 2: 'domain_suffix', 3: 'domain'}


def varint(data, position):
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(data):
            raise ValueError('truncated protobuf varint')
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError('protobuf varint overflow')
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ValueError('invalid protobuf varint')


def fields(data):
    data = memoryview(data)
    position = 0
    while position < len(data):
        key, position = varint(data, position)
        number, wire = key >> 3, key & 7
        if not number:
            raise ValueError('invalid protobuf field number')
        if wire == 0:
            value, position = varint(data, position)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, position = varint(data, position)
            else:
                length = 8 if wire == 1 else 4
            end = position + length
            if end > len(data):
                raise ValueError('truncated protobuf field')
            value, position = data[position:end], end
        else:
            raise ValueError('unsupported protobuf wire type: ' + str(wire))
        yield number, wire, value


def string(value):
    return bytes(value).decode('utf-8')


def decode_domain(data):
    kind, value, attrs = 0, None, set()
    for number, wire, item in fields(data):
        if number == 1 and wire == 0:
            kind = item
        elif number == 2 and wire == 2:
            value = string(item)
        elif number == 3 and wire == 2:
            for attr_number, attr_wire, attr in fields(item):
                if attr_number == 1 and attr_wire == 2:
                    attrs.add(string(attr).lower())
    if kind not in DOMAIN_FIELDS or not value:
        raise ValueError('unknown domain type or empty domain value')
    return DOMAIN_FIELDS[kind], value, attrs


def extract(data, categories):
    """Return category -> source-format document; explicit @ categories take precedence."""
    wanted = {name.lower() for name in categories}
    bases = {name.split('@', 1)[0] for name in wanted}
    documents, attributed = {}, {}
    seen = set()
    for number, wire, site in fields(data):
        if number != 1 or wire != 2:
            continue
        name = None
        domains = []
        # Protobuf field order is not guaranteed.
        for field, site_wire, value in fields(site):
            if field == 1 and site_wire == 2:
                name = string(value).lower()
            elif field == 2 and site_wire == 2:
                domains.append(value)
        if name not in wanted and name not in bases:
            continue
        if name in seen:
            raise ValueError('duplicate geosite category: ' + name)
        seen.add(name)
        selected = {target: {} for target in wanted if target == name or target.split('@', 1)[0] == name}
        for domain in domains:
            kind, value, attrs = decode_domain(domain)
            for target, rule in selected.items():
                if target != name:
                    attribute = target.split('@', 1)[1]
                    matches = attribute[1:] not in attrs if attribute.startswith('!') else attribute in attrs
                    if not matches:
                        continue
                rule.setdefault(kind, []).append(value)
        for target, rule in selected.items():
            if rule:
                document = {'version': 1, 'rules': [{k: sorted(set(v)) for k, v in rule.items()}]}
                (documents if target == name else attributed)[target] = document
    return {**attributed, **documents}
