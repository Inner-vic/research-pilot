#!/usr/bin/env python3
"""Prepare a fresh Linux BMad host, then delegate all SDM writes to setup.py."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', required=True)
    parser.add_argument('--host-root', required=True)
    parser.add_argument('--answers-file', required=True)
    parser.add_argument('--platform-skill', action='append', default=[], help='explicit candidate Skill identity to copy, without credentials')
    parser.add_argument('--governance-profile', choices=('lightweight', 'strict'), default='lightweight',
                        help='new-host business governance; pinned in installed engine bytes')
    args = parser.parse_args()
    source = Path(args.source_root).resolve(strict=True)
    host_input = Path(args.host_root)
    if sys.platform != 'linux' or not host_input.is_absolute():
        parser.error('requires Linux and an absolute host root')
    host = host_input.resolve()
    if host.exists() or host_input.is_symlink() or host.is_relative_to(Path('/mnt')) or host.is_relative_to(source):
        parser.error('host must be absent, outside source, and on Linux filesystem')
    answers = json.loads(Path(args.answers_file).read_text(encoding='utf-8-sig'))
    import re
    for identity in args.platform_skill:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', identity):
            parser.error('platform-skill must be one exact directory identity')
        candidate = source / '.agents/skills' / identity
        if candidate.is_symlink() or not (candidate / 'SKILL.md').is_file():
            parser.error(f'candidate unavailable: {identity}')
        if any(node.is_symlink() or not (node.is_dir() or node.is_file()) for node in candidate.rglob('*')):
            parser.error(f'unsafe candidate tree: {identity}')
    # Precheck all seed inputs before creating the new host. Never copy state/trust.
    directories = ['_bmad/scripts', '_bmad/core', '_bmad/bmm', 'skills/sdm-module', 'docs', 'scripts']
    custom = ['bmad-product-brief', 'bmad-prd', 'bmad-architecture', 'bmad-create-epics-and-stories', 'bmad-build', 'bmad-code-review', 'bmad-qa-generate-e2e-tests']
    for relative in directories + ['_bmad/config.toml', '_bmad/bmm/module-help.csv'] + [f'_bmad/custom/{name}.toml' for name in custom]:
        if not (source / relative).exists():
            parser.error(f'missing seed input: {relative}')
    os.umask(0o077)
    host.mkdir(parents=True, mode=0o700)
    ignore = shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache')
    for relative in directories:
        shutil.copytree(source / relative, host / relative, ignore=ignore)
    policy_path = host / 'skills/sdm-module/sdm-lifecycle/assets/authority-policy.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    policy['business_profile'] = args.governance_profile
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (host / '.agents/skills').mkdir(parents=True)
    for skill in sorted((source / '.agents/skills').glob('bmad-*')):
        if skill.is_dir():
            shutil.copytree(skill, host / '.agents/skills' / skill.name, ignore=ignore)
    for identity in sorted(set(args.platform_skill)):
        shutil.copytree(source / '.agents/skills' / identity, host / '.agents/skills' / identity, ignore=ignore)
    shutil.copy2(source / '_bmad/config.toml', host / '_bmad/config.toml')
    (host / '_bmad/custom').mkdir()
    for name in custom:
        shutil.copy2(source / f'_bmad/custom/{name}.toml', host / f'_bmad/custom/{name}.toml')
    (host / '_bmad/_config').mkdir()
    for relative in ['_bmad/_config/bmad-help.csv', '_bmad/module-help.csv']:
        shutil.copy2(host / '_bmad/bmm/module-help.csv', host / relative)
    setup = host / 'skills/sdm-module/sdm-setup/scripts/setup.py'
    common = ['--project-root', str(host), '--skills-source', str(host / 'skills/sdm-module')]
    for command in [
        ['install', *common, '--answers', json.dumps(answers), '--expected-install-digest', 'absent'],
        ['validate', *common],
    ]:
        completed = subprocess.run([sys.executable, '-B', str(setup), *command], check=False)
        if completed.returncode:
            return completed.returncode
    print(json.dumps({'command': 'prepare-linux-host', 'status': 'pass', 'host_root': str(host), 'governance_profile': args.governance_profile, 'trust_copied': False, 'workspace_copied': False}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
