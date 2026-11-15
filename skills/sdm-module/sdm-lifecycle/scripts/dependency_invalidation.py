"""Conservative cross-trial invalidation and opt-in immutable-check reuse."""
import json
from pathlib import Path
from execution_contracts import pinned_json

DIMENSIONS={'goal','source','data','parameters','protocol','environment'}
ROOTS={'goal':{'1'},'source':{'3','4','8','9','12'},'data':{'3'},
       'parameters':{'6','8'},'protocol':{'2','3','24','16'},'environment':{'3','7','12'}}
SCOPES={'3':{'goal','source','data','protocol','environment'},'4':{'goal','source','data','protocol'},
        '12':{'source','data','parameters','protocol','environment'}}

def context(config):
    from task_pipeline import digest
    inputs=config.get('dependency_inputs')
    if inputs is None:return None
    if not isinstance(inputs,dict) or set(inputs)!=DIMENSIONS or any(v is None for v in inputs.values()):
        raise ValueError('complete_dependency_input_identities_required')
    inputs={**inputs,'source':{'declared':inputs['source'],
        'driver':{k:v for k,v in config.get('project_driver',{}).items() if k in {'digest','bundle_digest'}}}}
    inputs['parameters']={'declared':inputs['parameters'],'effective':config.get('loop_parameters',config.get('experiment_parameters',{}))}
    inputs['goal']={'declared':inputs['goal'],'objective':config.get('objective','execute-and-learn')}
    inputs['environment']={'declared':inputs['environment'],'runtime':config.get('driver_runtime')}
    declared=config.get('check_dependencies',{})
    if not isinstance(declared,dict) or set(declared)-set(SCOPES):raise ValueError('known_check_dependency_contract_required')
    scopes={}
    for key,minimum in SCOPES.items():
        scope=declared.get(key,sorted(DIMENSIONS))
        if not isinstance(scope,list) or not all(isinstance(v,str) for v in scope) or not minimum<=set(scope)<=DIMENSIONS:
            raise ValueError('conservative_check_dependencies_required')
        scopes[key]=sorted(set(scope))
    return {'schema':'sdm-dependency-context/v1','inputs':inputs,
            'node_scopes':scopes,'fingerprints':{key:digest({name:inputs[name] for name in scope}) for key,scope in scopes.items()}}

def invalidated(nodes,before,after):
    changed={key for key in DIMENSIONS if before['inputs'][key]!=after['inputs'][key]}
    affected=set().union(*(ROOTS[key] for key in changed)) if changed else set()
    while True:
        expanded=affected|{key for key,node in nodes.items() if set(node['depends'])&affected}
        if expanded==affected:break
        affected=expanded
    return changed,affected

def reuse_drivers(config,task,nodes,root):
    from task_pipeline import digest
    ctx=context(config)
    if ctx is None:
        if config.get('reuse_from'):raise ValueError('reuse_requires_dependency_inputs')
        return
    task['dependency_context']=ctx
    report={'schema':'sdm-dependency-invalidation/v1','changed_dimensions':[],'invalidated':[],
            'reused':[],'not_reused':{},'scope':'declared-immutable-input-checks-only'}
    prior=config.get('reuse_from')
    if prior:
        parent=Path(prior)
        if not parent.is_absolute():raise ValueError('absolute_prior_attempt_required')
        previous=json.loads((parent/'coordinator-task.json').read_bytes())
        before=previous.get('dependency_context')
        if not before or before.get('schema')!=ctx['schema']:raise ValueError('prior_dependency_context_required')
        rows=[json.loads(line) for line in (parent/'events.jsonl').read_text(encoding='utf-8').splitlines()]
        if not rows or any(row['task_digest']!=digest(previous) for row in rows):raise ValueError('prior_task_binding_mismatch')
        changed,affected=invalidated(nodes,before,ctx)
        report.update(changed_dimensions=sorted(changed),invalidated=sorted(affected,key=int))
        latest={row['node']:row['result'] for row in rows}
        if any(row.get('uncertain_effect') for row in latest.values()):raise ValueError('prior_uncertain_effect_requires_reconciliation')
        for key in SCOPES:
            if key not in task['drivers'] or nodes[key]['policy']=='not-applicable':continue
            result=latest.get(key,{})
            if key in affected or before['fingerprints'].get(key)!=ctx['fingerprints'][key]:
                report['not_reused'][key]='dependency_changed';continue
            if result.get('status')!='completed' or not result.get('receipt'):
                report['not_reused'][key]='accepted_receipt_missing';continue
            ref={'path':result['receipt'],'digest':result['receipt_digest']};payload=pinned_json(ref)
            marker=payload.get('reusable_check',{})
            if marker.get('scope')!='immutable-input-check' or marker.get('self_contained') is not True:
                report['not_reused'][key]='project_cache_contract_missing';continue
            old_driver=previous.get('drivers',{}).get(key,{})
            if 'external_operation' in old_driver or 'external_operation' in task['drivers'][key]:
                report['not_reused'][key]='live_external_operation';continue
            if old_driver.get('read_only') is not True or task['drivers'][key].get('read_only') is not True:
                report['not_reused'][key]='read_only_contract_missing';continue
            target=task['drivers'][key]['receipt']
            task['drivers'][key]={'reuse_receipt':ref,'receipt':target,'read_only':True,
                'reuse_node':key,'input_fingerprint':ctx['fingerprints'][key]}
            report['reused'].append(key)
    from review_storage import atomic_json
    atomic_json(Path(root)/'dependency-invalidation.json',report)

def execute_reuse(driver,key):
    from task_pipeline import digest_bytes
    if key not in SCOPES or driver.get('reuse_node')!=key or driver.get('read_only') is not True:
        raise ValueError('immutable_check_reuse_only')
    payload=pinned_json(driver['reuse_receipt']);marker=payload.get('reusable_check',{})
    if payload.get('task_node')!=key or payload.get('status')!='completed' or marker.get('scope')!='immutable-input-check' or marker.get('self_contained') is not True:
        raise ValueError('reusable_check_contract_invalid')
    raw=Path(driver['reuse_receipt']['path']).read_bytes();target=Path(driver['receipt']).resolve()
    if target==Path(driver['reuse_receipt']['path']).resolve():raise ValueError('fresh_reuse_target_required')
    if target.exists():
        if target.read_bytes()!=raw:raise ValueError('reused_receipt_changed')
    else:
        import os
        with target.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    return {'status':'completed','receipt':str(target),'receipt_digest':digest_bytes(raw),
            'observation':payload,'reused':True,'source_receipt':driver['reuse_receipt'],
            'qualification':'reused-project-check-not-new-execution'}
