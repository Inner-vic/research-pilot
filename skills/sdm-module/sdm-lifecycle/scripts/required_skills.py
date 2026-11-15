"""Project-required installed Skill identities; no SDK, network or model import.

Availability and byte identity are prerequisites, not proof that a model loaded
the Skill, that an account is authenticated, or that a platform request succeeded.
"""
import hashlib
import json
from pathlib import Path


def _file(value):
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError('absolute_installed_skill_file_required')
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in [path, *path.parents]):
        raise ValueError('linked_skill_identity_rejected')
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('bounded_skill_identity_required')
    return path


def _sha(path):
    return 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(config):
    policy = config.get('platform_requirements')
    if policy is None:
        return {'status': 'legacy-policy-not-declared', 'skills': [], 'platform_calls': 0,
                'new_execution_qualified': False}
    if (not isinstance(policy, dict) or set(policy) != {'version', 'platform', 'required_skills'}
            or policy.get('version') != 1 or policy.get('platform') != 'lpai'):
        raise ValueError('explicit_lpai_skill_requirement_required')
    rows = policy['required_skills']
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('official_lpai_skill_required')
    row = rows[0]
    if (not isinstance(row, dict) or set(row) != {'name', 'skill', 'manifest'} or row.get('name') != 'lpai'):
        raise ValueError('official_lpai_cannot_be_replaced_by_custom_adapter')
    refs = {}
    for key in ('skill', 'manifest'):
        ref = row[key]
        if not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}:
            raise ValueError('pinned_installed_skill_reference_required')
        path = _file(ref['path'])
        if _sha(path) != ref['sha256']:
            raise ValueError('required_skill_bytes_changed')
        refs[key] = path
    if (refs['skill'].name != 'SKILL.md' or refs['manifest'].name != 'manifest.json'
            or refs['skill'].parent != refs['manifest'].parent):
        raise ValueError('installed_official_skill_pair_required')
    text = refs['skill'].read_text(encoding='utf-8-sig').replace('\r\n', '\n')
    front = text.split('---', 2)
    if len(front) != 3 or front[0].strip() or 'name: lpai' not in front[1].splitlines():
        raise ValueError('official_lpai_skill_identity_required')
    metadata = json.loads(refs['manifest'].read_bytes())
    if (not isinstance(metadata, dict) or metadata.get('name') != 'lpai' or not isinstance(metadata.get('version'), str)
            or not isinstance(metadata.get('build_sha'), str) or not metadata['build_sha']):
        raise ValueError('official_skill_manifest_identity_required')
    return {'status': 'required-skills-available', 'platform': 'lpai', 'skills': [
        {'name': 'lpai', 'version': metadata['version'], 'build_sha': metadata['build_sha'],
         'skill': row['skill'], 'manifest': row['manifest']}],
        'identity_digest': 'sha256:' + hashlib.sha256(json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        'platform_calls': 0, 'execution_authority': False,
        'model_activation_verified': False, 'qualification': 'installed-identity-not-platform-or-signature-proof'}


def inherit(parent, child):
    """A goal-level replan cannot silently drop its required platform Skill."""
    import copy
    if 'platform_requirements' not in parent:
        return child
    inspect(parent)
    if 'platform_requirements' in child and child['platform_requirements'] != parent['platform_requirements']:
        raise ValueError('required_platform_policy_cannot_change_during_successor')
    child['platform_requirements'] = copy.deepcopy(parent['platform_requirements'])
    return child
