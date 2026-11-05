"""Configuration-driven projections of normalized catalogs. No vendor names or I/O."""
import fnmatch
import re
from merging import merge_category


def project_sources(sources, definitions):
    """Use a frozen input snapshot so aggregation order never creates implicit dependencies."""
    projected = []
    for source in sources:
        output = dict(source)
        output['documents'] = dict(source['documents']) if source.get('publish', True) else {}
        output['category_map'] = dict(source['category_map']) if source.get('publish', True) else {}
        output['members'] = {}
        projected.append(output)
    seen = set()
    for definition in definitions:
        target = definition['target']
        if not isinstance(target, str) or not re.fullmatch(r'[a-z0-9@!_-]+', target):
            raise ValueError('invalid aggregation target')
        if target in seen:
            raise ValueError('duplicate aggregation target: ' + target)
        seen.add(target)
        matched = 0
        for original, output in zip(sources, projected):
            members = set()
            for selection in definition['select']:
                if not fnmatch.fnmatchcase(original['id'], selection['source']):
                    continue
                for name in original['documents']:
                    if 'categories' in selection and name not in selection['categories']:
                        continue
                    labels = original.get('labels', {}).get(name, {})
                    if any(labels.get(key) not in (value if isinstance(value, list) else [value])
                           for key, value in selection.get('where', {}).items()):
                        continue
                    members.add(name)
            if not members:
                continue
            matched += len(members)
            # Retain existing contribution when augmenting an already published target.
            if target in output['documents'] and target in original['documents']:
                members.add(target)
            entries = [{'id': name, 'documents': {target: original['documents'][name]},
                        'category_map': {target: name}} for name in sorted(members)]
            output['documents'][target] = merge_category(target, entries)[0]
            output['category_map'][target] = target
            output['members'][target] = sorted(members)
        if not matched:
            raise ValueError('aggregation selects no members: ' + target)
    return [source for source in projected if source['documents']]
