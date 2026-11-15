"""Same-task native continuation: shared preflight, S12 permit, S13, release.

Each invocation is finite. There is no background monitor, resource discovery,
automatic successor or alternate submission path in this module.
"""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time

from canonical import canonical_bytes, digest
from execution_contracts import pinned_json
from project_driver import file_digest
from review_storage import atomic_json, locked
from roadmodel_normal_contract import require, check_runtime, check_training, choose_checkpoint, immutable
from task_pipeline import digest as plan_digest
import normal_shared as shared
import normal_execution as protocol


def validate(config,plan):
    spec=config['normal_native'];execution=spec['execution']
    require((plan['evidence_kind']=='cpu-simulation')==(execution.get('mode')=='cpu-fixture'),'normal_execution_qualification_mode_mismatch')
    auth=protocol.authorization(config,plan)
    protocol.allocation_budget_seconds(config,plan)
    require(spec.get('restricted_shared_control_mount') is True,'restricted_shared_control_mount_required')
    for key in ('local_control_root','local_output_root'):shared.directory(execution[key])
    require(Path(execution['local_control_root'])!=Path(execution['local_output_root']),'normal_control_output_roots_must_differ')
    if plan['evidence_kind']=='project-execution':
        require(Path(spec['sdk_python']).is_absolute() and Path(spec['sdk_python']).is_file(),'normal_sdk_interpreter_unavailable')
        shared.validate_mapping(spec,plan,pinned_json(spec['component_template']))
        bundle_value=pinned_json(spec['bundle_ref'])
        require(bundle_value.get('plan')==plan and bundle_value.get('authorization_digest')==digest(auth)
            and bundle_value.get('component_uid')==spec['adapter']['component_uid'],'normal_runtime_bundle_binding_mismatch')
        from lpai_operation_adapter import operation_binding,submission_metadata
        adapter=pinned_json(spec['adapter_config_ref'])
        submission_metadata(adapter)
        require(adapter['total_gpus']==plan['world_size'],'normal_native_requested_gpu_count_mismatch')
        binding=config['external_operations']['14']
        expected=operation_binding(spec['adapter_config_ref']['path'],spec['sdk_python'],'submit',spec['request_key'],binding['authorization_ref'])
        require(binding['routes']==expected['routes'] and binding['configuration_digest']==adapter['configuration_ref']['digest'],
            'normal_official_gated_submission_binding_required')
    from workspace import validate_workspace
    from ledger import replay
    from contracts import AssetBundle
    workspace=Path(execution['workspace'])
    require(workspace.is_absolute() and validate_workspace(workspace)['valid'],'normal_authenticated_workspace_required')
    # Loading uses the workspace's frozen asset snapshot, never ambient schemas.
    import handoffs
    replayed,state,bundle=handoffs._context(workspace)
    bundle.operation_contract('ml.normal-execution')
    require(state.get('bindings',{}).get('ml_execution_platform_skill'),'normal_execution_binding_required')
    signer=execution['signer']
    path=Path(signer['entry']['path'])
    require(path.is_absolute() and file_digest(path)==signer['entry']['digest'],'normal_signer_binding_changed')
    require(Path(signer['python']).is_absolute() and signer.get('principal'),'normal_signer_runtime_required')
    if plan['evidence_kind']=='project-execution':
        require(Path(signer['python']).is_file(),'normal_signer_interpreter_unavailable')
        for key in ('lifecycle_root','trust_root','private_root'):
            require(isinstance(signer.get(key),str) and Path(signer[key]).is_absolute() and Path(signer[key]).is_dir(),
                'normal_signer_directory_required:'+key)
    trust,_=handoffs._producer_trust(replayed)
    require(any(k.get('principal_id')==signer['principal'] and 'platform-producer' in k.get('roles',[]) for k in trust['keys']),
        'normal_signer_not_trusted_by_workspace')
    require(isinstance(execution.get('evaluation_metrics'),dict) and execution['evaluation_metrics'],'normal_evaluation_metrics_required')
    for row in execution['evaluation_metrics'].values():
        import re
        require(isinstance(row.get('pattern'),str) and len(row['pattern'])<=512,'normal_metric_pattern_required')
        re.compile(row['pattern'])
    return spec,execution


def acquired(config):
    root=Path(config['root']);journal=root/'events.jsonl'
    require(journal.exists(),'normal_accepted_acquisition_required')
    rows=[json.loads(line) for line in journal.read_text().splitlines()]
    matches=[r for r in rows if r['node']=='14' and r['result'].get('status')=='completed']
    require(len(matches)==1,'one_normal_accepted_acquisition_required')
    result=matches[0]['result']
    if result.get('receipt'):require(file_digest(Path(result['receipt']))==result['receipt_digest'],'normal_acquisition_receipt_changed')
    value=result['observation']['external_operation']['observation']
    spec=config['external_operations']['14']
    require(all(value.get(k)==spec[k] for k in ('provider','namespace','owner','component_uid','request_key')),'normal_acquisition_identity_mismatch')
    require(isinstance(value.get('uid'),str) and value['uid'],'normal_acquisition_uid_required')
    return value,matches[0]['at']


def sdk(config,operation,uid):
    """Official adapter only; validate pinned code/config before every call."""
    from external_operations import envelope
    require(config['normal_native']['execution'].get('mode')!='cpu-fixture','cpu_simulation_cannot_call_official_sdk')
    spec=config['external_operations']['14'];route=spec['routes'][0]
    require(route['kind'] in {'official-sdk','official-cli'},'normal_official_route_required')
    adapter=pinned_json(config['normal_native']['adapter_config_ref'])
    body=adapter['configuration_ref']['path']
    for ref in route['adapter_refs']:
        if operation in {'read','stop'} and ref['path']==body:continue
        require(file_digest(Path(ref['path']))==ref['digest'],'normal_native_adapter_changed')
    argv=[x.replace('{uid}',uid).replace('{request_key}',spec['request_key']) for x in route['commands'][operation]]
    result=subprocess.run(argv,capture_output=True,text=True,timeout=60,shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return envelope(json.loads(result.stdout),result.returncode)


def current(config,transport):
    original,at=acquired(config);value=transport(config,'read',original['uid'])
    for key in ('provider','namespace','owner','component_uid','request_key','uid'):
        require(value.get(key)==original[key],'normal_runtime_resource_identity_mismatch:'+key)
    require(type(value.get('observed_at')) in (int,float) and 0<=time.time()-value['observed_at']<=120,'normal_fresh_official_readback_required')
    return value,at


def paused(reason,node,**extra):
    return {'status':'paused','reason':reason,'continuation':'same-normal-task-readback','uncertain_effect':False,
        'execution_started':None if node in {'15','16'} else False,**extra}


def ensure_request(config,plan,resource,node,checkpoint=None):
    import handoffs
    from ledger import replay
    from projection import materialize
    spec=config['normal_native'];workspace=Path(spec['execution']['workspace'])
    logical=protocol.request_payload(config,plan,resource,node,spec['bundle_ref']['digest'],checkpoint)
    state=materialize(replay(workspace));row=state['handoffs'].get(logical['request_id'])
    if row is None:
        handoffs.create(workspace,logical,expected_revision=replay(workspace).revision,operation_id=logical['request_id']+'-create')
    path=workspace/'packets/S12'/(logical['request_id']+'.json')
    request=json.loads(path.read_bytes())
    require(request['subjects']==logical['subjects'],'normal_existing_s12_changed')
    state=materialize(replay(workspace));row=state['handoffs'][logical['request_id']]
    correlation=logical['request_id']+'-dispatch'
    if row['status']=='created':
        handoffs.mark_dispatched(workspace,request_id=logical['request_id'],dispatch_correlation=correlation,
            expected_revision=replay(workspace).revision,operation_id=correlation)
    return request,correlation


def sign(config,request,unsigned,target):
    signer=config['normal_native']['execution']['signer'];entry=signer['entry']
    require(file_digest(Path(entry['path']))==entry['digest'],'normal_signer_binding_changed')
    request_path=Path(config['normal_native']['execution']['workspace'])/'packets/S12'/(request['request_id']+'.json')
    argv=[signer['python'],'-B',entry['path'],'--lifecycle-root',signer['lifecycle_root'],
        '--request',str(request_path),'--result',str(unsigned),'--trust-root',signer['trust_root'],
        '--private-root',signer['private_root'],'--principal',signer['principal'],'--output',str(target)]
    result=subprocess.run(argv,capture_output=True,timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    require(result.returncode==0 and target.is_file(),'normal_producer_signing_failed')
    return json.loads(target.read_bytes())


def feedback(config,request,actual,correlation,okay,signer=sign):
    import handoffs
    from ledger import replay
    from projection import materialize
    from task_pipeline import verified_s13
    execution=config['normal_native']['execution'];workspace=Path(execution['workspace'])
    root=Path(config['root'])/'normal-session'/request['request_id'];root.mkdir(parents=True,exist_ok=True)
    unsigned=protocol.unsigned_result(request,actual,correlation,execution['signer']['principal'],okay)
    unsigned_path=root/'unsigned.json'
    if unsigned_path.exists():
        old=json.loads(unsigned_path.read_bytes())
        require(protocol.validate_result(request,old)==actual and old['terminal_status']==unsigned['terminal_status'],'normal_unsigned_result_changed')
    else:immutable(unsigned_path,unsigned)
    target=root/'signed.json'
    if target.exists():sealed=json.loads(target.read_bytes())
    else:
        sealed=signer(config,request,unsigned_path,target)
        if not target.exists():target.write_bytes(canonical_bytes(sealed))
    state=materialize(replay(workspace));row=state['handoffs'][request['request_id']]
    if row['status'] not in {'successful','failed','cancelled'}:
        handoffs.ingest(workspace,sealed,expected_revision=replay(workspace).revision,operation_id=request['request_id']+'-ingest')
    row=materialize(replay(workspace))['handoffs'][request['request_id']]
    ref={'workspace':str(workspace),'request_id':request['request_id'],'result_digest':row.get('result_digest')}
    require(verified_s13(ref,'successful' if okay else 'failed'),'normal_s13_not_accepted')
    result={'status':'completed' if okay else 'failed','actual':actual,'uncertain_effect':False,
        **({'accepted_s13':ref} if okay else {'signed_result':ref,'reason':actual.get('reason','normal_execution_failed')})}
    if actual.get('failure_evidence'):result['failure_evidence']=actual['failure_evidence']
    immutable(root/'accepted-observation.json',result)
    return result


def _step(config,plan,node,transport,signer):
    spec=config['normal_native'];execution=spec['execution'];control=execution['local_control_root']
    resource,submitted_at=current(config,transport)
    if node=='cleanup':return cleanup(config,plan,resource,transport)
    auth=protocol.authorization(config,plan)
    queue_limit=auth['queue_wall_seconds']
    ready=shared.ready(config,plan)
    if queue_limit is not None and ready is None and resource['status']!='running' and time.time()-submitted_at>queue_limit:
        return {'status':'failed','reason':'normal_explicit_queue_budget_exhausted','uncertain_effect':False}
    if node in {'7','13'}:
        failure=shared.read(control,'runtime-failure.json')
        if failure is not None:
            return {'status':'failed','reason':'normal_runtime_preflight_failed','actual_failure':failure,'uncertain_effect':False,
                'failure_evidence':{'training_started':False,'exception_type':failure['exception_type'],
                    'lines':[{'kind':'runtime-exception','text':failure['traceback']}]}}
        if resource['status'] in {'failed','stopped','cancelled','completed','succeeded'}:
            return {'status':'failed','reason':'normal_resource_terminal_before_preflight','uncertain_effect':False}
        if ready is None:return paused('normal_shared_deployment_readback_pending' if resource['status']=='running'
            else 'normal_waiting_for_allocation_and_shared_readback',node,resource_uid=resource['uid'])
        if time.time()>ready['valid_until']:
            pointer=Path(config['root'])/'normal-session'/('preflight-'+node+'.json')
            state=json.loads(pointer.read_bytes()) if pointer.exists() else None
            refreshed=shared.read(control,'ready-'+state['nonce']+'.json') if state else None
            if refreshed and time.time()<=refreshed['valid_until']:
                require(refreshed.get('bundle_digest')==spec['bundle_ref']['digest'] and refreshed.get('session_nonce')==ready['session_nonce'],'normal_refreshed_deployment_mismatch')
                ready=refreshed;ready['_shared_receipt_digest']=file_digest(Path(control)/('ready-'+state['nonce']+'.json'))
            else:
                if not state or refreshed:
                    nonce=str(time.time_ns());state={'nonce':nonce}
                    atomic_json(pointer,state);shared.publish(control,'preflight-request-'+nonce+'.json',{'node':node,'plan_digest':plan_digest(plan)})
                return paused('normal_waiting_for_fresh_preflight',node)
        check_runtime(plan,ready)
        immutable(Path(config['root'])/'normal-session'/(node+'-ready.json'),ready)
        return {'status':'completed','actual':ready,'execution_started':False,'resource_uid':resource['uid']}
    require(node in {'15','16'},'normal_session_node_required')
    selected=None
    if node=='16':
        accepted_path=Path(config['root'])/'normal-session'/'training-accepted.json'
        training=pinned_json({'path':str(accepted_path),'digest':file_digest(accepted_path)})
        from roadmodel_normal_driver import accepted_observation
        training=accepted_observation(config,'15',training);check_training(plan,training['actual'])
        index=pinned_json(training['actual']['checkpoint_index'])
        selector=config.get('normal_evaluation_checkpoint_selector',{'policy':'latest-complete','origin':plan['identity']})
        require(selector.get('origin')==plan['identity'],'normal_evaluation_checkpoint_origin_mismatch')
        selected=choose_checkpoint(index,selector,plan['identity'],'weights')
        require(file_digest(shared.output_file(config,selected['relative_path']))==selected['digest'],'normal_evaluation_checkpoint_changed')
    request,correlation=ensure_request(config,plan,resource,node,selected['digest'] if selected else None)
    from projection import materialize
    from ledger import replay
    row=materialize(replay(execution['workspace']))['handoffs'][request['request_id']]
    saved=Path(config['root'])/'normal-session'/request['request_id']/'accepted-observation.json'
    if saved.exists():
        cached=json.loads(saved.read_bytes())
        from task_pipeline import verified_s13
        ref=cached.get('accepted_s13',cached.get('signed_result'))
        require(verified_s13(ref,'successful' if cached['status']=='completed' else 'failed'),'normal_cached_s13_not_verified')
        packet=json.loads((Path(execution['workspace'])/'packets/S13'/(request['request_id']+'.json')).read_bytes())
        require(protocol.validate_result(request,packet)==cached['actual'],'normal_cached_actual_not_signed')
        return cached
    packet=Path(execution['workspace'])/'packets/S12'/(request['request_id']+'.json')
    shared.publish(control,request['request_id']+'.S12.json',request)
    permit={'schema':'roadmodel-start-permit/v1','plan_digest':plan_digest(plan),'identity':plan['identity'],
        'authorization_digest':digest(auth),'node':node,'request_id':request['request_id'],
        's12_digest':file_digest(Path(control)/(request['request_id']+'.S12.json'))}
    if node=='15':
        accepted=Path(config['root'])/'normal-session/13-ready.json'
        require(accepted.is_file(),'normal_accepted_preflight_required_before_permit')
        value=json.loads(accepted.read_bytes())
        if not (Path(control)/'start-permit.json').exists():check_runtime(plan,value)
        permit['ready_digest']=value['_shared_receipt_digest']
    else:permit.update(checkpoint_digest=selected['digest'],checkpoint_relative_path=selected['relative_path'])
    shared.publish(control,'start-permit.json' if node=='15' else 'evaluation-permit.json',permit)
    raw=shared.read(control,'training-result.json' if node=='15' else 'evaluation-result.json')
    if raw is None:
        failure=shared.read(control,'runtime-failure.json')
        if failure is not None:
            raw={'exit_code':-1,'reason':'normal_runtime_exception','request_id':request['request_id'],'actual_failure':failure}
        elif resource['status'] in {'failed','stopped','cancelled','completed','succeeded'}:
            raw={'exit_code':-1,'reason':'normal_terminal_task_missing_execution_report','request_id':request['request_id']}
        else:return paused('normal_same_task_execution_pending',node,request_id=request['request_id'],resource_uid=resource['uid'])
    require(raw.get('request_id')==request['request_id'],'normal_runtime_result_request_mismatch')
    actual={**raw,**{k:plan[k] for k in ('identity','source_commit','data_digest','effective_config_digest','runtime_output_root','evidence_kind')},
        'plan_digest':plan_digest(plan),'resource_uid':resource['uid'],'component_uid':resource['component_uid'],'bundle_digest':spec['bundle_ref']['digest']}
    okay=False
    if node=='15':
        if raw.get('checkpoint_index_digest'):
            index_path=Path(control)/'checkpoint-index.json'
            require(file_digest(index_path)==raw['checkpoint_index_digest'],'normal_checkpoint_index_changed')
            index=json.loads(index_path.read_bytes())
            for cp in index['checkpoints']:require(file_digest(shared.output_file(config,cp['relative_path']))==cp['digest'],'normal_produced_checkpoint_changed')
            actual['checkpoint_index']={'path':str(index_path),'digest':file_digest(index_path)}
        try:check_training(plan,actual);okay=raw.get('completed') is True
        except (ValueError,KeyError,TypeError):pass
    else:
        okay=type(actual.get('exit_code')) is int and actual['exit_code']==0 and actual.get('evaluation_complete') is True
        require(actual.get('checkpoint_digest')==selected['digest'],'normal_evaluation_checkpoint_mismatch')
        require(isinstance(actual.get('metrics',{}),dict),'normal_metrics_required')
        okay=okay and set(actual.get('metrics',{}))==set(execution['evaluation_metrics'])
    result=feedback(config,request,actual,correlation,okay,signer)
    if node=='15' and okay:immutable(Path(config['root'])/'normal-session/training-accepted.json',result)
    return result


def cleanup(config,plan,resource,transport):
    protocol.authorization(config,plan)
    state=Path(config['root'])/'normal-session/stop-intent.json'
    if resource.get('released') is not True and not state.exists():
        immutable(state,{'uid':resource['uid'],'component_uid':resource['component_uid'],'operation':'stop'})
        transport(config,'stop',resource['uid'])
    resource,_=current(config,transport)
    terminal=resource['status'] in {'stopped','failed','succeeded','completed','cancelled'}
    okay=terminal and resource.get('released') is True and isinstance(resource.get('pods'),list) and all(p['status'] in {'Succeeded','Failed','stopped','failed','completed','succeeded','cancelled'} for p in resource['pods'])
    return {'status':'completed' if okay else 'failed','reason':'normal_release_verified' if okay else 'normal_release_pending_readonly_reconciliation',
        'uncertain_effect':not okay,'resource_identity':{k:resource[k] for k in ('provider','namespace','uid')},'resource':resource,
        'execution_started':False,'stop_intent':str(state) if state.exists() else None}


def step(config,plan,node,transport=sdk,signer=sign):
    if node=='cleanup':return release(config,plan,transport)
    validate(config,plan)
    require(plan['evidence_kind']!='cpu-simulation' or transport is not sdk,'cpu_simulation_requires_injected_transport')
    root=Path(config['root'])/'normal-session';root.mkdir(parents=True,exist_ok=True)
    with locked(root,'.session.lock','normal_session_busy'):
        return _step(config,plan,node,transport,signer)


def release(config,plan,transport=sdk):
    """Release ownership independently of model files, signer or workspace health."""
    protocol.authorization(config,plan)
    require(plan['evidence_kind']!='cpu-simulation' or transport is not sdk,'cpu_simulation_requires_injected_transport')
    root=Path(config['root'])/'normal-session';root.mkdir(parents=True,exist_ok=True)
    with locked(root,'.session.lock','normal_session_busy'):
        resource,_=current(config,transport)
        return cleanup(config,plan,resource,transport)


def cleanup_readback(config,transport=sdk):
    plan=pinned_json(config['normal_plan']);protocol.authorization(config,plan)
    require(plan['evidence_kind']!='cpu-simulation' or transport is not sdk,'cpu_simulation_requires_injected_transport')
    resource,_=current(config,transport)
    return {'task_node':'cleanup-readback','status':'completed','resource_identity':{k:resource[k] for k in ('provider','namespace','uid')},
        'resource':{**resource,'pods_complete':True},'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'evidence_kind':plan['evidence_kind'],'source_ref':('CPU injected task/pod readback; not platform evidence' if plan['evidence_kind']=='cpu-simulation'
            else 'official LPAI SDK task get and official CLI tasks pods; same accepted task UID')}


def reconcile_cleanup(config):
    """Read-only closure of the original cleanup; never repeats stop/training."""
    from cleanup_reconciliation import reconcile,original,effective
    root=Path(config['root']);value,original_digest=original(root)
    if (root/'cleanup-reconciled.json').exists():return effective(root,value)
    generation='readback-'+str(time.time_ns());work=root/'cleanup-reconciliation'/generation
    receipt=work/'readback.json'
    request={'schema':'sdm-cleanup-reconciliation/v1','original_cleanup_digest':original_digest,
        'ownership_node':'14','identity_field':'observation.external_operation.resource_identity',
        'reconciliation_id':generation,'driver':{'argv':[sys.executable,'-B',str(Path(__file__).resolve()),
            '--project',str(root/'project.json'),'--readback','--receipt',str(receipt)],
            'receipt':str(receipt),'read_only':True,'timeout_seconds':60}}
    task=json.loads((root/'coordinator-task.json').read_bytes())
    try:return reconcile(task,root,request)
    except ValueError as error:
        if str(error) in {'release_not_verified','complete_terminal_pods_required'}:
            return {'status':'cleanup-required','reason':str(error),'read_only':True,'original_failure_retained':True}
        raise


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--readback',action='store_true');p.add_argument('--receipt',required=True)
    a=p.parse_args();require(a.readback,'normal_readback_only_entry')
    value=cleanup_readback(json.loads(Path(a.project).read_bytes()));Path(a.receipt).write_text(json.dumps(value))
