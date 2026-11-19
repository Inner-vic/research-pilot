"""Bind current and parent execution facts; shared Manager/Guard own effects."""
import ast,hashlib,json
from pathlib import Path
from component_payload import executed_request_files,ref
from component_research import reference,round_evidence
from review_storage import read_json
from external_operations import sha
from research_replan import once

def fingerprint(request):
    files,proof=executed_request_files(request)
    project=json.loads(files['_researchpilot_project.json'])['project']
    params=json.loads(files[project['parameters_file']]);sources={}
    for name in project['editable_files']:
        if name==project['parameters_file']:continue
        sources[name]=(ast.dump(ast.parse(files[name]),include_attributes=False) if name.endswith('.py') else files[name])
    training={k:v for k,v in params['training'].items() if k not in ('round','expected_minutes')}
    return {'comparable':True,'recipe_sha256':sha({'project_sources':sources,'training':training,
        'parameters':{k:v for k,v in params.items() if k not in ('training','execution_scope','platform_requirements')},
        'comparison_identity':params['comparison_identity']}),'training':training,'code_sha256':proof['code_sha256'],
        'definition':'Exact project editable sources and scientific parameters, excluding host round/duration metadata; not general semantic equivalence.'}

def produce(manager,candidate,parent_plan,parent_result,history,root,reproduction_scope=None,workload=None,preparation_recovery=None,on_progress=None):
    if reproduction_scope is not None:raise ValueError('project_reproduction_adapter_not_yet_qualified')
    root=Path(root);root.mkdir(parents=True,exist_ok=True);plan=read_json(reference(parent_plan));ready=read_json(reference(parent_result))
    if ready.get('original_plan_sha256')!=parent_plan['sha256']:raise ValueError('project_original_parent_plan_changed')
    parent=root/'parent-task-readback.json'
    if not parent.exists():once(parent,manager.checked('task',ready['uid']))
    task=read_json(parent);guard=manager.guard(plan)
    with guard.lock():
        guard.load()
        if guard.state['phase']!='completed' or guard.state.get('uid')!=ready['uid']:raise ValueError('project_original_parent_guard_not_completed')
    request=plan['request']
    if (task.get('uid')!=ready['uid'] or task.get('status') not in ('succeeded','failed','stopped','cancelled','canceled')
            or task.get('namespace')!=manager.config['namespace'] or task.get('owner')!=manager.config['owner']):
        raise ValueError('project_exact_terminal_parent_required')
    if manager.config.get('schema')=='sdm-project-manager/v1':
        if task.get('payload_digest')!=sha(request['data']) or task.get('request_key')!=request['request_key'] or task.get('resource_uid')!=request['uid']:
            raise ValueError('project_parent_payload_readback_mismatch')
    else:
        from lpai_manager import native_config_matches
        if task.get('project_uid')!=request['uid'] or not native_config_matches(request['data'],task.get('data')):
            raise ValueError('project_parent_native_readback_mismatch')
    if ready.get('valid_training_round') is True:round_evidence(parent_result)
    elif ready.get('platform_status')!=task['status']:raise ValueError('project_failed_parent_status_changed')
    candidate_recipe=fingerprint(candidate);records=[]
    # The complete original study ledger is authoritative for scope. This
    # adapter does not invent a provider-wide inventory endpoint.
    for row in history:
        actual=round_evidence(row['result']);original=read_json(reference(row['result']))
        identity=original.get('submission_identity',{}).get('manager_plan')
        if identity:
            recipe=fingerprint(read_json(reference(identity))['request']);records.append({'uid':actual['uid'],**recipe})
            if recipe['recipe_sha256']==candidate_recipe['recipe_sha256']:raise ValueError('project_recipe_already_executed')
    if fingerprint(request)['recipe_sha256']==candidate_recipe['recipe_sha256']:
        raise ValueError('project_parent_recipe_cannot_be_blindly_repeated')
    report=root/'operational-verification.json'
    value={'schema':'sdm-project-operational-verification/v1','candidate_request_sha256':sha(candidate),
        'parent_plan':parent_plan,'parent_result':parent_result,'parent_uid':ready['uid'],'workload':workload,
        'candidate_recipe':candidate_recipe,'registered_study_history':records,'raw_evidence':[ref(parent)],
        'inventory_scope':'Complete verified study history, not an inferred provider-wide inventory.',
        'execution_authority':False,'new_platform_writes':0}
    once(report,value)
    if on_progress:on_progress({'phase':'parent-and-project-history-verified','platform_writes':0})
    return ref(report)
