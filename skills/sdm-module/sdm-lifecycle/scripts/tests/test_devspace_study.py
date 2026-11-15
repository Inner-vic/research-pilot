"""Actual local CPU subprocess rounds; no SDK, GPU or message transport."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from test_devspace_execution import setup_case
import devspace_study as study
from task_pipeline import digest
import task_event_wakeup as wakeup

ENTRY = Path(study.__file__).with_name('run_project_pipeline.py')


def case(tmp_path, report_parameter=True):
    code = """import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--learning-rate',type=float);a=p.parse_args()
Path('progress.json').write_text(json.dumps({'optimizer_updates':2}))
time.sleep(.1)
Path('result.json').write_text(json.dumps({'score':a.learning_rate,'parameters':{'learning_rate':a.learning_rate},'data_digest':'fixed-data','checkpoint_verified':True}))
"""
    if not report_parameter: code = code.replace("'learning_rate':a.learning_rate", "'learning_rate':999")
    config, root = setup_case(tmp_path, code, duration=90)
    config['devspace_study'] = {'version':1, 'study_id':'cpu-study', 'objective':'Test real parameter continuation',
        'stop_criteria':'Two rounds then analyze', 'max_rounds':2, 'initial_parameters':{'learning_rate':.01},
        'allowed_variables':{'learning_rate':{'type':'number','minimum':.001,'maximum':.1}},
        'parameter_flags':{'learning_rate':'--learning-rate'}, 'result_artifact':'result.json',
        'fixed_fields':['data_digest'], 'metrics':['score'], 'required_values':{'checkpoint_verified':True}}
    config['devspace_study']['authorization'] = {'confirmed':True, 'study_digest':study.binding(config),
        'source_ref':'CPU acceptance user scope, not a human confirmation simulation'}
    return config, root


def public(config, root, watch=False):
    path = root.parent / 'project.json'; path.write_text(json.dumps(config))
    result = subprocess.run([sys.executable, '-B', str(ENTRY), '--project', str(path), '--output',str(root),
        '--devspace-execution'] + (['--watch-devspace'] if watch else []), capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def respond(state, action='run-next-round', params=None, changed_variable='learning_rate'):
    review = state['review']; packet = json.loads(Path(review['request_path']).read_bytes())
    key='r1.evaluation.score'
    if key not in packet['evidence']:
        key=next(k for k in packet['evidence'] if k.endswith('.evaluation.exit_code'))
    value=packet['evidence'][key]
    step={'action':'Execute the next declared round' if action=='run-next-round' else 'Finish the Study',
        'reason':'CPU evidence collected', 'acceptance':'Two distinct computations, each once',
        'authorization':'existing', 'status':'ready', 'stop_decision':'stop', 'stop_reason':'Current allocation stopped',
        'iteration':{'action':action}}
    if action in {'run-next-round','retry-round'}:step['iteration'].update(parameters=params or {'learning_rate':.02},
        changed_variable=changed_variable, hypothesis='Declared change modifies actual execution',
        expected_observations='Observed rate equals chosen rate')
    if action=='retry-round':step['iteration']['failed_attempt_binding']=state['binding']
    response={'request_digest':digest(packet), 'evidence_kind':'cpu-simulation', 'action':'stop-analysis',
        'facts':[{'evidence_id':key,'value':value}], 'hypotheses':[{'statement':'Actual subprocess output',
        'evidence_ids':[key],'limitations':'Generated local CPU acceptance, no platform evidence'}],
        'previous_experiment_assessment':'Subprocess completed', 'reason':'Grounded round assessment', 'next_step':step}
    Path(review['response_path']).write_text(json.dumps(response))


def test_public_two_round_actual_subprocess_and_resume_no_relaunch(tmp_path):
    config, root=case(tmp_path)
    first=public(config,root,True)
    assert first['active_round']==1 and first['status']=='awaiting-review' and first['stop_verified']
    respond(first)
    second=public(config,root,True)
    assert second['active_round']==2 and second['status']=='awaiting-review'
    assert second['history'][0]['parameters']=={'learning_rate':.01}
    observed=json.loads((root/'round-02/artifacts/result.json').read_bytes())
    assert observed['parameters']=={'learning_rate':.02}
    context=json.loads((root/'round-02/execution-evidence/study-context.json').read_bytes())
    assert json.loads(context['previous_results'])[0]['metrics']=={'score':.01}
    plans=[json.loads((root/f'round-{i:02d}/round-plan.json').read_bytes()) for i in (1,2)]
    assert plans[1]['parent_decision_digest']==digest(json.loads((root/'round-01/iteration-decision.json').read_bytes()))
    respond(second,'stop')
    done=public(config,root)
    assert done['status']=='completed' and len(done['history'])==2 and done['study_complete']
    processes=[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]
    result=(root/'study-result.json').read_bytes()
    public(config,root)
    assert processes==[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]
    assert result==(root/'study-result.json').read_bytes()
    with pytest.raises(ValueError,match='original_workspace'):
        study.execution(config,tmp_path/'other')


@pytest.mark.parametrize('params,error', [({'learning_rate':.2},'outside_bounds'), ({'learning_rate':.01},'exactly_one')])
def test_bad_next_decision_never_creates_or_launches_second_round(tmp_path,params,error):
    config,root=case(tmp_path); first=public(config,root,True);respond(first,params=params)
    blocked=study.execution(config,root)
    assert blocked['status']=='study-blocked' and error in blocked['reason']
    assert not (root/'round-02').exists() and not (tmp_path/'remote/r02').exists()


def test_scope_change_rejected_before_execution(tmp_path):
    config,root=case(tmp_path);config['devspace_study']['max_rounds']=3
    with pytest.raises(ValueError,match='scope_authorization'):study.execution(config,root)
    assert not root.exists()


def test_event_driven_study_blocks_next_round_until_exact_consumer_record(tmp_path):
    config,root=case(tmp_path);config['devspace_study']['event_driven_continuation']=True
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    first=public(config,root,True);respond(first)
    pending=public(config,root)
    assert pending['status']=='awaiting-event-consumer' and not (root/'round-02').exists()
    received={'dispatch_id':'test-cpu-fixture','received_at':1,'execution_binding':first['binding']}
    with patch.object(study,'terminal_consumer',return_value=received):
        next_round=study.execution(config,root)
    assert next_round['active_round']==2
    transition=json.loads((root/'round-01/round-transition.json').read_bytes())
    assert transition['event_consumer']==received and transition['next_result_pending']
    assert transition['next_action']['parameters']=={'learning_rate':.02}


def test_mismatched_observed_parameter_blocks_continuation(tmp_path):
    config,root=case(tmp_path,False);first=public(config,root,True);respond(first)
    blocked=study.execution(config,root)
    assert blocked['status']=='study-blocked' and 'observed_parameters' in blocked['reason']
    assert not (root/'round-02').exists()


@pytest.mark.parametrize('missing_result',[False,True])
def test_failed_round_without_metrics_is_reviewed_repaired_and_not_relaunched(tmp_path,missing_result):
    config,root=recovery_case(tmp_path)
    source=tmp_path/'workload.py'
    code=source.read_text()
    early="""if bad:
 import sys
 print('early arithmetic failure before final evaluation',flush=True)
"""
    if not missing_result:
        early+=" Path('result.json').write_text(json.dumps({'parameters':{'learning_rate':a.learning_rate,'checkpoint_schema':a.checkpoint_schema},'data_digest':'fixed-data','score':None,'checkpoint_verified':False}))\n"
    early+=" sys.exit(7)\n"
    code=code.replace("Path('progress.json')",early+"Path('progress.json')",1)
    source.write_text(code)
    config['devspace_execution']['plan']['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(config['devspace_execution']['plan'])
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    first=public(config,root,True);respond(first,params={'learning_rate':.02,'checkpoint_schema':'legacy'})
    failed=public(config,root,True)
    assert failed['status']=='awaiting-review' and failed['outcome']=='failed' and failed['stop_verified']
    assert failed['diagnostic_only'] and failed['required_artifacts_complete'] is (not missing_result)
    original=(tmp_path/'remote/r02/process.json').read_bytes()
    packet=json.loads(Path(failed['review']['request_path']).read_bytes())
    assert any(k.endswith('.evaluation.exit_code') and v==7 for k,v in packet['evidence'].items())
    assert not any(k.endswith('.evaluation.score') for k in packet['evidence'])
    respond(failed,'retry-round',{'learning_rate':.02,'checkpoint_schema':'current'},'checkpoint_schema')
    repaired=public(config,root,True)
    assert repaired['active_attempt']==2 and repaired['outcome']=='succeeded'
    failure_record=json.loads((root/'round-02/round-observation.json').read_bytes())
    assert failure_record['metrics']=={'score':None} and failure_record['diagnostic_only']
    respond(repaired,'stop');done=public(config,root)
    assert done['status']=='completed' and len(done['attempt_history'])==3
    again=public(config,root)
    assert again['study_complete'] and original==(tmp_path/'remote/r02/process.json').read_bytes()
    assert not (root/'round-02/attempt-03').exists()


def test_reviewed_plan_revision_runs_new_parameters_and_rejects_stale_change(tmp_path):
    config,root=case(tmp_path)
    config['devspace_study']['initial_plan_review_required']=True
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    pending=public(config,root)
    assert pending['status']=='awaiting-study-plan' and not (tmp_path/'remote').exists()
    request=json.loads(Path(pending['plan_request']).read_bytes())
    response={'request_digest':digest(request),'action':'revise','reason':'Review proposes higher baseline learning rate',
              'parameters':{'learning_rate':.015}}
    Path(pending['plan_response']).write_text(json.dumps(response))
    first=public(config,root,True)
    assert json.loads((root/'round-01/artifacts/result.json').read_bytes())['parameters']==response['parameters']
    original=(root/'study-plan-decision.json').read_bytes()
    response['parameters']['learning_rate']=.018
    Path(pending['plan_response']).write_text(json.dumps(response))
    with pytest.raises(ValueError,match='immutable_study_record_changed'):study.execution(config,root)
    assert (root/'study-plan-decision.json').read_bytes()==original


@pytest.mark.parametrize('bad_digest,rate,error',[(True,.02,'bound_study_plan_response'),(False,.2,'outside_bounds')])
def test_old_or_out_of_scope_plan_response_never_dispatches(tmp_path,bad_digest,rate,error):
    config,root=case(tmp_path);config['devspace_study']['initial_plan_review_required']=True
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    pending=public(config,root);request=json.loads(Path(pending['plan_request']).read_bytes())
    Path(pending['plan_response']).write_text(json.dumps({'request_digest':'old-request' if bad_digest else digest(request),
        'action':'revise','parameters':{'learning_rate':rate},'reason':'Proposed bounded revision'}))
    with pytest.raises(ValueError,match=error):study.execution(config,root)
    assert not (root/'round-01').exists() and not (tmp_path/'remote').exists()


def recovery_case(tmp_path):
    config,root=case(tmp_path)
    code="""import argparse,json,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--learning-rate',type=float);p.add_argument('--checkpoint-schema');a=p.parse_args()
bad=a.learning_rate>=.02 and a.checkpoint_schema=='legacy'
Path('progress.json').write_text(json.dumps({'optimizer_updates':2}))
Path('result.json').write_text(json.dumps({'score':a.learning_rate,'parameters':{'learning_rate':a.learning_rate,'checkpoint_schema':a.checkpoint_schema},'data_digest':'fixed-data','checkpoint_verified':not bad}))
if bad:
 Path('failure.json').write_text(json.dumps({'exception_type':'ValueError','marker':'controlled-checkpoint-schema','step':2}))
 raise ValueError('controlled checkpoint schema mismatch')
time.sleep(.1)
"""
    source=tmp_path/'workload.py';source.write_text(code)
    config['devspace_execution']['plan']['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(config['devspace_execution']['plan'])
    spec=config['devspace_study'];spec['max_attempts_per_round']=2
    spec['initial_parameters']['checkpoint_schema']='legacy'
    spec['allowed_variables']['checkpoint_schema']={'type':'enum','choices':['legacy','current']}
    spec['parameter_flags']['checkpoint_schema']='--checkpoint-schema'
    spec['authorization']['study_digest']=study.binding(config)
    return config,root


def test_real_cpu_round_failure_repair_new_attempt_preserves_previous_results(tmp_path):
    config,root=recovery_case(tmp_path)
    first=public(config,root,True)
    respond(first,params={'learning_rate':.02,'checkpoint_schema':'legacy'})
    failed=public(config,root,True)
    assert failed['active_round']==2 and failed['active_attempt']==1 and failed['outcome']=='failed' and failed['stop_verified']
    assert 'controlled checkpoint schema mismatch' in (root/'round-02/artifacts/stdout.log').read_text()
    first_hash=hashlib.sha256((root/'round-01/round-observation.json').read_bytes()).hexdigest()
    failed_process=(tmp_path/'remote/r02/process.json').read_bytes()
    respond(failed,'retry-round',{'learning_rate':.02,'checkpoint_schema':'current'},'checkpoint_schema')
    repaired=public(config,root,True)
    assert repaired['active_round']==2 and repaired['active_attempt']==2 and repaired['outcome']=='succeeded'
    assert (tmp_path/'remote/r02/process.json').read_bytes()==failed_process
    assert first_hash==hashlib.sha256((root/'round-01/round-observation.json').read_bytes()).hexdigest()
    respond(repaired,'stop')
    done=public(config,root)
    assert done['status']=='completed' and len(done['history'])==2 and len(done['attempt_history'])==3
    bad=done['attempt_history'][1]
    assert bad['outcome']=='failed' and bad['requirement_failures']==['checkpoint_verified']
    completed=(root/'study-result.json').read_bytes();public(config,root)
    assert completed==(root/'study-result.json').read_bytes()


def test_retry_requires_precise_failure_release_and_available_attempt_scope():
    spec={'max_rounds':2,'max_attempts_per_round':2,'allowed_variables':{'learning_rate':{'type':'number','minimum':.001,'maximum':.1}}}
    record={'parameters':{'learning_rate':.01},'outcome':'failed','exit_code':19,'stop_verified':True,
        'attempt':1,'execution_binding':'sha256:failed','review':{'next_step':{'action':'Repair','reason':'Stack inspected',
        'acceptance':'Real new result','authorization':'existing','status':'ready','stop_decision':'stop','stop_reason':'Failed allocation stopped',
        'iteration':{'action':'retry-round','parameters':{'learning_rate':.02},'changed_variable':'learning_rate','hypothesis':'Repair inside grant','expected_observations':'New success'}}}}
    with pytest.raises(ValueError,match='exact_failed_attempt_binding'):study.continuation(spec,record,[record])
    record['review']['next_step']['iteration']['failed_attempt_binding']='sha256:failed'
    record['stop_verified']=False
    with pytest.raises(ValueError,match='known_failed_released'):study.continuation(spec,record,[record])
    record['stop_verified']=True;record['attempt']=2
    with pytest.raises(ValueError,match='attempt_authorization_exhausted'):study.continuation(spec,record,[record])


def resume_case(tmp_path):
    config,root=recovery_case(tmp_path)
    source=tmp_path/'workload.py'
    source.write_text("""import argparse,json,hashlib,math
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--learning-rate',type=float);p.add_argument('--checkpoint-schema');p.add_argument('--resume-checkpoint');a=p.parse_args()
start=json.loads(Path(a.resume_checkpoint).read_bytes())['updates'] if a.resume_checkpoint else 0
updates=start+2
Path('checkpoint.json').write_text(json.dumps({'updates':updates}))
Path('progress.json').write_text(json.dumps({'optimizer_updates':updates}))
resume={'restored':bool(a.resume_checkpoint),'input_sha256':hashlib.sha256(Path(a.resume_checkpoint).read_bytes()).hexdigest() if a.resume_checkpoint else None,'start_updates':start,'end_updates':updates}
Path('result.json').write_text(json.dumps({'score':a.learning_rate,'parameters':{'learning_rate':a.learning_rate,'checkpoint_schema':a.checkpoint_schema},'data_digest':'fixed-data','checkpoint_verified':True,'resume':resume}))
if a.checkpoint_schema=='legacy':
 try:math.exp(1000)
 except OverflowError:
  Path('failure.json').write_text(json.dumps({'exception_type':'OverflowError','step':updates,'marker':'induced-arithmetic-overflow'}))
  raise
""")
    plan=config['devspace_execution']['plan'];plan['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    plan['artifacts'].append('checkpoint.json')
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    spec=config['devspace_study'];spec['max_rounds']=1
    spec['checkpoint_resume']={'artifact':'checkpoint.json','input_name':'resume.json','flag':'--resume-checkpoint'}
    spec['authorization']['study_digest']=study.binding(config)
    return config,root


def resume_response(state, root, wrong_hash=False):
    respond(state,'retry-round',{'learning_rate':.01,'checkpoint_schema':'current'},'checkpoint_schema')
    path=Path(state['review']['response_path']);response=json.loads(path.read_bytes())
    checksum=hashlib.sha256((root/'round-01/artifacts/checkpoint.json').read_bytes()).hexdigest()
    response['next_step']['iteration']['resume']={'from_execution_binding':state['binding'],
        'artifact_sha256':'0'*64 if wrong_hash else checksum}
    path.write_text(json.dumps(response))


def test_public_checkpoint_handoff_restores_real_state_and_deduplicates(tmp_path):
    config,root=resume_case(tmp_path);failed=public(config,root,True)
    original=(root/'round-01/artifacts/checkpoint.json').read_bytes()
    assert failed['outcome']=='failed' and failed['process']['exit_code']!=0
    resume_response(failed,root)
    repaired=public(config,root,True)
    actual=json.loads((root/'round-01/attempt-02/artifacts/result.json').read_bytes())
    assert actual['resume']['restored'] and actual['resume']['start_updates']==2 and actual['resume']['end_updates']==4
    assert actual['resume']['input_sha256']==hashlib.sha256(original).hexdigest()
    assert (root/'round-01/artifacts/checkpoint.json').read_bytes()==original
    respond(repaired,'stop');done=public(config,root)
    assert done['study_complete'] and done['attempt_history'][1]['resume']['start_updates']==2
    before=(root/'study-result.json').read_bytes();public(config,root)
    assert (root/'study-result.json').read_bytes()==before


def test_wrong_checkpoint_binding_blocks_before_new_attempt(tmp_path):
    config,root=resume_case(tmp_path);failed=public(config,root,True);resume_response(failed,root,True)
    blocked=public(config,root)
    assert blocked['status']=='study-blocked' and 'registered_checkpoint' in blocked['reason']
    assert not (root/'round-01/attempt-02').exists()


def test_changed_checkpoint_never_reaches_new_resource_action(tmp_path):
    config,root=resume_case(tmp_path);failed=public(config,root,True);resume_response(failed,root)
    (root/'PAUSE').touch();assert public(config,root)['status']=='paused'
    (root/'round-01/artifacts/checkpoint.json').write_text('{"updates":999}')
    (root/'PAUSE').unlink()
    result=subprocess.run([sys.executable,'-B',str(ENTRY),'--project',str(root.parent/'project.json'),
        '--output',str(root),'--devspace-execution'],capture_output=True,text=True)
    state=json.loads(result.stdout)
    assert state['status']=='awaiting-reconciliation' and state['last_error']=='collected_artifact_changed'
    assert not (tmp_path/'remote/r01/attempt-02').exists()


def test_unproven_restore_is_not_success(tmp_path):
    config,root=resume_case(tmp_path)
    source=tmp_path/'workload.py';source.write_text(source.read_text().replace("'restored':bool(a.resume_checkpoint)","'restored':False"))
    plan=config['devspace_execution']['plan'];plan['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    failed=public(config,root,True);resume_response(failed,root);repaired=public(config,root,True);respond(repaired,'stop')
    blocked=public(config,root)
    assert blocked['status']=='study-blocked' and 'restore_evidence' in blocked['reason']


@pytest.mark.parametrize('invalid',['source','hash','not-retry','undeclared'])
def test_resume_decision_rejects_unbound_sources_and_allows_no_parameter_change(tmp_path,invalid):
    config,root=resume_case(tmp_path);spec=config['devspace_study']
    step={'action':'Resume the diagnosed interruption','reason':'Evidence inspected','acceptance':'Updates increase',
          'authorization':'existing','status':'ready','stop_decision':'stop','stop_reason':'Allocation released',
          'iteration':{'action':'retry-round','parameters':spec['initial_parameters'],'changed_variable':None,
                       'failed_attempt_binding':'failure','hypothesis':'Transient failure removed',
                       'expected_observations':'Same training state continues',
                       'resume':{'from_execution_binding':'failure','artifact_sha256':'1'*64}}}
    record={'parameters':spec['initial_parameters'],'outcome':'failed','exit_code':1,'stop_verified':True,
            'attempt':1,'execution_binding':'failure','checkpoint':{'sha256':'1'*64},'review':{'next_step':step}}
    assert study.continuation(spec,record,[])['changed_variable'] is None
    if invalid=='source':step['iteration']['resume']['from_execution_binding']='other'
    elif invalid=='hash':step['iteration']['resume']['artifact_sha256']='2'*64
    elif invalid=='not-retry':step['iteration']['action']='run-next-round'
    else:spec.pop('checkpoint_resume')
    with pytest.raises(ValueError,match='registered_checkpoint_resume'):study.continuation(spec,record,[])


def test_pause_after_grounded_decision_then_resume_same_rounds(tmp_path):
    config,root=case(tmp_path);first=public(config,root,True);respond(first)
    (root/'PAUSE').touch()
    assert public(config,root)['status']=='paused' and not (root/'round-02').exists()
    (root/'PAUSE').unlink()
    assert public(config,root,True)['active_round']==2


def test_exhausted_failed_and_missing_structured_decisions_block():
    spec={'max_rounds':1}
    record={'outcome':'succeeded','exit_code':0,'parameters':{'learning_rate':.01},'review':{'next_step':{
        'action':'Continue', 'reason':'Evidence', 'acceptance':'Two rounds', 'authorization':'existing',
        'status':'ready','stop_decision':'stop','stop_reason':'Current resource stopped'}}}
    with pytest.raises(ValueError,match='structured'):study.continuation(spec,record,[record])
    record['review']['next_step']['iteration']={'action':'run-next-round'}
    with pytest.raises(ValueError,match='exhausted'):study.continuation(spec,record,[record])
    record['outcome']='failed'
    with pytest.raises(ValueError,match='failed_study'):study.continuation(spec,record,[record])


def test_root_notification_owner_preserves_exact_persisted_project_contract(tmp_path):
    config,root=case(tmp_path)
    from test_devspace_execution import native_case
    (tmp_path/'native').mkdir()
    native,_=native_case(tmp_path/'native')
    config['devspace_execution']=native['devspace_execution']
    cli=tmp_path/'codex.exe';cli.touch()
    config['task_event_wakeup']={'version':1,'enabled':True,'thread_id':'00000000-0000-4000-8000-000000000001',
        'codex_home':str(tmp_path),'cli_path':str(cli),'project_path':str(tmp_path/'project.json'),
        'coordinator_path':str(ENTRY),'resource_kind':'devspace'}
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    (tmp_path/'project.json').write_text(json.dumps(config))
    owners=[]
    def check(owner,db):
        owners.append(owner);return wakeup.settings(owner)
    # Synthetic live-route event fixture, like wakeup contract tests; no SDK,
    # SSH or actual queue is called. Local-cpu dispatch protection stays intact.
    child=study.round_config(config,1,config['devspace_study']['initial_parameters'],None)
    import task_event_collector as collector
    db=collector.connect(collector.validate(config),'live-platform-observation');db.close()
    state={'binding':digest(child['devspace_execution']['plan']),
           'uid':child['devspace_execution']['plan']['resource']['uid'],'evidence_kind':'live-platform-observation'}
    with patch.object(wakeup,'dispatch',side_effect=check):
        for kind in ['resource_ready','process_terminal','artifacts_collected','review_ready']:
            study.single.emit(child,root,state,kind,{},notification_config=config)
    assert owners and all(owner==config for owner in owners)
    with pytest.raises(ValueError,match='persisted_project_binding_mismatch'):
        wakeup.settings(child)  # no relaxation of the original security contract
    wrong=json.loads(json.dumps(config));wrong['task_event_collector']['store']+='-other'
    with pytest.raises(ValueError,match='owner_binding_mismatch'):
        study.single.notification_owner(child,wrong)


def test_start_failure_recovers_original_round_with_real_bound_receipt(tmp_path):
    from test_devspace_execution import native_case,action_receipt
    config,root=case(tmp_path)
    (tmp_path/'native').mkdir()
    native,_=native_case(tmp_path/'native')
    config['devspace_execution']=native['devspace_execution']
    config['task_event_collector']=native['task_event_collector']
    config['task_event_service']=native['task_event_service']
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    phase=['stopped']
    def observe(*args):return {'uid':config['devspace_execution']['plan']['resource']['uid'],'phase':phase[0]}
    with patch.object(study.single,'resource_call',side_effect=observe):
        first=study.execution(config,root,allow_live=True)
        folder=Path(first['execution_root']);saved=json.loads((folder/'execution.json').read_bytes())
        saved.update(cleanup_required=True,last_error='persisted_project_binding_mismatch',
                     original_failure={'error':'persisted_project_binding_mismatch'})
        (folder/'execution.json').write_text(json.dumps(saved))
        (root/'execution.json').unlink()  # exception before Study wrapper saved
        receipt=action_receipt(folder,'start');phase[0]='pending'
        recovered=study.execution(config,root,receipt,True)
    assert recovered['active_round']==1 and recovered['status']=='awaiting-start-readback'
    assert recovered['input_error_reconciled'] and not recovered.get('cleanup_required')
    assert recovered['original_failure']['error']=='persisted_project_binding_mismatch'
    assert not (root/'round-02').exists()
