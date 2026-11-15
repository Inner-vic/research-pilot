"""Actual local CPU capsules through the unchanged five-phase DAG, no LPAI/Codex."""
import datetime as dt
import hashlib,json,time
from pathlib import Path
import pytest
from test_devspace_study import case,respond
import devspace_study as study
from task_pipeline import digest


def fixture(tmp_path,profile='legacy'):
    config,root=case(tmp_path);p=tmp_path/'probe.py';p.write_text("import json;print(json.dumps({'cpu_probe':True,'gpu_compute_invoked':False}))")
    plan=config['devspace_execution']['plan'];plan['inputs'].append({'name':p.name,'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    plan['preflight']={'input_name':p.name,'parameters':{},'timeout_seconds':10}
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    now=dt.datetime.now(dt.timezone.utc);e=tmp_path/'project-evidence.txt';e.write_text('Generated CPU fixture, no real model evidence')
    names=['factor-and-controls','actual-progress','schedule-and-resume','evaluation-and-selection','uncertainty','total-cost']
    config['devspace_main_workflow']={'version':1,'objective':'CPU main DAG integration','protocol':{'epochs':1,'validation_samples':2},
        'hypothesis':'Declared parameters reach actual subprocess','baseline_ref':'CPU fixture initializer','evidence_paths':[str(e)],
        'method_checks':[{'id':n,'status':'unknown','rationale':'Synthetic CPU fixture only'} for n in names],
        'resource_request':{'provider':'cpu','namespace':'cpu','cluster':'cpu','queue_id':'cpu',
            'roles':[{'instances':1,'gpu_type':'none','gpus_per_instance':0,'cpu_cores_per_instance':1,'memory_gib_per_instance':1}]},
        'inventory_observed_at':now.isoformat(),'inventory_valid_until':(now+dt.timedelta(hours=1)).isoformat(),
        'inventory_ref':str(e),'quote_source':'CPU fixture, no charge'}
    config['workflow_profile']=profile
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    return config,root


def plan_reply(state):
    q=json.loads(Path(state['plan_request']).read_bytes())
    Path(state['plan_response']).write_text(json.dumps({'request_digest':digest(q),'reviewer':'CPU fixture Agent',
        'reason':'Verify actual subprocess order and once-only continuation','alternatives':'Do not execute if source or input contract changes',
        'limitations':'CPU fixtures are not platform qualification'}))


def test_project_research_facts_override_roadmodel_labels_without_execution(tmp_path):
    import devspace_main_workflow as main
    config, root = fixture(tmp_path)
    root.mkdir(parents=True)
    spec = config['devspace_main_workflow']
    spec.update(initialization={'kind':'fresh','source_ref':'seeded-public-classifier'},
                evaluation={'scope':'5000 class-stratified validation images','selection_rule':'best validation top1'},
                decision_criteria='Compare clean train/validation evidence before choosing one factor')
    _, _, _, research, _, _ = main.records(config, root)
    assert {k:research[k] for k in ('initialization','evaluation','decision_criteria')} == {
        k:spec[k] for k in ('initialization','evaluation','decision_criteria')}
    assert not (tmp_path/'remote').exists()


def test_absent_project_research_facts_preserve_frozen_defaults(tmp_path):
    import devspace_main_workflow as main
    config, root = fixture(tmp_path)
    root.mkdir(parents=True)
    _, _, _, research, _, _ = main.records(config, root)
    assert research['initialization'] == {'kind':'weights-only','source_ref':'CPU fixture initializer'}
    assert research['evaluation'] == {'scope':'2 fixed held-out real frames',
                                     'selection_rule':'Compare reported loss and mIoU; no promotion from one small run'}
    assert research['decision_criteria'] == 'Actual optimizer updates and finite metrics; Agent-reviewed single-variable successor only'
    assert not (tmp_path/'remote').exists()


def test_invalid_initialization_fails_inspection_before_journal_or_resource(tmp_path):
    import devspace_execution as single
    import devspace_main_workflow as main
    config, root = fixture(tmp_path)
    config['devspace_main_workflow']['initialization']={'kind':'random'}
    with pytest.raises(ValueError,match='initialization_semantics_required'):single.inspect(config)
    with pytest.raises(ValueError,match='initialization_semantics_required'):main.records(config,root)
    assert not root.exists()


def result_reply(state,action='run-next-round'):
    respond(state,action)
    p=Path(state['review']['response_path']);x=json.loads(p.read_bytes());x['workflow_assessment']={
        'evidence_consistent':True,'conclusion_supported':True,'supporting_evidence':[x['facts'][0]['evidence_id']],
        'counterevidence':['No scientific quality inference'],'limitations':['CPU generated fixture only'],
        'alternatives':'Stop or execute one declared parameter change'};p.write_text(json.dumps(x))


def until_wait(config,root):
    for _ in range(80):
        x=study.execution(config,root)
        if x['status'] not in {'running','cleanup-required','launch-outcome-unknown'}:return x
        time.sleep(.1)
    raise AssertionError(x)


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_two_actual_cpu_rounds_pass_main_graph_before_and_after_compute(tmp_path,profile):
    config,root=fixture(tmp_path,profile)
    first=study.execution(config,root)
    assert first['status']=='awaiting-main-plan-review'
    assert not (tmp_path/'remote').exists()
    plan_reply(first);first=until_wait(config,root)
    assert first['status']=='awaiting-review',first
    assert first['stop_verified'] and first['main_workflow']['waiting_node']=='31'
    graph1=json.loads((root/'round-01/main/summary.json').read_bytes())
    assert graph1['nodes']['13']['status']=='completed' and graph1['nodes']['15']['status']=='completed'
    assert not json.loads((root/'round-01/main/preflight.json').read_bytes())['report']['gpu_compute_invoked']
    result_reply(first);second=until_wait(config,root)
    assert second['active_round']==2 and second['status']=='awaiting-main-plan-review',second
    plan_reply(second);second=until_wait(config,root)
    assert second['status']=='awaiting-review'
    result_reply(second,'stop');done=until_wait(config,root)
    assert done['status']=='completed' and len(done['history'])==2
    for i in (1,2):
        summary=json.loads((root/f'round-{i:02d}/main/summary.json').read_bytes())
        assert summary['status']=='complete'
        assert summary['coverage']['required_completed_count']==summary['coverage']['required_node_count']
        assert all(p['status']=='completed' or p['display_id']=='3.1' and p['status']=='not-applicable' for p in summary['work_packages'])
    before=[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]
    assert study.execution(config,root)['status']=='completed'
    assert before==[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_plan_and_result_judgments_cannot_be_automatically_manufactured(tmp_path,profile):
    config,root=fixture(tmp_path,profile);first=study.execution(config,root)
    q=json.loads(Path(first['plan_request']).read_bytes())
    Path(first['plan_response']).write_text(json.dumps({'request_digest':'wrong','reviewer':'fixture','reason':'x','alternatives':'x','limitations':'x'}))
    with pytest.raises(ValueError,match='grounded_main_plan_response'):study.execution(config,root)
    assert not (tmp_path/'remote').exists()
    Path(first['plan_response']).unlink();plan_reply(first);first=until_wait(config,root)
    respond(first)
    waiting=study.execution(config,root)
    assert waiting['status']=='awaiting-review' and not (root/'round-02').exists()
    assert 'workflow_assessment' in waiting['review']['main_workflow_requirement']


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_failed_real_preflight_never_releases_training_and_cleans_local_allocation(tmp_path,profile):
    config,root=fixture(tmp_path,profile)
    probe=tmp_path/'probe.py';probe.write_text("raise ValueError('controlled preflight failure')")
    plan=config['devspace_execution']['plan'];plan['inputs'][-1]['sha256']=hashlib.sha256(probe.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    first=study.execution(config,root);plan_reply(first);failed=until_wait(config,root)
    assert failed['status']=='main-workflow-failed' and failed['stop_verified']
    assert not (tmp_path/'remote/r01/claim').exists()
    summary=json.loads((root/'round-01/main/summary.json').read_bytes())
    assert summary['nodes']['13']['status']=='failed' and summary['nodes']['15']['status']=='skipped'


def test_unbounded_main_resource_gate_requires_exact_explicit_grant():
    import orchestration as o
    from test_orchestration import OrchestrationTests
    fixture=OrchestrationTests();fixture.setUp()
    try:
        fixture.update('proposal',limit_policy='user-authorized-unbounded',limit_authorization_ref='CPU-fixture-grant',
            max_cost=None,max_seconds=None,queue_wait_seconds=None)
        proposal=o.needed(fixture.task,'proposal')
        with pytest.raises(ValueError,match='unbounded_resource_authorization'):o.validate_record('proposal',proposal,fixture.task,fixture.now)
        fixture.task['resource_authorization']={'confirmed':True,'source_ref':'CPU-fixture-grant'}
        o.validate_record('proposal',proposal,fixture.task,fixture.now)
        fixture.update('confirmation',proposal_digest=digest(proposal),source_ref='CPU-fixture-grant',expires_at=None)
        o.validate_record('confirmation',o.needed(fixture.task,'confirmation'),fixture.task,fixture.now)
        o.dispatch_guard(fixture.task,True)
        fixture.task['resource_authorization']['source_ref']='different'
        with pytest.raises(ValueError,match='unbounded_confirmation_scope'):o.dispatch_guard(fixture.task,True)
    finally:fixture.doCleanups()


def test_force_cleanup_cannot_take_ownership_before_any_acquisition(tmp_path):
    from test_devspace_execution import setup_case
    import devspace_execution as single
    config,root=setup_case(tmp_path)
    result=single.execution(config,root,force_cleanup=True)
    assert result['status']=='ownership-reconciliation-required'
    assert not result['stop_verified'] and not result['intents']
    assert not (tmp_path/'remote').exists()


@pytest.mark.parametrize('profile',['legacy','exploration'])
@pytest.mark.parametrize('mutation',['changed','missing'])
def test_changed_input_during_main_execution_stops_owned_cpu_process(tmp_path,profile,mutation):
    import devspace_main_workflow as main
    import devspace_execution as single
    config,root=fixture(tmp_path,profile)
    source=tmp_path/'workload.py'
    source.write_text(source.read_text().replace('time.sleep(.1)','time.sleep(30)'))
    plan=config['devspace_execution']['plan']
    plan['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    try:
        pending=main.execution(config,root);plan_reply(pending)
        running=main.execution(config,root)
        assert running['status']=='running' and running['launch_intent']
        process=json.loads((tmp_path/'remote/process.json').read_bytes())
        original=source.read_bytes()
        if mutation=='missing':source.unlink()
        else:source.write_text(source.read_text()+'\n# Changed after the owned capsule started\n')
        for _ in range(60):
            stopped=main.execution(config,root)
            if stopped.get('stop_verified') and stopped.get('process_terminal'):break
            time.sleep(.05)
        assert stopped['status']=='main-workflow-failed' and stopped['stop_verified']
        assert stopped['process_terminal'] and not stopped.get('study_complete',False)
        assert stopped['main_preparation_failure']['reason']
        assert stopped['new_execution_requested'] is False
        observed=json.loads((tmp_path/'remote/process.json').read_bytes())
        assert observed['started_at']==process['started_at']
        source.write_bytes(original)  # Fixing preparation cannot reopen the aborted run.
        before=(root/'stop-request.json').read_bytes() if (root/'stop-request.json').exists() else None
        assert main.execution(config,root)['status']=='main-workflow-failed'
        assert ((root/'stop-request.json').read_bytes() if (root/'stop-request.json').exists() else None)==before
    finally:
        if (root/'execution.json').exists():
            for _ in range(60):
                state=json.loads((root/'execution.json').read_bytes())
                if state.get('stop_verified') and state.get('process_terminal'):break
                single.execution(config,root,force_cleanup=True)
                time.sleep(.05)


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_invalid_preparation_before_ownership_never_queries_or_cleans_resource(tmp_path,profile,monkeypatch):
    import devspace_main_workflow as main
    config,root=fixture(tmp_path,profile)
    (tmp_path/'workload.py').unlink()
    monkeypatch.setattr(main.single,'execution',lambda *a,**k:pytest.fail('unowned cleanup/launch'))
    with pytest.raises(ValueError,match='bounded_pinned_input_required'):main.execution(config,root)
    assert not (root/'execution.json').exists()
    assert not (root/'main/preparation-failure.json').exists()
    assert not (tmp_path/'remote').exists()


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_latched_failure_accepts_native_stop_receipt_but_requires_readback(tmp_path,profile,monkeypatch):
    """Mock platform reads + CPU transport; no real platform/UI operations."""
    import devspace_main_workflow as main
    import devspace_execution as single
    from test_devspace_execution import action_receipt,pin,UID
    config,root=fixture(tmp_path,profile);plan=config['devspace_execution']['plan']
    binary=tmp_path/'lpai-agent';binary.touch()
    plan.update(transport='ssh',supervised_cleanup=True,route_evidence='CPU fixture only',
        ssh={'host':'example.invalid','port':22,'user':'fixture','key':str(tmp_path/'unused-key')})
    plan['resource'].update(route='native-ui',cli_path=str(binary),name='cpu-space',cluster='cpu')
    pin(config)
    phase=['stopped'];calls=[]
    def read(plan,action):
        calls.append(action);assert action=='get'
        return {'uid':UID,'phase':phase[0],'observed_at':time.time()}
    real=single.transport
    monkeypatch.setattr(single,'resource_call',read)
    monkeypatch.setattr(single,'transport',lambda plan,packet:real({**plan,'transport':'local-cpu'},packet))
    main.execution(config,root,allow_live=True)
    single.execution(config,root,allow_live=True,acquire_only=True)
    phase[0]='ready'
    assert single.execution(config,root,action_receipt(root,'start'),True,acquire_only=True)['status']=='resource-ready'
    source=tmp_path/'workload.py';before=source.read_bytes();source.unlink()
    pending=main.execution(config,root,allow_live=True)
    assert pending['status']=='cleanup-required' and not pending['stop_verified']
    failure=(root/'main/preparation-failure.json').read_bytes()
    source.write_bytes(before)
    from copy import deepcopy
    foreign=deepcopy(config)
    foreign_uid='00000000-0000-4000-8000-000000000999'
    foreign['devspace_execution']['plan']['resource']['uid']=foreign_uid
    foreign['task_event_collector']['tasks'][0]['uid']=foreign_uid
    foreign['task_event_service']['bindings'][0]['uid']=foreign_uid
    pin(foreign);observed=len(calls)
    with pytest.raises(ValueError,match='allocation_binding_changed'):
        main.execution(foreign,root,allow_live=True)
    assert len(calls)==observed and (root/'main/preparation-failure.json').read_bytes()==failure
    pending=main.execution(config,root,action_receipt(root,'stop'),True)
    assert 'stop' in pending['intents'] and not pending['stop_verified']
    phase[0]='stopped'
    done=main.execution(config,root,allow_live=True)
    assert done['status']=='main-workflow-failed' and done['stop_verified']
    assert done['new_execution_requested'] is False and not done.get('launch_intent')
    assert (root/'main/preparation-failure.json').read_bytes()==failure
    assert set(calls)=={'get'} and not (tmp_path/'remote/claim').exists()
    observed=len(calls)
    assert main.execution(config,root,allow_live=True)['status']=='main-workflow-failed'
    assert len(calls)==observed


@pytest.mark.parametrize('profile',['legacy','exploration'])
@pytest.mark.parametrize('watch',[False,True])
def test_public_single_round_honors_main_workflow_before_allocating(tmp_path,profile,watch):
    from test_devspace_execution import public,pin
    import devspace_execution as single
    config,root=fixture(tmp_path,profile);config.pop('devspace_study')
    config['devspace_execution']['plan']['argv']+=['--learning-rate','0.01'];pin(config)
    try:
        state=public(config,root,*(['--watch-devspace'] if watch else []))
        assert state['status']=='awaiting-main-plan-review'
        assert not (tmp_path/'remote').exists()
        assert not (root/'execution.json').exists()
        plan_reply(state)
        for _ in range(10):
            state=public(config,root,*(['--watch-devspace'] if watch else []))
            if state['status']=='awaiting-review':break
            time.sleep(.05)
        assert state['status']=='awaiting-review' and state['stop_verified']
        summary=json.loads((root/'main/summary.json').read_bytes())
        assert all(summary['nodes'][key]['status']=='completed' for key in ('1','2','3','4','6','8','13','15','16'))
        result_reply(state,'stop')
        assert public(config,root,*(['--watch-devspace'] if watch else []))['status']=='completed'
        process=(tmp_path/'remote/process.json').read_bytes()
        assert public(config,root)['status']=='completed'
        assert (tmp_path/'remote/process.json').read_bytes()==process
    finally:
        if (root/'execution.json').exists():
            single.execution(config,root,force_cleanup=True)


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_existing_standalone_attempt_keeps_its_original_route(tmp_path,profile):
    """A pre-fix standalone attempt must not acquire a new main graph midflight."""
    from test_devspace_execution import public,pin
    import devspace_execution as single
    config,root=fixture(tmp_path,profile);config.pop('devspace_study')
    config['devspace_execution']['plan']['argv']+=['--learning-rate','0.01'];pin(config)
    try:
        single.execution(config,root)
        process=json.loads((tmp_path/'remote/process.json').read_bytes())
        state=public(config,root,'--watch-devspace')
        assert state['status']=='awaiting-review' and state['stop_verified']
        assert json.loads((tmp_path/'remote/process.json').read_bytes())['started_at']==process['started_at']
        assert not (root/'main').exists()
    finally:
        if (root/'execution.json').exists():single.execution(config,root,force_cleanup=True)
