"""Focused repros of actual continuation gaps. No ML/models/platform calls."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from component_pipeline import ComponentPipeline
from component_research import advance
from component_payload import ref
from research_continuation import failed, observed, start_owner
from research_event_service import configuration
from research_progress import project
from review_storage import atomic_json, read_json
from task_pipeline import digest
from test_research_event_service import setup
from test_component_research_continuation import required


def cfg(root):
    return {'schema':'sdm-event-service/v3','project_id':'control','root':str(root),
        'component_pipeline':{'version':4},'continuation_policy':{'version':1}}


def owner(tmp_path,stage='watching'):
    inbox=SimpleNamespace(cfg=cfg(tmp_path),root=tmp_path,done=threading.Event(),condition=threading.Condition())
    p=ComponentPipeline.__new__(ComponentPipeline);p.inbox=inbox;p.root=tmp_path/'pipeline'
    state={'status':'running','rounds':[],'attempts':[{'id':'initial','stage':stage,'source':{},'poll_at':0}]}
    p.load=lambda:state;p.save=Mock();return p,state


def test_unknown_submit_is_never_presented_as_healthy_queue(tmp_path):
    c=cfg(tmp_path);state={'config_digest':digest(c),'status':'running','rounds':[],
        'attempts':[{'id':'initial','stage':'watching','observation':'submission-outcome-unknown'}]}
    view=project(c,state)
    assert view['needs_intervention'] and view['intervention_reason']=='submission-outcome-unknown'
    assert view['automatic_resubmit'] is False


def test_unknown_observation_can_resolve_without_creating_another_request():
    current={'stage':'watching','source':{'plan':{'sha256':'same-original'}},'poll_at':100}
    before=json.dumps(current['source']);observed(current,'submission-outcome-unknown')
    assert current['continuation']['next_action']=='read-original-identity'
    observed(current,'running')
    assert 'continuation' not in current and json.dumps(current['source'])==before


def test_retry_is_persisted_and_resume_does_not_turn_60_seconds_into_15(tmp_path,monkeypatch):
    p,state=owner(tmp_path);waits=[]
    class Condition:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def wait(self,timeout):waits.append(timeout);p.inbox.done.set()
    p.inbox.condition=Condition();p.step=Mock(side_effect=ValueError('complete_component_result_required'))
    monkeypatch.setattr('component_pipeline.validate',lambda c:None)
    before=time.time();p.run();value=state['attempts'][0]['continuation']
    assert value['phase']=='retry-wait' and value['retry_at']>=before+29
    assert 29<=waits[0]<=30
    assert state['attempts'][0]['stage']=='watching' and p.step.call_count==1
    assert p.save.call_args.kwargs['health']['status']=='retry-wait'


def test_blocked_research_owner_stays_alive_and_can_receive_control(tmp_path,monkeypatch):
    p,state=owner(tmp_path,'needs-attention');state['attempts'][0]['reason']='authentication-blocked'
    entered=threading.Event();p.save=Mock(side_effect=lambda *a,**k:entered.set())
    monkeypatch.setattr('component_pipeline.validate',lambda c:None)
    thread=threading.Thread(target=p.run,daemon=True);thread.start()
    assert entered.wait(2)
    try:assert thread.is_alive() and state['attempts'][0]['continuation']['phase']=='blocked'
    finally:
        p.inbox.done.set()
        with p.inbox.condition:p.inbox.condition.notify_all()
        thread.join(2)
    assert not thread.is_alive()


def test_unexpected_owner_exit_shuts_down_http_only_host(tmp_path,monkeypatch):
    inbox=SimpleNamespace(cfg=cfg(tmp_path),root=tmp_path,done=threading.Event(),condition=threading.Condition())
    class BrokenOwner:
        def __init__(self,inbox):pass
        def run(self):return
        def load(self):return {'status':'running'}
    monkeypatch.setattr('component_pipeline.ComponentPipeline',BrokenOwner)
    shutdown=threading.Event();thread=start_owner(inbox,SimpleNamespace(shutdown=shutdown.set));thread.join(2)
    assert inbox.done.is_set() and shutdown.wait(2)
    assert read_json(tmp_path/'component-owner.json')['status']=='failed'


def test_control_binding_survives_missing_compute_workspace(tmp_path):
    c,_=setup(tmp_path);c['cwd']=str(tmp_path/'moved-workspace')
    assert configuration(c,control_only=True)==c
    with pytest.raises(ValueError,match='existing_execution_workspace'):configuration(c)


def test_first_failure_enters_original_planning_gate_without_scoring_or_new_authority(tmp_path,monkeypatch):
    diagnosis=tmp_path/'failed.json';atomic_json(diagnosis,{'schema':'sdm-component-diagnosis-ready/v1','uid':'failed-original',
        'platform_status':'failed','valid_training_round':False,'logs':['model shape failure']})
    c={'platform_requirements':required(tmp_path),'research_replan':{'version':2,'parent_route':'lpai-component',
        'result_contract':'sdm-binary-classification/v1','objective':'repair the authorized initial preparation',
        'rounds':[],'parent_diagnosis':ref(diagnosis),'preparation_recovery':{'version':1},'sources':[]},
        'proposal_review':{'version':4,'backend':'model','context_required':True,'models':{'a':'proposer','b':'critic'}}}
    calls=[]
    def gate(config,root,evidence_query=None):
        context=read_json(Path(root)/'review-context.json');calls.append(context)
        assert context['configuration']['goal_progress']['valid_training_rounds']==0
        return {'status':'awaiting-proposal-evidence'}
    monkeypatch.setattr('proposal_review.gate',gate)
    result=advance(c,tmp_path/'preparation');assert result['status']=='awaiting-proposal-evidence' and len(calls)==1
    c['research_replan'].pop('preparation_recovery');result=advance(c,tmp_path/'legacy')
    assert result['status']=='initial-baseline-execution-failed' and len(calls)==1


def test_venv_bootstrap_preserves_exact_guard_pid_and_startup_barrier(tmp_path,monkeypatch):
    import os,sys,subprocess
    from model_runner import ModelRunner
    from research_model_receipts import seal,checked
    if os.name!='nt':pytest.skip('exact Windows process regression')
    folder=tmp_path/'call';folder.mkdir();expected={'control':'actual local process, no models'}
    atomic_json(folder/'request.json',seal({'schema':'sdm-model-runner-envelope/v1','binding':expected}))
    target=tmp_path/'ready.py'
    target.write_text("from pathlib import Path\nimport sys,os,time\nfrom review_storage import atomic_json\nfrom research_model_receipts import checked,seal\np=Path(sys.argv[1]);j=checked(p/'guard-joined.json','sdm-model-guard-joined/v1')\natomic_json(p/'backend-ready.json',seal({'schema':'sdm-model-backend-ready/v1','binding':j['binding'],'job_name':j['job_name'],'helper_pid':os.getpid()}))\nend=time.monotonic()+4\nwhile not (p/'backend-accepted.json').exists() and time.monotonic()<end:time.sleep(.025)\nassert checked(p/'backend-accepted.json','sdm-model-backend-accepted/v1')['helper_pid']==os.getpid()\n",encoding='utf-8')
    native=subprocess.Popen;created=[]
    def launch(argv,**kwargs):
        assert Path(argv[0]).resolve()==Path(sys._base_executable).resolve()
        argv=list(argv);argv[-2]=str(target);p=native(argv,**kwargs);created.append(p);return p
    runner=ModelRunner.__new__(ModelRunner);runner.config={'version':4,'sdk_recovery':{'version':1},'startup_timeout_seconds':5,'timeout_seconds':30,'cwd':str(tmp_path)}
    monkeypatch.setattr('model_runner.subprocess.Popen',launch)
    runner._launch_contained(folder,os.environ.copy())
    accepted=checked(folder/'backend-accepted.json','sdm-model-backend-accepted/v1')
    assert accepted['helper_pid']==created[0].pid
    assert checked(folder/'containment.json','sdm-model-containment/v1')['local_empty_verified']


def test_rejected_plan_preserves_actual_objections_and_call_ids_do_not_fake_progress(tmp_path):
    from research_continuation import attention
    from proposal_review import FIELDS
    base=tmp_path/'review';base.mkdir()
    proposal={key:'same scientific content '+key for key in FIELDS}
    proposal.update(research_revision={'action':'revise-plan','intervention':'same actual intervention','evidence_ids':['source-1']},
        proposer={'call_ref':'first-call'},subject_digest='first-subject',revision=1)
    critic={'verdict':'reject','rationale':'held-out evaluation must stay protected',
        'challenges':[{'id':'first-id','severity':'blocker','question':'Why use TEST for selection?',
        'basis':'original held-out contract','resolution_criterion':'Remove TEST from selection'}]}
    atomic_json(base/'proposal.json',proposal);atomic_json(base/'rejected.json',{'approved':False,'response':critic})
    owner=SimpleNamespace(root=tmp_path,completed=Mock(return_value={'coordinator_result':{'status':'proposal-rejected','receipt':str(base/'rejected.json')}}),replan=Mock())
    state={};current={'reason':'original-plan-review:proposal-rejected','job':'first-job','replan_root':str(base),'result':{'sha256':'same-original-result'}}
    assert attention(owner,state,current,base)
    feedback=read_json(state['continuation_history'][0]['feedback']['path'])
    challenge=feedback['problem']['original_critic']['challenges'][0]
    assert challenge['question']=='Why use TEST for selection?' and challenge['resolution_criterion']=='Remove TEST from selection'
    assert feedback['critic_receipt']['path']==str((base/'rejected.json').resolve())
    assert read_json(base/'rejected.json')['approved'] is False and owner.replan.call_count==1
    # A different call/subject/revision/challenge identifier adds no evidence.
    proposal.update(proposer={'call_ref':'second-call'},subject_digest='second-subject',revision=2)
    proposal['research_revision']['evidence_ids']=['source-renumbered'];critic['challenges'][0]['id']='second-id'
    atomic_json(base/'proposal.json',proposal);atomic_json(base/'rejected.json',{'approved':False,'response':critic})
    current.update(stage='needs-attention',reason='original-plan-review:proposal-rejected',job='second-job',continuation={})
    assert attention(owner,state,current,base) is False
    assert current['continuation']['next_action']=='new-evidence-required' and owner.replan.call_count==1


def test_sdk_read_error_transport_keeps_safe_status_and_never_retries_mutation(tmp_path,monkeypatch):
    from lpai_manager_backend import OfficialBackend,OfficialReadFailure
    from research_continuation import category
    from lpai_manager_backend import payload,ProviderResponseFailure
    with pytest.raises(ProviderResponseFailure) as safe:payload({'code':503,'error':'must-not-escape-provider-text'})
    assert safe.value.status_code==503 and str(safe.value)=='provider_error_response'
    backend=OfficialBackend({'namespace':'ns','owner':'me','state_root':str(tmp_path),'official_cli':'lpai-agent'})
    status=[503]
    def worker(argv,**kwargs):
        source=Path(argv[-1]);spec=read_json(source)
        body={'status':'error','error':'RuntimeError','provider_status_code':status[0],
            'input_sha256':__import__('hashlib').sha256(source.read_bytes()).hexdigest(),'submission_outcome_known':False}
        atomic_json(source.with_suffix('.result.json'),body)
        return SimpleNamespace(returncode=1,stdout=json.dumps(body))
    monkeypatch.setattr('lpai_manager_backend.subprocess.run',worker)
    with pytest.raises(OfficialReadFailure) as failure:backend.sdk_call('read',{'uid':'original'})
    assert failure.value.status_code==503 and category(failure.value)=='transient-read'
    status[0]=403
    with pytest.raises(OfficialReadFailure) as failure:backend.sdk_call('read',{'uid':'original'})
    assert category(failure.value)=='authentication-blocked'
    status[0]=503
    with pytest.raises(ValueError,match='official_sdk_worker_request_failed') as failure:backend.sdk_call('mutate',{'request':{'action':'task.submit'}})
    assert not isinstance(failure.value,OfficialReadFailure)
    assert read_json(backend.last_mutation_receipt['path'])['submission_outcome_known'] is False


def test_lost_submit_ack_reenters_only_the_existing_guard_reconciliation(tmp_path,monkeypatch):
    from lpai_manager import Manager
    from research_continuation import submission_intent
    p,state=owner(tmp_path,'initial-submitting');current=state['attempts'][0]
    plan=tmp_path/'original-plan.json';atomic_json(plan,{'request':{'uid':'component','request_key':'same-original-intent'}})
    auth=tmp_path/'auth.json';atomic_json(auth,{'scope':'control only, never submitted'})
    current['source']={'plan':ref(plan)};p.manager_config={}
    manager=Manager.__new__(Manager);manager.config={'namespace':'ns','owner':'owner','state_root':str(tmp_path/'state'),'submission_registry':str(tmp_path/'registry')}
    guard=manager.guard(read_json(plan))
    with guard.lock():guard.load();guard.before_submit(tmp_path/'original-receipt.json')
    before=guard.path.read_bytes()
    manager.apply=Mock(side_effect=AssertionError('original POST must never repeat'));manager.reconcile=Mock(return_value={'status':'submission-outcome-unresolved'})
    monkeypatch.setattr('lpai_manager.Manager',lambda config:manager)
    error=ValueError('write_outcome_unverified_reconcile_only');proof=submission_intent(p,current,error)
    value=failed(current,error,original_intent=proof)
    assert current['stage']=='initial-submitting' and value['phase']=='reconciling'
    current['continuation']['retry_at']=0;p.spec={'initial_submission':{'authorization':ref(auth)}}
    assert p.step(state)==0 and current['stage']=='watching'
    assert manager.apply.call_count==0 and manager.reconcile.call_args.args==(str(plan),)
    assert guard.path.read_bytes()==before
    no_intent={'stage':'submitting'};failed(no_intent,error)
    assert no_intent['stage']=='needs-attention'


def test_repeated_capability_claim_cannot_gain_progress_by_new_receipt_paths(tmp_path):
    p,state=owner(tmp_path,'freezing');current=state['attempts'][0]
    plan=tmp_path/'plan.json';atomic_json(plan,{'scope':'local control only'});current.update(science_plan=ref(plan),result={'sha256':'same-actual-parent'})
    def report(index):
        contract=tmp_path/('contract-'+str(index)+'.json')
        atomic_json(contract,{'model_api':'same protected interface','next_round':2,
            'protected_inputs':[{'path':'different-copy-'+str(index),'sha256':'same-protected-bytes'}]})
        current['execution_contract']=ref(contract)
        path=tmp_path/('feedback-'+str(index)+'.json');atomic_json(path,{'schema':'sdm-implementation-capability-gap/v1',
            'approved_plan':current['science_plan'],'execution_contract':current['execution_contract'],
            'native_completion':{'job_id':'different-completed-job-'+str(index)},'reported_kind':'capability',
            'implementer_claim':'extra evaluation hook unavailable','execution_authority':False})
        return {'feedback':ref(path)}
    p.replan=Mock();p.capability_replan(state,current,tmp_path,report(1))
    assert p.replan.call_count==1
    p.capability_replan(state,current,tmp_path,report(2))
    assert current['stage']=='needs-attention' and p.replan.call_count==1
    assert current['continuation']['next_action']=='independent-host-diagnosis-required'
    assert len(state['implementation_block_history'])==2
