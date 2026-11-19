"""Real original validators and Guard, synthetic model/provider boundaries."""
import copy,hashlib,json
from pathlib import Path
import pytest
from review_storage import atomic_json,read_json
from component_payload import ref,contract,task_request,verify_request_payload,executed_request_files
from research_project import FILE,freeze,inspect,relative,snapshot
from project_results import recount,round_evidence
from research_acceptance import prepare,publish,load
from research_execution import Manager
from external_operations import sha

def fixture(root,kind='classification'):
    from test_research_acceptance import material
    root.mkdir(exist_ok=True);spec,intent=material(root)
    project=root/'project';project.mkdir();labels=['a','b','c'] if kind=='classification' else []
    data={'rows':list(range(9)),'labels':labels};atomic_json(project/'data.json',data)
    split={'train_ids':[0,1,2],'validation_ids':[3,4,5,6,7,8],'test_used':False}
    raw=json.dumps(split,sort_keys=True).encode();(project/'split-input.json').write_bytes(raw)
    source_hash=hashlib.sha256((project/'data.json').read_bytes()).hexdigest();split_hash=hashlib.sha256(raw).hexdigest()
    for name in ('train.py','check.py','evaluate.py'):(project/name).write_text('print("syntax-only control; never ML")\n')
    atomic_json(project/'params.json',{'training':{'round':1,'epochs':1,'expected_minutes':1,'model':'project-control'}})
    comparison={'protocol':'project-control-v1','source_sha256':source_hash,'split_sha256':split_hash,'selection':'last'}
    metric='mean_iou' if kind=='classification' else 'mae'
    contracts={k:copy.deepcopy(v['content']) for k,v in spec['sdm_governance']['records']['contracts'].items() if k not in ('G01','G02','G13')}
    for c in contracts.values():c.pop('source_refs',None)
    contracts['G03'].update(version=source_hash,dataset_identity='sha256:'+source_hash,
        schema={'x':'project-owned input','label':kind},lineage={'source':'data.json'})
    tensor={'inputs':['x'],'outputs':['prediction'],'shape':{'x':[2],'prediction':[3] if labels else [1]},'dtype':'float32','constraints':{'finite':True}}
    contracts['G09'].update({k:tensor[k] for k in ('inputs','outputs','shape','dtype')})
    contracts['G09']['runtime_validator_digest']='sha256:'+ref(project/'check.py')['sha256']
    contracts['G10'].update(evaluator_identity=comparison['protocol'],metric_definitions={metric:'Metric independently recounted from all fixed validation predictions.'})
    if labels:contracts['G07'].update(classes=labels,semantics={label:'Declared project label '+label for label in labels})
    else:
        contracts['G07'].pop('classes',None)
        contracts['G07']['value_space']={'kind':'continuous','targets':['value'],'units':{'value':'dimensionless'},'ranges':{'value':{'min':None,'max':None}}}
        contracts['G07']['semantics']={'value':'Real-valued regression target from the declared data.'}
    omitted={'G%02d'%i:'This synthetic control makes no claim under this contract; it tests only declared project task facts.' for i in range(1,14) if 'G%02d'%i not in set(contracts)|{'G01','G02','G13'}}
    definition={'schema':'researchpilot-project/v1','project_id':'project-control','source_files':['train.py','params.json','check.py','evaluate.py','data.json','split-input.json'],
        'editable_files':['train.py','params.json'],'parameters_file':'params.json',
        'entrypoints':{'preflight':['python3','check.py'],'train':['python3','train.py'],'evaluate':['python3','evaluate.py']},
        'data':{'identity':source_hash,'partition_identity':split_hash,'description':'Synthetic control data, not a real training result.','labels':labels,'quality_policy':{'partition':'fixed'}},
        'tensor':tensor,'evaluation':{'kind':kind,'primary_metric':metric,'operator':'>=' if labels else '<=','target':.99 if labels else .01,
            'selection':'last','report':'evaluation.json','predictions':'predictions.json'},
        'preflight_checks':[{'name':'finite','applicable':True,'rationale':'Check actual finite numeric outputs before training.'}],
        'training_limits':{'min_epochs':1,'default_epochs':1,'max_epochs':2},'contract_contents':contracts,'not_applicable_contracts':omitted}
    atomic_json(project/FILE,definition);frozen=freeze(project,root/'snapshot')
    spec.update(workload={'version':3,'adapter':'project/v1','project_contract':frozen,'source_ref':'Synthetic interface control, no ML.'},
        comparison_identity=comparison,baseline_source=frozen,baseline_model=ref(root/'snapshot/train.py'))
    spec['sdm_governance']={'schema':'sdm-component-governance/v1','data_source':ref(project/'data.json'),'max_submissions':2,
        'variables':{'independent':'project training implementation','dependent':metric},'author_principal_ids':['research-owner']}
    intent['brief']['content']['ml_contract_applicability']['required_contract_ids']=sorted(set(contracts)|{'G01','G02','G13'})
    from canonical import digest
    intent['brief']['content_digest']=digest(intent['brief']['content'])
    return spec,intent,definition,split

def test_empty_project_discovery_needs_facts_without_side_effects(tmp_path):
    assert inspect(tmp_path)['status']=='needs-project-facts'
    assert not list(tmp_path.iterdir())

@pytest.mark.parametrize('value',['../train.py','/train.py','D:/train.py','x\\train.py','x/../train.py'])
def test_project_paths_cannot_escape_owned_source(value):
    with pytest.raises(ValueError):relative(value)

@pytest.mark.parametrize('kind',['classification','regression'])
def test_project_facts_use_real_original_sdm_objects_and_replay(tmp_path,kind):
    spec,intent,definition,split=fixture(tmp_path,kind);value=prepare(spec,intent)
    assert all(r['passed'] for r in value['record_checks'])
    policy=publish(value,tmp_path/'governance.json')
    assert load(policy)==value
    # An unchanged cached acceptance cannot hide a changed project evaluator.
    (tmp_path/'snapshot/evaluate.py').write_text('print("changed")')
    with pytest.raises(ValueError,match='snapshot_source_changed'):load(policy)

def test_multiclass_and_regression_metrics_are_not_binary_aliases():
    actual=recount({'y_true':['a','a','b','b','c','c'],'y_pred':['a','a','b','c','a','c']},{'kind':'classification'},['a','b','c'])
    assert actual['accuracy']==pytest.approx(2/3) and actual['mean_iou']==pytest.approx(.5)
    assert actual['macro_f1']==pytest.approx((.8+2/3+.5)/3)
    assert recount({'y_true':[0,2],'y_pred':[1,0]},{'kind':'regression'},[])=={'mae':1.5,'mse':2.5}
    with pytest.raises(ValueError):recount({'y_true':[0],'y_pred':[float('nan')]},{'kind':'regression'},[])

def test_project_payload_preserves_arbitrary_files_and_never_executes_them(tmp_path):
    spec,_,project,_=fixture(tmp_path)
    manager={'namespace':'scope','owner':'owner'}
    binding=contract(spec['baseline_source'],tmp_path/'contract',spec['comparison_identity'],manager,'pool',workload=spec['workload'],next_round=1)
    profile=tmp_path/'profile';profile.mkdir();base=tmp_path/'snapshot'
    for name in project['editable_files']:(profile/name).write_bytes((base/name).read_bytes())
    request={'action':'task.submit','request_key':'new','name':'new','uid':'pool','cluster':'compute','data':{'purpose':'training'}}
    candidate,expected,manifest=task_request({'root':str(profile),'files':{n:ref(profile/n)['sha256'] for n in project['editable_files']}},binding,request,'project-control',1)
    assert verify_request_payload(candidate,manifest)['ml_executed'] is False
    files,proof=executed_request_files(candidate,manifest['code_sha256'])
    assert 'train.py' in files and 'model.py' not in files and proof['format']=='project/v1'
    assert proof['decoded_project_metadata']==json.loads(files['_researchpilot_project.json'])
    broken=copy.deepcopy(candidate);broken['data']['command']=broken['data']['command'].replace('1800s','9999s')
    with pytest.raises(ValueError):verify_request_payload(broken,manifest)

class Provider:
    def __init__(self):self.writes=0;self.lost=False;self.task=None
    def call(self,operation,data):
        if operation=='probe':return {'namespace':'scope','owner':'owner','resource_uid':'pool','configuration':{'cpu':1,'gpu':0}}
        if operation=='lookup':return {'candidates':[self.task['uid']] if self.task else []}
        if operation=='read':return self.task
        raise AssertionError(operation)
    def mutate(self,request,path):
        self.writes+=1;self.task={'uid':'actual-job-1','namespace':'scope','owner':'owner','resource_uid':'pool',
            'cluster':'compute','request_key':request['request_key'],'payload_digest':sha(request['data']),'status':'succeeded'}
        if self.lost:raise TimeoutError('lost reply after actual effect')
        return {'uid':self.task['uid']}

def manager_fixture(tmp_path,location='remote'):
    config={'schema':'sdm-project-manager/v1','provider':'test-command','namespace':'scope','owner':'owner',
        'state_root':str(tmp_path/'state'),'submission_registry':str(tmp_path/'registry'),
        'platform_requirements':{'version':2,'platform':'test-command','required_skills':[]},
        'execution_adapter':{'location':location}}
    backend=Provider();manager=Manager(config,backend)
    request={'action':'task.submit','request_key':'one','name':'test','uid':'pool','cluster':'compute','data':{'purpose':'training','command':'reviewed bytes'}}
    plan=manager.plan(request);atomic_json(tmp_path/'plan.json',plan)
    atomic_json(tmp_path/'auth.json',{'schema':'lpai-manager-authorization/v1','plan_sha256':'sha256:'+ref(tmp_path/'plan.json')['sha256'],
        'direct_user_confirmation':True,'source_ref':'Explicit synthetic transport control.'})
    return manager,backend,tmp_path/'plan.json',tmp_path/'auth.json'

def test_provider_uses_original_guard_and_unknown_outcome_is_read_only(tmp_path):
    manager,backend,plan,auth=manager_fixture(tmp_path);backend.lost=True
    with pytest.raises(ValueError,match='reconcile_only'):manager.apply(plan,auth)
    with pytest.raises(ValueError,match='reconcile_only'):manager.apply(plan,auth)
    assert backend.writes==1
    assert manager.reconcile(plan)['status']=='verified'
    assert manager.apply(plan,auth)['status']=='already-completed' and backend.writes==1

def test_local_engineering_provider_cannot_execute_training(tmp_path):
    with pytest.raises(ValueError,match='local_ml_execution_forbidden'):manager_fixture(tmp_path,'local-engineering')
