"""Pinned offline behavioral challenges and per-invocation contract binding."""
import json
from pathlib import Path
import secrets
from execution_contracts import pinned_json
from project_driver import file_digest
from review_storage import atomic_json


def cases_for(manifest,nodes):
    cases=[];expected={}
    if manifest.get('handshake_protocol')!='sdm-driver-handshake/v1':raise ValueError('driver_handshake_protocol_required')
    for node in sorted(nodes):
        rows=manifest['nodes'][node].get('behavior_cases')
        if not isinstance(rows,list) or not 2<=len(rows)<=8:raise ValueError('valid_and_invalid_behavior_cases_required:'+node)
        kinds=set()
        for index,row in enumerate(rows):
            if not isinstance(row,dict):raise ValueError('typed_behavior_case_required')
            kind=row.get('kind');value=row.get('expected')
            if kind not in {'valid','invalid'} or not isinstance(row.get('input'),dict) or not isinstance(value,dict):raise ValueError('typed_behavior_case_required')
            if value.get('task_node')!=node or value.get('status')!=('completed' if kind=='valid' else 'failed'):raise ValueError('behavior_case_result_contract_required')
            if kind=='invalid' and not value.get('reason'):raise ValueError('invalid_case_rejection_reason_required')
            key=node+':'+str(index);cases.append({'case_id':key,'node':node,'input':row['input']});expected[key]=value;kinds.add(kind)
        if kinds!={'valid','invalid'}:raise ValueError('valid_and_invalid_behavior_cases_required:'+node)
    return cases,expected


def build(config,nodes,provider,prefix,root,convert,budget):
    from task_pipeline import digest
    if not nodes:return None
    if type(budget) is not int or budget<1:raise ValueError('positive_handshake_budget_required')
    manifest=pinned_json(config['driver_capabilities']) if config.get('driver_capabilities') else None
    if not manifest or manifest.get('schema')!='sdm-driver-capabilities/v2':
        return {'schema':'sdm-driver-handshake/v1','status':'missing','reason':'behavioral_manifest_v2_required'}
    if not config.get('driver_runtime') and not config.get('project_driver',{}).get('files'):
        raise ValueError('behavioral_handshake_requires_guarded_driver_runtime')
    cases,_=cases_for(manifest,nodes)
    request_path=root/'driver-handshake-request.json'
    contracts={k:{field:manifest['nodes'][k][field] for field in ('input_contract','output_contract')} for k in sorted(nodes)}
    fixed={'schema':'sdm-driver-handshake-request/v1','provider':config['provider'],'graph_revision':3,
        'driver_digest':file_digest(provider),'manifest_digest':config['driver_capabilities']['digest'],
        'nodes':contracts,'cases':cases,'execution_mode':'offline-contract-test','execution_authority_created':False}
    if request_path.exists():
        request=json.loads(request_path.read_bytes())
        if {k:v for k,v in request.items() if k not in {'nonce','unsupported_case'}}!=fixed:raise ValueError('driver_handshake_request_changed')
    else:
        nonce=secrets.token_hex(24)
        request={**fixed,'nonce':nonce,'unsupported_case':{'case_id':'unsupported','node':'unsupported-'+nonce,'input':{}}}
        atomic_json(request_path,request)
    response=root/'driver-handshake-response.json'
    return {'schema':'sdm-driver-handshake/v1','status':'required','provider':config['provider'],
        'driver_ref':{'path':str(provider),'digest':file_digest(provider)},'manifest_ref':config['driver_capabilities'],
        'request_ref':{'path':str(request_path),'digest':file_digest(request_path)},
        'request_digest':digest(request),'argv':prefix+['--handshake',convert(request_path),'--receipt',convert(response)],
        'receipt':str(response),'timeout_seconds':budget}


def invocation(spec,node):
    from task_pipeline import digest
    request=pinned_json(spec['request_ref'])
    row=request['nodes'][node]
    return {'schema':'sdm-driver-execution-contract/v1','provider':request['provider'],'driver_digest':request['driver_digest'],
        'manifest_digest':request['manifest_digest'],'handshake_request_digest':digest(request),'node':node,**row}


def ensure(spec,root,budget):
    from task_pipeline import execute,digest
    if spec.get('status')!='required':raise ValueError('behavioral_driver_handshake_required')
    driver=Path(spec['driver_ref']['path'])
    if not driver.is_absolute() or driver.is_symlink() or file_digest(driver)!=spec['driver_ref']['digest']:raise ValueError('handshake_driver_changed')
    request=pinned_json(spec['request_ref']);manifest=pinned_json(spec['manifest_ref'])
    if (request['driver_digest']!=file_digest(driver) or manifest.get('driver_digest')!=file_digest(driver)
        or request.get('manifest_digest')!=spec['manifest_ref']['digest'] or request.get('provider')!=spec['provider']
        or manifest.get('provider')!=spec['provider'] or manifest.get('graph_revision')!=3
        or digest(request)!=spec['request_digest']):raise ValueError('driver_handshake_binding_mismatch')
    cases,expected=cases_for(manifest,set(request['nodes']))
    contracts={k:{field:manifest['nodes'][k][field] for field in ('input_contract','output_contract')} for k in request['nodes']}
    if request.get('cases')!=cases or request['nodes']!=contracts:raise ValueError('handshake_cases_changed')
    unknown=request.get('unsupported_case',{})
    if not request.get('nonce') or unknown.get('node')!='unsupported-'+request['nonce'] or unknown.get('case_id')!='unsupported':raise ValueError('unsupported_handshake_case_required')
    expected['unsupported']={'task_node':unknown['node'],'status':'failed','reason':'unsupported_node'}
    driver_call={'argv':spec['argv'],'receipt':spec['receipt'],'read_only':True,'timeout_seconds':spec['timeout_seconds']}
    result=execute(driver_call,Path(root),'driver-handshake',min(budget,spec['timeout_seconds']))
    if result['status']!='completed':raise ValueError('driver_handshake_process_failed')
    payload=result['observation']
    if payload.get('schema')!='sdm-driver-handshake-response/v1' or payload.get('request_digest')!=digest(request) or payload.get('nonce')!=request['nonce'] or payload.get('execution_mode')!='offline-contract-test':raise ValueError('driver_handshake_response_mismatch')
    rows=payload.get('results')
    if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError('complete_behavior_results_required')
    seen=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('typed_behavior_result_required')
        key=row.get('case_id')
        if key not in expected or key in seen or row.get('result')!=expected[key]:raise ValueError('driver_behavior_case_failed')
        seen.add(key)
    accepted={'schema':'sdm-driver-handshake-acceptance/v1','status':'passed','request_digest':digest(request),
        'response_digest':result['receipt_digest'],'contracts':contracts,'qualification':'offline-protocol-conformance-not-business-or-platform-proof'}
    path=Path(root)/'driver-handshake-accepted.json'
    if path.exists() and json.loads(path.read_bytes())!=accepted:raise ValueError('accepted_driver_handshake_changed')
    if not path.exists():atomic_json(path,accepted)
    return accepted


def verify_runtime(driver,payload):
    expected=driver.get('behavior_contract')
    if expected is not None and payload.get('driver_contract')!=expected:raise ValueError('runtime_driver_contract_mismatch')


def bindings(task,spec):
    if spec.get('status')!='required':return
    request=pinned_json(spec['request_ref'])
    from task_pipeline import graph
    nodes=graph(task)
    # The graph owns dispatch: ignored gate bindings and inactive legacy entries
    # cannot become extra project operations just because an argv was supplied.
    owned={k:v for k,v in task.get('drivers',{}).items() if k in nodes and not nodes[k].get('builtin')
        and nodes[k]['policy'] in {'required','record'} and 'external_operation' not in v}
    cleanup=task.get('cleanup_driver')
    if cleanup and 'external_operation' not in cleanup:owned['cleanup']=cleanup
    if set(owned)!=set(request['nodes']):raise ValueError('handshake_selected_driver_set_mismatch')
    for node,driver in owned.items():
        if 'reuse_receipt' not in driver and driver.get('behavior_contract')!=invocation(spec,node):
            raise ValueError('handshake_runtime_binding_missing:'+node)
