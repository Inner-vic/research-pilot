"""Independent RoadModel normal-v2 entry. No SDK, CUDA, old-driver or model import.

Native submission and release remain explicit external operations owned by SDM.
Training/evaluation completion consumes accepted S13 observations, never an argv.
"""
import argparse
import json
from pathlib import Path

from execution_contracts import pinned_json
from project_driver import file_digest
from roadmodel_normal_contract import (NODES,PROVIDER,business,check_runtime,check_training,
    choose_checkpoint,require,validate_project,execution_request,immutable,plain,IMPLEMENTATION_FILES)
from task_pipeline import digest,verified_s13


class BridgeFailure(ValueError):
    def __init__(self,result):
        super().__init__('normal_bridge_not_accepted:'+result.get('reason','execution-failed'))
        self.result=result
        self.uncertain_effect=result.get('uncertain_effect',result.get('observation',{}).get('uncertain_effect',True)) is not False


def implementation(config):
    manifest=pinned_json(config['driver_capabilities'])
    require(manifest.get('provider')==config.get('provider')==PROVIDER,'normal_provider_required')
    require(manifest.get('driver_digest')==file_digest(Path(__file__)),'normal_driver_digest_mismatch')
    files=manifest.get('implementation_files',{})
    require(set(files)==set(IMPLEMENTATION_FILES),'normal_implementation_binding_required')
    for name,wanted in files.items():require(file_digest(Path(__file__).with_name(name))==wanted,'normal_implementation_changed')
    return manifest


def binding(config,context,node):
    manifest=implementation(config)
    require(context.get('provider')==PROVIDER and context.get('driver_digest')==manifest['driver_digest']
        and context.get('manifest_digest')==config['driver_capabilities']['digest'],'normal_handshake_context_mismatch')
    require(node in context.get('nodes',{}) and context['nodes'][node]=={k:manifest['nodes'][node][k] for k in ('input_contract','output_contract')},'normal_node_contract_mismatch')
    from driver_handshake import cases_for
    cases,_=cases_for(manifest,set(context['nodes']))
    require(cases==context.get('cases') and context.get('nonce') and context.get('unsupported_case',{}).get('node')=='unsupported-'+context['nonce'],'normal_handshake_context_cases_mismatch')
    return {'schema':'sdm-driver-execution-contract/v1','provider':PROVIDER,'driver_digest':context['driver_digest'],
        'manifest_digest':context['manifest_digest'],'handshake_request_digest':digest(context),'node':node,**context['nodes'][node]}


def observation(config,node):
    ref=config.get('normal_observations',{}).get(node)
    require(ref is not None,'normal_bound_observation_required:'+node)
    return pinned_json(ref)


def accepted_observation(config,node,value=None):
    value=observation(config,node) if value is None else value;ref=value.get('accepted_s13')
    require(verified_s13(ref),'normal_execution_requires_accepted_s13')
    from canonical import digest as canonical_digest
    from roadmodel_normal_contract import plain
    request_id=ref['request_id']
    require(isinstance(request_id,str) and __import__('re').fullmatch(r'[A-Za-z0-9_.-]+',request_id),'normal_s13_request_id_required')
    root=plain(ref['workspace'])
    packet=json.loads(plain(root/'packets/S13'/(request_id+'.json')).read_bytes())
    require(canonical_digest(packet)==ref['result_digest'],'normal_signed_packet_changed')
    request=json.loads(plain(root/'packets/S12'/(request_id+'.json')).read_bytes())
    require(request.get('subjects',{}).get('execution_kind')==('run' if node=='15' else 'evaluate'),'normal_signed_execution_kind_mismatch')
    rows=[e['value'] for e in packet.get('evidence',[]) if e.get('type')=='execution_observation']
    require(len(rows)==1,'one_normal_signed_observation_required')
    actual=json.loads(rows[0]['actual_report'])
    require(isinstance(actual,dict) and canonical_digest(actual)==rows[0]['observation_digest'],'normal_signed_observation_changed')
    require(value.get('actual')==actual,'normal_exported_observation_not_signed')
    return value


def bridge(config,plan,node):
    """Dispatch only an explicit pinned S12 execution gateway, after SDM gates.

    The core execute primitive owns started/receipt recovery and uncertainty;
    this is not a raw subprocess retry or an alternate submission path.
    """
    spec=config.get('normal_execution_bridge')
    require(isinstance(spec,dict),'normal_execution_bridge_binding_required')
    if plan['evidence_kind']=='cpu-simulation':require(spec.get('kind')=='cpu-fixture','simulated_normal_bridge_must_be_fixture')
    else:require(spec.get('kind') in {'official-sdk','official-cli'} and spec.get('documentation_ref'),'normal_official_execution_bridge_required')
    path=plain(spec['entry']['path']);require(file_digest(path)==spec['entry']['digest'],'normal_execution_bridge_changed')
    python=Path(spec['python']);require(python.is_absolute() and python.is_file(),'absolute_normal_bridge_python_required')
    positive_timeout=spec.get('call_timeout_seconds')
    require(type(positive_timeout) is int and positive_timeout>0,'normal_bridge_call_timeout_required')
    root=plain(config['root'])/'normal-execution-bridge'/node;root.mkdir(parents=True,exist_ok=True)
    immutable(root/'request.json',execution_request(config,plan,node))
    from task_pipeline import execute
    result=execute({'argv':[str(python),'-B',str(path),'--request',str(root/'request.json'),'--receipt',str(root/'receipt.json')],
        'receipt':str(root/'receipt.json'),'timeout_seconds':positive_timeout},root,node,positive_timeout)
    if result.get('status')!='completed':raise BridgeFailure(result)
    return result['observation']


def runtime(config,node):
    require(node in NODES,'unsupported_node')
    if config.get('normal_native_session_version')==1 and node=='cleanup':
        from normal_session import release
        return release(config,pinned_json(config['normal_plan']))
    # Full content hash at prepare/public entry and the 13/15 dispatch boundary;
    # background/identity records do not reread multi-GB weights repeatedly.
    plan=validate_project(config,_verify_checkpoint_content=node in {'13','15'})
    if config.get('normal_native_session_version')==1 and node in {'7','13','15','16','cleanup'}:
        from normal_session import step
        result=step(config,plan,node)
        if node in {'15','16'} and result['status']=='completed':accepted_observation(config,node,result)
        return result
    if node in {'1','2','3','4','6','8'}:
        actual={'1':{'identity':plan['identity']},
            '2':{'objective':'normal-training','epochs':plan['training']['epochs']},
            '3':{'data_digest':plan['data_digest'],'scope':'full-declared-input'},
            '4':{'initialization_mode':plan['initialization_mode'],'selected_checkpoint':plan['selected_checkpoint']},
            '6':plan,'8':{'source_commit':plan['source_commit'],'effective_config_digest':plan['effective_config_digest'],
                         'resolved_config_digest':plan['resolved_config_digest']}}[node]
        return {'status':'completed','plan_digest':digest(plan),'actual':actual,'execution_started':False,
                'qualification':'validated normal plan; not training execution'}
    if node in {'7','13'}:
        actual=observation(config,'7');checked=check_runtime(plan,actual)
        return {'status':'completed','actual':actual,**checked,'execution_started':False}
    if node=='14':raise ValueError('normal_native_submit_requires_external_operation_binding')
    if node in {'15','16'}:
        selected=None
        if node=='16':
            training=accepted_observation(config,'15');check_training(plan,training['actual'])
            index=pinned_json(training['actual']['checkpoint_index'])
            selector=config.get('normal_evaluation_checkpoint_selector',{})
            require(selector.get('origin')==plan['identity'],'normal_evaluation_checkpoint_origin_mismatch')
            selected=choose_checkpoint(index,selector,plan['identity'],'weights')
            config={**config,'normal_selected_evaluation_checkpoint':selected}
        value=(accepted_observation(config,node) if config.get('normal_observations',{}).get(node)
               else accepted_observation(config,node,bridge(config,plan,node)))
        actual=value['actual']
        if node=='15':check_training(plan,actual)
        else:
            require(actual.get('plan_digest')==digest(plan) and actual.get('identity')==plan['identity'],'normal_evaluation_plan_identity_mismatch')
            for key in ('source_commit','data_digest','effective_config_digest','runtime_output_root'):
                require(actual.get(key)==plan[key],'normal_evaluation_identity_mismatch:'+key)
            require(actual.get('checkpoint_digest')==selected['digest'],'normal_evaluation_checkpoint_mismatch')
            require(type(actual.get('exit_code')) is int and actual['exit_code']==0 and actual.get('evaluation_complete') is True,'normal_evaluation_incomplete')
        return {'status':'completed','actual':actual,'accepted_s13':value['accepted_s13'],'execution_started':False,
            'qualification':'revalidated bound S13 observation; no fresh platform call'}
    if node=='18':
        return {'status':'completed','actual':{'plan_digest':digest(plan),'result_collection':'retained observations; no model success inferred'}}
    if node=='21':return {'status':'completed','merge_invoked':False,'execution_started':False}
    raise ValueError('normal_cleanup_requires_external_release_binding')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,required=True)
    p.add_argument('--node');p.add_argument('--receipt',type=Path,required=True)
    p.add_argument('--handshake',type=Path);p.add_argument('--handshake-context',type=Path)
    a=p.parse_args();config=json.loads(a.project.read_bytes())
    require(bool(a.node)!=bool(a.handshake),'one_normal_driver_operation_required')
    require(not a.receipt.exists(),'fresh_normal_driver_receipt_required')
    if a.handshake:
        implementation(config);request=json.loads(a.handshake.read_bytes())
        for node in request.get('nodes',{}):binding(config,request,node)
        rows=[{'case_id':row['case_id'],'result':business(row['node'],row['input'])} for row in request['cases']+[request['unsupported_case']]]
        value={'task_node':'driver-handshake','status':'completed','schema':'sdm-driver-handshake-response/v1',
            'request_digest':digest(request),'nonce':request['nonce'],'execution_mode':'offline-contract-test','results':rows}
    else:
        contract=None
        try:
            require(a.handshake_context is not None,'normal_handshake_context_required')
            context=json.loads(a.handshake_context.read_bytes());contract=binding(config,context,a.node)
            value={'task_node':a.node,**runtime(config,a.node)}
        except (ValueError,KeyError,TypeError,OSError) as error:
            value={'task_node':a.node,'status':'failed','reason':str(error) if isinstance(error,ValueError) else 'normal_driver_input_error',
                'uncertain_effect':getattr(error,'uncertain_effect',False),
                'execution_started':None if isinstance(error,BridgeFailure) else False}
            if isinstance(error,BridgeFailure):value['bridge_result']=error.result
        if contract:value['driver_contract']=contract
        if config.get('normal_plan'):
            try:value['evidence_kind']=pinned_json(config['normal_plan'])['evidence_kind']
            except (ValueError,OSError,KeyError):pass
    with a.receipt.open('x',encoding='utf-8') as stream:json.dump(value,stream,allow_nan=False)
    print(json.dumps({'status':value['status'],'task_node':value['task_node']}))
    return 0 if value['status'] in {'completed','paused'} else 3


if __name__=='__main__':raise SystemExit(main())
