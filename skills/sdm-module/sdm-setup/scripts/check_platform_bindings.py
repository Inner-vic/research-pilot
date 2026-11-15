#!/usr/bin/env python3
"""Read-only candidate registration checks; never configures or signs a result."""
import argparse
import hashlib
import json
from pathlib import Path
import re

DOMAINS = {'data_platform_skill', 'source_control_platform_skill', 'ci_platform_skill', 'ml_execution_platform_skill'}


def inspect(root, identity):
    path = root / '.agents/skills' / identity
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', identity):
        return {'registration_ready': False, 'error': 'invalid_identity'}
    if path.is_symlink() or not path.is_dir():
        return {'registration_ready': False, 'error': 'skill_unavailable'}
    files = []
    for entry in sorted(path.rglob('*')):
        if entry.is_symlink() or not (entry.is_file() or entry.is_dir()):
            return {'registration_ready': False, 'error': 'unsafe_skill_node'}
        if entry.is_file():
            files.append({'path': entry.relative_to(path).as_posix(), 'digest': 'sha256:' + hashlib.sha256(entry.read_bytes()).hexdigest()})
    if not any(row['path'] == 'SKILL.md' for row in files):
        return {'registration_ready': False, 'error': 'skill_incomplete'}
    files.sort(key=lambda row: row['path'])
    manifest = {'algorithm': 'sha256-skill-tree-v1', 'files': files}
    encoded = json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return {'registration_ready': True, 'skill_tree_digest': 'sha256:' + hashlib.sha256(encoded).hexdigest(), 'file_count': len(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--binding', action='append', required=True, help='domain=exact-installed-identity')
    args = parser.parse_args()
    root = Path(args.project_root)
    if not root.is_absolute():
        parser.error('project-root must be absolute')
    rows = {}
    for item in args.binding:
        domain, separator, identity = item.partition('=')
        if not separator or domain not in DOMAINS or domain in rows:
            parser.error('binding must name one unique supported domain')
        rows[domain] = {'binding_identity': identity, **inspect(root, identity)}
    ready = all(row['registration_ready'] for row in rows.values())
    print(json.dumps({'command': 'setup.check-platform-bindings', 'status': 'pass' if ready else 'blocked', 'bindings': rows, 'scope': 'registration-only', 'producer_readiness': 'not_verified', 's13_emitted': False, 'configuration_mutated': False}, sort_keys=True))
    return 0 if ready else 3


if __name__ == '__main__':
    raise SystemExit(main())
