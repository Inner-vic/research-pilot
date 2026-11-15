"""Run each shared development-space round through the existing five-phase DAG.

No new scheduler or platform controller: coordinate() owns dependencies, the
shared devspace executor owns the allocation/capsule, and Agent responses own
judgments. This route is opt-in and never migrates an existing frozen attempt.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
from task_pipeline import coordinate, current_task, digest, graph
from review_storage import atomic_json
import devspace_execution as single
from proposal_review import gate as proposal_gate,mode as review_mode,pro_binding,settings as proposal_settings


def once(path,value):
    if path.exists():
        if json.loads(path.read_bytes())!=value:raise ValueError('main_workflow_record_changed:'+path.name)
    else:atomic_json(path,value)


def validate_research_spec(spec):
    """Reject project metadata mistakes before a journal or resource is acquired."""
    if 'initialization' in spec and (not isinstance(spec['initialization'],dict) or
            spec['initialization'].get('kind') not in {'fresh','weights-only','resume','not-applicable'}):
        raise ValueError('initialization_semantics_required')
    if 'evaluation' in spec and (not isinstance(spec['evaluation'],dict) or
            any(not isinstance(spec['evaluation'].get(k),str) or not spec['evaluation'][k].strip() for k in ('scope','selection_rule'))):
        raise ValueError('evaluation_selection_required')
    if 'decision_criteria' in spec and (not isinstance(spec['decision_criteria'],str) or not spec['decision_criteria'].strip()):
        raise ValueError('decision_criteria_required')


def records(config,root):
    spec=config['devspace_main_workflow'];plan=config['devspace_execution']['plan'];auth=config['devspace_execution']['authorization']
    if spec.get('version')!=1:raise ValueError('devspace_main_workflow_v1_required')
    validate_research_spec(spec)
    if not plan.get('preflight'):raise ValueError('main_workflow_actual_preflight_required')
    single.pinned_inputs(plan)
    for p in spec['evidence_paths']:
        if not Path(p).is_absolute() or not Path(p).is_file():raise ValueError('existing_main_project_evidence_required')
    folder=root/'main';folder.mkdir(exist_ok=True)
    clock=folder/'origin.json'
    if not clock.exists():atomic_json(clock,{'utc':dt.datetime.now(dt.timezone.utc).isoformat()})
    stamp=json.loads(clock.read_bytes())['utc'];until=(dt.datetime.fromisoformat(stamp)+dt.timedelta(hours=2)).isoformat()
    task=current_task({'objective':'execute-and-learn','code_change':False,'baseline_required':False,
        **({'workflow_profile':config['workflow_profile']} if 'workflow_profile' in config else {}),
        **({'exploration_revision':config['exploration_revision']} if 'exploration_revision' in config else {}),
        'timeout_seconds':600,'cleanup_timeout_seconds':600,'resource_authorization':auth,
        'orchestration':{'review_mode':review_mode(config),'source_isolation':False}},folder)
    task['shared_implementation']={name:'sha256:'+hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('devspace_main_workflow.py','devspace_execution.py','native_lifecycle.py','devspace_worker.py','task_pipeline.py','workflow_definition.py','orchestration.py','exploration_workflow.py','execution_contracts.py','review_storage.py','work_packages.py','agent_roles.py',
                     *(['proposal_dialogue.py'] if config.get('agent_roles') or config.get('research_worker',{}).get('dialogue_version') in {2,3} or config.get('proposal_review',{}).get('dialogue_version') in {2,3} else []),
                     *(['research_worker.py','devspace_observer.py','devspace_study.py'] if config.get('research_worker') else []),
                     *(['research_decision_store.py'] if config.get('research_worker',{}).get('version',0) >= 2 else []),
                     *(['research_model_receipts.py'] if config.get('research_worker',{}).get('version',0) >= 3 else []),
                     *(['research_consumption.py'] if config.get('research_worker',{}).get('version',0) >= 4 else []),
                     *(['research_model_runner.py','model_runner.py','model_runner_transport.py'] if config.get('research_worker',{}).get('version') == 5 else []),
                     *(['model_process_receipts.py'] if config.get('research_worker',{}).get('model_runner',{}).get('version',0) >= 2 else []),
                     *(['model_containment.py','model_process_guard.py','model_process_probe.py'] if config.get('research_worker',{}).get('model_runner',{}).get('version',0) >= 3 else []),
                     *(['model_context.py'] if config.get('research_worker',{}).get('model_runner',{}).get('version') == 4 else []),
                     *(['proposal_model_runner.py','proposal_review.py','review_context.py','research_model_runner.py',
                        'research_model_receipts.py','research_worker.py','model_runner.py','model_runner_transport.py',
                        'model_process_receipts.py','model_containment.py','model_process_guard.py','model_context.py']
                       if config.get('proposal_review',{}).get('version') == 4 and config['proposal_review'].get('backend') == 'model' else []))}
    task['project_evidence']={p:'sha256:'+hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in spec['evidence_paths']}
    paths={n:Path(v['path']) for n,v in task['orchestration']['records'].items()}
    def put(name,value):
        from exploration_workflow import put as put_record
        return put_record(task,paths,name,value)
    objective=spec['objective'];protocol=spec['protocol']
    background=put('background',{'goal':objective,'scope':'Real small GPU study; not full-dataset/AMP/DDP qualification',
        'success_criteria':('Actual collected results, consumed review and archive; project completion requires the declared research targets'
                            if config.get('research_iteration') else 'Actual results in the declared Study scope, grounded decisions, full checkpoints, stop/readback and archives'),
        'user_agreement_ref':auth['source_ref']})
    research=put('research',{'question':objective,'hypothesis':spec['hypothesis'],'stage':'screening',
        'comparison':spec.get('comparison',{'fixed':protocol,'variables':config.get('devspace_round_context',{}).get('allowed_variables','profile-defined')}),
        'schedule':{'unit':'epochs','horizon':protocol.get('schedule_horizon',protocol['epochs']),'stop_at':protocol['epochs'],'warmup':0},
        'initialization':spec.get('initialization',{'kind':'weights-only','source_ref':spec['baseline_ref']}),
        'evaluation':spec.get('evaluation',{'scope':str(protocol['validation_samples'])+' fixed held-out real frames','selection_rule':'Compare reported loss and mIoU; no promotion from one small run'}),
        'decision_criteria':spec.get('decision_criteria','Actual optimizer updates and finite metrics; Agent-reviewed single-variable successor only')})
    method=put('method_review',{'research_digest':digest(research),'method_checks':spec['method_checks']})
    request=spec['resource_request'];inventory=put('inventory',{'observed_at':spec['inventory_observed_at'],
        'valid_until':spec['inventory_valid_until'],'account_ref':config['devspace_execution']['owner'],
        'source_ref':spec['inventory_ref'],'options':[{**{k:request[k] for k in ('provider','namespace','cluster','queue_id')},
        'availability':'unknown','source_ref':spec['inventory_ref'],'request':request}]})
    proposal=put('proposal',{'observed_at':stamp,'valid_until':until,'inventory_digest':digest(inventory),'request':request,
        'purpose':objective,'quote_source':spec['quote_source'],'currency':'CNY','billing_basis':'shared devspace; actual invoice not reconciled',
        'monitoring':('Independent bound research worker; chat delivery does not schedule computation'
                      if config.get('research_worker') else 'Exact bound worker and change-triggered Codex queue'),
        'stop_path':'Exact UID native stop and official readback',
        'output_path':str(root),'max_cost':plan['budget'],'max_seconds':plan['max_seconds'],
        'cleanup_seconds':300 if plan['max_seconds'] is None else min(300,plan['max_seconds']/4),
        'queue_wait_seconds':None if plan.get('limit_policy')=='user-authorized-unbounded' else 0,
        **({'limit_policy':'user-authorized-unbounded','limit_authorization_ref':auth['source_ref']} if plan.get('limit_policy')=='user-authorized-unbounded' else {})})
    put('confirmation',{'proposal_digest':digest(proposal),'confirmed':True,'actor':'human','channel':'user-message',
        'source_ref':auth['source_ref'],'confirmed_at':stamp,'expires_at':None if plan.get('limit_policy')=='user-authorized-unbounded' else until,'approval_route':'direct-user',
        'qualification':'Inherited exact Study resource scope; not a new human response'})
    from review_collaboration import plan_context
    context=plan_context(task);q={'schema':'sdm-main-plan-request/v1','context':context,'context_digest':digest(context),
        'parameters':config.get('devspace_round_context',{}).get('planned_parameters'),
        'response_fields':['request_digest','reviewer','reason','alternatives','limitations']}
    from review_guidance import bind_iteration_guidance
    prior=folder/'plan-request.json'
    q=bind_iteration_guidance(q,json.loads(prior.read_bytes()) if prior.exists() else None,purpose='plan')
    once(folder/'plan-request.json',q)
    response=folder/'plan-response.json'
    proof={}
    if proposal_settings(config) is not None:
        ready=proposal_gate(config,root)
        if ready['status']=='proposal-approved':
            accepted=json.loads(Path(ready['receipt']).read_bytes());critic=accepted['response'];p=accepted['proposal']
            value={'request_digest':digest(q),'reviewer':critic['reviewer']['identity'],'reason':critic['rationale'],
                   'alternatives':p['alternatives'],'limitations':p['counterevidence'],'proposal_review_receipt':ready['receipt']}
            once(response,value)
            if ready['review_mode']=='pro':
                proof={'session_ref':pro_binding(config)['session_root'],
                       'transport_receipt_ref':str(Path(accepted['pro_binding']['review_root'])/'chat-receipt.json')}
        elif response.exists():raise ValueError('main_plan_requires_accepted_proposal_dialogue')
    if response.exists():
        value=json.loads(response.read_bytes())
        if value.get('request_digest')!=digest(q) or not all(isinstance(value.get(k),str) and value[k].strip() for k in ('reviewer','reason','alternatives','limitations')):
            raise ValueError('grounded_main_plan_response_required')
        put('plan_review',{'context_digest':digest(context),'mode':review_mode(config),'reviewer':value['reviewer'],
            'response_ref':str(response),'alignment_acknowledged':True,'background_digest':digest(background),
            'proposal_digest':digest(proposal),'questions':[],'blocking_gaps':[],'status':'final',
            **{k:value[k] for k in ('reason','alternatives','limitations')},**proof})
    return task,paths,background,research,method,proposal


def cleanup_failed_preparation(config,root,failure,allow_live,notification_config,action_receipt=None):
    """Latch an invalid active preparation and use the original allocation cleanup."""
    _,plan,binding,_=single.validate(config)
    state_path=root/'execution.json'
    state=json.loads(state_path.read_bytes())
    if (state.get('binding')!=binding or failure.get('binding')!=binding
        or failure.get('uid')!=plan['resource']['uid'] or state.get('uid')!=plan['resource']['uid']):
        raise ValueError('failed_preparation_allocation_binding_changed')
    if not single.owns_allocation(state):raise ValueError('failed_preparation_ownership_required')
    once(root/'main/preparation-failure.json',failure)
    if not state.get('stop_verified') or action_receipt is not None:
        state=single.execution(config,root,force_cleanup=True,action_receipt=action_receipt,
            allow_live=allow_live,notification_config=notification_config)
    state.update(status='main-workflow-failed' if state.get('stop_verified') else 'cleanup-required',
        main_preparation_failure=failure,new_execution_requested=False)
    atomic_json(state_path,state)
    return state


def execution(config,output,action_receipt=None,allow_live=False,notification_config=None):
    root=Path(output).resolve();root.mkdir(parents=True,exist_ok=True)
    failure_path=root/'main/preparation-failure.json'
    if failure_path.exists():
        return cleanup_failed_preparation(config,root,json.loads(failure_path.read_bytes()),allow_live,notification_config,action_receipt)
    if not (root/'execution.json').exists() and action_receipt is None:
        ready=proposal_gate(config,root)
        if ready and ready['status']!='proposal-approved':return ready
    try:task,paths,background,research,method,proposal=records(config,root)
    except ValueError as error:
        state_path=root/'execution.json'
        state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
        if state.get('stop_verified') or not single.owns_allocation(state):raise
        failure={'schema':'sdm-main-preparation-failure/v1','binding':state['binding'],
            'uid':state['uid'],'reason':str(error),'new_execution_allowed':False}
        return cleanup_failed_preparation(config,root,failure,allow_live,notification_config,action_receipt)
    main=root/'main';plan=config['devspace_execution']['plan']
    state_path=root/'execution.json'
    state=json.loads(state_path.read_bytes()) if state_path.exists() else {'status':'awaiting-main-plan-review',
        'binding':digest(plan),'uid':plan['resource']['uid'],'evidence_kind':'cpu-simulation' if plan['transport']=='local-cpu' else 'live-platform-observation',
        'intents':[],'process_terminal':False,'stop_verified':False,'billing_release_verified':False,'deadline':None}
    def advance(**options):
        nonlocal state
        state=single.execution(config,root,allow_live=allow_live,notification_config=notification_config,**options);return state
    if action_receipt is not None:advance(action_receipt=action_receipt,acquire_only=True)
    # The shared reviewer consumes only a real Agent response. Its original
    # material binding remains the authority; no gate invents a review.
    if state.get('stop_verified') and state.get('artifacts_collected') and state.get('process_terminal'):
        state=single.review(config,root,state,notification_config)
        if state.get('review',{}).get('status')=='completed':
            reply=state['review']['decision'];assessment=reply.get('workflow_assessment',{})
            cited={f.get('evidence_id') for f in reply.get('facts',[])}
            if (type(assessment.get('evidence_consistent')) is not bool or type(assessment.get('conclusion_supported')) is not bool
                or not assessment.get('supporting_evidence') or not set(assessment['supporting_evidence'])<=cited
                or not all(isinstance(assessment.get(k),list) and all(isinstance(v,str) and v for v in assessment[k]) for k in ('counterevidence','limitations'))
                or not assessment.get('limitations') or not assessment.get('alternatives')):
                state['status']='awaiting-review';state['review']['main_workflow_requirement']='Provide explicit workflow_assessment in the same Agent response; no automatic quality judgment'
                atomic_json(state_path,state);return state
    def put(name,value):
        from exploration_workflow import put as put_record
        return put_record(task,paths,name,value)
    def post_records():
        review=state.get('review',{});refs=[str(root/'artifact-manifest.json'),str(root/'resource-reconciliation.json')]
        context=put('review_context',{'goal':background['goal'],'user_agreement':{'source_ref':config['devspace_execution']['authorization']['source_ref']},
            'baseline':config['devspace_main_workflow']['baseline_ref'],'accepted_plan':str(main/'plan-response.json'),
            'change_summary':config.get('devspace_round_context',{}).get('planned_parameters','profile parameters'),
            'fixed_conditions':config['devspace_main_workflow']['protocol'],'prior_findings':[],
            'evidence_index':refs,'open_questions':[],'current_scope':{'execution_allowed':False},
            'decision_question':'Assess actual result and one authorized change or stop','claim_scope':'screening',
            **{k+'_digest':digest(v) for k,v in [('background',background),('research',research),('method_review',method),('proposal',proposal)]}})
        if review.get('status')!='completed':return
        decision=review['decision'];step=review['next_step'];assessment=decision['workflow_assessment'];response=put('review_response',{'context_digest':digest(context),
            'mode':review_mode(config),'reviewer':'Agent response verified by shared evidence engine','response_ref':str(root/'review'),
            'alignment_acknowledged':True,'questions':[],'blocking_gaps':[],'status':'final','reason':decision['reason'],
            'alternatives':'; '.join(h['statement'] for h in decision['hypotheses']),
            'limitations':'; '.join(h['limitations'] for h in decision['hypotheses']),
            **({'session_ref':pro_binding(config)['session_root'],
                'transport_receipt_ref':str(Path(decision['external_review_binding']['review_root'])/'chat-receipt.json')}
               if review_mode(config)=='pro' else {})})
        checks=[{'claim':f['evidence_id'],'evidence_ref':str(root/'review'),'finding':'Exact fact value accepted by original analysis validator','consistent':True} for f in decision['facts']]
        consistent=assessment['evidence_consistent'];supported=assessment['conclusion_supported']
        for check in checks:check['consistent']=consistent
        consistency=put('evidence_consistency',{'review_response_digest':digest(response),'review_context_digest':digest(context),'status':'passed' if consistent else 'needs-revision','checks':checks})
        quality=put('conclusion_quality',{'review_response_digest':digest(response),'consistency_digest':digest(consistency),
            'status':'passed' if consistent and supported else 'needs-revision','supporting_evidence':assessment['supporting_evidence'],
            'counterevidence':assessment['counterevidence'],
            'limitations':assessment['limitations'],'blocking_gaps':[] if consistent and supported else ['Agent assessment requires revision'],
            'alternatives_considered':'; '.join(assessment['alternatives']) if isinstance(assessment['alternatives'],list) else assessment['alternatives'],
            'reason':decision['reason']})
        action='propose-successor' if step.get('iteration',{}).get('action') in {'run-next-round','retry-round'} else 'stop'
        nxt=put('next_action',{'quality_digest':digest(quality),'conclusion':decision['previous_experiment_assessment'],
            'reason':step['reason'],'evidence_ref':str(root/'review'),'recommended_action':action,
            'options':[{'action':action,'reason':step['reason']}],'execution_allowed':False})
        put('transition',{'action':action,'execution_allowed':False,'reason':step['reason'],
            'evidence_ref':str(root/'review'),'next_action_digest':digest(nxt)})
    if state.get('artifacts_collected'):post_records()
    nodes=graph(task)
    task['drivers']={k:{'shared_operation':k,'resumable_observation':k in {'7','13','15','16'}}
        for k,n in nodes.items() if n['policy']=='required' and not n.get('builtin')}
    task['cleanup_driver']={'shared_operation':'cleanup'}
    def receipt(key,details):
        p=main/('node-'+key+'.json');payload={'status':'completed','task_node':key,'details':details,
            'qualification':'bound shared implementation observation; not signed platform qualification'}
        once(p,payload)
        return {'status':'completed','receipt':str(p),'receipt_digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()}
    def invoke(driver,folder,key,budget):
        if key=='cleanup':
            if not state_path.exists() or state.get('stop_verified'):return receipt('cleanup',{'stop_verified':state.get('stop_verified',False),'allocation_started':state_path.exists()})
            advance(force_cleanup=True)
            if state.get('stop_verified'):return receipt('cleanup',{'stop_verified':True})
            return {'status':'failed','reason':'same_devspace_cleanup_pending','uncertain_effect':False}
        if key=='7':
            advance(acquire_only=True)
            if state['status']!='resource-ready':return {'status':'paused','uncertain_effect':False,'continuation':state['status']}
            return receipt(key,{'resource':state.get('resource_reconciliation') or json.loads((root/'resource-observation.json').read_bytes()),'workload_launched':False})
        if key=='13':
            target=main/'preflight.json'
            if not target.exists():
                probe=plan['preflight'];entry=next(r for r in plan['inputs'] if r['name']==probe['input_name'])
                single.pinned_inputs(plan)
                raw=Path(entry['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('pinned_preflight_input_changed')
                source=raw.decode('utf-8')
                answer=single.transport(plan,{'action':'preflight','binding':digest(plan),'source':source,
                    'sha256':hashlib.sha256(source.encode('utf-8')).hexdigest(),'parameters':probe['parameters'],
                    'timeout_seconds':probe['timeout_seconds']})
                once(target,{**answer,'probe_input_sha256':entry['sha256']})
            answer=json.loads(target.read_bytes())
            if answer.get('status')!='passed':return {'status':'failed','reason':'actual_remote_preflight_failed','receipt':str(target),
                'receipt_digest':'sha256:'+hashlib.sha256(target.read_bytes()).hexdigest(),'uncertain_effect':False}
            return receipt(key,answer)
        if key=='15':
            advance()
            if not state.get('process_terminal'):return {'status':'paused','uncertain_effect':False,'continuation':state['status']}
            return receipt(key,{'process':state['process'],'outcome':state.get('outcome')})
        if key=='16':
            advance()
            if not state.get('artifacts_collected') or not state.get('stop_verified'):
                return {'status':'paused','uncertain_effect':False,'continuation':state['status']}
            return receipt(key,{'artifact_manifest':str(root/'artifact-manifest.json'),'stop_verified':True})
        if key=='18':
            if not state.get('artifacts_collected'):return {'status':'failed','reason':'no_actual_materials_for_result_summary','uncertain_effect':False}
            post_records();return receipt(key,{'original_materials':str(root/'artifact-manifest.json'),'review':state.get('review',{}).get('request_path')})
        if key=='21':return receipt(key,{'review_archive':str(root/'review'),'stop_verified':state['stop_verified'],'next_step':state['review']['next_step']})
        return receipt(key,{'plan_digest':digest(plan),'project_evidence':config['devspace_main_workflow']['evidence_paths'],
            'meaning':nodes[key]['name'],'scope':'Local declared project/material selection; actual remote inputs rechecked at13'})
    summary=coordinate(task,main,invoke)
    state.update(main_workflow={'path':str(main),'status':summary['status'],'work_packages':summary.get('work_packages',[]),
        'coverage':summary.get('coverage',{}),'waiting_node':summary.get('waiting_node')},
        plan_request=str(main/'plan-request.json'),plan_response=str(main/'plan-response.json'))
    if summary.get('waiting_node') in {'22','34'}:state['status']='awaiting-main-plan-review'
    elif summary.get('waiting_node') in {'29','31','19','32','20','33'}:state['status']='awaiting-review'
    elif summary.get('status')=='complete' and state.get('review',{}).get('status')=='completed':state['status']='completed'
    elif summary.get('status') in {'needs-driver','cleanup-required','blocked'}:state['status']=summary['status']
    if not state.get('artifacts_collected') and any(v.get('status')=='failed' for v in summary.get('nodes',{}).values()):
        state['status']='main-workflow-failed';state['failure_nodes']={k:v for k,v in summary['nodes'].items() if v.get('status')=='failed'}
    atomic_json(state_path if state_path.exists() else main/'pending.json',state)
    return state
