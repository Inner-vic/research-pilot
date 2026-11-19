"""ModelRunner producer/critic adapter for the existing pre-submission gate."""
import copy
import hashlib
from pathlib import Path
import re

from model_runner import ModelRunner,binding,completed,configuration,InvalidModelReply,KnownModelTermination
from research_model_receipts import checked,seal
from research_model_runner import object_schema,shared_evidence,guidance_sources
from review_storage import atomic_json,read_json,locked
from task_pipeline import digest
import proposal_review as review
from proposal_dialogue import prompt as dialogue_prompt


class ModelInputBlocked(ValueError):
    def __init__(self,path):
        super().__init__('model_request_exceeds_bounded_input');self.path=path


def bind_sources(folder):
    names=('proposal_model_runner.py','proposal_review.py','review_context.py','research_worker.py',
        'research_model_runner.py','research_model_receipts.py','model_runner.py','model_runner_transport.py',
        'model_runner_recovery.py','model_context.py','planner_session.py','working_sessions.py','research_context.py','model_process_receipts.py','model_containment.py','model_process_guard.py',
        'review_storage.py','task_pipeline.py','proposal_dialogue.py','research_replan.py','component_research.py','agent_roles.py','proposal_evidence.py',
        'implementation_code_review.py','bounded_evidence.py','component_candidates.py','component_reproduction.py',
        'research_diagnostics.py','research_workloads.py','research_chain.py','proposal_input.py','research_language.py','review_resume.py',
        'research_intent.py','lifecycle_rules.py','contracts.py','canonical.py','windows_assets.py')
    value={'schema':'sdm-proposal-implementation/v1','sha256':{
        name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}}
    path=folder/'implementation.json'
    if path.exists():
        if checked(path,value['schema'])!=seal(value):raise ValueError('frozen_proposal_implementation_changed')
    else:atomic_json(path,seal(value))


def settings(config):
    from agent_roles import review_settings
    policy=review_settings(config,'proposal_review')
    from proposal_evidence import enabled
    enabled(policy)
    dialogue_prompt('a',policy.get('dialogue_version',1))
    if policy.get('context_required') is not True:raise ValueError('model_proposal_shared_context_required')
    models=policy.get('models',{})
    if (set(models)!={'a','b'} or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.-]+',v) for v in models.values())
            or models['a'].casefold()==models['b'].casefold()):
        raise ValueError('distinct_proposal_models_required')
    runner=policy.get('model_runner')
    if not isinstance(runner,dict):raise ValueError('explicit_proposal_model_runner_required')
    configuration(runner)
    from planner_session import selection
    selection(policy,runner)
    from working_sessions import settings as working_settings
    working_settings(policy,runner)
    if runner['backend']=='sdk':
        if runner['version']!=4:raise ValueError('proposal_sdk_explicit_context_v4_required')
        from model_context import sdk_options
        sdk_options(runner)
    return policy,runner


def schemas(role,source_ids,strategy=False,registered_evidence=False,candidates=None,sdm_intent=None,sdm_governance=None):
    string={'type':'string'}
    if role=='a':
        props={**{key:string for key in review.FIELDS},
            'addresses':{'type':'array','items':object_schema({'challenge_id':string,'reason':string})},
            'advice_dispositions':{'type':'array','items':object_schema({'challenge_id':string,
                'decision':{'type':'string','enum':['accept','modify','reject']},'applicability':string,'reason':string})}}
        if sdm_intent is not None:
            from research_intent import proposal_schema
            props['sdm_brief']=proposal_schema(sdm_intent)
        if strategy:
            from research_replan import direction_schema
            props['research_revision']=direction_schema(source_ids)
            if candidates:
                from component_candidates import decision_schema
                props['research_revision']['properties']['candidate_decision']=decision_schema(candidates)
                props['research_revision']['required'].append('candidate_decision')
        return object_schema(props)
    result=object_schema({'verdict':{'type':'string','enum':['approve','revise','reject','needs-evidence','stalled']},
        'rationale':string,'challenges':{'type':'array','items':object_schema({'id':string,
            'severity':{'type':'string','enum':['blocker','major','minor']},'question':string,'basis':string,'resolution_criterion':string})},
        'resolutions':{'type':'array','items':object_schema({'challenge_id':string,
            'status':{'type':'string','enum':['open','resolved']},'reason':string})},
        'evidence_assessment':object_schema({'source_ids':{'type':'array','items':{'type':'string','enum':source_ids}},
            'judgment':{'type':'string','enum':['supported','hypothesis','insufficient']},
            'limitations':{'type':'array','items':string}})})
    if sdm_governance:
        lenses=('protocol','statistics','failure_cases','reproducibility','deployment')
        result['properties']['sdm_evidence_review']=object_schema({
            'five_lenses':object_schema({name:{'type':'string','enum':['pass','fail','inconclusive']} for name in lenses}),
            'reasons':object_schema({name:{'type':'string','minLength':8} for name in lenses}),
            'deviation_disposition':{'type':'string','enum':['none','resolved','unresolved','not_applicable']}})
        result['required'].append('sdm_evidence_review')
    if registered_evidence:
        from proposal_evidence import query_schema
        result['properties']['evidence_queries']=query_schema(source_ids,registered_evidence)
        result['required'].append('evidence_queries')
    return result


def canonical(role,raw,value,call):
    if not isinstance(raw,dict):raise ValueError('proposal_model_object_required')
    from proposal_input import restore
    payload=restore(value['evidence'],Path(call).parent.parent);actor={'role':'proposer' if role=='a' else 'critic',
        'identity':role+':'+value['model'],'source_ref':str(call/'backend-completed.json'),
        'model':{'provider':'codex','id':value['model']},'call_ref':str(call/'backend-completed.json')}
    if role=='a':
        return {**copy.deepcopy(raw),'schema':'sdm-proposal/v1','subject_digest':payload['subject_digest'],
            'revision':payload['revision'],'proposer':actor,
            'evidence':proposal_evidence(payload,call)}
    return {**copy.deepcopy(raw),'schema':'sdm-proposal-review/v1','request_digest':digest(payload['packet']),
        'reviewer':actor}


def proposal_evidence(payload,call):
    refs=[{'path':row['path'],'sha256':row['sha256']}
          for row in payload['shared']['packet']['context']['sources']]
    if len(refs)<=32:return refs
    # The context permits 64 sources while the proposal permits 32 references.
    # Retain every raw source in the shared bound index, without making the model
    # repair an adapter-generated evidence list it cannot control.
    path=Path(call).parent.parent/'shared-evidence.json'
    saved=checked(path,'sdm-shared-research-evidence/v1')
    if saved['evidence']!=payload['shared']:
        raise ValueError('proposal_shared_evidence_index_changed')
    return [{'path':str(path),'sha256':'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()}]


def validate(role,value,payload):
    if role=='b':
        if payload.get('sdm_governance'):
            from research_acceptance import evidence_review
            evidence_review(payload['sdm_governance'],value,digest(payload['shared']))
        if payload.get('registered_evidence_policy'):
            from proposal_evidence import validate as validate_queries
            validate_queries(value,payload)
        return review.validate_response(value,payload['packet'],payload['packet']['proposal'],payload['previous'],None,payload['revision'])
    review.validate_proposal(value,payload['subject_digest'],True,payload.get('sdm_intent'))
    if payload['shared']['project'].get('kind')=='research-replan':
        from research_replan import validate_direction
        validate_direction(value.get('research_revision'),[row['id'] for row in payload['shared']['packet']['context']['sources']],strategy=True)
        candidates=payload['shared']['packet']['context']['configuration'].get('candidate_management')
        if candidates:
            from component_candidates import validate_decision
            validate_decision(value['research_revision'],candidates)
    previous=payload['previous']
    if previous and (previous[-1]['response']['verdict']=='revise' or
            payload.get('registered_evidence_policy') and previous[-1]['response']['verdict']=='needs-evidence'):
        for challenge in previous[-1]['response']['challenges']:
            key=challenge['id']
            if not any(row.get('challenge_id')==key and row.get('reason') and row.get('applicability')
                for row in value['advice_dispositions']):raise ValueError('proposer_must_check_advice_applicability')
            if challenge['severity'] in {'major','blocker'} and not any(row.get('challenge_id')==key and row.get('reason')
                for row in value['addresses']):raise ValueError('proposal_must_address_critical_challenges')
    return value


def invoke(config,folder,role,revision,shared,previous,control_revision,packet=None):
    policy,runner=settings(config)
    from working_sessions import select as select_session
    runner,working_session=select_session(policy,runner,role)
    from proposal_evidence import enabled
    registered_evidence=(policy['evidence_policy']['version'] if enabled(policy) else False)
    payload={'shared':shared,'subject_digest':shared['packet']['subject_digest'],'revision':revision,
        'previous':previous,'control_revision':control_revision}
    if registered_evidence and role=='b':
        # The critic packet already carries bound supplements. Avoid duplicating
        # their full text a second time in the same bounded tool output.
        payload['previous']=[{k:v for k,v in row.items() if k!='registered_supplements'} for row in previous]
    if packet is not None:payload['packet']=packet
    if config.get('sdm_intent') is not None:
        from research_intent import review_context
        payload['sdm_intent']=config['sdm_intent']
        payload['sdm_intent_context']=review_context(config['sdm_intent'])
    if config.get('sdm_governance'):
        from research_acceptance import load as load_governance
        payload['sdm_governance']=config['sdm_governance']
        payload['sdm_governance_context']=load_governance(config['sdm_governance'])
    if registered_evidence:payload['registered_evidence_policy']=registered_evidence
    value={'schema':'sdm-model-request/v1','purpose':'proposal' if role=='a' else 'method-review',
        'contract_id':'sdm-proposal/v1' if role=='a' else 'sdm-proposal-review/v1','model':policy['models'][role],
        'input_kind':runner['context_policy']['input_kind'] if runner['version']==4 else 'text',
        'evidence':payload,'prompt':dialogue_prompt(role,policy.get('dialogue_version',1)),
        'output_schema':schemas(role,[row['id'] for row in shared['packet']['context']['sources']],shared['project'].get('kind')=='research-replan',registered_evidence,
            (shared['packet']['context']['configuration'].get('candidate_management')
                if isinstance(shared['packet']['context']['configuration'],dict) else None),config.get('sdm_intent'),config.get('sdm_governance'))}
    if config.get('sdm_intent') is not None:
        value['prompt']+=(' Return sdm_brief using original SDM S02 fields. Keep hypothesis identical to the proposal hypothesis. '
            'Bind the exact S01 reference/digest from sdm_intent_context. State mechanism and uncertainty explicitly; '
            'target_as_must must be false: the operational target is not a predetermined scientific outcome. '
            'When fixed_brief is present, reproduce that exact brief; critique it without silently replacing the frozen intent. '
            'A needed scientific change blocks this intent and requires a newly bound plan. Review is not execution authority.')
    if config.get('sdm_governance'):
        value['prompt']+=(' The pinned sdm_governance_context is the shared SDM acceptance basis. '
            'Inspect applicability, original contracts, fixed baseline/evaluation, experiment kernel and nonrenewing budget. '
            'Review independent evidence through the original five lenses: protocol, statistics, failure_cases, reproducibility, deployment. Return sdm_evidence_review with a disposition and grounded reason for each lens. For deployment outside this scope, retain inconclusive and explain non-applicability rather than claiming a deployment pass. '
            'Missing remote tiny-fit or checkpoint-continuation evidence is pending, never inferred from compilation or review agreement. '
            'Operational validation target attainment is distinct from scientific support; adaptive validation remains exploratory. '
            'An unresolved/failed attempt remains in accounting. Approval grants no execution authority.')
    if policy.get('revision_target')=='implementation':
        value['prompt']+=(' Review the exact frozen implementation against the already approved plan and protected evaluation. '
            'Neither read-only reviewer can edit these files. A promise or suggested patch does not resolve a code defect. '
            'A grounded revise verdict hands the recorded critique to the implementation worker; it is not execution approval. '
            'Use revise for a concrete correctable implementation mismatch under the accepted plan. '
            'Reject plan/scope/evaluation changes and distinguish unproven effectiveness from actual implementation defects. ')
    if registered_evidence:
        value['prompt']+=(' Inspect each raw_sources entry independently: visibility=complete-text and omitted_bytes=0 '
            'means that exact file is complete; an excerpt of another file does not truncate it. '
            'Do not inherit another role\'s unsupported visibility claim. Registered supplements in previous '
            'must inform the revised proposal and explicit challenge dispositions. ')
        if role=='b':value['prompt']+=(' For needs-evidence, return evidence_queries using registered source_id and bounded '
            'start_line/line_count/question. Return [] when the gap requires new external evidence or measurements. '
            'For other verdicts return evidence_queries=[]. Do not repeat a supplied range without new bytes. ')
    if registered_evidence==2:
        value['prompt']+=(' Registered evidence queries v2 are byte-bounded, not merely line-bounded. '
            'Every query includes json_pointers: [] requests raw whole lines; nonempty RFC6901 paths request '
            'exact selected JSON values, with * for array members. JSONL uses start_line/line_count as records; '
            'a JSON document field query uses start_line=1,line_count=1. For historical recipes prefer the '
            'registered historical-recipe-index and historical-recipes sources; for historical-results JSONL '
            'request /uid, /result/config, /result/validation, /result/code_sha256 and /result_ref as needed, '
            'rather than every epoch curve. Selected fields are NOT the whole source. Follow next_start_line '
            'for omitted records and narrow a field if it alone exceeds capacity. Preserve unresolved decisive '
            'gaps; never approve by treating omitted bytes as inspected or demand irrelevant full archives. ')
    from planner_session import attach
    attach(value,policy,runner,implementation=policy.get('revision_target')=='implementation')
    from working_sessions import attach as attach_session
    attach_session(value,working_session)
    attempt=1;feedback=None;seen=set()
    while True:
        from research_worker import check_control
        check_control(folder.parents[1],control_revision)
        call=folder/'model-calls'/f'{revision:04d}-{role}-{attempt:04d}'
        value['evidence']={**payload,'attempt':attempt,'validation_feedback':feedback}
        if role=='a':review.evidence_refs({'evidence':proposal_evidence(value['evidence'],call)})
        from proposal_input import project as project_input,restore as restore_input
        launch_value=project_input(value,folder)
        if (call/'request.json').exists():
            saved=checked(call/'request.json','sdm-model-runner-envelope/v1')['request']
            if saved['evidence']['control_revision']!=control_revision:
                from review_resume import authorized
                expected={**launch_value,'evidence':{**launch_value['evidence'],'control_revision':saved['evidence']['control_revision']}}
                if not authorized(folder.parents[1],call,control_revision) or saved!=expected:
                    raise ValueError('proposal_control_changed_requires_reconciliation')
                launch_value=saved # ModelRunner consumes its original completed receipt, never a new intent.
        validation_payload=restore_input(launch_value['evidence'],folder)
        from model_runner import request as validate_request
        try:validate_request(launch_value)
        except ValueError as error:
            if str(error)!='model_request_exceeds_bounded_input' or (call/'intent.json').exists():raise
            import json
            proof={'schema':'sdm-model-input-preflight/v1','status':'not-issued',
                'call':str(call),'request_digest':digest(launch_value),
                'input_bytes':len(json.dumps(launch_value,ensure_ascii=False,allow_nan=False).encode('utf-8')),
                'limit_bytes':256*1024,'reason':str(error),'model_calls':0,'automatic_retry':False}
            path=folder/('input-blocked-'+call.name+'.json')
            if path.exists() and read_json(path)!=proof:raise ValueError('input_preflight_receipt_changed')
            atomic_json(path,proof)
            raise ModelInputBlocked(path)
        try:
            result=ModelRunner(runner).run(launch_value,call,
                lambda raw:validate(role,canonical(role,raw,launch_value,call),validation_payload))
            value=launch_value
            break
        except InvalidModelReply as error:
            reply=completed(call,binding(runner,launch_value));key=digest(error.response)
            feedback={'schema':'sdm-proposal-invalid-reply/v1','response':error.response,'reason':str(error),
                'completion_digest':digest(reply)}
            if role=='b':
                feedback['allowed_resolution_ids']=[c['id'] for r in previous for c in r['response']['challenges']]
                feedback['resolution_rule']='Only these current-subject IDs are allowed; an empty list requires resolutions=[]'
            path=call/'invalid.json'
            if path.exists():
                if checked(path,feedback['schema'])!=seal(feedback):raise ValueError('invalid_proposal_feedback_changed')
            else:atomic_json(path,seal(feedback))
            if key in seen or attempt>=3:raise ValueError('proposal_model_invalid_reply_stalled')
            seen.add(key);attempt+=1
    output=canonical(role,result['response'],value,call)
    proof={'schema':'sdm-proposal-model-output/v1','role':role,'canonical':output,
        'request_digest':digest(value),'completion_digest':digest(result)}
    path=call/'canonical.json'
    if path.exists():
        if checked(path,proof['schema'])!=seal(proof):raise ValueError('proposal_model_canonical_changed')
    else:atomic_json(path,seal(proof))
    return output,call


def verify_calls(config,folder,shared,control_revision,historical=False):
    policy,base_runner=settings(config);outputs=[]
    for path in sorted((folder/'model-calls').glob('*/canonical.json')):
        proof=checked(path,'sdm-proposal-model-output/v1');call=path.parent
        saved=checked(call/'request.json','sdm-model-runner-envelope/v1');value=saved['request'];role=proof['role']
        from working_sessions import select as select_session
        runner,working_session=select_session(policy,base_runner,role)
        if value.get('working_session')!=working_session:raise ValueError('working_role_call_binding_changed')
        expected_context=(policy.get('planner_context') if role=='a' and policy.get('revision_target')!='implementation' else None)
        if value.get('planner_context')!=expected_context:raise ValueError('proposal_planner_context_binding_changed')
        payload=value['evidence']
        if (role not in {'a','b'} or saved['config']!=runner or value['model']!=policy['models'][role] or digest(value)!=proof['request_digest']
                or value['purpose']!=('proposal' if role=='a' else 'method-review')
                or payload['shared']!=shared or payload['subject_digest']!=shared['packet']['subject_digest']
                or call.name!=f"{payload['revision']:04d}-{role}-{payload['attempt']:04d}"
                or saved['binding']!=binding(runner,value)):
            raise ValueError('proposal_model_call_binding_changed')
        if not historical and value['evidence']['control_revision']!=control_revision:
            from review_resume import authorized
            if not authorized(folder.parents[1],call,control_revision):raise ValueError('proposal_control_changed_requires_reconciliation')
        reply=completed(call,saved['binding'])
        if (reply['status']!='completed' or digest(reply)!=proof['completion_digest']
                or canonical(role,reply['response'],value,call)!=proof['canonical']):
            raise ValueError('proposal_model_completion_changed')
        from proposal_input import restore as restore_input
        validate(role,proof['canonical'],restore_input(value['evidence'],folder));outputs.append((call,proof['canonical'],role))
    return outputs


def publish(path,value,call,prior=None):
    marker=call/'published.json'
    if marker.exists():
        if read_json(path)!=value or read_json(marker)!={'path':str(path),'digest':digest(value)}:
            raise ValueError('published_model_proposal_view_changed')
        return
    if path.exists() and read_json(path) not in (value,prior):raise ValueError('unowned_proposal_view_requires_reconciliation')
    atomic_json(path,value);atomic_json(marker,{'path':str(path),'digest':digest(value)})


def verify_views(root,folder,outputs):
    proposals=[(call,value) for call,value,role in outputs if role=='a'];proposal_path=root/'proposal.json'
    if proposal_path.exists() and not proposals:raise ValueError('unowned_proposal_view_requires_reconciliation')
    if proposals:
        call,value=proposals[-1]
        if (call/'published.json').exists() and (not proposal_path.exists() or read_json(proposal_path)!=value):
            raise ValueError('published_model_proposal_view_changed')
        if not (call/'published.json').exists() and proposal_path.exists():
            if read_json(proposal_path)==value:publish(proposal_path,value,call)
            elif read_json(proposal_path)!=(proposals[-2][1] if len(proposals)>1 else None):
                raise ValueError('unowned_proposal_view_requires_reconciliation')
    owned={}
    for call,value,role in outputs:
        if role=='b':
            revision=read_json(call/'request.json')['request']['evidence']['revision']
            target=folder/f'round-{revision:02d}'/'review-response.json';owned[target]=value
            if (call/'published.json').exists() and (read_json(call/'published.json')['path']!=str(target)
                    or not target.exists() or read_json(target)!=value):
                raise ValueError('published_model_review_view_changed')
    for target in folder.glob('round-*/review-response.json'):
        if target not in owned or read_json(target)!=owned[target]:raise ValueError('unowned_proposal_review_requires_reconciliation')


def drive(config,root,evidence_query=None,verify_only=False):
    from review_context import context,FIELDS
    from research_worker import control,check_control
    from proposal_evidence import enabled,history,auto_supply
    policy,_=settings(config);registered_evidence=enabled(policy)
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    subject=review.subject(config);bound=digest(subject)
    folder=root/'proposal-review'/bound.removeprefix('sha256:')[:32]
    with locked(folder,'.model-dialogue.lock','proposal_model_dialogue_busy'):
        bind_sources(folder)
        steering=control(root);historical=(root/'execution.json').exists()
        if steering['paused'] and not historical:return {'status':'proposal-review-paused','execution_authority':False}
        context_path=root/'review-context.json'
        if not context_path.exists():
            return {'status':'awaiting-review-context','context_path':str(context_path),'subject_digest':bound,
                'required_fields':list(FIELDS),'schema':'sdm-review-context/v1','execution_authority':False}
        ctx=context(context_path,bound)
        sources={row['path']:'sha256:'+row['sha256'].removeprefix('sha256:') for row in ctx['sources']}
        sources[str(context_path)]='sha256:'+hashlib.sha256(context_path.read_bytes()).hexdigest();guidance_sources(sources)
        if config.get('sdm_intent') is not None:
            from component_research import reference
            path=reference(config['sdm_intent']['seed']);sources[str(path)]='sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()
        shared=shared_evidence(subject,{}, {'subject_digest':bound,'context':ctx},sources,folder,
            **({'source_visibility':1} if registered_evidence else {}),
            **({'source_budget':policy['source_budget']} if 'source_budget' in policy else {}))
        proposal_path=root/'proposal.json'
        while True:
            bind_sources(folder)
            verify_views(root,folder,verify_calls(config,folder,shared,steering['revision'],historical))
            result=review.advance(config,root,subject,evidence_query);evidence_query=None
            if verify_only:return result
            if result.get('revision_target')=='implementation' and result['status']=='proposal-revision-required':return result
            state_path=folder/'state.json';rows=read_json(state_path)['rounds'] if state_path.exists() else []
            previous=history(folder,rows) if registered_evidence else [read_json(folder/row['directory']/'accepted.json') for row in rows]
            if registered_evidence and result['status']=='awaiting-proposal-evidence':
                check_control(root,steering['revision'])
                delivered=auto_supply(folder,rows)
                if delivered and delivered['status']=='review-evidence-supplied':continue
                if delivered:return delivered
            if result['status'] in {'proposal-approved','proposal-rejected','proposal-review-stalled','awaiting-proposal-evidence',
                'review-evidence-supplied','awaiting-review-context'}:return result
            if historical:return {'status':'proposal-model-blocked','reason':'Existing allocation requires original observation/cleanup route','execution_authority':False}
            check_control(root,steering['revision'])
            revision=len(previous)+1
            try:
                if result['status'] in {'awaiting-proposal','awaiting-proposal-revision','proposal-revision-required'}:
                    value,call=invoke(config,folder,'a',revision,shared,previous,steering['revision'])
                    check_control(root,steering['revision'])
                    bind_sources(folder)
                    publish(proposal_path,value,call,previous[-1]['proposal'] if previous else None)
                elif result['status']=='awaiting-proposal-review':
                    packet=read_json(result['request_path']);target=Path(result['response_path'])
                    if target.exists():raise ValueError('unowned_proposal_review_requires_reconciliation')
                    value,call=invoke(config,folder,'b',revision,shared,previous,steering['revision'],packet)
                    check_control(root,steering['revision']);bind_sources(folder);publish(target,value,call)
                else:return result
            except ModelInputBlocked as error:
                return {'status':'proposal-input-blocked','reason':'model_request_exceeds_bounded_input',
                    'call_status':'not-issued','preflight_receipt':str(error.path),
                    'automatic_retry':False,'execution_authority':False}
            except KnownModelTermination as error:
                path=error.folder/'backend-completed.json'
                return {'status':'proposal-model-failed','known_terminal':True,'terminal_status':error.result['status'],
                    'completion_proof':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},
                    'automatic_retry':False,'execution_authority':False}
            except InvalidModelReply as error:
                return {'status':'proposal-model-reply-invalid','reason':str(error),'known_response_preserved':True,
                    'automatic_retry':False,'execution_authority':False}
            except (ValueError,OSError) as error:
                return {'status':'proposal-review-stalled' if str(error)=='proposal_model_invalid_reply_stalled' else 'proposal-model-blocked',
                    'reason':str(error),'automatic_retry':False,'execution_authority':False}
