"""Normal S12/S13 contracts, independent of the bounded diagnostic operation."""
import json
import math
from pathlib import Path
from canonical import canonical_bytes, digest
from execution_contracts import pinned_json
from task_pipeline import digest as plan_digest


def require(ok,reason):
    if not ok:raise ValueError(reason)


def authorization(config,plan):
    spec=config['normal_native'];value=pinned_json(spec['training_authorization'])
    require(value.get('schema')=='sdm-normal-training-authorization/v1','normal_authorization_schema_required')
    require(value.get('direct_user_confirmation') is True and value.get('source_ref'),'normal_direct_user_authorization_required')
    for k,want in {'plan_digest':plan_digest(plan),'identity':plan['identity'],'evidence_kind':plan['evidence_kind'],
        'component_uid':spec['adapter']['component_uid'],'namespace':spec['adapter']['namespace'],
        'owner':spec['adapter']['owner'],'training_wall_seconds':plan['training_wall_seconds']}.items():
        require(k in value and value[k]==want,'normal_authorization_scope_mismatch:'+k)
    require(value.get('scope')=='normal-training' and value.get('allow_evaluation') is True
        and value.get('allow_cleanup') is True,'normal_execution_and_cleanup_authorization_required')
    # None means explicitly unrestricted, never absent/defaulted to 1800 seconds.
    for k in ('queue_wall_seconds','total_budget'):
        require(k in value and (value[k] is None or type(value[k]) in (int,float) and math.isfinite(value[k]) and value[k]>0),'normal_explicit_budget_required:'+k)
    return value


def request_payload(config,plan,resource,node,bundle_digest,checkpoint_digest=None):
    require(node in {'15','16'},'normal_execution_node_required')
    auth=authorization(config,plan)
    subjects={'artifact_id':digest(plan['identity']),'plan_digest':plan_digest(plan),'identity_digest':digest(plan['identity']),
        'authorization_digest':digest(auth),'resource_uid':resource['uid'],'component_uid':resource['component_uid'],
        'execution_kind':'run' if node=='15' else 'evaluate','evidence_kind':plan['evidence_kind'],
        'execution_scope':'normal-training','source_commit':plan['source_commit'],'data_digest':plan['data_digest'],
        'effective_config_digest':plan['effective_config_digest'],'bundle_digest':bundle_digest,
        'training_wall_seconds':json.dumps(plan['training_wall_seconds'])}
    subjects.update(target_epochs=str(plan['training']['epochs']),world_size=str(plan['world_size']))
    if checkpoint_digest:subjects['checkpoint_digest']=checkpoint_digest
    payload={'operation':'ml.normal-execution','subjects':subjects,'gate':'execution.observation','step':5,'portable_paths':[]}
    payload['request_id']='REQ-normal-'+digest(payload).split(':')[1][:40]
    return payload


def allocation_budget_seconds(config,plan):
    auth=authorization(config,plan)
    if auth['total_budget'] is None:return None
    quote=pinned_json(config['normal_native']['budget_quote'])
    require(quote.get('component_uid')==auth['component_uid'] and quote.get('namespace')==auth['namespace']
        and quote.get('currency')==auth.get('currency') and isinstance(quote.get('currency'),str)
        and quote.get('source_ref'),'normal_bound_component_quote_required')
    price=quote.get('hourly_cost')
    require(type(price) in (int,float) and math.isfinite(price) and price>0,'normal_finite_hourly_quote_required')
    seconds=auth['total_budget']*3600/price
    require(math.isfinite(seconds) and seconds>=1,'normal_budget_below_one_second')
    return seconds


def validate_result(request,result):
    require(request['operation']=='ml.normal-execution','normal_s12_operation_required')
    rows=[e['value'] for e in result.get('evidence',[]) if e.get('type')=='execution_observation']
    require(len(rows)==1,'one_normal_execution_observation_required')
    value=rows[0];subjects=request['subjects'];actual=json.loads(value['actual_report'])
    require(digest(actual)==value['observation_digest'],'normal_observation_digest_mismatch')
    for key in ('plan_digest','authorization_digest','resource_uid','component_uid','execution_kind','evidence_kind','execution_scope'):
        require(value.get(key)==subjects[key],'normal_evidence_subject_mismatch:'+key)
    for key in ('plan_digest','source_commit','data_digest','effective_config_digest','resource_uid','component_uid','evidence_kind','bundle_digest'):
        require(actual.get(key)==subjects[key],'normal_actual_subject_mismatch:'+key)
    require(digest(actual.get('identity'))==subjects['identity_digest'],'normal_actual_identity_mismatch')
    if subjects['execution_kind']=='evaluate':
        require(actual.get('checkpoint_digest')==subjects.get('checkpoint_digest'),'normal_actual_checkpoint_mismatch')
    if result['terminal_status']=='successful':
        require(type(actual.get('exit_code')) is int and actual['exit_code']==0,'normal_success_requires_zero_exit')
        if subjects['execution_kind']=='run':
            require(type(actual.get('completed_epochs')) is int and actual['completed_epochs']==int(subjects['target_epochs'])
                and type(actual.get('optimizer_updates')) is int and actual['optimizer_updates']>0
                and type(actual.get('world_size')) is int and actual['world_size']==int(subjects['world_size']) and actual.get('checkpoint_index'),
                'normal_success_requires_target_epochs_updates_and_checkpoint')
        else:
            metrics=actual.get('metrics')
            require(actual.get('evaluation_complete') is True and isinstance(metrics,dict) and metrics
                and all(type(v) in (int,float) and math.isfinite(v) for v in metrics.values()),'normal_success_requires_evaluation_metrics')
    return actual


def unsigned_result(request,actual,correlation,principal,successful):
    from ledger import utc_now
    subjects=request['subjects']
    value={k:subjects[k] for k in ('plan_digest','authorization_digest','resource_uid','component_uid',
        'execution_kind','evidence_kind','execution_scope')}
    value.update(observation_digest=digest(actual),actual_report=canonical_bytes(actual).decode())
    result={'schema':'sdm-handoff-result/v1','result_id':'RES-normal-'+digest(actual).split(':')[1][:40],
        **{k:request[k] for k in ('request_id','operation','binding_identity','binding_skill_digest','adapter_contract_digest',
            'workspace_id','workspace_revision','gate','step','subjects','idempotency_key')},
        'dispatch_correlation':correlation,'producer_identity':request['binding_identity'],
        'producer_principal_id':principal,'terminal_status':'successful' if successful else 'failed',
        'evidence':[{'type':'execution_observation','value':value}],'completed_at':utc_now()}
    validate_result(request,result)
    return result
