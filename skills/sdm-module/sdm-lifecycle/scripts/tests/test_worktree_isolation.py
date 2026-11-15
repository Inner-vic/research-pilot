from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import worktree_isolation as isolation
from review_storage import atomic_json


def git(repo, *args):
    run = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=20)
    assert run.returncode == 0, run.stderr.decode(errors='replace')
    return run.stdout.decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / 'source'; repo.mkdir()
    git(repo, 'init', '-b', 'main'); git(repo, 'config', 'user.name', 'CPU fixture')
    git(repo, 'config', 'user.email', 'fixture@example.invalid')
    (repo / 'model.py').write_text('MODULE = "baseline"\n')
    (repo / 'configuration.json').write_text('{"batch": 4}\n')
    (repo / '.gitignore').write_text('cache/\n')
    git(repo, 'add', '.'); git(repo, 'commit', '-m', 'CPU fixture baseline')
    return repo


def plan(repo, name='control'):
    return {'schema': 'sdm-worktree-plan/v1', 'backend': 'git-local', 'project': 'fixture-project',
            'repository': str(repo), 'base_commit': git(repo, 'rev-parse', 'HEAD'), 'branch': 'fixture/' + name,
            'allowed_worktree_root': str(repo.parent / 'worktrees'), 'worktree_path': str(repo.parent / 'worktrees' / name)}


def create(repo, name='control'):
    spec = plan(repo, name); root = repo.parent / ('records-' + name)
    result = isolation.manage(spec, root, 'create')
    return spec, root, Path(spec['worktree_path']), result


def freeze(spec, root, target):
    return isolation.manage(spec, root, 'freeze', target / 'configuration.json')


def test_two_real_worktrees_isolate_changes_and_preserve_main(repository):
    original = git(repository, 'rev-parse', 'HEAD')
    left, lr, lp, _ = create(repository, 'control'); right, rr, rp, _ = create(repository, 'treatment')
    (lp / 'model.py').write_text('MODULE = "control"\n')
    (rp / 'model.py').write_text('MODULE = "treatment"\n')
    assert (lp / 'model.py').read_text() != (rp / 'model.py').read_text()
    assert (repository / 'model.py').read_text() == 'MODULE = "baseline"\n'
    assert git(repository, 'rev-parse', 'HEAD') == original
    git(lp, 'add', 'model.py'); git(lp, 'commit', '-m', 'fixture control change')
    frozen = freeze(left, lr, lp)
    assert frozen['state']['snapshot']['digest'].startswith('sha256:')
    assert (rp / 'model.py').read_text() == 'MODULE = "treatment"\n'
    assert isolation.manage(left, lr, 'validate')['status'] == 'active'


def test_dirty_source_is_retained_and_not_copied(repository):
    (repository / 'model.py').write_text('UNCOMMITTED = True\n')
    spec, root, target, result = create(repository)
    assert result['state']['source_dirty_at_creation'] is True
    assert (repository / 'model.py').read_text() == 'UNCOMMITTED = True\n'
    assert (target / 'model.py').read_text() == 'MODULE = "baseline"\n'


def test_create_resume_is_idempotent_and_keeps_local_work(repository):
    spec, root, target, result = create(repository)
    (target / 'notes.txt').write_text('unfinished fixture work')
    before = git(repository, 'worktree', 'list', '--porcelain')
    assert isolation.manage(spec, root, 'create')['status'] == 'active'
    assert (target / 'notes.txt').read_text() == 'unfinished fixture work'
    assert git(repository, 'worktree', 'list', '--porcelain') == before


def test_creation_intent_recovers_owned_effect_without_duplicate(repository):
    spec, root, target, result = create(repository)
    state = result['state']; state['phase'] = 'creating'; atomic_json(root / 'state.json', state)
    assert isolation.manage(spec, root, 'create')['status'] == 'active'
    assert sum(str(target) in row for row in git(repository, 'worktree', 'list', '--porcelain').splitlines()) == 1


def test_missing_owner_marker_never_adopts_an_uncertain_worktree(repository):
    spec, root, target, _ = create(repository)
    isolation.marker_path(target).unlink()
    with pytest.raises(ValueError, match='ownership_uncertain'): isolation.manage(spec, root, 'create')
    assert target.exists()


@pytest.mark.parametrize('kind', ['modified', 'untracked', 'ignored'])
def test_cleanup_preserves_all_unarchived_work(repository, kind):
    spec, root, target, _ = create(repository); freeze(spec, root, target)
    if kind == 'modified': (target / 'model.py').write_text('dirty fixture')
    elif kind == 'untracked': (target / 'notes.txt').write_text('new fixture')
    else:
        (target / 'cache').mkdir(); (target / 'cache/file').write_text('ignored fixture')
    with pytest.raises(ValueError, match='uncommitted_untracked_or_ignored'): isolation.manage(spec, root, 'retire')
    assert target.exists()


def test_freeze_reentry_and_validated_cleanup_restore(repository):
    spec, root, target, _ = create(repository)
    first = freeze(spec, root, target); snap = Path(first['state']['snapshot']['path'])
    before = (snap.read_bytes(), snap.stat().st_mtime_ns)
    freeze(spec, root, target)
    assert before == (snap.read_bytes(), snap.stat().st_mtime_ns)
    assert isolation.manage(spec, root, 'retire')['status'] == 'retired' and not target.exists()
    assert snap.exists() and git(repository, 'rev-parse', 'refs/heads/' + spec['branch']) == spec['base_commit']
    assert isolation.manage(spec, root, 'restore')['status'] == 'active'
    assert isolation.manage(spec, root, 'validate')['status'] == 'active'


def test_bundle_can_restore_source_in_a_separate_repository(repository):
    spec, root, target, _ = create(repository); state = freeze(spec, root, target)['state']
    manifest = isolation.snapshot(state)
    recovered = repository.parent / 'recovered'; recovered.mkdir(); git(recovered, 'init')
    git(recovered, 'fetch', manifest['source_bundle']['path'], 'HEAD')
    git(recovered, 'checkout', '--detach', 'FETCH_HEAD')
    assert (recovered / 'model.py').read_bytes() == (target / 'model.py').read_bytes()
    assert git(recovered, 'rev-parse', 'HEAD') == manifest['source_commit']


def test_changed_snapshot_prevents_removal(repository):
    spec, root, target, _ = create(repository); state = freeze(spec, root, target)['state']
    bundle = Path(isolation.snapshot(state)['source_bundle']['path']); bundle.write_bytes(b'changed fixture bundle')
    with pytest.raises(ValueError, match='snapshot_bytes_changed'): isolation.manage(spec, root, 'retire')
    assert target.exists()


def test_new_commit_after_freeze_requires_new_snapshot(repository):
    spec, root, target, _ = create(repository); freeze(spec, root, target)
    (target / 'model.py').write_text('MODULE = "changed"\n'); git(target, 'add', 'model.py'); git(target, 'commit', '-m', 'fixture change')
    with pytest.raises(ValueError, match='head_changed'): isolation.manage(spec, root, 'validate')
    with pytest.raises(ValueError, match='branch_changed'): isolation.manage(spec, root, 'retire')
    freeze(spec, root, target)
    assert isolation.manage(spec, root, 'validate')['status'] == 'active'


def test_changed_external_configuration_is_detected(repository):
    spec, root, target, _ = create(repository)
    config = repository.parent / 'resolved.json'; config.write_text('{"batch":4}')
    isolation.manage(spec, root, 'freeze', config)
    config.write_text('{"batch":8}')
    with pytest.raises(ValueError, match='configuration_changed'): isolation.manage(spec, root, 'validate')


def test_restore_does_not_reset_a_changed_branch(repository):
    spec, root, target, _ = create(repository); freeze(spec, root, target); isolation.manage(spec, root, 'retire')
    git(repository, 'checkout', spec['branch']); (repository / 'model.py').write_text('new branch work\n')
    git(repository, 'add', 'model.py'); git(repository, 'commit', '-m', 'fixture later change'); git(repository, 'checkout', 'main')
    with pytest.raises(ValueError, match='restore_branch_changed'): isolation.manage(spec, root, 'restore')
    assert not target.exists()


def test_no_cleanup_before_freeze_and_no_adoption_of_existing_branch(repository):
    spec, root, target, _ = create(repository)
    with pytest.raises(ValueError, match='frozen_snapshot_required'): isolation.manage(spec, root, 'retire')
    other = deepcopy(spec); other['worktree_path'] = str(repository.parent / 'worktrees/other')
    with pytest.raises(ValueError, match='fresh_worktree'): isolation.manage(other, repository.parent / 'other-records', 'create')
    assert target.exists()


def test_outside_or_nested_targets_are_rejected_before_git_write(repository):
    for target in (repository, repository / 'nested', repository.parent / 'outside'):
        spec = plan(repository); spec['worktree_path'] = str(target)
        with pytest.raises(ValueError, match='independent_owned'): isolation.manage(spec, repository.parent / 'records', 'create')
    assert git(repository, 'branch', '--list', 'fixture/*') == ''


def test_changed_plan_and_symlink_paths_are_rejected(repository):
    spec, root, target, _ = create(repository)
    altered = deepcopy(spec); altered['branch'] = 'fixture/foreign'
    with pytest.raises(ValueError, match='plan_changed'): isolation.manage(altered, root, 'create')
    alias = repository.parent / 'alias'; alias.symlink_to(repository.parent / 'worktrees', target_is_directory=True)
    altered = deepcopy(spec); altered['worktree_path'] = str(alias / 'control')
    with pytest.raises(ValueError, match='linked_path'): isolation.manage(altered, root, 'create')


def test_public_cli_requires_apply_and_plan_does_not_create_records(repository):
    spec = plan(repository); path = repository.parent / 'plan.json'; path.write_text(json.dumps(spec))
    root = repository.parent / 'records'
    args = [sys.executable, '-B', str(Path(isolation.__file__)), '--plan', str(path), '--record-root', str(root)]
    result = subprocess.run(args[:3] + ['plan'] + args[3:], capture_output=True, timeout=20)
    assert result.returncode == 0 and not root.exists()
    result = subprocess.run(args[:3] + ['create'] + args[3:], capture_output=True, timeout=20)
    assert result.returncode == 2 and not root.exists()


def test_two_configuration_locations_keep_distinct_immutable_snapshots(repository):
    spec, root, target, _ = create(repository)
    first = freeze(spec, root, target)['state']['snapshot']
    config = repository.parent / 'same-bytes.json'; config.write_bytes((target / 'configuration.json').read_bytes())
    second = isolation.manage(spec, root, 'freeze', config)['state']['snapshot']
    assert first['path'] != second['path'] and Path(first['path']).exists()


def test_freeze_recovers_after_bundle_saved_before_manifest(repository, monkeypatch):
    spec, root, target, _ = create(repository)
    original = isolation.atomic_json
    def fail_manifest(path, value):
        if Path(path).name == 'snapshot.json': raise RuntimeError('fixture interruption')
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(isolation, 'atomic_json', fail_manifest)
        with pytest.raises(RuntimeError): freeze(spec, root, target)
    assert list((root / 'snapshots').glob('*/source.bundle'))
    assert target.exists()
    with pytest.raises(ValueError, match='frozen_snapshot_required'): isolation.manage(spec, root, 'validate')
    freeze(spec, root, target)
    assert isolation.manage(spec, root, 'validate')['status'] == 'active'


def test_retire_recovers_after_git_removal_before_state_commit(repository, monkeypatch):
    spec, root, target, _ = create(repository); freeze(spec, root, target)
    original = isolation.atomic_json
    def fail_retired(path, value):
        if Path(path).name == 'state.json' and value.get('phase') == 'retired': raise RuntimeError('fixture interruption')
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(isolation, 'atomic_json', fail_retired)
        with pytest.raises(RuntimeError): isolation.manage(spec, root, 'retire')
    assert not target.exists() and json.loads((root / 'state.json').read_bytes())['phase'] == 'retiring'
    assert isolation.manage(spec, root, 'retire')['status'] == 'retired'
    before = (root / 'state.json').read_bytes()
    assert isolation.manage(spec, root, 'retire')['status'] == 'retired'
    assert (root / 'state.json').read_bytes() == before
    assert isolation.manage(spec, root, 'restore')['status'] == 'active'
    before = (root / 'state.json').read_bytes()
    assert isolation.manage(spec, root, 'restore')['status'] == 'active'
    assert (root / 'state.json').read_bytes() == before


def test_registry_lock_prevents_second_lifecycle_writer(repository):
    from review_storage import locked
    spec = plan(repository); root = repository.parent / 'records'
    with locked(root):
        with pytest.raises(ValueError, match='busy'): isolation.manage(spec, root, 'create')
    assert not Path(spec['worktree_path']).exists()


def test_real_source_snapshots_feed_bound_comparison_records(repository):
    import comparison_reporting as reports
    import hashlib
    def bind(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')
        return {'path': str(path), 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()}
    definition = {'evaluation.source_bytes': {'direction': 'maximize', 'unit': 'bytes'}}
    fixed = dict(initial_checkpoint_ref='fixture-no-model', evaluator_ref='fixture-file-length',
                 training_budget='fixture-no-training', effective_batch=1, precision='fixture-text',
                 randomness_pairing='fixture-deterministic', checkpoint_selection='fixture-no-checkpoint')
    conditions = {**fixed, 'dataset_version': 'fixture-source', 'split_digest': 'fixture-no-dataset', 'metric_definition': definition}
    group = {'schema': 'sdm-comparison-group-planning/v1', 'group_id': 'git-source-fixture',
             'project': 'fixture-project', 'question': 'CPU source isolation contract only',
             'changed_factor': 'implementation_variant', 'schedule': 'sequential', 'arms': []}
    spec = {'schema': 'sdm-comparison-export/v1', 'record_refs': {},
            'metrics': [{'field': 'evaluation.source_bytes', **definition['evaluation.source_bytes']}]}
    values = []
    for name in ('control', 'treatment'):
        wp, root, target, _ = create(repository, name)
        if name == 'treatment':
            (target / 'model.py').write_text('MODULE = "treatment implementation fixture"\n')
            git(target, 'add', 'model.py'); git(target, 'commit', '-m', 'fixture independent candidate')
        state = freeze(wp, root, target)['state']; snapshot = isolation.snapshot(state)
        params = {'implementation_variant': name}
        observed = len((target / 'model.py').read_bytes()); values.append(observed)
        run_id = 'fixture-' + name
        group['arms'].append({'id': name, 'record_identity': {'run_id': run_id, 'round': 1},
            'source_commit': snapshot['source_commit'], 'config_digest': snapshot['configuration']['digest'],
            'source_snapshot_ref': state['snapshot'], 'parameters': params, 'conditions': conditions})
        record = {'schema': 'sdm-round-record/v1', 'run_id': run_id, 'round': 1, 'status': 'completed',
            'parameters': params, 'provenance': {'source_commit': snapshot['source_commit'],
            'data': {'version': 'fixture-source', 'split_digest': 'fixture-no-dataset'},
            'fixed_conditions': {**fixed, 'config_digest': snapshot['configuration']['digest']}},
            'metric_definitions': definition, 'metrics': {'evaluation.source_bytes': {'value': observed}},
            'stages': {s: {'status': 'completed', 'receipt': None} for s in ('training', 'evaluation')},
            'limitations': ['Synthetic stage metadata; actual Git/source-byte observations only. No model execution.']}
        spec['record_refs'][name] = bind(repository.parent / (name + '-round.json'), record)
    spec['plan_ref'] = bind(repository.parent / 'comparison-plan.json', group)
    result = reports.export(spec, repository.parent / 'comparison-export')
    assert result['status'] == 'paired-observations'
    assert result['contrasts'][0]['treatment_minus_control'] == values[1] - values[0]
    assert result['arms'][0]['source_commit'] != result['arms'][1]['source_commit']
    assert all(a['source_snapshot_ref'] for a in result['arms'])


@pytest.mark.parametrize('flag', ['--assume-unchanged', '--skip-worktree'])
def test_hidden_index_changes_cannot_be_frozen_or_cleaned_up(repository, flag):
    spec, root, target, _ = create(repository); freeze(spec, root, target)
    git(target, 'update-index', flag, 'model.py')
    (target / 'model.py').write_text('hidden source change\n')
    with pytest.raises(ValueError, match='hidden_git_index'): freeze(spec, root, target)
    with pytest.raises(ValueError, match='hidden_git_index'): isolation.manage(spec, root, 'retire')
    assert target.exists()
