"""Prepare a dedicated publication worktree, rejecting coverage regressions."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil


def prepare(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if not (target / '.git').exists() or source == target or source in target.parents or target in source.parents:
        raise ValueError('target must be a separate Git worktree')
    current = json.loads((source / 'manifest.json').read_text())
    old_path = target / 'manifest.json'
    if old_path.exists():
        previous = json.loads(old_path.read_text())
        replacements = {}
        for name, entry in current['categories'].items():
            old_name = entry.get('replaces')
            if old_name and entry['status'] == 'complete' and old_name in previous['categories']:
                old_file = target / previous['categories'][old_name]['ruleset']
                new_file = source / entry['ruleset']
                if old_file.read_bytes() == new_file.read_bytes():
                    replacements[old_name] = name
        regressions = [name for name, entry in previous['categories'].items()
                       if entry['status'] == 'complete' and name not in replacements and
                       current['categories'].get(name, {}).get('status') != 'complete']
        if regressions:
            raise ValueError('previously published categories became unsupported or disappeared; keeping old publication: ' + ', '.join(regressions))
    for path in target.iterdir():
        if path.name == '.git':
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True)
    # A real daily check record also keeps the publication's freshness observable.
    (target / 'status.json').write_text(json.dumps({
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'upstream_revision': current['upstream_revision'],
        'complete': current['complete'], 'unsupported': current['unsupported']
    }, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    prepare(args.source, args.target)
