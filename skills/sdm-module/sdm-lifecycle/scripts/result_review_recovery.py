"""Frozen review-only producer for released results; original executor consumes."""
import hashlib
from pathlib import Path
from review_storage import read_json,locked
from task_pipeline import digest


def file_sha(path):return 'sha256:'+hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate(config,root,manifest):
    root=Path(root).resolve();manifest=Path(manifest).resolve();value=read_json(manifest)
    if (value.get('schema')!='sdm-result-review-recovery/v1' or value.get('project_digest')!=digest(config)
            or Path(value.get('output','')).resolve()!=root or value.get('execution_authority') is not False):
        raise ValueError('bound_review_only_recovery_required')
    cfg=config['research_worker']
    if cfg.get('version')!=5 or read_json(cfg['project_path'])!=config:raise ValueError('original_v5_project_required')
    state=read_json(root/'execution.json')
    if (state.get('status')!='awaiting-review' or not state.get('process_terminal')
            or not state.get('artifacts_collected') or not state.get('stop_verified')
            or state.get('binding')!=value.get('execution_binding')):
        raise ValueError('released_terminal_result_required_for_review_recovery')
    runtime=Path(value['producer_runtime']).resolve()
    if runtime/'scripts/run_project_pipeline.py'!=Path(__file__).with_name('run_project_pipeline.py').resolve():
        raise ValueError('explicit_frozen_recovery_runtime_required')
    for path,expected in value['producer_files'].items():
        target=Path(path).resolve()
        if not target.is_relative_to(runtime) or file_sha(target)!=expected:raise ValueError('recovery_producer_changed')
    required={'research_worker.py','research_model_runner.py','research_decision_store.py','result_review_recovery.py','run_project_pipeline.py'}
    if not required<={Path(p).name for p in value['producer_files']}:raise ValueError('complete_recovery_producer_binding_required')
    if not value.get('unlaunched_intents'):raise ValueError('prior_call_reconciliation_required')
    for row in value['unlaunched_intents']:
        intent=Path(row['path']).resolve()
        if not intent.is_relative_to(root/'research-decisions') or file_sha(intent)!=row['sha256']:
            raise ValueError('original_unlaunched_intent_required')
        old=read_json(intent);target=root/'model-calls'/old['call_id'].split(':')[-1][:24]
        if target.exists() or (intent.parent/'result.json').exists() or (intent.parent/'completed.json').exists():
            raise ValueError('unknown_or_completed_call_cannot_be_reissued')
        record=read_json(intent.parent.parent/'state.json')
        if record.get('status')!='blocked' or 'model_request_exceeds_bounded_input' not in record.get('reason',''):
            raise ValueError('known_prelaunch_size_failure_required')
    for row in value.get('evidence',[]):
        if file_sha(row['path'])!=row['sha256']:raise ValueError('recovery_evidence_changed')
    return value


def run(config,root,manifest):
    validate(config,root,manifest)
    from research_worker import review
    with locked(root,'.research-worker.lock','research_worker_already_running'):
        return review(config,root,read_json(Path(root)/'execution.json'),review_recovery=manifest)
