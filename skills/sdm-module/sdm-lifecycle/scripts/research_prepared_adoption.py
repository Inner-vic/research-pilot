"""Prepare a new frozen owner for the proved pre-submit receipt-path defect.

Reuse the original prepared request, authorization, reviews, Context and budget.
The old configuration, state, Inbox and frozen files are retained. No model or
platform call is made here. Other integrity failures and started Guards refuse.
"""
import argparse
import copy
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from component_payload import ref
from component_research import reference
from review_storage import atomic_json,read_json
from task_pipeline import digest


def prepare(config_path,destination,new_root,output,reason_ref):
    from research_event_service import configuration
    from research_runtime import freeze,owner_alive
    from research_context import Context
    from lpai_manager import Manager,runtime_digest
    from component_submission import successor_reviews,verify
    from research_acceptance import project
    cfg=configuration(read_json(config_path),control_only=True);old_root=Path(cfg['root']).resolve()
    if owner_alive(cfg):raise ValueError('prepared_adoption_original_owner_must_exit')
    state_path=old_root/'component-pipeline/pipeline.json';state=read_json(state_path)
    if state.get('config_digest')!=digest(cfg):raise ValueError('prepared_adoption_original_state_changed')
    current=state['attempts'][-1]
    if (current.get('stage')!='needs-attention'
            or current.get('reason')!='integrity-blocked:immutable_research_replan_changed'
            or current.get('continuation',{}).get('resume_stage')!='submitting'):
        raise ValueError('prepared_adoption_requires_exact_documented_receipt_collision')
    context=Context(cfg).load()
    if context.get('pending_job') or context.get('pending_turn'):
        raise ValueError('prepared_adoption_native_outcome_unresolved')
    with closing(sqlite3.connect((old_root/'delivery.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
        counts=[list(row) for row in db.execute('select status,count(*) from jobs group by status')]
        if any(status!='completed' for status,count in counts):
            raise ValueError('prepared_adoption_all_original_jobs_must_be_completed')
    if any(not p.with_suffix('.receipt.json').exists() for p in (old_root/'deferred-events').glob('*.json') if not p.name.endswith('.receipt.json')):
        raise ValueError('prepared_adoption_deferred_event_unresolved')
    spec=cfg['component_pipeline'];plan=read_json(reference(current['manager_plan']))
    authorization=read_json(reference(current['manager_authorization']))
    manager_cfg=read_json(reference(spec['initial_source']['manager_config']))
    if plan['runtime_digest']!=runtime_digest():raise ValueError('prepared_adoption_manager_runtime_changed')
    guard=Manager(manager_cfg).guard(plan)
    with guard.lock():
        guard.load()
        if guard.state['phase']!='new':raise ValueError('prepared_adoption_started_guard_requires_original_reconciliation')
    old_acceptance=read_json(reference(current['submission_acceptance']))
    if (old_acceptance['manager_plan']!=spec['initial_source']['plan']
            or old_acceptance['manager_plan']==current['manager_plan']):
        raise ValueError('prepared_adoption_baseline_receipt_collision_not_proved')
    reviews=successor_reviews(current,reference(authorization['code_review']))
    original_ref=ref(config_path)
    replay_spec=copy.deepcopy(spec)
    replay_spec['prior_submission_verifiers']={plan_ref['sha256']:original_ref for plan_ref in
        [spec['initial_source']['plan'],current['manager_plan']]}
    accepted=verify(replay_spec,manager_cfg,current['manager_plan'],current['manager_authorization'],
                    reviews,current['payload_manifest'],science_plan=current['science_plan'])
    reason=read_json(reference(reason_ref))
    if reason.get('schema')!='sdm-prepared-receipt-repair/v1' or not reason.get('source_ref'):
        raise ValueError('prepared_adoption_exact_repair_evidence_required')
    if reason.get('pending_plan')!=current['manager_plan'] or reason.get('baseline_receipt')!=current['submission_acceptance']:
        raise ValueError('prepared_adoption_repair_evidence_identity_changed')
    destination=Path(destination).resolve();new_root=Path(new_root).resolve();output=Path(output).resolve()
    for path in (destination,new_root):
        if path.exists() or path.is_relative_to(old_root) or path.is_relative_to(Path(cfg['cwd']).resolve()):
            raise ValueError('prepared_adoption_fresh_independent_roots_required')
    if output.exists() or output.is_relative_to(old_root) or output.is_relative_to(Path(cfg['cwd']).resolve()):
        raise ValueError('prepared_adoption_fresh_control_output_required')
    manifest=freeze(destination);new_cfg=copy.deepcopy(cfg)
    new_cfg.update(root=str(new_root),**{k:v for k,v in manifest.items() if k!='schema'})
    new_cfg['component_pipeline']=replay_spec
    proof=output.with_suffix('.adoption.json')
    if proof.exists():raise ValueError('prepared_adoption_existing_intent_requires_reconciliation')
    atomic_json(proof,{'schema':'sdm-prepared-submission-adoption/v1','source_ref':reason['source_ref'],
        'original_config':ref(config_path),'original_state':ref(state_path),'repair_evidence':reason_ref,
        'pending_plan':current['manager_plan'],'authorization':current['manager_authorization'],
        'original_jobs':counts,'native_context':ref(Path(cfg['research_context']['root'])/'context.json'),
        'original_inbox_retained':True,'old_records_modified':False,'guard_phase':'new',
        'accepted_pending_digest':digest(accepted),'model_calls':0,'platform_writes':0})
    new_cfg['prepared_submission_adoption']=ref(proof)
    new_state=copy.deepcopy(state);new_state['config_digest']=digest(new_cfg)
    resumed=new_state['attempts'][-1]
    resumed['continuation_folder']=str(new_root/'component-pipeline/adopted'/current['manager_plan']['sha256'].removeprefix('sha256:'))
    resumed.update(stage='submitting');resumed.pop('reason',None);resumed.pop('continuation',None)
    # Reuse the already completed native recovery exercise without firing it a
    # second time. Known-task-wait remains eligible for the remaining new task.
    new_root.mkdir(parents=True)
    exercised=old_root/'recovery-exercise/native-receipt-durable.json'
    if exercised.exists():
        value=read_json(exercised);value['prior_checkpoint']=ref(exercised);value['config_sha256']=digest(new_cfg)
        value['scope']='Previously exercised native completion boundary retained; no new interruption.'
        atomic_json(new_root/'recovery-exercise/native-receipt-durable.json',value)
    code='import sys,json;sys.path.insert(0,sys.argv[1]);from research_event_service import configuration;configuration(json.load(sys.stdin))'
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',code,new_cfg['runtime_root']],
        input=json.dumps(new_cfg),capture_output=True,encoding='utf-8',timeout=60,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise ValueError('prepared_adoption_frozen_validation_failed:'+result.stderr.splitlines()[-1][:200])
    project(new_cfg,new_state)  # Original applicable-core and original review-owner replay.
    atomic_json(new_root/'component-pipeline/pipeline.json',new_state);atomic_json(output,new_cfg)
    return {'status':'prepared','config':str(output),'proof':ref(proof),'pending_plan':current['manager_plan'],
            'native_context_retained':True,'submission_budget_renewed':False,'model_calls':0,'platform_writes':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config_path','destination','new_root','output','reason_ref'):parser.add_argument('--'+name.replace('_','-'),required=True)
    args=vars(parser.parse_args());args['reason_ref']=read_json(args['reason_ref'])
    print(json.dumps(prepare(**args)))
