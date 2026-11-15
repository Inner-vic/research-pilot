"""Explicit read-only inventory collection and context-bound review import."""
import json
from pathlib import Path
from execution_contracts import pinned_json
from project_driver import file_digest
from review_storage import atomic_json

REVIEW_CONTEXTS={'method_review':'research','plan_review':'plan-context','review_response':'review_context'}


def reference(ref):
    import re
    if not isinstance(ref,dict) or set(ref)!={'path','digest'} or not isinstance(ref['path'],str) or not Path(ref['path']).is_absolute() or not isinstance(ref['digest'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',ref['digest']):
        raise ValueError('pinned_collector_reference_required')


def validate(name,spec):
    kind=spec.get('kind')
    if kind=='review-import':
        if name not in REVIEW_CONTEXTS or set(spec)!={'kind','source'}:raise ValueError('review_import_kind_required')
        reference(spec['source']);return
    if kind!='local-collector' or name!='inventory' or set(spec)!={'kind','python','driver','inputs','timeout_seconds'}:
        raise ValueError('mechanical_inventory_collector_required')
    reference(spec['driver'])
    if not isinstance(spec['python'],str) or not Path(spec['python']).is_absolute():raise ValueError('absolute_collector_python_required')
    if type(spec['timeout_seconds']) is not int or not 1<=spec['timeout_seconds']<=300:raise ValueError('bounded_collector_timeout_required')
    if not isinstance(spec['inputs'],dict) or not spec['inputs']:raise ValueError('collector_inputs_required')
    for key,ref in spec['inputs'].items():
        if not isinstance(key,str) or not key:raise ValueError('collector_input_name_required')
        reference(ref)


def collect(task,name,spec,root,budget):
    from task_pipeline import digest,execute
    from orchestration import needed,value_digest
    validate(name,spec)
    if spec['kind']=='review-import':
        value=pinned_json(spec['source'])
        context_name=REVIEW_CONTEXTS[name]
        if context_name=='plan-context':
            from review_collaboration import plan_context
            context=plan_context(task)
        else:context=needed(task,context_name)
        expected=value_digest(context)
        if not isinstance(value,dict) or value.get('schema')!='sdm-reviewed-record/v1' or value.get('record_kind')!=name or value.get('status')!='final' or value.get('context_digest')!=expected or value.get('mode')!=task['orchestration']['review_mode'] or any(not isinstance(value.get(k),str) or not value[k].strip() for k in ('reviewer','source_ref')):
            raise ValueError('completed_context_bound_review_required')
        record=value.get('record')
        field='research_digest' if name=='method_review' else 'context_digest'
        if not isinstance(record,dict) or record.get(field)!=expected:raise ValueError('review_record_context_mismatch')
        return record,{'kind':'review-import','source_digest':spec['source']['digest'],'context_digest':expected,
            'qualification':'imported-review-not-new-review-or-human-authority'}
    if root is None:raise ValueError('collector_attempt_root_required')
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    request={'schema':'sdm-record-collection-request/v1','record_kind':name,'execution_mode':'read-only-collection',
        'driver':spec['driver'],'python':spec['python'],'inputs':spec['inputs']}
    request_path=root/('record-producer-'+name+'-request.json')
    if request_path.exists():
        if json.loads(request_path.read_bytes())!=request:raise ValueError('collector_request_changed')
    else:atomic_json(request_path,request)
    # Recheck even on replay: a cached receipt cannot mask changed original inputs.
    for ref in spec['inputs'].values():pinned_json(ref)
    driver=Path(spec['driver']['path'])
    if driver.is_symlink() or not driver.is_file() or file_digest(driver)!=spec['driver']['digest']:raise ValueError('collector_driver_changed')
    receipt=root/('record-producer-'+name+'.json')
    argv=[spec['python'],'-B',str(Path(__file__).resolve()),'--request',str(request_path),'--receipt',str(receipt)]
    result=execute({'argv':argv,'receipt':str(receipt),'read_only':True,'timeout_seconds':spec['timeout_seconds']},root,
        'record-producer-'+name,min(budget,spec['timeout_seconds']))
    if result['status']!='completed':raise ValueError('record_collection_failed')
    value=result['observation']
    route=value.get('source_route',{})
    if (value.get('schema')!='sdm-record-collection-result/v1' or value.get('request_digest')!=digest(request)
        or value.get('record_kind')!=name or value.get('input_digests')!={k:v['digest'] for k,v in spec['inputs'].items()}
        or not isinstance(route,dict) or route.get('kind') not in {'official-sdk','official-cli','browser','computer-use','custom-adapter'}):
        raise ValueError('collector_receipt_binding_mismatch')
    if route['kind'] not in {'official-sdk','official-cli'} and not route.get('official_capability_gap'):
        raise ValueError('official_capability_gap_required')
    return value.get('record'),{'kind':'local-collector','request_digest':digest(request),'receipt_digest':result['receipt_digest'],
        'input_digests':value['input_digests'],'source_route':route,'qualification':'project-read-only-observation-not-resource-authority'}


def main():
    import argparse,os
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();request=json.loads(a.request.read_bytes());driver=Path(request['driver']['path'])
    if a.receipt.exists():raise ValueError('fresh_collector_receipt_required')
    if driver.is_symlink() or not driver.is_absolute() or file_digest(driver)!=request['driver']['digest']:raise ValueError('collector_driver_changed')
    for ref in request['inputs'].values():pinned_json(ref)
    argv=[request['python'],'-B',str(driver),'--request',str(a.request),'--receipt',str(a.receipt)]
    os.execve(argv[0],argv,{**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})


if __name__=='__main__':main()
