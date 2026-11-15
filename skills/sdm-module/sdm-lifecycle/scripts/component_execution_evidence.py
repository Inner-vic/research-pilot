"""Official read-only parent reconciliation and owned-component recipe inventory.

No submission, model, training or arbitrary command. These machine facts support
the original read-only review; original Manager/Guard still owns every write.
"""
import ast
import copy
import json
from pathlib import Path

from component_payload import executed_request_files,ref
from component_research import reference,round_evidence
from external_operations import sha
from lpai_manager import native_config_matches,subset_matches,task_name_matches
from research_replan import once
from review_storage import read_json


def fingerprint(request,default_loss_compatibility=False):
    files,proof=executed_request_files(request)
    if set(files)!={'trainer.py','model.py','parameters.json'}:
        return {'comparable':False,'reason':'original monolithic baseline, retained as separate code format',
                'code_sha256':proof['code_sha256']}
    class NoDocs(ast.NodeTransformer):
        def visit_Module(self,node):return self.body(node)
        def visit_FunctionDef(self,node):return self.body(node)
        def visit_ClassDef(self,node):return self.body(node)
        def body(self,node):
            self.generic_visit(node)
            if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
                node.body=node.body[1:]
            return node
    tree=NoDocs().visit(ast.parse(files['model.py']))
    if default_loss_compatibility:
        functions=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='build_training_loss']
        if len(functions)==1:
            node=functions[0]
            canonical=ast.parse('def build_training_loss(config):\n    return nn.CrossEntropyLoss()').body[0]
            if ast.dump(node,include_attributes=False)==ast.dump(canonical,include_attributes=False):tree.body.remove(node)
    model=ast.dump(tree,include_attributes=False)
    parameters=json.loads(files['parameters.json'])
    training={k:v for k,v in parameters['training'].items() if k not in ('round','expected_minutes')}
    return {'comparable':True,'recipe_sha256':sha({'model_ast':model,'training':training,
        'comparison_identity':parameters['comparison_identity']}),
        'training':training,'code_sha256':proof['code_sha256'],
        'definition':'Exact model/optimizer AST excluding docstrings plus training parameters excluding round/duration metadata; not general semantic equivalence.'}


def differences(wanted,actual,path='data'):
    result=[]
    def value(item):
        raw=json.dumps(item,sort_keys=True,ensure_ascii=False)
        return item if len(raw)<300 else {'sha256':sha(item),'serialized_chars':len(raw)}
    if isinstance(wanted,dict) and isinstance(actual,dict):
        for key in sorted(set(wanted)|set(actual)):
            if key not in wanted:result.append({'path':path+'.'+key,'kind':'provider-added','actual':value(actual[key])})
            elif key not in actual:result.append({'path':path+'.'+key,'kind':'provider-removed','expected':value(wanted[key])})
            else:result.extend(differences(wanted[key],actual[key],path+'.'+key))
    elif isinstance(wanted,list) and isinstance(actual,list) and len(wanted)==len(actual):
        for i,(a,b) in enumerate(zip(wanted,actual)):result.extend(differences(a,b,path+'['+str(i)+']'))
    elif wanted!=actual:result.append({'path':path,'kind':'changed','expected':value(wanted),'actual':value(actual)})
    return result


def preparation(manager,candidate,parent_plan,parent_result,root,workload):
    """A failed task is evidence for repair, never a low-scoring baseline."""
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/'preparation-verification.json'
    if path.exists():
        saved=read_json(path)
        if saved['candidate_request_sha256']!=sha(candidate) or saved['parent_plan']!=parent_plan or saved['parent_result']!=parent_result or saved['workload']!=workload:
            raise ValueError('original_preparation_verification_changed')
        for item in saved['raw_evidence']:reference(item)
        return ref(path)
    plan=read_json(reference(parent_plan));diagnosis=read_json(reference(parent_result));request=plan['request']
    if (diagnosis.get('schema')!='sdm-component-diagnosis-ready/v1' or diagnosis.get('valid_training_round') is not False
            or diagnosis.get('original_plan_sha256')!=parent_plan['sha256'].removeprefix('sha256:')):
        raise ValueError('original_preparation_diagnosis_required')
    parent=root/'failed-parent-readback.json'
    if not parent.exists():once(parent,manager.checked('task',diagnosis['uid']))
    task=read_json(parent);guard=manager.guard(plan)
    with guard.lock():
        guard.load()
        if guard.state['phase']!='completed' or guard.state.get('uid')!=task['uid']:raise ValueError('original_failed_parent_guard_required')
    if (task['uid']!=diagnosis['uid'] or task['status'] not in {'failed','stopped','cancelled','canceled'}
            or task['project_uid']!=request['uid'] or candidate['uid']!=request['uid']
            or task['namespace']!=manager.config['namespace'] or task['owner']!=manager.config['owner']
            or not task_name_matches(request['name'],task['name'],task['uid'])
            or not native_config_matches(request['data'],task['data']) or not subset_matches(request['component_settings'],task)):
        raise ValueError('original_failed_parent_native_configuration_mismatch')
    compatibility=bool(workload and workload['training_loss_hook'])
    before=fingerprint(request,compatibility);after=fingerprint(candidate,compatibility)
    if (not before.get('comparable') or not after.get('comparable') or before['recipe_sha256']==after['recipe_sha256']):
        raise ValueError('evidenced_preparation_change_required_before_new_task')
    once(path,{'schema':'sdm-component-preparation-verification/v1','candidate_request_sha256':sha(candidate),
        'parent_plan':parent_plan,'parent_result':parent_result,'workload':workload,'failed_task_uid':task['uid'],
        'valid_training_round':False,'quality_score':None,'original_recipe':before,'repair_recipe':after,
        'raw_evidence':[ref(parent),parent_plan,parent_result],'new_platform_writes':0,'local_ml_executed':False,
        'scope':'Known original task failure and a changed reviewed repair; not evidence of a trained baseline, improvement or execution approval.'})
    return ref(path)


def produce(manager,candidate,parent_plan,parent_result,history,root,reproduction_scope=None,workload=None,preparation_recovery=None,on_progress=None):
    if preparation_recovery is not None:
        if preparation_recovery!={'version':1} or history or reproduction_scope is not None:raise ValueError('explicit_initial_preparation_recovery_scope_required')
        return preparation(manager,candidate,parent_plan,parent_result,root,workload)
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    report=root/'operational-verification.json'
    if report.exists():
        saved=read_json(report)
        if (saved['candidate_request_sha256']!=sha(candidate) or saved['parent_result']!=parent_result or saved['parent_plan']!=parent_plan
                or (saved.get('explicit_reproduction') or {}).get('scope')!=reproduction_scope
                or saved.get('workload')!=workload):
            raise ValueError('original_operational_verification_changed')
        for item in saved['raw_evidence']:reference(item)
        return ref(report)
    plan=read_json(reference(parent_plan));ready=read_json(reference(parent_result))
    round_evidence(parent_result)
    parent_path=root/'parent-task-readback.json'
    if not parent_path.exists():once(parent_path,manager.checked('task',ready['uid']))
    task=read_json(parent_path);request=plan['request']
    guard=manager.guard(plan)
    with guard.lock():
        guard.load()
        if guard.state['phase']!='completed' or guard.state.get('uid')!=task['uid']:
            raise ValueError('original_parent_guard_not_completed')
    if (task['uid']!=ready['uid'] or task['status']!='succeeded' or task['project_uid']!=request['uid']
            or not task_name_matches(request['name'],task['name'],task['uid'])
            or not native_config_matches(request['data'],task['data']) or not subset_matches(request['component_settings'],task)):
        raise ValueError('actual_parent_native_configuration_mismatch')
    inventory_path=root/'owned-component-tasks.json'
    if not inventory_path.exists():
        from component_task_inventory import collect
        once(inventory_path,collect(manager,candidate['uid'],ready['uid'],root/'inventory-read',on_progress))
    inventory=read_json(inventory_path)
    compatibility=bool(workload and workload['training_loss_hook'])
    current=fingerprint(candidate,compatibility);recipes=[]
    for item in inventory['tasks']:
        recipe=fingerprint({'data':item['data']},compatibility)
        recipes.append({'uid':item['uid'],'status':item['status'],'name':item['name'],**recipe})
    matches=[row['uid'] for row in recipes if current.get('comparable') and row.get('recipe_sha256')==current['recipe_sha256']]
    reproduction=None
    if reproduction_scope is not None:
        from component_reproduction import settings
        settings(reproduction_scope)
        baseline=round_evidence(reproduction_scope['baseline_result'])
        permitted=(set(reproduction_scope['prior_completed_recipe_uids']) if reproduction_scope['version']==2 else {baseline['uid']})
        if (set(matches)!=permitted or baseline['uid'] not in matches
                or any(row['status']!='succeeded' for row in recipes if row['uid'] in matches)):
            raise ValueError('reproduction_requires_baseline_only_match_reconcile_existing_intent')
        reproduction={'scope':reproduction_scope,'baseline_uid':baseline['uid'],
            'matching_actual_recipe':True,'new_intent_not_yet_present':True}
    elif matches:raise ValueError('existing_exact_recipe_requires_original_intent_or_explicit_reproduction_scope')
    _,parent_proof=executed_request_files(request)
    _,candidate_proof=executed_request_files(candidate)
    launch_comparison={'parent':parent_proof.get('launch'),'candidate':candidate_proof.get('launch'),
        'same_wrapper_template':bool(parent_proof.get('launch') and candidate_proof.get('launch') and
            parent_proof['launch']['wrapper_template_sha256']==candidate_proof['launch']['wrapper_template_sha256']),
        'interpretation':'Payload and packed digest change with reviewed files; the wrapper template stays qualified. Static decoding only.'}
    once(report,{'schema':'sdm-component-operational-verification/v1','launch_comparison':launch_comparison,
        'candidate_request_sha256':sha(candidate),
        'parent_plan':parent_plan,'parent_result':parent_result,'parent_uid':task['uid'],
        'parent_guard_completed':True,'parent_native_configuration_matches':True,
        'parent_initial_data_digest':ready.get('submission_identity',{}).get('initial_readback_data_digest'),
        'parent_current_data_digest':sha(task['data']),'provider_differences_from_original_request':differences(request['data'],task['data']),
        'interpretation':'Raw provider metadata/catalog/default filling can change the data hash. The original Manager field comparison and the actual remote code/data/config/split/artifact identities were verified; raw-hash inequality alone is not an unresolved intent or command change.',
        'candidate_recipe':current,'owned_component_declared_recipes':recipes,'matching_exact_recipe_uids':matches,
        'default_loss_compatibility':compatibility,'workload':workload,
        'explicit_reproduction':reproduction,
        'scope':'Complete official owned-component task inventory at read time; exact declared AST/parameter comparison, not global semantic equivalence or an approval grant.',
        'project_accepted_interventions':[{'uid':round_evidence(row['result'])['uid'],
            'intervention':read_json(reference(row['accepted_plan']))['research_revision']['intervention'] if row.get('accepted_plan') else 'original baseline'} for row in history],
        'raw_evidence':[ref(parent_path),ref(inventory_path)],'new_platform_writes':0,'local_ml_executed':False})
    return ref(report)
