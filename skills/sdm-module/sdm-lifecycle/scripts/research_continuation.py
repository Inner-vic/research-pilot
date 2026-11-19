"""Reason-driven recovery inside the existing frozen research owner.

No provider writes/model calls. This module never owns a second task store,
changes authorization, clears an unknown intent, or counts scientific trials.
"""
import hashlib
import re
import threading
import time
from pathlib import Path
from review_storage import atomic_json, read_json, native_path
from task_pipeline import digest

UNKNOWN = {'submission-outcome-unknown','submission-outcome-unresolved'}
APPROVAL = {'pending-approval','pending_approval','pending-approval-unresolved'}
TRANSIENT = {'complete_component_result_required','component_result_log_read_unavailable',
    'component_result_log_not_ok','component_log_window_required','component_terminal_pod_not_ready'}


def enabled(cfg):
    value=cfg.get('continuation_policy')
    if value is None:return False
    if value!={'version':1} or cfg.get('schema')!='sdm-event-service/v3' or cfg.get('component_pipeline',{}).get('version')!=4:
        raise ValueError('explicit_frozen_continuation_policy_v1_required')
    return True


def code(error):
    text=str(error)
    return text if re.fullmatch(r'[a-z][a-z0-9_:-]{0,180}',text) else type(error).__name__


def category(error):
    text=code(error).lower()
    status=getattr(error,'status_code',None) or getattr(error,'code',None)
    if any(x in text for x in ('changed','mismatch','integrity','tamper','digest')):return 'integrity-blocked'
    if status in (401,403) or any(x in text for x in ('credential','unauthorized','forbidden','permission')):return 'authentication-blocked'
    if any(x in text for x in ('unknown','uncertain','reconcile_only','pending_turn')):return 'original-outcome-unknown'
    if getattr(error,'read_only',False) and getattr(error,'retryable',False):return 'transient-read'
    if status in (408,429,500,502,503,504) or text in TRANSIENT or any(x in text for x in ('timeout','connection','transport','temporarily','unavailable','log_read')):
        return 'transient-read'
    return 'prerequisite-blocked'


def failed(current,error,now=None,original_intent=None):
    now=time.time() if now is None else now
    kind=category(error);previous=current.get('continuation',{})
    count=previous.get('consecutive_failures',0)+1 if previous.get('error_code')==code(error) else 1
    value={'schema':'sdm-research-continuation/v1','category':kind,'error_code':code(error),
        'consecutive_failures':count,'at':now,'resume_stage':previous.get('resume_stage',current['stage']),
        'automatic_resubmit':False,'model_retry_authorized':False}
    if (kind=='original-outcome-unknown' and original_intent
            and current['stage'] in {'initial-submitting','submitting'}):
        value.update(phase='reconciling',retry_at=now+60,next_action='read-original-identity',
            original_intent=original_intent,needs_intervention=True)
    elif kind=='transient-read':
        delay=min(900,30*2**min(count-1,5));value.update(phase='retry-wait',retry_at=now+delay,next_action='read-original-identity')
    else:
        value.update(phase='blocked',next_action='reconcile-original-identity' if kind=='original-outcome-unknown' else 'resolve-exact-prerequisite')
        current.update(stage='needs-attention',reason=kind+':'+code(error))
    current['continuation']=value
    return value



def submission_intent(owner,current,error):
    if category(error)!='original-outcome-unknown' or current['stage'] not in {'initial-submitting','submitting'}:return None
    from component_research import reference
    from lpai_manager import Manager
    selected=current['source']['plan'] if current['stage']=='initial-submitting' else current['manager_plan']
    plan=read_json(reference(selected));guard=Manager(owner.manager_config).guard(plan)
    with guard.lock():
        guard.load();phase=guard.state['phase']
        if phase not in {'intent','pending-approval','completed'}:return None
        return {'plan':selected,'guard_phase':phase,'request_key':plan['request']['request_key'],
            'action':'same original Guard; reconcile only, never a new submit identity'}

def observed(current,status):
    if status in UNKNOWN|APPROVAL:
        current['continuation']={'schema':'sdm-research-continuation/v1','phase':'reconciling',
            'category':'original-outcome-unknown' if status in UNKNOWN else 'platform-approval-pending',
            'next_action':'read-original-identity','retry_at':current.get('poll_at'),
            'needs_intervention':True,'automatic_resubmit':False,'model_retry_authorized':False}
    elif current.get('continuation',{}).get('phase') in {'retry-wait','reconciling'}:
        current.pop('continuation',None)


def take_resume(inbox,current,owner=None):
    path=inbox.root/'pipeline-resume.json'
    if not path.exists():return
    request=read_json(path)
    if str(request.get('config_sha256','')).removeprefix('sha256:')!=digest(inbox.cfg).removeprefix('sha256:'):raise ValueError('pipeline_resume_identity_changed')
    if request.get('id')==current.get('consumed_resume_id'):return
    review=request.get('review')
    if review:
        if owner is None or review['original_job']!=current.get('job'):raise ValueError('resume_original_review_job_changed')
        from research_pause import active_review
        selected=active_review(inbox.root,current)
        if not selected or tuple(str(p) for p in selected[:2])!=(review['project'],review['output']) or selected[2:]!=(review['stage'],review['mode']):
            raise ValueError('resume_original_review_scope_changed')
        if digest(read_json(review['receipt']))!=review['proof_digest']:raise ValueError('resume_review_proof_changed')
        from component_payload import ref
        current['job']=owner.enqueue('user-resume:'+request['id'],owner.spec['coordinator_template'],
            {'mode':review['mode'],'project':ref(Path(review['project'])),'output':review['output']})
        current.update(stage=review['stage']);current.pop('reason',None);current.pop('continuation',None)
        current['consumed_resume_id']=request['id'];return
    current['consumed_resume_id']=request['id']
    if request.get('scope')=='user-pause-only':return
    value=current.get('continuation',{})
    # A click cannot manufacture new evidence or settle an unknown external call.
    if value.get('phase')!='blocked' or value.get('next_action')=='new-evidence-required':return
    previous=value.get('resume_stage')
    if previous and previous!='needs-attention':
        current['stage']=previous;current.pop('reason',None);current.pop('continuation',None)



def original_review_runner(policy,saved):
    """Reconstruct the exact existing per-role runner, never trust arbitrary saved config."""
    from model_runner import binding
    runner=policy['model_runner'];request=saved['request']
    if request.get('working_session'):
        from working_sessions import select,context_config
        role='a' if request.get('purpose') in ('proposal','result-analysis') else 'b'
        runner,selected=select(policy,runner,role)
        if request['working_session']!=selected:
            raise ValueError('review_reconciliation_original_config_changed')
        context_config(runner,request)
    if saved['config']!=runner or saved['binding']!=binding(runner,request):
        raise ValueError('review_reconciliation_original_config_changed')
    return runner


def reconcile_review(owner,state,current):
    """Read original completed SDK turns, then re-enter the same gate."""
    reason=current.get('reason','')
    if reason not in {'original-plan-review:proposal-model-blocked','original-code-review:proposal-model-blocked',
            'original-initial-review:proposal-model-blocked'}:return None
    initial_review=reason.startswith('original-initial-review:')
    code_review=initial_review or reason.startswith('original-code-review:')
    root=Path(current['code_review_root'] if code_review else current['replan_root'])
    project=Path(current['code_review_project']) if code_review else root.parent/'replan-project.json'
    config=read_json(project);runner=config['proposal_review']['model_runner']
    if runner.get('sdk_recovery')!={'version':2}:return None
    from model_runner_recovery import recover
    from model_runner import binding
    from research_model_receipts import checked
    proofs=[];unresolved=[];retry=[]
    for request in root.glob('proposal-review/*/model-calls/*/request.json'):
        folder=request.parent;saved=checked(request,'sdm-model-runner-envelope/v1')
        actual_runner=original_review_runner(config['proposal_review'],saved)
        if not native_path(folder/'intent.json').exists():continue
        if not native_path(folder/'backend-completed.json').exists():
            if not recover(actual_runner,saved['request'],folder,saved['binding']):unresolved.append(str(folder))
        marker=folder/'reconciliation.json'
        if native_path(marker).exists():
            value=read_json(marker)
            if value.get('status')=='recovered-original-completion':
                proofs.append({'path':str(marker),'sha256':hashlib.sha256(native_path(marker).read_bytes()).hexdigest()})
            elif value.get('retry_at'):retry.append(value['retry_at'])
    if unresolved:
        current['continuation']={'phase':'reconciling','category':'original-model-outcome-unresolved',
            'next_action':'read-original-thread-and-turn','retry_at':min(retry) if retry else time.time()+60,
            'unresolved_calls':unresolved,'automatic_resubmit':False,'model_retry_authorized':False}
        return False
    if not proofs:return None
    fingerprint=digest(proofs);history=state.setdefault('model_reconciliation_history',[])
    if any(row['fingerprint']==fingerprint for row in history):return None
    original_job=current['job']
    job=owner.enqueue('reconciled-review:'+fingerprint,owner.spec['coordinator_template'],
        {'mode':'proposal-review' if code_review else 'research-replan','project':{'path':str(project),
        'sha256':hashlib.sha256(native_path(project).read_bytes()).hexdigest()},'output':str(root)})
    history.append({'fingerprint':fingerprint,'proofs':proofs,'original_job':original_job,'continuation_job':job,'at':time.time(),'resend':False})
    current.update(job=job,stage='reviewing-initial' if initial_review else 'reviewing-code' if code_review else 'reviewing-plan',
        continuation={'phase':'original-completion-recovered','proofs':proofs,'automatic_resubmit':False})
    current.pop('reason',None)
    return True

def attention(owner,state,current,folder):
    """A known rejected proposal may seed a NEW reviewed plan, not be approved."""
    reason=current.get('reason','')
    value=current.get('continuation',{})
    reconciled=reconcile_review(owner,state,current)
    if reconciled is not None:return reconciled
    if value.get('phase')=='blocked':return False
    eligible=(reason.startswith('original-plan-review:') or reason.startswith('original-code-review:')
        or reason in {'implementation-validation-stalled','implementation-code-review-stalled',
            'completed-correction-made-no-source-progress','same_capability_gap_requires_host_resolution'})
    if not eligible:
        current['continuation']={'phase':'blocked','category':'prerequisite-blocked','next_action':'resolve-exact-prerequisite','automatic_resubmit':False}
        return False
    detail=owner.completed(current['job'])
    if detail is None:return False
    result=detail.get('coordinator_result',{})
    if result.get('status')=='proposal-input-blocked':
        proof=read_json(result['preflight_receipt'])
        if (proof.get('schema')!='sdm-model-input-preflight/v1' or proof.get('status')!='not-issued'
                or (Path(proof['call'])/'intent.json').exists()):
            raise ValueError('unissued_model_input_preflight_proof_required')
        current['continuation']={'phase':'blocked','category':'model-input-capacity',
            'next_action':'repair-lossless-input-packing','preflight_receipt':result['preflight_receipt'],
            'automatic_resubmit':False,'model_retry_authorized':False}
        return False
    if result.get('status')=='proposal-model-blocked' and not result.get('known_response_preserved'):
        current['continuation']={'phase':'blocked','category':'original-model-outcome-unresolved',
            'next_action':'reconcile-original-identity','automatic_resubmit':False}
        return False
    roots=[Path(current[key]) for key in ('replan_root','code_review_root') if current.get(key)]
    proposals=[p/'proposal.json' for p in roots if native_path(p/'proposal.json').is_file()]
    from proposal_review import FIELDS
    actual_proposals=[]
    for p in proposals:
        original=read_json(p)
        actual_proposals.append({**{key:original[key] for key in FIELDS if key in original},
            'research_revision':{k:v for k,v in original.get('research_revision',{}).items() if k!='evidence_ids'},
            'addresses':[{k:v for k,v in row.items() if k!='challenge_id'} for row in original.get('addresses',[])],
            'advice_dispositions':[{k:v for k,v in row.items() if k!='challenge_id'} for row in original.get('advice_dispositions',[])]})
    critic=None;critic_ref=None
    if result.get('receipt'):
        receipt=Path(result['receipt']).resolve()
        if not any(receipt.is_relative_to(p.resolve()) for p in roots):raise ValueError('original_recovery_critic_receipt_scope_changed')
        saved=read_json(receipt);response=saved.get('response')
        if isinstance(response,dict):
            critic={key:response[key] for key in ('verdict','rationale','evidence_assessment','evidence_queries') if key in response}
            critic['challenges']=[{k:v for k,v in row.items() if k!='id'} for row in response.get('challenges',[])]
            critic['resolutions']=[{k:v for k,v in row.items() if k!='challenge_id'} for row in response.get('resolutions',[])]
            critic_ref={'path':str(receipt),'sha256':hashlib.sha256(native_path(receipt).read_bytes()).hexdigest()}
    signatures=sorted({r['signature'] for r in current.get('implementation_failures',[]) if r.get('signature')})
    problem={'reason':reason,'parent_result':current.get('result'),'proposals':actual_proposals,'failure_signatures':signatures,'original_critic':critic}
    key=digest(problem)
    history=state.setdefault('continuation_history',[])
    if any(v['fingerprint']==key for v in history):
        current['continuation']={'phase':'blocked','category':'no-new-evidence-or-plan-progress',
            'next_action':'new-evidence-required','fingerprint':key,'automatic_resubmit':False}
        return False
    target=owner.root/'recovery'/key.removeprefix('sha256:')[:24]
    feedback=target/'feedback.json'
    from research_replan import once
    once(feedback,{'schema':'sdm-research-recovery-feedback/v1','problem':problem,'coordinator_result':result,
        'proposal_sources':[{'path':str(p),'sha256':hashlib.sha256(native_path(p).read_bytes()).hexdigest()} for p in proposals],
        'original_job':current['job'],'critic_receipt':critic_ref,'authority':'original delegation unchanged; no submission or approval',
        'required_response':'Retain all objections. Propose a genuinely different supported plan or an authorized diagnostic. Missing evidence remains missing; do not evade the critic with a new identifier.'})
    ref={'path':str(feedback),'sha256':hashlib.sha256(native_path(feedback).read_bytes()).hexdigest()}
    if ref not in state.setdefault('implementation_block_history',[]):state['implementation_block_history'].append(ref)
    history.append({'fingerprint':key,'feedback':ref,'original_job':current['job'],'at':time.time()})
    current.update(continuation_folder=str(target/'replan-attempt'),continuation={'phase':'replanning','category':'known-plan-or-implementation-failure','feedback':ref,'automatic_resubmit':False})
    owner.replan(state,current,Path(current['continuation_folder']))
    return True


def start_owner(inbox,server):
    """Never keep an HTTP-only host green after its research owner died."""
    def run():
        from component_pipeline import ComponentPipeline
        failure=None
        atomic_json(inbox.root/'component-owner.json',{'status':'starting','config_sha256':digest(inbox.cfg),'at':time.time()})
        try:
            pipeline=ComponentPipeline(inbox)
            atomic_json(inbox.root/'component-owner.json',{'status':'running','config_sha256':digest(inbox.cfg),'at':time.time()})
            pipeline.run()
            state=pipeline.load()
            if not inbox.done.is_set() and state.get('status') not in {'research-goal-met','research-stopped'}:
                failure='research_owner_exited_before_terminal'
        except BaseException as error:failure=code(error)
        if failure:
            inbox.component_worker_failure=failure
            atomic_json(inbox.root/'component-owner.json',{'status':'failed','reason':failure,'config_sha256':digest(inbox.cfg),'at':time.time(),'automatic_resubmit':False})
            inbox.done.set()
            with inbox.condition:inbox.condition.notify_all()
            threading.Thread(target=server.shutdown,daemon=True).start()
        else:atomic_json(inbox.root/'component-owner.json',{'status':'exited','config_sha256':digest(inbox.cfg),'at':time.time()})
    thread=threading.Thread(target=run,name='sdm-component-owner',daemon=True)
    inbox.component_worker=thread;thread.start();return thread
