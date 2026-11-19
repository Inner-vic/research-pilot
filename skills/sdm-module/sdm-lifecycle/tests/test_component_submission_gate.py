"""Original SDM gates with synthetic model/platform transports; no ML execution."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from component_payload import ref, contract, task_request
from component_pipeline import ComponentPipeline
from component_submission import binding, verify, initial_review
from lpai_manager import Manager as OriginalManager, runtime_digest, digest_file
from model_runner import ModelRunner
from model_runner_transport import save
from proposal_review import FIELDS, gate, subject
from research_event_service import Inbox, sdk_execute
from review_storage import atomic_json, read_json
from task_pipeline import digest
from test_component_pipeline import fixture, BASELINE
from test_lpai_manager import FakeBackend, data


class Backend(FakeBackend):
    def mutate(self, request, path):
        value = super().mutate(request, path)
        if request['action'] == 'task.submit':
            self.task['uid'] = 'task-'+str(self.writes)
            return self.task['uid']
        return value


@pytest.fixture
def research(tmp_path, monkeypatch, request):
    old, _ = fixture(tmp_path)
    cfg = copy.deepcopy(old.cfg); cfg['root'] = str(tmp_path/'gated-service')
    spec = cfg['component_pipeline']; spec.update(version=4, minimum_rounds=1, minimum_revisions=0,
        submission_gate={'version':1}, decision_policy={'version':2,
            'target':{'metric':'validation.macro_f1','operator':'>=','value':.9},
            'budget':{'known_task_uids':[]}, 'termination':{'mode':'until-target','source_ref':'Synthetic control delegation'}})
    manager_cfg = read_json(tmp_path/'manager.json')
    manager_cfg.update(schema='lpai-manager/v1', state_root=str(tmp_path/'manager-state'),
        submission_registry=str(tmp_path/'registry'))
    atomic_json(tmp_path/'manager.json',manager_cfg)
    backend = Backend(); backend.component['uid'] = 'component'
    manager = OriginalManager(manager_cfg,backend)
    monkeypatch.setattr('lpai_manager.Manager',lambda config: OriginalManager(config,backend))
    cli = tmp_path/'codex.exe'; cli.write_bytes(b'Never executed: synthetic model transport')
    spec['proposal_review']['model_runner'] = {'version':1,'backend':'cli', 'cli_path':str(cli),
        'cli_sha256':ref(cli)['sha256'], 'codex_home':str(tmp_path),'cwd':str(tmp_path),'timeout_seconds':30}
    delegation = read_json(tmp_path/'delegation.json')
    delegation['training_limits'] = {'min_epochs':10,'max_epochs':30,'default_epochs':20}
    atomic_json(tmp_path/'delegation.json',delegation); spec['delegation'] = ref(tmp_path/'delegation.json')
    comparison = spec['comparison_identity']
    execution = contract(spec['baseline_source'],tmp_path/'protected',comparison,manager_cfg,'component',
        training_limits=delegation['training_limits'],next_round=1)
    profile = tmp_path/'profile'; profile.mkdir()
    (profile/'model.py').write_text('def build_model(config): raise AssertionError("not executed")\n'
        'def build_optimizer(parameters,config): raise AssertionError("not executed")\n')
    if getattr(request,'param',None)=='large-payload':
        with (profile/'model.py').open('a') as stream:stream.write('# retained raw source\n'*20000)
    training = {'round':1,'epochs':20,'lr':.001,'batch_size':64,'seed':42,'threads':1,
                'model':'synthetic-baseline','channels':16,'expected_minutes':10}
    atomic_json(profile/'parameters.json',{'comparison_identity':comparison,
        'execution_scope':{'provider':'lpai','namespace':'ns','owner':'owner@example.com','component_uid':'component'},
        'platform_requirements':manager_cfg['platform_requirements'],'training':training})
    parent = {'action':'task.submit','uid':'component','name':'initial','request_key':'initial',
        'cluster':'cluster','data':data(),'task_info':{'problem_statement':'control','solution_approach':'control',
        'expected_result':'unmeasured','expected_duration':10,'expected_duration_desc':'synthetic control'}}
    request, expected, payload = task_request({'root':str(profile),'files':{n:ref(profile/n)['sha256']
        for n in ('model.py','parameters.json')}},execution,parent,cfg['project_id'],1)
    plan_path = tmp_path/'initial-plan.json'; atomic_json(plan_path,manager.plan(request))
    atomic_json(tmp_path/'initial-request.json',read_json(plan_path)['request'])
    spec['initial_request'] = ref(tmp_path/'initial-request.json')
    spec['initial_source'].update(plan=ref(plan_path), manager_config=ref(tmp_path/'manager.json'), expected=expected)
    confirmed = tmp_path/'confirmed.json'; atomic_json(confirmed,{'schema':'sdm-confirmed-initial-study/v1',
        'objective':spec['objective'],'source_ref':delegation['source_ref'],'comparison_identity':comparison,
        'target':spec['decision_policy']['target'],'training':training,'payload_manifest':payload})
    authorization = tmp_path/'initial-auth.json'; atomic_json(authorization,{'schema':'lpai-manager-authorization/v1',
        'plan_sha256':digest_file(plan_path),'direct_user_confirmation':True,'source_ref':delegation['source_ref'],
        'delegation':spec['delegation']})
    spec['initial_submission'] = {'version':2,'authorization':ref(authorization),'confirmed_plan':ref(confirmed)}
    calls = []; decision = {'verdict':'approve','unknown':False}
    def launch(runner, folder):
        envelope = read_json(folder/'request.json'); request = envelope['request']
        from proposal_input import restore
        logical = Path(str(folder)[4:]) if str(folder).startswith('\\\\?\\') else folder
        value = restore(request['evidence'],logical.parent.parent)
        role = 'a' if request['purpose']=='proposal' else 'b'
        calls.append((role,request['model']))
        if decision['unknown']: raise OSError('Synthetic lost model acknowledgement')
        if role=='a':
            response = {name:'Synthetic falsifiable initial '+name for name in FIELDS}
            response.update(addresses=[],advice_dispositions=[])
        else:
            ids = [row['id'] for row in value['shared']['packet']['context']['sources']]
            response = {'verdict':decision['verdict'],'rationale':'Synthetic control, no scientific acceptance claim',
                'challenges':[],'resolutions':[], 'evidence_assessment':{'source_ids':ids[:1],
                'judgment':'hypothesis','limitations':['No real model or platform invocation']}}
        save(folder,envelope['binding'],'completed',response,{'backend':'explicit-control-substitute'})
    monkeypatch.setattr(ModelRunner,'_launch',launch)
    def coordinate(selected,folder,timeout):
        assert selected['mode']=='proposal-review'
        return gate(read_json(selected['project']),Path(selected['output']))
    monkeypatch.setattr('event_coordinator.run',coordinate)
    inbox=Inbox(cfg); pipeline=ComponentPipeline(inbox)
    return SimpleNamespace(cfg=cfg,spec=spec,inbox=inbox,pipeline=pipeline,state=pipeline.load(),
        manager=manager,manager_cfg=manager_cfg,backend=backend,calls=calls,decision=decision,
        profile=profile,execution=execution,parent=parent,payload=payload,tmp=tmp_path)


def finish(research):
    current=research.state['attempts'][-1]; job=current['job']; inbox=research.inbox
    with inbox.db() as db:row=dict(db.execute('select * from jobs where id=?',(job,)).fetchone())
    if row['status']=='completed':return
    folder=inbox.root/job; folder.mkdir(exist_ok=True)
    value=sdk_execute(inbox,row,folder)
    atomic_json(folder/'result.json',{'job_id':job,**value})
    inbox.transition(job,value['status'],**value['detail'])


def approve_initial(research):
    p=research.pipeline; state=research.state
    assert p.step(state)==0
    assert state['attempts'][0]['stage']=='reviewing-initial' and research.backend.writes==0
    finish(research); p.step(state)
    assert state['attempts'][0]['initial_review_phase']=='code' and research.backend.writes==0
    finish(research); p.step(state)
    assert state['attempts'][0]['stage']=='initial-submitting' and research.backend.writes==0


def checked_initial(research, **changes):
    current=research.state['attempts'][0]
    values=dict(spec=research.spec,manager_config=research.manager_cfg,
        plan_ref=research.spec['initial_source']['plan'], authorization_ref=research.spec['initial_submission']['authorization'],
        reviews=current['submission_reviews'],payload=research.payload)
    return verify(**{**values,**changes})


def test_initial_has_two_original_reviews_and_replays_without_calls_or_writes(research,monkeypatch):
    approve_initial(research)
    assert research.calls==[('a','control-proposer'),('b','control-critic')]*2
    import contracts
    spy=Mock(wraps=contracts.evaluate_gate); monkeypatch.setattr(contracts,'evaluate_gate',spy)
    accepted=checked_initial(research)
    assert accepted['model_calls']==accepted['platform_calls']==0 and spy.call_count>=1
    current=research.state['attempts'][0]
    research.pipeline.save(research.state)
    p=ComponentPipeline(research.inbox); state=p.load()
    assert p.step(state)==0 and state['attempts'][0]['stage']=='watching'
    assert research.backend.writes==1 and len(research.calls)==4
    assert read_json(current['code_review_project'])['component_submission_review']['phase']=='code'
    # Original completed Guard identity bypasses models and does not post again.
    p.dispatch_submission(state['attempts'][0],p.root/'initial',research.spec['initial_source']['plan'],
        research.spec['initial_submission']['authorization'],initial=True)
    assert research.backend.writes==1 and len(research.calls)==4


@pytest.mark.parametrize('research',['large-payload'],indirect=True)
def test_initial_large_code_uses_original_bounded_view_and_preserves_raw_identity(research):
    approve_initial(research)
    science=research.state['attempts'][0]['submission_reviews']['science']
    shared=next(Path(science['root']).glob('proposal-review/*/shared-evidence.json'))
    sources=read_json(shared)['evidence']['raw_sources']
    model=next(row for row in sources if row['path'].endswith('model.py'))
    assert model['omitted_bytes']>0 and model['digest'].removeprefix('sha256:')==ref(research.profile/'model.py')['sha256']
    assert len(research.calls)==4 and research.backend.writes==0


@pytest.mark.parametrize('missing',['science','code'])
def test_missing_review_cannot_submit(research,missing):
    approve_initial(research)
    reviews=copy.deepcopy(research.state['attempts'][0]['submission_reviews']); reviews.pop(missing)
    with pytest.raises(ValueError,match='both_original_submission_reviews_required'):
        checked_initial(research,reviews=reviews)
    assert research.backend.writes==0 and len(research.calls)==4


def test_self_reported_approved_receipt_cannot_replace_original_model_trace(research):
    approve_initial(research)
    current=research.state['attempts'][0]; reviews=copy.deepcopy(current['submission_reviews'])
    fake=Path(reviews['science']['root'])/'fake-approved.json'
    atomic_json(fake,{'approved':True,'response':{'verdict':'approve'}})
    reviews['science']['receipt']=ref(fake)
    with pytest.raises(ValueError,match='original_submission_review_not_approved'):
        checked_initial(research,reviews=reviews)
    assert research.backend.writes==0 and len(research.calls)==4


def test_accepted_file_mutation_fails_before_submission(research):
    approve_initial(research)
    receipt=Path(research.state['attempts'][0]['submission_reviews']['code']['receipt']['path'])
    original=read_json(receipt); original['response']['rationale']='Altered after approval'; atomic_json(receipt,original)
    with pytest.raises(ValueError):research.pipeline.step(research.state)
    assert research.backend.writes==0 and len(research.calls)==4


def test_valid_but_wrong_review_subject_cannot_approve_payload(research):
    approve_initial(research)
    reviews=copy.deepcopy(research.state['attempts'][0]['submission_reviews']); reviews['code']=reviews['science']
    with pytest.raises(ValueError,match='initial_review_does_not_bind_exact_submission'):
        checked_initial(research,reviews=reviews)
    assert research.backend.writes==0


def test_changed_plan_cannot_reuse_previous_approvals(research):
    approve_initial(research)
    plan=read_json(research.spec['initial_source']['plan']['path']); plan['request']['task_info']['expected_result']='Changed intent'
    path=research.tmp/'different-plan.json'; atomic_json(path,plan)
    auth=read_json(research.spec['initial_submission']['authorization']['path']); auth['plan_sha256']=digest_file(path)
    auth_path=research.tmp/'different-auth.json';atomic_json(auth_path,auth)
    with pytest.raises(ValueError,match='initial_review_does_not_bind_exact_submission'):
        checked_initial(research,plan_ref=ref(path),authorization_ref=ref(auth_path))
    assert research.backend.writes==0


@pytest.mark.parametrize('change',[{'direct_user_confirmation':False},{'source_ref':'other-scope'}])
def test_review_approval_does_not_create_authority(research,change):
    approve_initial(research)
    auth=read_json(research.spec['initial_submission']['authorization']['path']); auth.update(change)
    path=research.tmp/'unauthorized.json';atomic_json(path,auth)
    with pytest.raises(ValueError):checked_initial(research,authorization_ref=ref(path))
    assert research.backend.writes==0 and len(research.calls)==4


def test_pause_during_receipt_replay_prevents_submit_and_resume_reuses_reviews(research,monkeypatch):
    approve_initial(research)
    import component_submission
    original=component_submission.verified_review
    marker=research.inbox.root/'PAUSED'
    def replay(value):
        result=original(value); marker.write_text('Synthetic local pause'); return result
    with monkeypatch.context() as m:
        m.setattr(component_submission,'verified_review',replay)
        assert research.pipeline.step(research.state)==15
    assert research.backend.writes==0 and research.state['attempts'][0]['stage']=='initial-submitting'
    marker.unlink()
    research.pipeline.step(research.state)
    assert research.backend.writes==1 and len(research.calls)==4


def test_initial_rejection_blocks_both_code_and_training(research):
    research.decision['verdict']='reject'
    research.pipeline.step(research.state);finish(research);research.pipeline.step(research.state)
    current=research.state['attempts'][0]
    assert current['stage']=='needs-attention' and current['reason']=='original-initial-review:proposal-rejected'
    assert research.backend.writes==0 and len(research.calls)==2


def test_initial_code_rejection_cannot_reuse_scientific_approval_as_authority(research):
    research.pipeline.step(research.state); finish(research); research.pipeline.step(research.state)
    research.decision['verdict']='reject'; finish(research); research.pipeline.step(research.state)
    current=research.state['attempts'][0]
    assert current['stage']=='needs-attention' and set(current['submission_reviews'])=={'science'}
    assert research.backend.writes==0 and len(research.calls)==4


def test_current_component_change_after_review_blocks_original_manager_apply(research):
    approve_initial(research)
    research.backend.component['queue_code']='changed-after-review'
    with pytest.raises(ValueError):research.pipeline.step(research.state)
    assert research.backend.writes==0 and len(research.calls)==4


@pytest.mark.parametrize('legacy',[True,False])
def test_fresh_intent_cannot_disable_required_initial_gate(research,legacy):
    if legacy:research.spec['initial_submission']['version']=1
    else:research.spec.pop('submission_gate')
    with pytest.raises(ValueError,match='fresh_initial_submission_requires_sdm_review_gate'):
        research.pipeline.step(research.state)
    assert research.backend.writes==0 and not research.calls


def test_initial_pause_uses_existing_original_review_resume_contract(research):
    research.pipeline.step(research.state)
    current=research.state['attempts'][0]
    from research_pause import active_review
    selected=active_review(research.inbox.root,current)
    assert selected==(Path(current['code_review_project']),Path(current['code_review_root']),
        'reviewing-initial','proposal-review')
    research.pipeline.save(research.state)
    restored=ComponentPipeline(research.inbox); state=restored.load()
    original_job=state['attempts'][0]['job']
    assert restored.step(state)==15 and state['attempts'][0]['job']==original_job
    assert research.backend.writes==0 and not research.calls


def test_unknown_guard_intent_reconciles_without_new_reviews(research,monkeypatch):
    plan=read_json(research.spec['initial_source']['plan']['path']); guard=research.manager.guard(plan)
    with guard.lock():guard.load();guard.before_submit(research.tmp/'unknown-original-receipt.json')
    before=guard.path.read_bytes()
    manager=research.manager; manager.apply=Mock(side_effect=AssertionError('Must never re-post unknown intent'))
    manager.reconcile=Mock(return_value={'status':'submission-outcome-unresolved'})
    monkeypatch.setattr('lpai_manager.Manager',lambda config:manager)
    research.pipeline.step(research.state)
    assert manager.apply.call_count==0 and manager.reconcile.call_count==1
    assert research.state['attempts'][0]['stage']=='watching'
    assert not research.calls and research.backend.writes==0 and guard.path.read_bytes()==before


def test_successor_dispatch_consumes_the_same_gate_and_original_authorization(research):
    approve_initial(research)
    first=research.state['attempts'][0]; science=first['submission_reviews']['science']
    plan=research.tmp/'science-plan.json'; accepted=read_json(science['receipt']['path'])
    atomic_json(plan,{'schema':'sdm-research-plan/v1','execution_authority':False,
        'review_project_ref':science['project'],'review_root':science['root'],
        'accepted_review_ref':science['receipt'],'proposal':accepted['proposal']})
    profile=research.tmp/'successor-profile';profile.mkdir()
    (profile/'model.py').write_bytes((research.profile/'model.py').read_bytes())
    parameters=read_json(research.profile/'parameters.json');parameters['training']['round']=2
    atomic_json(profile/'parameters.json',parameters)
    request,expected,payload=task_request({'root':str(profile),'files':{n:ref(profile/n)['sha256']
        for n in ('model.py','parameters.json')}},research.execution,research.parent,research.cfg['project_id'],2)
    folder=research.pipeline.root/'successor-control';folder.mkdir()
    request_path=folder/'request.json';atomic_json(request_path,request)
    preflight=folder/'preflight.json';atomic_json(preflight,research.manager.plan(request))
    cfg=research.pipeline.base_project();cfg['component_code_review']={'accepted_plan':ref(plan),
        'request':ref(request_path),'payload_manifest':payload,'execution_contract':research.execution}
    if research.spec.get('sdm_intent') is not None:
        from research_intent import for_review
        cfg['sdm_intent']=for_review(research.spec['sdm_intent'],accepted['proposal']['sdm_brief'])
    project=folder/'code-project.json';atomic_json(project,cfg)
    review_root=folder/'code-review';review_root.mkdir()
    atomic_json(review_root/'review-context.json',{'schema':'sdm-review-context/v1','subject_digest':digest(subject(cfg)),
        **{k:'Synthetic successor '+k for k in ('goal','constraints','data','code_revision','configuration','history','unknowns')},
        'sources':[{'id':'successor-request','kind':'config',**ref(request_path)}]})
    checked=gate(cfg,review_root);assert checked['status']=='proposal-approved'
    current={'id':'successor-control','stage':'reviewing-code','source':research.spec['initial_source'],
        'science_plan':ref(plan),'successor_request':ref(request_path),'expected':expected,
        'payload_manifest':payload,'preflight_plan':ref(preflight),
        'code_review_project':str(project),'code_review_root':str(review_root)}
    state={'attempts':[current]}
    research.pipeline.prepare_submission(current,folder,checked)
    research.pipeline.submit(state,current,folder)
    assert research.backend.writes==1 and current['stage']=='successor-registered'
    initial=checked_initial(research); successor=read_json(current['submission_acceptance']['path'])
    assert successor['gate_digest']==initial['gate_digest'] and successor['checks']==initial['checks']
    assert len(research.calls)==6 and state['attempts'][-1]['stage']=='watching'


@pytest.mark.skipif(os.name!='nt',reason='Current initializer selects the existing Windows-contained v4 runner')
def test_initializer_freezes_review_required_policy_without_models_or_task_writes(research,monkeypatch):
    import sys
    from research_initialize import prepare
    monkeypatch.setattr('research_initialize.Manager',lambda config:OriginalManager(config,research.backend))
    root=research.tmp
    template=copy.deepcopy(research.cfg)
    sdk=root/'config-only-sdk'; (sdk/'openai_codex').mkdir(parents=True)
    (sdk/'openai_codex/client.py').write_text('# Configuration-only marker; never imported or executed.\n')
    # An explicit SDK package-marker substitute, not actual SDK qualification.
    runner=template['component_pipeline']['proposal_review']['model_runner']
    runner.update(version=4,backend='sdk',sdk_version='0.163.0',sdk_root=str(sdk),
        context_policy={'version':1,'user_config':'ignore','project_doc_max_bytes':0,
            'reasoning_effort':'medium','input_kind':'tool-output'})
    template_path=root/'initialization-template.json';atomic_json(template_path,template)
    request=root/'request-template.json';atomic_json(request,research.parent)
    from test_sdm_governance import contents
    spec={'schema':'sdm-research-initialization/v2','research_intent':contents(),'project_id':'initialization-control',
        'source_ref':'Synthetic delegated fixture only','objective':'Verify frozen initial gate policy',
        'template':ref(template_path),'manager_config':research.spec['initial_source']['manager_config'],
        'request_template':ref(request),'baseline_source':research.spec['baseline_source'],
        'baseline_model':ref(research.profile/'model.py'),'comparison_identity':research.spec['comparison_identity'],
        'training':read_json(research.profile/'parameters.json')['training'],
        'target':research.spec['decision_policy']['target'],
        'workload':{'version':1,'adapter':'binary-component/v2','training_loss_hook':False,
            'seed_values':[42],'source_ref':'Synthetic adapter fixture'}}
    spec_path=root/'initialization.json';atomic_json(spec_path,spec)
    output=root/'new-control'/'service.json'
    args=(spec_path,root/'new-frozen',root/'new-store',root/'new-model-workspace',output)
    result=prepare(*args)
    assert result['status']=='prepared' and result['platform_task_writes']==result['model_calls']==0
    cfg=read_json(output); frozen=Path(cfg['runtime_root'])
    assert 'planner_context' not in cfg['component_pipeline']['proposal_review']
    assert cfg['working_sessions']==cfg['component_pipeline']['proposal_review']['working_sessions']
    assert cfg['research_context']['root']==str(output.parent/'sessions/main')
    assert cfg['research_context']['history_required'] is True
    assert (frozen/'planner_session.py').is_file()
    assert not (output.parent/'planner-context/session.json').exists()
    assert cfg['component_pipeline']['initial_submission']['version']==2
    assert cfg['component_pipeline']['submission_gate']=={'version':2}
    assert cfg['component_pipeline']['proposal_review']['dialogue_version']==4
    assert len(cfg['normative_manifest'])==26
    assert cfg['component_pipeline']['sdm_intent']['engine_contract_digest']==cfg['engine_contract_digest']
    assert (frozen/'component_submission.py').is_file() and (frozen/'contracts.py').is_file()
    assert prepare(*args)['status']=='already-prepared'
    assert not research.calls and research.backend.writes==0
