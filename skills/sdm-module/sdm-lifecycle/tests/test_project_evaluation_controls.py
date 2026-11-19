"""Reject false metric/preflight/label claims under the same SDM validators."""
import copy,hashlib,json
from pathlib import Path
import pytest
from test_project_integration import fixture
from review_storage import atomic_json,read_json
from component_payload import ref
from project_results import recount,round_evidence
from research_project import freeze,FILE
from research_acceptance import prepare
from component_candidates import registry

@pytest.mark.parametrize('fault',['missing-unit','bad-range','mixed-discrete','missing-semantics'])
def test_continuous_g07_is_closed_and_cannot_fake_categories(tmp_path,fault):
    spec,intent,definition,split=fixture(tmp_path,'regression');label=definition['contract_contents']['G07']
    if fault=='missing-unit':label['value_space']['units']={}
    elif fault=='bad-range':label['value_space']['ranges']['value']={'min':4,'max':1}
    elif fault=='mixed-discrete':label['classes']=['fake-category']
    else:label['semantics']={}
    atomic_json(tmp_path/'project'/FILE,definition);spec['workload']['project_contract']=freeze(tmp_path/'project',tmp_path/'bad-snapshot')
    with pytest.raises(ValueError,match='original_sdm_record_invalid:G07'):prepare(spec,intent)

@pytest.mark.parametrize('fault',['empty-classes','missing-semantics'])
def test_original_discrete_label_rejections_remain(tmp_path,fault):
    spec,intent,definition,split=fixture(tmp_path)
    if fault=='empty-classes':definition['contract_contents']['G07']['classes']=[]
    else:definition['contract_contents']['G07']['semantics'].pop('a')
    atomic_json(tmp_path/'project'/FILE,definition);spec['workload']['project_contract']=freeze(tmp_path/'project',tmp_path/'bad-snapshot')
    with pytest.raises(ValueError,match='original_sdm_record_invalid:G07'):prepare(spec,intent)

def saved_result(root,spec,definition,split,index,predictions):
    root.mkdir();kind=definition['evaluation']['kind'];truth=['a','a','b','b','c','c'] if kind=='classification' else [0.]*6
    scores=recount({'y_true':truth,'y_pred':predictions},definition['evaluation'],definition['data']['labels'])
    checkpoint=b'explicit synthetic checkpoint control, not trained weights'
    preflight={'passed':True,'training_only':True,'checks':{'finite':{'status':'passed','evidence':'Synthetic finite-value transport fixture, not actual model evaluation.'}}}
    result={'schema':'sdm-project-result/v1',**spec['comparison_identity'],'code_sha256':str(index)*64,
        'config':{'round':index,'epochs':1,'expected_minutes':1,'model':'synthetic-result-control'},
        'validation':scores,'selected_epoch':1,'history':[{'epoch':1,'validation':scores}],
        'evaluation':definition['evaluation'],'engineering_preflight':preflight,'test_used':False,
        'checkpoint_sha256':hashlib.sha256(checkpoint).hexdigest()}
    for name,value in [('result.json',result),('predictions.json',{'row_ids':split['validation_ids'],'y_true':truth,'y_pred':predictions}),('preflight.json',preflight)]:atomic_json(root/name,value)
    (root/'split.json').write_bytes((Path(spec['workload']['project_contract']['path']).parent/'split-input.json').read_bytes())
    (root/'checkpoint.bin').write_bytes(checkpoint)
    atomic_json(root/'manager-plan.json',{'request':{'scope':'synthetic immutable reference; no platform request'}})
    identity=ref(root/'manager-plan.json');metric=definition['evaluation']['primary_metric']
    ready={'schema':'sdm-project-result-ready/v1','uid':'task-'+str(index),'valid_training_round':True,
        'artifact_root':str(root),'project_contract':spec['workload']['project_contract'],'primary_metric':scores[metric],
        'metric_name':'validation.'+metric,'metric_operator':definition['evaluation']['operator'],
        'artifact_sha256':{n:ref(root/n)['sha256'] for n in ('result.json','predictions.json','split.json','checkpoint.bin','preflight.json')},
        'submission_identity':{'manager_plan':identity},'original_plan_sha256':identity['sha256']}
    atomic_json(root/'ready.json',ready)
    return {'result':ref(root/'ready.json')}

def test_lower_error_selects_the_right_incumbent_counts_failures_and_displays_above_one(tmp_path):
    spec,_,definition,split=fixture(tmp_path,'regression')
    a=saved_result(tmp_path/'r1',spec,definition,split,1,[3.]*6)
    b=saved_result(tmp_path/'r2',spec,definition,split,2,[2.]*6)
    policy={'version':5,'target':{'metric':'validation.mae','operator':'<=','value':.1},'budget':{'known_task_uids':[],'max_new_submissions':3}}
    checked=registry([a,b],policy,['task-1','task-2','failed-task-3'])
    assert checked['incumbent']['uid']=='task-2' and checked['goal']['gap']==pytest.approx(1.9)
    assert checked['budget']['remaining_submissions']==0 and checked['required_exit']=='stop-research'
    from research_dashboard_data import DashboardData
    rows,issues=DashboardData(tmp_path).rounds({'rounds':[a,b]},policy['target'])
    assert not issues and rows[0]['metric']==3 and rows[1]['new_best'] and rows[1]['best_so_far']==2
    from component_diagnostics import prepare as diagnose
    refs=diagnose([a,b],tmp_path/'diagnosis')
    index=read_json(refs[0]['path'])
    assert index['incumbent_uid']=='task-2'
    assert all(r['metric_name']=='validation.mae' and 'macro_f1' not in r for r in index['observations'])

def test_outer_digest_refresh_cannot_hide_false_recount_or_missing_preflight(tmp_path):
    spec,_,definition,split=fixture(tmp_path)
    a=saved_result(tmp_path/'r1',spec,definition,split,1,['a','a','b','c','a','c'])
    assert round_evidence(a['result'])['primary_metric']==pytest.approx(.5)
    result=read_json(tmp_path/'r1/result.json');result['validation']['mean_iou']=.99
    atomic_json(tmp_path/'r1/result.json',result)
    ready=read_json(tmp_path/'r1/ready.json');ready['artifact_sha256']['result.json']=ref(tmp_path/'r1/result.json')['sha256'];ready['primary_metric']=.99
    atomic_json(tmp_path/'r1/ready.json',ready)
    with pytest.raises(ValueError,match='project_metric_recount_mismatch'):round_evidence(ref(tmp_path/'r1/ready.json'))

def test_all_declared_preflight_checks_are_shared_by_remote_and_replay():
    from project_preflight import verify_report
    declared=[{'name':'finite','applicable':True,'rationale':'Concrete functional invariant.'}]
    with pytest.raises(ValueError):verify_report({'passed':True,'training_only':True,'checks':{}},declared)
    with pytest.raises(ValueError):verify_report({'passed':True,'training_only':True,'checks':{'finite':{'status':'not-applicable','evidence':'Claimed omission without a genuine capability boundary.'}}},declared)

def test_adapters_share_the_original_controller_effect_transaction_and_polling():
    from research_pipeline import ResearchPipeline
    from component_pipeline import ComponentPipeline
    from research_execution import ProjectManager
    from lpai_manager import ManagerCore
    from research_execution_source import ProjectSource
    from component_event_source import ComponentSource
    assert ResearchPipeline is ComponentPipeline
    assert ProjectManager.apply is ManagerCore.apply
    assert ProjectSource.cycle is ComponentSource.cycle


def test_command_source_checks_original_payload_and_replays_durable_completion(tmp_path,monkeypatch):
    """Exercise the original polling/outbox with synthetic provider artifacts."""
    import base64,gzip
    from types import SimpleNamespace
    from test_project_integration import manager_fixture
    from research_execution_source import ProjectSource
    import research_execution
    from external_operations import sha
    spec,_,definition,split=fixture(tmp_path/'facts')
    row=saved_result(tmp_path/'saved',spec,definition,split,1,['a','a','b','c','a','c'])
    result=read_json(tmp_path/'saved/result.json')
    manager,backend,plan,auth=manager_fixture(tmp_path/'execution')
    manager.apply(plan,auth)
    original=read_json(plan);original['route']='project-command'
    # plan() already sets that route; preserve the immutable original bytes.
    assert read_json(plan)['route']==original['route']
    atomic_json(tmp_path/'manager.json',manager.config)
    expected={**spec['comparison_identity'],'code_sha256':result['code_sha256'],'config':result['config'],
        'project_contract':spec['workload']['project_contract'],'evaluation':definition['evaluation'],'result_contract':'sdm-project-result/v1'}
    def line(value):return 'SDM_JSON '+json.dumps(value)
    lines=[line({'kind':'preflight',**{k:expected[k] for k in ('source_sha256','split_sha256','code_sha256','config')}})]
    for name in ('result.json','predictions.json','split.json','checkpoint.bin','preflight.json'):
        raw=(tmp_path/'saved'/name).read_bytes();encoded=base64.b64encode(gzip.compress(raw)).decode()
        chunks=[encoded[i:i+4096] for i in range(0,len(encoded),4096)]
        lines.append(line({'kind':'artifact','name':name,'encoding':'base64+gzip','bytes':len(raw),'chunks':len(chunks),'sha256':hashlib.sha256(raw).hexdigest()}))
        lines.extend(line({'kind':'artifact-chunk','name':name,'index':i,'value':part}) for i,part in enumerate(chunks))
    lines.append(line({'kind':'completed','result_sha256':ref(tmp_path/'saved/result.json')['sha256']}))
    original_call=backend.call;collections=[]
    def call(operation,data):
        if operation=='collect':collections.append(data['uid']);return {'uid':data['uid'],'lines':lines}
        return original_call(operation,data)
    backend.call=call
    monkeypatch.setattr(research_execution,'Manager',lambda configuration:manager)
    root=tmp_path/'inbox';root.mkdir();delivered=[]
    inbox=SimpleNamespace(root=root,cfg={'schema':'sdm-event-service/v3','root':str(root)},task=lambda binding:{'kind':'component-source'},accept=lambda event:delivered.append(event) or {'status':'accepted'})
    source=ProjectSource(inbox,{'version':4,'transport':'project-command','binding':'test',
        'manager_config':ref(tmp_path/'manager.json'),'plan':ref(plan),'expected':expected,
        'phase_intervals_seconds':{'queued':15,'running':15,'finishing':15,'terminal':15,'unknown':15}})
    assert source.reconcile() is not None  # archive the original, valid submission readback
    good=backend.task['payload_digest'];backend.task['payload_digest']=sha({'changed':'unreviewed terminal payload'})
    with pytest.raises(ValueError,match='submitted_project_task_payload_changed'):source.cycle()
    assert not delivered and not (source.root/'completion-event.json').exists()
    backend.task['payload_digest']=good
    assert source.cycle()[0]=='delivered'
    envelope=delivered[0];assert round_evidence(envelope['state']['result_ready'])['primary_metric']==pytest.approx(.5)
    assert source.cycle()[0]=='delivered' and delivered==[envelope,envelope]
    assert len(collections)==2 and backend.writes==1


def test_execution_view_exposes_resources_without_displacing_them_by_payload(tmp_path):
    from component_submission import execution_binding_view
    command='x'*120000
    value={'request':{'uid':'pool','data':{'lpai_task':{'cmd':command,'roles':[{'cmd':command,'replica':1,'resource':{'Cpu':1,'Mem':4,'Gpu':0}}]},'lpai_input_pvc':[{'name':'read-only-input','read_only':True}]}},
        'baseline':{'uid':'pool'},'namespace':'scope','owner':'owner'}
    atomic_json(tmp_path/'plan.json',value);original=ref(tmp_path/'plan.json')
    view=execution_binding_view(original,tmp_path/'plan.json',tmp_path)
    assert view.stat().st_size<5000 and ref(tmp_path/'plan.json')==original
    data=read_json(view)
    assert data['original_plan']==original and data['execution_authority'] is False
    assert data['current_request']['data']['lpai_task']['roles'][0]['resource']=={'Cpu':1,'Mem':4,'Gpu':0}
    assert data['current_request']['data']['lpai_input_pvc']==value['request']['data']['lpai_input_pvc']
    assert data['current_request']['data']['lpai_task']['cmd']['sha256']==hashlib.sha256(command.encode()).hexdigest()


def test_valid_project_result_enters_original_projection_and_replan(tmp_path):
    """A valid project result must reach the original analysis coordinator."""
    from types import SimpleNamespace
    from component_payload import contract,task_request
    from component_pipeline import ResearchPipeline
    from research_acceptance import publish,project
    from task_pipeline import digest
    spec,intent,definition,split=fixture(tmp_path/'facts')
    governance=prepare(spec,intent);policy=publish(governance,tmp_path/'governance.json')
    from research_intent import publish as publish_intent
    intent_policy=publish_intent(intent,tmp_path/'intent.json')
    manager={'namespace':'scope','owner':'owner','platform_requirements':{'version':2,'platform':'control-provider','required_skills':[]}}
    bound=contract(spec['baseline_source'],tmp_path/'capabilities',spec['comparison_identity'],manager,'pool',workload=spec['workload'],next_round=1)
    base=Path(spec['baseline_source']['path']).parent;profile=tmp_path/'profile';profile.mkdir()
    for name in definition['editable_files']:(profile/name).write_bytes((base/name).read_bytes())
    request,expected,manifest=task_request({'root':str(profile),'files':{n:ref(profile/n)['sha256'] for n in definition['editable_files']}},bound,
        {'action':'task.submit','request_key':'one','name':'synthetic','uid':'pool','cluster':'control','data':{'purpose':'training'}},'projection-control',1)
    row=saved_result(tmp_path/'saved',spec,definition,split,1,['a','a','b','c','a','c'])
    result=read_json(tmp_path/'saved/result.json');result['code_sha256']=manifest['code_sha256'];atomic_json(tmp_path/'saved/result.json',result)
    atomic_json(tmp_path/'saved/manager-plan.json',{'request':request})
    plan=ref(tmp_path/'saved/manager-plan.json');ready=read_json(tmp_path/'saved/ready.json')
    ready.update(submission_identity={'manager_plan':plan},original_plan_sha256=plan['sha256'])
    ready['artifact_sha256']['result.json']=ref(tmp_path/'saved/result.json')['sha256'];atomic_json(tmp_path/'saved/ready.json',ready)
    row={'result':ref(tmp_path/'saved/ready.json')}
    atomic_json(tmp_path/'delegation.json',{'training_limits':definition['training_limits'],'resources':'Synthetic remote-only scope; no platform operation.'})
    atomic_json(tmp_path/'request.json',request)
    atomic_json(tmp_path/'manager.json',manager)
    pipeline={**spec,'version':4,'objective':'Synthetic result handoff control; no real execution.','sdm_governance':policy,'sdm_intent':intent_policy,
        'initial_source':{'plan':plan,'expected':{**expected,'code_sha256':manifest['code_sha256']},'manager_config':ref(tmp_path/'manager.json')},
        'initial_request':ref(tmp_path/'request.json'),'delegation':ref(tmp_path/'delegation.json'),'proposal_review':{},
        'minimum_rounds':1,'minimum_revisions':0,'decision_policy':{'version':5,'target':{'metric':'validation.mean_iou','operator':'>=','value':.99},'budget':{'known_task_uids':[],'max_new_submissions':2}}}
    atomic_json(tmp_path/'manager.json',manager)
    cfg={'schema':'sdm-event-service/v3','project_id':spec['project_id'],'component_pipeline':pipeline,'continuation_policy':{'version':1}}
    current={'id':'initial','stage':'result-ready','source':pipeline['initial_source'],'result':row['result'],'result_handoff':{'model_requested':False}}
    state={'config_digest':digest(cfg),'rounds':[],'attempts':[current]}
    projection=project(cfg,state)
    assert projection['steps'][7]['status']=='operational-verified'
    owner=object.__new__(ResearchPipeline);owner.spec=pipeline;owner.manager_config=manager;owner.inbox=SimpleNamespace(cfg=cfg)
    jobs=[]
    owner.coordinator=lambda mode,path,root:jobs.append((mode,path,root)) or 'synthetic-coordinator-no-model-call'
    output=tmp_path/'replan';output.mkdir()
    owner.replan(state,current,output)
    assert current['stage']=='reviewing-plan' and current['result_handoff']['model_requested'] is True
    assert len(state['rounds'])==1 and jobs[0][0]=='research-replan'
    actual=read_json(jobs[0][1])['research_replan']
    assert actual['parent_route']=='project' and actual['rounds']==state['rounds']
    history=read_json(output/'historical-recipes.json')['records'][0]
    assert history['editable_sources']['train.py']==(base/'train.py').read_bytes().decode('utf-8')
    assert 'model_source' not in history


@pytest.mark.parametrize('role',['a','b'])
def test_review_recovery_reconstructs_exact_role_runner_and_rejects_changes(tmp_path,role):
    from working_sessions import select,attach
    from model_runner import binding
    from research_continuation import original_review_runner
    runner={'version':4,'backend':'sdk','sdk_recovery':{'version':2},'cwd':str(tmp_path/'base'),'codex_home':str(tmp_path/'home')}
    policy={'model_runner':runner,'working_sessions':{'version':1,'project_id':'control','model_provider':None,
        'main':{'root':str(tmp_path/'main-context'),'cwd':str(tmp_path/'main-work'),'model':'main-model'},
        'review':{'root':str(tmp_path/'review-context'),'cwd':str(tmp_path/'review-work'),'model':'review-model'}}}
    selected,session=select(policy,runner,role)
    request={'purpose':'proposal' if role=='a' else 'method-review','model':session['model'],
        'prompt':'Bound synthetic recovery control.','contract_id':'control/v1','evidence':{},'output_schema':{'type':'object'}}
    attach(request,session)
    saved={'config':selected,'request':request,'binding':binding(selected,request)}
    assert original_review_runner(policy,saved)==selected
    altered=copy.deepcopy(saved);altered['config']['cwd']=str(tmp_path/'other')
    altered['binding']=binding(altered['config'],request)
    with pytest.raises(ValueError,match='original_config_changed'):original_review_runner(policy,altered)


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows extended receipt paths')
def test_persistent_history_acknowledges_deep_receipt_without_identity_rewrite(tmp_path):
    from research_context import Context
    from review_storage import native_path
    cfg={'project_id':'control','cwd':str(tmp_path/'workspace'),'model':'model','sandbox':'workspace-write','codex_home':str(tmp_path/'home'),
        'research_context':{'version':1,'root':str(tmp_path/'context'),'history_required':True,'session_role':'main'}}
    ctx=Context(cfg);atomic_json(ctx.path,{'schema':'sdm-research-context/v1','scope':ctx.scope,'phase':'ready','thread_id':'original-thread','pending_job':'original-job','pending_turn':'original-turn','turns':[]})
    path=tmp_path/('long-recovery-'*6)/('registered-review-'*5)/('model-call-'*6)/'receipt.json'
    assert len(str(path))>260
    atomic_json(path,{'job_id':'original-job','status':'completed','detail':{'thread_id':'original-thread','turn_id':'original-turn','terminal':'completed'}})
    raw=native_path(path).read_bytes();ctx.acknowledge('original-job',path);loaded=Context(cfg).load()
    assert not loaded['pending_job'] and loaded['thread_id']=='original-thread'
    assert native_path(loaded['turns'][0]['receipt'])==native_path(path.resolve()) and Path(loaded['turns'][0]['receipt']).read_bytes()==raw


@pytest.mark.skipif(__import__('os').name!='nt',reason='Windows retained process handle')
def test_exited_owner_with_retained_process_handle_is_not_running(tmp_path):
    import subprocess,sys
    from research_runtime import owner_alive
    process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B','-c','import sys;sys.stdin.read(1)'],
        stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:
        atomic_json(tmp_path/'ready.json',{'pid':process.pid})
        assert owner_alive({'root':str(tmp_path)})
        process.stdin.write(b'x');process.stdin.flush();process.wait(timeout=10)
        assert process.returncode==0 and not owner_alive({'root':str(tmp_path)})
    finally:
        if process.poll() is None:process.kill();process.wait(timeout=10)
        process.stdin.close()
