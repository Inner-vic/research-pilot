"""Owned local Git worktrees and frozen source/config bundles; never trains or pushes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

from review_storage import atomic_json, locked, read_json
from round_reporting import digest


def plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(os.path, 'isjunction') and os.path.isjunction(part)):
            raise ValueError('linked_path_not_supported')
    return path.resolve()


def git(repo, *args, check=True):
    environment = {k: v for k, v in os.environ.items() if k not in ('GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')}
    run = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false', '-C', str(repo), *args],
                         capture_output=True, timeout=60,
                         env={**environment, 'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1'}, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if check and run.returncode: raise ValueError('git_operation_failed:' + args[0])
    return run.stdout.decode('utf-8', errors='strict').strip()


def registry(repo):
    raw = git(repo, 'worktree', 'list', '--porcelain', '-z')
    rows, row = [], {}
    for token in raw.split('\0'):
        if not token:
            if row: rows.append(row); row = {}
        else:
            key, _, value = token.partition(' '); row[key] = value
    if row: rows.append(row)
    return rows


def normalize(spec, root):
    if not isinstance(spec, dict) or spec.get('schema') != 'sdm-worktree-plan/v1' or spec.get('backend') != 'git-local':
        raise ValueError('local_git_worktree_plan_required')
    if not isinstance(spec.get('project'), str) or not spec['project'].strip(): raise ValueError('project_required')
    branch = spec.get('branch')
    if not isinstance(branch, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,100}', branch):
        raise ValueError('explicit_new_branch_required')
    commit = spec.get('base_commit')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit):
        raise ValueError('exact_base_commit_required')
    for key in ('repository', 'allowed_worktree_root', 'worktree_path'):
        if not isinstance(spec.get(key), str) or not Path(spec[key]).is_absolute():
            raise ValueError('absolute_project_paths_required')
    repo, allowed, target, root = [plain(p) for p in (spec['repository'], spec['allowed_worktree_root'], spec['worktree_path'], root)]
    if plain(git(repo, 'rev-parse', '--show-toplevel')) != repo: raise ValueError('exact_repository_root_required')
    common = plain(git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    if (target == allowed or not target.is_relative_to(allowed) or allowed.is_relative_to(repo)
        or target.is_relative_to(repo) or root.is_relative_to(target) or target.is_relative_to(root)
        or root.is_relative_to(common) or '.git' in target.parts or '.git' in root.parts):
        raise ValueError('independent_owned_worktree_and_record_paths_required')
    git(repo, 'check-ref-format', '--branch', branch)
    if git(repo, 'rev-parse', commit + '^{commit}') != commit: raise ValueError('base_commit_not_available')
    filters = git(repo, 'config', '--get-regexp', r'^filter\..*\.(smudge|process)$', check=False)
    if any(len(line.split(maxsplit=1)) == 2 and line.split(maxsplit=1)[1] for line in filters.splitlines()):
        raise ValueError('checkout_filters_require_project_adapter')
    return {**spec, 'repository': str(repo), 'allowed_worktree_root': str(allowed),
            'worktree_path': str(target), 'git_common_dir': str(common), 'record_root': str(root)}


def marker_path(target):
    return Path(git(target, 'rev-parse', '--path-format=absolute', '--git-path', 'sdm-worktree-owner.json'))


def marker(state):
    return {'schema': 'sdm-worktree-owner/v1', 'nonce': state['nonce'],
            'plan_digest': state['plan_digest'], 'record_root': state['plan']['record_root']}


def owned(state):
    plan = state['plan']; target = plain(plan['worktree_path']); repo = plain(plan['repository'])
    if not target.is_relative_to(plain(plan['allowed_worktree_root'])) or target == plain(plan['allowed_worktree_root']):
        raise ValueError('worktree_target_outside_owned_root')
    if not target.is_dir(): raise ValueError('owned_worktree_missing')
    matches = [r for r in registry(repo) if plain(r['worktree']) == target]
    if len(matches) != 1 or matches[0].get('branch') != 'refs/heads/' + plan['branch']:
        raise ValueError('worktree_registration_or_branch_changed')
    if plain(git(target, 'rev-parse', '--path-format=absolute', '--git-common-dir')) != plain(plan['git_common_dir']):
        raise ValueError('worktree_repository_changed')
    owner = marker_path(target)
    if not owner.is_file() or read_json(owner) != marker(state):
        raise ValueError('worktree_ownership_uncertain')
    return target


def clean(target):
    if any(token and (token[0].islower() or token[0] == 'S') for token in git(target, 'ls-files', '-v', '-z').split('\0')):
        raise ValueError('hidden_git_index_flags_require_project_adapter')
    if git(target, 'status', '--porcelain=v1', '--untracked-files=all', '--ignored'):
        raise ValueError('worktree_has_uncommitted_untracked_or_ignored_files')


def checksum(path):
    path = plain(path)
    if not path.is_file(): raise ValueError('snapshot_file_missing')
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): hasher.update(block)
    return 'sha256:' + hasher.hexdigest()


def snapshot(state):
    ref = state.get('snapshot')
    if not isinstance(ref, dict): raise ValueError('frozen_snapshot_required')
    root = plain(state['plan']['record_root'])
    path = plain(ref['path'])
    if not path.is_relative_to(root / 'snapshots') or checksum(path) != ref['digest']:
        raise ValueError('frozen_snapshot_binding_changed')
    value = read_json(path)
    if value['plan_digest'] != state['plan_digest'] or value['nonce'] != state['nonce']:
        raise ValueError('snapshot_ownership_changed')
    for kind in ('source_bundle', 'configuration'):
        file = plain(value[kind]['path'])
        if file.parent != path.parent or checksum(file) != value[kind]['digest']:
            raise ValueError('snapshot_bytes_changed:' + kind)
    git(state['plan']['repository'], 'bundle', 'verify', value['source_bundle']['path'])
    return value


def save(root, state, phase):
    state['history'].append({'from': state['phase'], 'to': phase})
    state['phase'] = phase; atomic_json(root / 'state.json', state)


def attach(state, root, restoring=False):
    plan = state['plan']; repo = Path(plan['repository']); target = Path(plan['worktree_path'])
    phase = 'restoring' if restoring else 'creating'
    if target.exists():
        owned(state)  # A missing nonce blocks uncertain adoption after a crash.
    else:
        existing = git(repo, 'show-ref', '--verify', 'refs/heads/' + plan['branch'], check=False)
        if restoring:
            frozen = snapshot(state)
            if git(repo, 'rev-parse', 'refs/heads/' + plan['branch']) != frozen['source_commit']:
                raise ValueError('restore_branch_changed')
        elif existing:
            raise ValueError('existing_branch_requires_reconciliation')
        save(root, state, phase)
        Path(plan['allowed_worktree_root']).mkdir(parents=True, exist_ok=True)
        if restoring: git(repo, 'worktree', 'add', str(target), plan['branch'])
        else: git(repo, 'worktree', 'add', '-b', plan['branch'], str(target), plan['base_commit'])
        atomic_json(marker_path(target), marker(state))
        owned(state)
    save(root, state, 'active')
    return state


def freeze(state, root, configuration):
    target = owned(state); clean(target)
    config = plain(configuration)
    if not config.is_file(): raise ValueError('resolved_configuration_file_required')
    head = git(target, 'rev-parse', 'HEAD'); tree = git(target, 'rev-parse', 'HEAD^{tree}')
    modes = [line.split()[0] for line in git(target, 'ls-tree', '-r', 'HEAD').splitlines()]
    if any(mode in ('120000', '160000') for mode in modes):
        raise ValueError('linked_source_requires_project_adapter')
    config_digest = checksum(config)
    key = digest({'commit': head, 'configuration_digest': config_digest, 'configuration_origin': str(config)})[7:]
    directory = root / 'snapshots' / key; directory.mkdir(parents=True, exist_ok=True)
    bundle, copy, manifest = directory / 'source.bundle', directory / 'configuration.bin', directory / 'snapshot.json'
    if not bundle.exists(): git(target, 'bundle', 'create', str(bundle), 'HEAD')
    git(target, 'bundle', 'verify', str(bundle))
    if not copy.exists():
        with copy.open('xb') as stream: stream.write(config.read_bytes())
    if checksum(copy) != config_digest: raise ValueError('snapshot_configuration_changed')
    clean(target)
    if git(target, 'rev-parse', 'HEAD') != head or checksum(config) != config_digest:
        raise ValueError('source_or_configuration_changed_during_freeze')
    value = {'schema': 'sdm-source-snapshot/v1', 'plan_digest': state['plan_digest'], 'nonce': state['nonce'],
             'source_commit': head, 'source_tree': tree, 'branch': state['plan']['branch'],
             'worktree_path': str(target), 'configuration_origin': str(config),
             'source_bundle': {'path': str(bundle), 'digest': checksum(bundle)},
             'configuration': {'path': str(copy), 'digest': config_digest}}
    if manifest.exists() and read_json(manifest) != value: raise ValueError('frozen_manifest_changed')
    if not manifest.exists(): atomic_json(manifest, value)
    state['snapshot'] = {'path': str(manifest), 'digest': checksum(manifest)}
    atomic_json(root / 'state.json', state)
    return state


def validate_frozen(state):
    target = owned(state); value = snapshot(state); clean(target)
    if git(target, 'rev-parse', 'HEAD') != value['source_commit']:
        raise ValueError('worktree_head_changed_after_freeze')
    if checksum(value['configuration_origin']) != value['configuration']['digest']:
        raise ValueError('resolved_configuration_changed_after_freeze')
    return value


def retire(state, root):
    plan = state['plan']; target = Path(plan['worktree_path']); repo = Path(plan['repository'])
    frozen = snapshot(state)
    if git(repo, 'rev-parse', 'refs/heads/' + plan['branch']) != frozen['source_commit']:
        raise ValueError('branch_changed_after_freeze')
    if target.exists():
        validate_frozen(state)
        save(root, state, 'retiring')
        # Exact registered target, clean source and verified bundle precede removal.
        git(repo, 'worktree', 'remove', str(owned(state)))
    elif state['phase'] not in ('retiring', 'retired'):
        raise ValueError('unexpected_missing_worktree')
    if target.exists() or any(plain(r['worktree']) == target for r in registry(repo)):
        raise ValueError('worktree_removal_not_confirmed')
    if state['phase'] == 'retired': return state
    save(root, state, 'retired')
    return state


def manage(spec, record_root, action, configuration=None):
    root = plain(record_root); plan = normalize(spec, root)
    state_path = root / 'state.json'
    if action == 'plan': return {'status': 'ready', 'plan': plan, 'plan_digest': digest(plan), 'execution_authorized': False}
    if action == 'status':
        state = read_json(state_path) if state_path.is_file() else None
        if state and state['plan_digest'] != digest(plan): raise ValueError('worktree_plan_changed')
        return {'status': state['phase'] if state else 'not-created', 'state': state, 'execution_authorized': False}
    with locked(root):
        state = read_json(state_path) if state_path.is_file() else None
        if state is None:
            if action != 'create': raise ValueError('owned_worktree_record_required')
            if Path(plan['worktree_path']).exists() or git(plan['repository'], 'show-ref', '--verify', 'refs/heads/' + plan['branch'], check=False):
                raise ValueError('fresh_worktree_path_and_branch_required')
            state = {'schema': 'sdm-owned-worktree/v1', 'plan': plan, 'plan_digest': digest(plan),
                     'nonce': uuid.uuid4().hex, 'phase': 'creating', 'history': [],
                     'source_dirty_at_creation': bool(git(plan['repository'], 'status', '--porcelain=v1', '--untracked-files=all'))}
            atomic_json(state_path, state)
        if state['plan_digest'] != digest(plan): raise ValueError('worktree_plan_changed')
        if action == 'create':
            if state['phase'] == 'active': owned(state)
            elif state['phase'] == 'creating': attach(state, root)
            else: raise ValueError('create_cannot_replace_existing_lifecycle')
        elif action == 'freeze':
            if state['phase'] != 'active': raise ValueError('active_worktree_required')
            freeze(state, root, configuration)
        elif action == 'validate':
            if state['phase'] != 'active': raise ValueError('active_worktree_required')
            validate_frozen(state)
        elif action == 'retire':
            if state['phase'] not in ('active', 'retiring', 'retired'): raise ValueError('worktree_cannot_retire_now')
            retire(state, root)
        elif action == 'restore':
            if state['phase'] == 'active': validate_frozen(state)
            elif state['phase'] in ('retired', 'restoring'): attach(state, root, restoring=True)
            else: raise ValueError('retired_worktree_required')
        else: raise ValueError('unsupported_worktree_action')
        return {'status': state['phase'], 'state': state, 'execution_authorized': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('plan', 'create', 'status', 'freeze', 'validate', 'retire', 'restore'))
    parser.add_argument('--plan', type=Path, required=True); parser.add_argument('--record-root', type=Path, required=True)
    parser.add_argument('--configuration', type=Path); parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        if args.action in ('create', 'freeze', 'retire', 'restore') and not args.apply:
            raise ValueError('explicit_apply_required')
        if args.action == 'freeze' and args.configuration is None: raise ValueError('configuration_required')
        result = manage(read_json(args.plan), args.record_root, args.action, args.configuration)
        print(json.dumps(result, ensure_ascii=False)); return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'blocked', 'problem': str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
