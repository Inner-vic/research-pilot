"""Adapt real project facts into original SDM objects, never new gate rules."""
import copy,hashlib
from pathlib import Path
from canonical import digest
from component_research import reference
from research_project import snapshot
from research_intent import _record

COMMON={'G01','G02','G03','G09','G10','G13'}
def required(workload):
    project,_,_=snapshot(workload['project_contract'])
    return COMMON|set(project['contract_contents'])

def assemble(spec,intent):
    value=spec['sdm_governance'];project,_,_=snapshot(spec['workload']['project_contract'])
    expected={'schema','data_source','max_submissions','variables','author_principal_ids'}
    if set(value)!=expected:raise ValueError('project_governance_authoring_facts_required')
    key=lambda kind:kind.lower()+'-'+spec['project_id']
    record=lambda kind,content:_record(kind,key(kind),content,spec['project_id'],intent['requirement']['created_at'])
    source={'S02:'+intent['brief']['instance_key']:digest(intent['brief'])}
    contract=lambda kind,content:record(kind,{'source_refs':source,**content})
    contents=copy.deepcopy(project['contract_contents'])
    if not {'G03','G07','G08','G09','G10'}<=set(contents):raise ValueError('project_specific_original_contract_facts_required')
    contracts={kind:contract(kind,content) for kind,content in contents.items()}
    contracts['G01']=contract('G01',{'principles':['Account all Runs and Attempts, including failures and unknown effects.',
        'Bind the exact project, evaluator and original authorization.'],
        'separation_of_duties':{'required':True,'proposal':'research main conversation','review':'independent critic','execution':'original Manager/Guard'},
        'freeze_policy':{'immutable':True,'basis':'Project sources, checks, evaluator, accepted plans and provider binding'},
        'exception_policy':{'authority':'required','missing_evidence':'block without overrides'}})
    contracts['G13']=contract('G13',{'node_types':['Plan','Run','Attempt','Result'],'edge_types':['uses','used-by'],
        'reciprocity':{'uses':'used-by'},'closure':{'required':True}})
    contracts['G02']=contract('G02',{'references':sorted(k+':'+v['instance_key'] for k,v in contracts.items()),
        'source_refs':{k+':'+v['instance_key']:digest(v) for k,v in contracts.items()}})
    baseline=record('B01',{'baseline_id':key('B01'),'scope':'Define the exploration starting recipe and fixed comparison scope; actual results support later reference selection, not automatic reproduction or baseline-lock qualification.',
        'model':'sha256:'+spec['baseline_model']['sha256'].removeprefix('sha256:'),
        'data':'G03:'+key('G03'),'evaluator':'G10:'+key('G10'),'tolerances':{'metric_recomputation':1e-10}})
    experiment={'experiment_id':spec['project_id']+'-experiment','protocol_channel':'exploratory','promotable':False,
        'hypothesis':intent['brief']['content']['hypothesis'],'variables':value['variables'],
        'budget':{'max_submissions':value['max_submissions']},'author_principal_ids':value['author_principal_ids'],
        'decision_rule':{'schema':'sdm-scientific-rule/v1','support_predicate':'confirmatory_support',
            'rejection_predicate':'confirmatory_rejection','inconclusive_rule':'underpowered_or_no_decisive_result','invalid_rule':'protocol_invalid'}}
    omitted=project['not_applicable_contracts']
    if set(omitted)!={'G%02d'%n for n in range(1,14)}-set(contracts):raise ValueError('every_omitted_project_contract_requires_reason')
    return {'schema':value['schema'],'records':{'contracts':contracts,'baseline':baseline,'experiment':experiment},
        'not_applicable':omitted,'data_source':value['data_source'],'max_submissions':value['max_submissions']}

def bind_facts(spec,intent,value,bundle,checks):
    project,hashes,base=snapshot(spec['workload']['project_contract']);comparison=spec['comparison_identity'];c=value['records']['contracts']
    if (hashlib.sha256(reference(value['data_source']).read_bytes()).hexdigest()!=comparison['source_sha256']
            or project['data']['identity']!=comparison['source_sha256']
            or project['data']['partition_identity']!=comparison['split_sha256']
            or project['evaluation']['selection']!=comparison['selection']):raise ValueError('actual_project_data_partition_or_selection_changed')
    if (c['G03']['content']['version']!=comparison['source_sha256']
            or c['G10']['content']['evaluator_identity']!=comparison['protocol']
            or project['evaluation']['primary_metric'] not in c['G10']['content']['metric_definitions']):raise ValueError('project_original_data_or_metric_binding_changed')
    tensor=c['G09']['content']
    if any(tensor.get(k)!=project['tensor'][k] for k in ('inputs','outputs','shape','dtype')):
        raise ValueError('project_original_tensor_binding_changed')
    if project['evaluation']['kind']=='classification':
        if c.get('G07',{}).get('content',{}).get('classes')!=project['data']['labels']:
            raise ValueError('project_original_label_ontology_changed')
    if c['G02']['content']['references']!=sorted(k+':'+v['instance_key'] for k,v in c.items() if k!='G02'):
        raise ValueError('original_project_contract_bundle_not_closed')
    if any(c['G02']['content']['source_refs'].get(k+':'+v['instance_key'])!=digest(v) for k,v in c.items() if k!='G02'):
        raise ValueError('original_project_contract_source_digest_changed')
    b=value['records']['baseline']['content']
    if (b['model']!='sha256:'+spec['baseline_model']['sha256'].removeprefix('sha256:')
            or b['data']!='G03:'+c['G03']['instance_key'] or b['evaluator']!='G10:'+c['G10']['instance_key']):raise ValueError('project_baseline_source_binding_changed')
    if spec['baseline_model']['sha256'].removeprefix('sha256:') not in hashes.values():raise ValueError('initial_baseline_must_belong_to_project_snapshot')
    check_entry=next(arg for arg in project['entrypoints']['preflight'] if arg in project['source_files'])
    if tensor.get('runtime_validator_digest')!='sha256:'+hashes[check_entry]:raise ValueError('actual_project_preflight_validator_changed')
    runner=Path(__file__).with_name('project_remote.py')
    return {**value,'project_id':spec['project_id'],'comparison_identity':comparison,
        'engine_contract_digest':bundle.engine_contract_digest,'record_checks':checks,'workload':spec['workload'],
        'baseline_source':spec['baseline_source'],'baseline_model':spec['baseline_model'],
        'protected_trainer_sha256':hashlib.sha256(runner.read_bytes()).hexdigest(),
        'protected_runtime_sha256':{'_researchpilot_runner.py':hashlib.sha256(runner.read_bytes()).hexdigest(),
            '_researchpilot_preflight.py':hashlib.sha256(Path(__file__).with_name('project_preflight.py').read_bytes()).hexdigest()},
        'data_facts':project['data'],'intent_digest':digest(intent),'intent':intent,
        'scope':'Project-declared training and supported metric recount using the same original SDM objects/gates. No formal signed qualification or implicit qualification of arbitrary providers.'}
