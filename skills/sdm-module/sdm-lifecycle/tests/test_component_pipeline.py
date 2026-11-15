"""Connected three-result/replan/implementation controls, no actual ML/models/platform.

Original evidence, implementation freeze, Inbox and Context are exercised.
Review and native/platform effects are explicit substitutes, never science proof.
"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/tests'))
from test_component_research_continuation import required,result,direction
from test_research_event_service import setup
from component_payload import ref,harness,task_request
from component_pipeline import ComponentPipeline
from research_event_service import Inbox,sdk_execute,digest
from research_context import Context
from review_storage import atomic_json,read_json
from task_pipeline import digest as record_digest


# AST-only producer fixture: includes current protected validity/emit ABI sites; never runs ML.
BASELINE=b'''import json
from pathlib import Path
CONFIG={'epochs':20}
def sha(raw): return 'unused-control'
def emit(event,payload): return None
def main():
    code_sha=sha(Path(__file__).read_bytes())
    protected_evaluation='must remain exactly unchanged'
    c=CONFIG['channels']
    model=nn.Sequential(nn.Linear(c,2))
    optimizer=torch.optim.Adam(model.parameters(),lr=CONFIG['lr'])
    emit('preflight', {})
    for epoch in range(1):
        for batch in []:
            loss=criterion(model(x[batch]),y[batch]); loss.backward(); optimizer.step()
        val,_=evaluate(vx,vy)
    return protected_evaluation
'''


def fixture(tmp,version=1,code_feedback=False):
    cfg,_=setup(tmp);workspace=tmp/'workspace';workspace.mkdir()
    cfg.update(schema='sdm-event-service/v3',cwd=str(workspace),tasks={},
        research_context={'version':1,'root':str(tmp/'context')},task_templates={
            'original':{'kind':'coordinator','instructions':'Only original SDM.', 'evidence_kind':'original SDM control',
                        'modes':['research-replan','prepare-implementation','freeze-implementation','proposal-review']},
            'implement':{'kind':'implementation','instructions':'Implement the approved job only.','evidence_kind':'implementation',
                         'tool_name':'sdm_plan_approved'},
            'source':{'kind':'component-source','instructions':'Observation only.','evidence_kind':'remote result'}})
    policy=required(tmp)
    manager={'component_transport':'official-cli','namespace':'ns','owner':'owner@example.com','official_cli':'lpai-agent',
             'platform_requirements':policy}
    atomic_json(tmp/'manager.json',manager)
    request={'action':'task.submit','uid':'component','name':'original','request_key':'initial','cluster':'cluster',
             'data':{'lpai_task':{'cmd':'old','roles':[{'cmd':'old','resource':{'Cpu':1}}]}}}
    atomic_json(tmp/'request.json',request)
    atomic_json(tmp/'plan.json',{'route':'official-cli','request':request})
    (tmp/'baseline.py').write_bytes(BASELINE)
    ready=result(tmp,1,[0,1,1,0])
    from component_research import round_evidence
    comparison=round_evidence(ready['result'])['fair_comparison']
    atomic_json(tmp/'delegation.json',{'schema':'sdm-research-delegation/v1','allow_successor_submission':True,
        'project_id':cfg['project_id'],'source_ref':'Synthetic delegated scope, not an actual user approval.',
        'comparison_identity':comparison,'namespace':'ns','owner':'owner@example.com','component_uid':'component'})
    source={'version':1,'binding':'initial-source','manager_config':ref(tmp/'manager.json'),'plan':ref(tmp/'plan.json'),
        'expected':{'config':{'epochs':20}},'phase_intervals_seconds':{
            'queued':120,'running':30,'finishing':15,'terminal':300,'unknown':60}}
    cfg['component_pipeline']={'version':version,'objective':'three actual rounds, two accepted revisions and final improvement',
        'initial_source':source,'initial_request':ref(tmp/'request.json'),'baseline_source':ref(tmp/'baseline.py'),
        'delegation':ref(tmp/'delegation.json'),'comparison_identity':comparison,'coordinator_template':'original',
        'implementation_template':'implement','source_template':'source','proposal_review':{
            'version':4,'backend':'model','context_required':True,'models':{'a':'control-proposer','b':'control-critic'}}}
    if code_feedback:cfg['component_pipeline']['code_review_feedback']={'version':1}
    inbox=Inbox(cfg)
    return inbox,ready


def approve(cfg,root,evidence_query=None):
    root=Path(root);root.mkdir(exist_ok=True,parents=True)
    assessment=cfg.get('research_replan',{}).get('rounds',[])
    revision=direction('goal-met' if len(assessment)>=3 else 'revise-plan')
    path=root/'accepted.json'
    proposal={'research_revision':revision,'comparison':'fixed real evaluation',
        'outcome_actions':'observe actual next result','expected_observations':'validate the specific intervention',
        'cost_and_queue':'control estimate with separate queue wait'}
    atomic_json(path,{'approved':True,'proposal':proposal,'response':{'verdict':'approve'}})
    return {'status':'proposal-approved','receipt':str(path)}


def coordinator(selected,folder,timeout):
    cfg=read_json(selected['project']);root=Path(selected['output']);mode=selected['mode']
    if mode=='research-replan':
        import research_replan
        return research_replan.advance(cfg,root)
    if mode=='prepare-implementation':
        import research_implementation
        return research_implementation.prepare(cfg,root)
    if mode=='freeze-implementation':
        import research_implementation
        return research_implementation.freeze(cfg,root)
    return approve(cfg,root)


def finish_job(inbox,job,model_calls,code=None):
    with inbox.db() as db:row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
    if row['status']!='received':return
    task=inbox.task(row['binding']);folder=inbox.root/job;folder.mkdir(exist_ok=True)
    if task['kind']=='coordinator':value=sdk_execute(inbox,row,folder)
    else:
        job_value=read_json(task['evidence'][0]['path']);spec=read_json(job_value['execution_contract']['path'])
        workspace=Path(job_value['workspace']);workspace.mkdir(exist_ok=True,parents=True)
        (workspace/'model.py').write_text(code if code is not None else
            'def build_model(config): raise AssertionError("control only")\n'
            'def build_optimizer(parameters,config): raise AssertionError("never execute locally")\n')
        # Native file generation is substituted. No code is imported/executed.
        plan=read_json(job_value['approved_plan']['path']);index=plan['goal_assessment']['valid_training_rounds']+1
        atomic_json(workspace/'parameters.json',{'comparison_identity':spec['comparison_identity'],
            'execution_scope':spec['execution_scope'],'platform_requirements':job_value['platform_requirements'],
            'training':{'round':index,'epochs':20,'lr':.001,'batch_size':64,'seed':42,'threads':1,
                        'model':'synthetic-implementation-'+str(index),'channels':16,'expected_minutes':10}})
        atomic_json(workspace/'implementation.json',{'schema':'sdm-research-implementation-output/v1','status':'implemented',
            'job_id':job_value['job_id'],'parent_plan_digest':record_digest(plan),'contract_sha256':job_value['execution_contract']['sha256'],
            'files':[{'path':name,'sha256':ref(workspace/name)['sha256']} for name in spec['allowed_files']]})
        context=Context(inbox.cfg)
        with context.lock():
            thread=context.open(SimpleNamespace(thread_start=lambda **kw:SimpleNamespace(id='same-thread'),
                thread_resume=lambda identity,**kw:SimpleNamespace(id=identity)),{})
            context.intent(job);context.started(job,'turn-'+str(len(model_calls)))
        model_calls.append(thread.id)
        value={'status':'completed','detail':{'thread_id':thread.id,'turn_id':'turn-'+str(len(model_calls)-1),'terminal':'completed'}}
    atomic_json(folder/'result.json',{'job_id':job,**value})
    inbox.acknowledge_context(job,folder/'result.json');inbox.transition(job,value['status'],**value['detail'])


class FakeManager:
    phases={};submitted=[]
    def __init__(self,cfg):pass
    def plan(self,request):return {'request':request,'route':'official-cli','created_at':time.time()}
    def guard(self,plan):
        state=self.phases.setdefault(digest(plan),{'phase':'new'})
        return SimpleNamespace(state=state,load=lambda:None,lock=contextlib.nullcontext)
    def apply(self,plan,authorization):
        saved=read_json(plan);state=self.guard(saved).state
        assert state['phase']=='new'
        state['phase']='pending-approval';self.submitted.append(saved['request']['request_key'])
        return {'status':'pending_approval','approval_id':'synthetic-only'}
    def reconcile(self,plan):return {'status':'pending-approval-unresolved'}


def test_three_results_drive_two_distinct_implementations_submissions_and_same_context(tmp_path):
    inbox,first=fixture(tmp_path);rows=[first,result(tmp_path,2,[0,0,1,0]),result(tmp_path,3,[0,0,1,1])]
    native=[];observed=[];FakeManager.phases={};FakeManager.submitted=[]
    def cycle(source):
        index=len(observed);ready=rows[index]['result'];observed.append(source.binding)
        source.deliver({'state':{'result_ready':ready}})
        return 'delivered',None
    with patch('component_event_source.ComponentSource.cycle',new=cycle),patch('proposal_review.gate',side_effect=approve),\
            patch('event_coordinator.run',side_effect=coordinator),patch('lpai_manager.Manager',FakeManager):
        pipeline=ComponentPipeline(inbox);state=pipeline.load()
        for unused in range(40):
            pipeline.step(state);pipeline.save(state)
            current=state['attempts'][-1]
            if current.get('job'):finish_job(inbox,current['job'],native)
            # A host restart at every boundary must reuse the same jobs/state.
            pipeline=ComponentPipeline(inbox);state=pipeline.load()
            if state['status']=='research-goal-met':break
    assert state['status']=='research-goal-met'
    assert state['goal_assessment']['goal_met'] and len(state['rounds'])==3
    assert len(FakeManager.submitted)==len(set(FakeManager.submitted))==2
    assert native==['same-thread','same-thread'] and len(observed)==3
    assert Context(inbox.cfg).load()['pending_job'] is None
    kinds=[inbox.task(row['binding'])['kind'] for row in inbox.snapshot()['jobs']]
    assert kinds.count('coordinator')==9 and kinds.count('implementation')==2


@pytest.mark.parametrize('repeat_failure',[False,True])
def test_completed_bad_implementation_gets_same_context_correction_or_conditional_stall(tmp_path,repeat_failure):
    inbox,first=fixture(tmp_path,version=2)
    rows=[first,result(tmp_path,2,[0,0,1,0]),result(tmp_path,3,[0,0,1,1])]
    native=[];observed=[];FakeManager.phases={};FakeManager.submitted=[]
    def cycle(source):
        ready=rows[len(observed)]['result'];observed.append(source.binding)
        source.deliver({'state':{'result_ready':ready}});return 'delivered',None
    with patch('component_event_source.ComponentSource.cycle',new=cycle),patch('proposal_review.gate',side_effect=approve),\
            patch('event_coordinator.run',side_effect=coordinator),patch('lpai_manager.Manager',FakeManager):
        pipeline=ComponentPipeline(inbox);state=pipeline.load()
        for unused in range(70):
            pipeline.step(state);pipeline.save(state)
            current=state['attempts'][-1]
            if current.get('job'):
                finish_job(inbox,current['job'],native,
                    code='def broken(:\n' if not native or repeat_failure else None)
            pipeline=ComponentPipeline(inbox);state=pipeline.load()
            if state['status']=='research-goal-met' or state['attempts'][-1]['stage']=='needs-attention':break
    first_attempt=state['attempts'][0];failures=first_attempt['implementation_failures']
    assert all(read_json(x['feedback']['path'])['reason']=='invalid_implementation_python' for x in failures)
    implementations=[row for row in inbox.snapshot()['jobs'] if inbox.task(row['binding'])['kind']=='implementation']
    assert len(implementations)==len(native) and all(row['status']=='completed' for row in implementations)
    assert set(native)=={'same-thread'} and Context(inbox.cfg).load()['pending_job'] is None
    if repeat_failure:
        assert current['stage']=='needs-attention' and current['reason']=='implementation-validation-stalled'
        assert len(native)==2 and len(failures)==2 and failures[0]['signature']==failures[1]['signature']
        assert not FakeManager.submitted
    else:
        assert state['status']=='research-goal-met' and len(state['rounds'])==3
        assert len(failures)==1 and len(native)==3 and len(FakeManager.submitted)==2
    old_feedback=read_json(failures[0]['feedback']['path'])
    assert __import__('review_storage').native_path(old_feedback['files']['model.py']['path']).read_text()=='def broken(:\n'


@pytest.mark.parametrize('unchanged',[False,True])
def test_semantic_critic_revision_reaches_implementation_then_new_gate_or_stalls(tmp_path,unchanged):
    inbox,first=fixture(tmp_path,version=2,code_feedback=True)
    rows=[first,result(tmp_path,2,[0,0,1,0]),result(tmp_path,3,[0,0,1,1])]
    observed=[];native=[];code_subjects=[];FakeManager.phases={};FakeManager.submitted=[]
    def cycle(source):
        source.deliver({'state':{'result_ready':rows[len(observed)]['result']}})
        observed.append(source.binding);return 'delivered',None
    def gate(cfg,root,evidence_query=None):
        if cfg.get('component_code_review'):
            code_subjects.append(cfg['component_code_review']['implemented_profile'])
            if len(code_subjects)==1:
                path=Path(root)/'accepted.json';path.parent.mkdir(parents=True,exist_ok=True)
                atomic_json(path,{'approved':False,'response':{'verdict':'revise','rationale':'Synthetic implementation mismatch'}})
                return {'status':'proposal-revision-required','revision_target':'implementation','receipt':str(path)}
        return approve(cfg,root,evidence_query)
    def replay(cfg,root,receipt):
        # Only the connected control replaces proof verification. Separate
        # tests above exercise the original ModelRunner receipts and replay.
        value=read_json(receipt['path']);assert value['approved'] is False and value['response']['verdict']=='revise'
        return value
    def code_coordinator(selected,folder,timeout):
        if selected['mode']=='proposal-review':return gate(read_json(selected['project']),Path(selected['output']))
        return coordinator(selected,folder,timeout)
    with patch('component_event_source.ComponentSource.cycle',new=cycle),patch('proposal_review.gate',side_effect=gate),\
            patch('event_coordinator.run',side_effect=code_coordinator),patch('implementation_code_review.replay',side_effect=replay),\
            patch('lpai_manager.Manager',FakeManager):
        pipeline=ComponentPipeline(inbox);state=pipeline.load()
        for unused in range(80):
            pipeline.step(state);pipeline.save(state)
            current=state['attempts'][-1]
            if current.get('job'):
                corrected='def build_model(config): raise AssertionError("corrected control only")\n' \
                          'def build_optimizer(parameters,config): raise AssertionError("never run")\n'
                finish_job(inbox,current['job'],native,code=corrected if native and not unchanged else None)
            pipeline=ComponentPipeline(inbox);state=pipeline.load()
            if state['status']=='research-goal-met' or state['attempts'][-1]['stage']=='needs-attention':break
    assert set(native)=={'same-thread'} and Context(inbox.cfg).load()['pending_job'] is None
    if unchanged:
        assert state['attempts'][-1]['reason']=='implementation-code-review-stalled'
        assert len(native)==2 and len(code_subjects)==1 and FakeManager.submitted==[]
    else:
        assert state['status']=='research-goal-met' and len(native)==3 and len(FakeManager.submitted)==2
        assert len(code_subjects)==3 and len(set(r['sha256'] for r in code_subjects))==3
    failure=state['attempts'][0]['implementation_failures'][0]
    assert read_json(failure['feedback']['path'])['schema']=='sdm-implementation-code-feedback/v1'


def test_queue_due_time_and_pause_never_schedule_models_or_writes(tmp_path):
    inbox,_=fixture(tmp_path);pipeline=ComponentPipeline(inbox);state=pipeline.load()
    state['attempts'][-1]['poll_at']=time.time()+120;pipeline.save(state)
    with patch('component_event_source.ComponentSource.cycle') as source:
        assert pipeline.step(state)>119
        atomic_json(inbox.root/'PAUSED',{'source':'control'})
        state['attempts'][-1]['poll_at']=0
        assert pipeline.step(state)==15
        source.assert_not_called()
    assert inbox.snapshot()['jobs']==[]


def test_successor_unknown_submission_watches_same_intent_without_inheriting_old_repair(tmp_path):
    inbox,_=fixture(tmp_path);pipeline=ComponentPipeline(inbox);state=pipeline.load()
    current=state['attempts'][-1]
    plan=tmp_path/'successor-plan.json';atomic_json(plan,{'request':{'name':'successor'}})
    current.update(manager_plan=ref(plan),manager_authorization=ref(tmp_path/'delegation.json'),
                   expected={'config':{'epochs':20}},science_plan={'digest':'accepted-control-plan'})
    # An initial-only recovery exception must not leak to a fresh plan.
    pipeline.spec['initial_source'].update(version=2,readonly_runtime_repair={'path':'old-plan-only','sha256':'old'})
    FakeManager.phases={digest(read_json(plan)):{'phase':'intent'}}
    with patch('lpai_manager.Manager',FakeManager),\
            patch.object(FakeManager,'apply',side_effect=AssertionError('must not resubmit')),\
            patch.object(FakeManager,'reconcile',return_value={'status':'submission-outcome-unresolved'}):
        pipeline.submit(state,current,tmp_path)
    following=state['attempts'][-1]
    assert len(state['attempts'])==2 and following['stage']=='watching'
    assert following['source']['plan']==ref(plan) and following['source']['version']==1
    assert 'readonly_runtime_repair' not in following['source']


def test_unknown_native_job_is_not_reenqueued_after_restart(tmp_path):
    inbox,_=fixture(tmp_path);pipeline=ComponentPipeline(inbox)
    source=tmp_path/'job.json';atomic_json(source,{})
    job=pipeline.enqueue('implement','implement',{'job':ref(source)})
    inbox.transition(job,'unknown')
    with pytest.raises(ValueError,match='unknown_reconcile_only'):ComponentPipeline(inbox).completed(job)
    assert pipeline.enqueue('implement','implement',{'job':ref(source)})==job
    assert len(inbox.snapshot()['jobs'])==1


def test_registration_cannot_be_replaced_or_scheduled_from_observation(tmp_path):
    inbox,_=fixture(tmp_path);pipeline=ComponentPipeline(inbox)
    value={'template':'source','origin':ref(pipeline.origin),'data':{'evidence':inbox.cfg['component_pipeline']['initial_request']}}
    inbox.register('source-only',value)
    changed=copy.deepcopy(value);changed['data']['evidence']=inbox.cfg['component_pipeline']['baseline_source']
    with pytest.raises(ValueError,match='registration_changed'):inbox.register('source-only',changed)
    with pytest.raises(ValueError,match='cannot_invoke_model'):
        sdk_execute(inbox,{'binding':'source-only'},tmp_path)


def test_harness_preserves_evaluator_and_payload_is_deterministic_without_execution(tmp_path):
    rendered=harness(BASELINE)
    assert b"protected_evaluation='must remain exactly unchanged'" in rendered
    assert b'build_model(CONFIG)' in rendered
    with pytest.raises(ValueError,match='baseline_harness_shape'):
        harness(BASELINE.replace(b'torch.optim.Adam',b'torch.optim.SGD'))


def test_invalid_delegation_and_identical_review_models_rejected(tmp_path):
    inbox,_=fixture(tmp_path);cfg=copy.deepcopy(inbox.cfg)
    cfg['component_pipeline']['proposal_review']['models']['b']='CONTROL-PROPOSER'
    with pytest.raises(ValueError,match='distinct_proposer'):Inbox(cfg)
    delegation=Path(cfg['component_pipeline']['delegation']['path'])
    saved=read_json(delegation);saved['namespace']='other';atomic_json(delegation,saved)
    cfg=copy.deepcopy(inbox.cfg);cfg['component_pipeline']['delegation']=ref(delegation)
    with pytest.raises(ValueError,match='delegation_scope'):Inbox(cfg)


def test_frozen_guidance_change_rejected_before_work(tmp_path):
    inbox,_=fixture(tmp_path);cfg=copy.deepcopy(inbox.cfg)
    guidance=tmp_path/'guidance.md';guidance.write_text('original')
    cfg['guidance_manifest']=[ref(guidance)];guidance.write_text('changed')
    with pytest.raises(ValueError,match='frozen_guidance_changed'):Inbox(cfg)


def test_interrupted_submission_reconciles_same_guard_without_second_apply(tmp_path):
    inbox,_=fixture(tmp_path);pipeline=ComponentPipeline(inbox);state=pipeline.load()
    current=state['attempts'][-1];folder=pipeline.root/current['id'];folder.mkdir()
    plan=folder/'manager-plan.json';atomic_json(plan,{'route':'official-cli','request':{'request_key':'one-write'}})
    auth=folder/'authorization.json';atomic_json(auth,{'synthetic':True})
    current.update(stage='submitting',manager_plan=ref(plan),manager_authorization=ref(auth),
        expected={'config':{'epochs':20}},science_plan=ref(plan))
    calls=[];guard_state={'phase':'new'}
    class Interrupted:
        def __init__(self,cfg):pass
        def guard(self,plan):return SimpleNamespace(state=guard_state,load=lambda:None,lock=contextlib.nullcontext)
        def apply(self,*args):
            calls.append('write');guard_state['phase']='intent';raise ValueError('unknown_write')
        def reconcile(self,*args):calls.append('read-only-reconcile');return {'status':'pending-approval-unresolved'}
    with patch('lpai_manager.Manager',Interrupted):
        with pytest.raises(ValueError,match='unknown_write'):pipeline.submit(state,current,folder)
        pipeline.save(state)
        recovered=ComponentPipeline(inbox);state=recovered.load()
        recovered.submit(state,state['attempts'][-1],folder)
    assert calls==['write','read-only-reconcile']
    assert len(state['attempts'])==2 and state['attempts'][-1]['stage']=='watching'


@pytest.mark.parametrize('newline',[b'\n',b'\r\n'])
def test_frozen_payload_preserves_exact_lf_or_crlf_reviewed_bytes(tmp_path,newline):
    import base64
    import gzip
    import re
    folder=tmp_path/'frozen';folder.mkdir()
    trainer=tmp_path/'trainer.py';trainer.write_bytes(b'# protected'+newline)
    original={'model.py':b'def build_model(config):'+newline+b'    raise AssertionError("control")'+newline}
    params={'training':{'epochs':20,'lr':.001,'batch_size':64,'seed':42,'threads':1,'round':2,
        'expected_minutes':10,'model':'control'}}
    original['parameters.json']=json.dumps(params,indent=2).encode().replace(b'\n',newline)
    for name,raw in original.items():(folder/name).write_bytes(raw)
    profile={'root':str(folder),'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in original.items()}}
    contract=tmp_path/'contract.json';atomic_json(contract,{'protected_inputs':[ref(trainer)],
        'comparison_identity':{'protocol':'control','source_sha256':'a'*64,'split_sha256':'b'*64}})
    parent={'uid':'component','cluster':'cluster','data':{'lpai_task':{'cmd':'old','roles':[{'cmd':'old','resource':{'Cpu':1}}]}}}
    request,expected,manifest=task_request(profile,ref(contract),parent,'control-project',2)
    packed=re.search(r"base64.b64decode\('([^']+)'",request['data']['lpai_task']['cmd']).group(1)
    files=json.loads(gzip.decompress(base64.b64decode(packed)))
    assert all(files[name].encode()==raw for name,raw in original.items())
    assert files['trainer.py'].encode()==trainer.read_bytes()
    assert request['data']['lpai_task']['roles'][0]['resource']=={'Cpu':1}
    assert expected['code_sha256']==manifest['code_sha256']
