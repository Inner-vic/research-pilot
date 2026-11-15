"""Persistent Study/Trial/Attempt identity; no scheduling or retry authority."""
import json
import os
from pathlib import Path

from execution_contracts import pinned_json
from project_driver import file_digest


def bind(config,root,attempt):
    from task_pipeline import digest
    parameters=config.get('loop_parameters',config.get('experiment_parameters',{}))
    inputs=config.get('trial_inputs')
    kind=config.get('attempt_kind','new-trial')
    if kind not in {'new-trial','retry','resume'}:raise ValueError('known_attempt_kind_required')
    if not isinstance(parameters,dict):raise ValueError('experiment_parameters_required')
    if inputs is not None and (not isinstance(inputs,dict) or any(not inputs.get(k) for k in ('source','data','protocol'))):
        raise ValueError('explicit_trial_input_identities_required')
    trial_fingerprint=digest({'inputs':inputs,'parameters':parameters,
        'driver_digest':config.get('project_driver',{}).get('digest'),
        'driver_bundle_digest':config.get('project_driver',{}).get('bundle_digest')}) if inputs is not None else None
    value={'schema':'sdm-experiment-identity/v1','study_id':config.get('study_id',config['run_id']),
        'trial_id':config.get('trial_id',config['run_id']),'attempt_id':config['run_id']+f'-a{attempt:02d}',
        'kind':kind,'trial_fingerprint':trial_fingerprint,
        'qualification':'project-declared-inputs' if inputs is not None else 'input-identities-unverified',
        'parameters':parameters,'trial_inputs':inputs,'parent_identity_digest':None}
    if any(not isinstance(value[k],str) or not value[k].strip() for k in ('study_id','trial_id','attempt_id')):
        raise ValueError('explicit_experiment_identity_required')
    if kind in {'retry','resume'}:
        parent=pinned_json(config.get('parent_identity'))
        if parent.get('schema')!=value['schema'] or trial_fingerprint is None or parent.get('trial_fingerprint')!=trial_fingerprint:
            raise ValueError('retry_or_resume_requires_same_trial_inputs')
        if any(parent.get(k)!=value[k] for k in ('study_id','trial_id')) or parent.get('attempt_id')==value['attempt_id']:
            raise ValueError('same_trial_new_attempt_required')
        value['parent_identity_digest']=config['parent_identity']['digest']
    if kind=='resume':
        checkpoint=config.get('resume_checkpoint',{})
        path=Path(checkpoint.get('path',''))
        if not path.is_absolute() or path.is_symlink() or not path.is_file() or file_digest(path)!=checkpoint.get('digest'):
            raise ValueError('pinned_resume_checkpoint_required')
        evidence=pinned_json(config.get('restore_evidence'))
        if evidence.get('schema')!='sdm-checkpoint-restore/v1' or evidence.get('checkpoint_digest')!=checkpoint['digest'] or evidence.get('status')!='verified':
            raise ValueError('verified_checkpoint_restore_evidence_required')
        required=config.get('required_resume_states')
        if not isinstance(required,list) or not {'model','optimizer','scheduler','rng'}<=set(required) or any(
                evidence.get('restored_states',{}).get(key) is not True for key in required):
            raise ValueError('complete_training_state_restore_required')
        value['checkpoint_digest']=checkpoint['digest']
        value['restore_evidence_digest']=config['restore_evidence']['digest']
    path=Path(root)/'experiment-identity.json'
    if path.exists():
        if json.loads(path.read_bytes())!=value:raise ValueError('experiment_identity_changed')
    else:
        try:
            with path.open('x',encoding='utf-8') as stream:
                json.dump(value,stream,ensure_ascii=False,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
        except FileExistsError:
            if json.loads(path.read_bytes())!=value:raise ValueError('experiment_identity_changed')
    return value
