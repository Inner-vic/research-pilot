"""Bounded, read-only entry/frozen-runtime diagnostics. No automatic migration."""
import hashlib
import json
from pathlib import Path

STARTUP_FILES = ('research_launch.py', 'research_initialize.py', 'research_supervisor.py',
                 'research_event_service.py', 'component_pipeline.py', 'model_runner.py',
                 'research_progress.py')


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def read(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('bounded_deployment_record_required')
    return json.loads(path.read_bytes())


def inspect(project, skill, runtime, indexes, mode):
    project, skill, runtime = (Path(p).resolve() for p in (project, skill, runtime))
    bundle_path = skill / 'BUNDLE.json'
    bundle = read(bundle_path) if bundle_path.is_file() else {}
    version_path = skill.parent.parent / 'VERSION'
    entry = {'mode': mode, 'version': bundle.get('version') or
             (version_path.read_text(encoding='utf-8').strip() if version_path.is_file() else 'source'),
             'bundle_sha256': sha(bundle_path) if bundle else None,
             'startup_files': {name: sha(runtime / name) for name in STARTUP_FILES if (runtime / name).is_file()},
             'verification': 'bundle-manifest-verified' if bundle else 'source-checkout'}
    studies = []
    for index in indexes[:24]:
        row = {'index': str(index), 'automatic_migration': False}
        try:
            reference = read(index)
            if reference.get('schema') != 'bmad-ml-active-study/v1':
                continue
            row['study_id'] = reference.get('study_id')
            config = Path(reference['config']).resolve()
            if not config.is_relative_to(project):
                row['status'] = 'outside-current-project'
                studies.append(row)
                continue
            cfg = read(config)
            if cfg.get('project_id') != row['study_id']:
                raise ValueError('registered_study_identity_changed')
            frozen = Path(cfg['runtime_root']).resolve()
            manifest = cfg.get('runtime_manifest') or {}
            checked, changed, drift = [], [], []
            for name, current_hash in entry['startup_files'].items():
                expected = manifest.get(name)
                if not isinstance(expected, str):
                    drift.append(name)
                    continue
                actual = sha(frozen / name)
                checked.append(name)
                if actual != expected.removeprefix('sha256:'):
                    changed.append(name)
                if actual != current_hash:
                    drift.append(name)
            row.update(status='integrity-mismatch' if changed else
                       'original-frozen-runtime' if drift else 'same-entry-runtime',
                       checked_startup_files=checked, changed_frozen_files=changed,
                       differs_from_current_entry=drift,
                       scope='Startup files only; original full-runtime checks remain authoritative.')
        except (OSError, ValueError, KeyError, TypeError):
            row.update(status='unverified', detail='原登记或启动文件暂不可核验。')
        studies.append(row)
    return {'schema': 'sdm-deployment-view/v1', 'entry': entry, 'studies': studies,
            'automatic_migration': False, 'execution_authority': False,
            'message': '新研究使用当前入口；已有研究保留原冻结，版本差异不自动触发迁移或恢复。',
            'model_calls': 0, 'platform_writes': 0}
