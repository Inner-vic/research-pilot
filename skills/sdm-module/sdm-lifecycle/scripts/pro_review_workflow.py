"""Durable, project-scoped Pro review orchestration; no browser or ML dispatch."""
import argparse
import datetime as dt
import json
from pathlib import Path

from loop_analysis import validate
from model_review_bridge import accept, emit, prepare as pack, sha
from pro_review_session import check, _record
from review_storage import atomic_json, locked, read_json
from task_pipeline import digest

STAGES={'prepared','delivery-in-progress','waiting-for-review','delivery-uncertain','received','rejected','validated','planned','cancelled'}


def _load(root):
    session=read_json(root/'session.json')
    path=root/'review-workflow.json'
    value=read_json(path) if path.exists() else {
        'schema':'sdm-pro-review-workflow/v1','project':session['project'],
        'workflow':session['workflow'],'revision':0,'active_review':None,'reviews':{}}
    if any(value.get(k)!=session[k] for k in ('project','workflow')):
        raise ValueError('workflow_project_mismatch')
    for item in value['reviews'].values():
        if item['stage'] not in STAGES:raise ValueError('unknown_review_stage')
    return value


def _save(root,state):
    state['revision']+=1
    atomic_json(root/'review-workflow.json',state)


def _item(state,review_id=None):
    review_id=review_id or state['active_review']
    if not review_id or review_id not in state['reviews']:raise ValueError('known_review_required')
    return state['reviews'][review_id]


def _bundle(root,item):
    return root/'reviews'/item['request_digest'].removeprefix('sha256:')


def _verify(root,item):
    bundle=_bundle(root,item)
    manifest=read_json(bundle/'manifest.json')
    for name,expected in manifest['files'].items():
        if Path(name).name!=name or sha((bundle/name).read_bytes())!=expected:
            raise ValueError('review_integrity_failed')
    packet=read_json(bundle/'analysis-request.json')
    if digest(packet)!=item['request_digest'] or manifest['request_digest']!=item['request_digest']:
        raise ValueError('review_request_changed')
    session=read_json(root/'session.json')
    if packet['review_binding']!={k:session[k] for k in ('project','workflow','chat_url')}:
        raise ValueError('review_project_binding_mismatch')
    if item['stage']=='cancelled':
        cancellation=read_json(bundle/'cancellation.json')
        if digest(cancellation)!=item.get('cancellation_digest') or cancellation['request_digest']!=item['request_digest']:
            raise ValueError('cancellation_integrity_failed')
        _cancellation_evidence(bundle,cancellation)
    return bundle,packet


def _no_cancellation_pending(bundle):
    if (bundle/'cancellation.json').exists():raise ValueError('cancellation_pending_recovery_required')


def _context(value,session):
    if value.get('schema')!='sdm-pro-review-context/v1' or any(value.get(k)!=session[k] for k in ('project','workflow')):
        raise ValueError('review_context_project_mismatch')
    if not isinstance(value.get('fixed_conditions'),dict) or not value['fixed_conditions']:
        raise ValueError('fixed_conditions_required')
    for field in ('excluded_explanations','remaining_unknowns'):
        if not isinstance(value.get(field),list) or not all(isinstance(x,str) for x in value[field]):
            raise ValueError('context_string_list_required:'+field)
    scope=value.get('current_authorization',{})
    for flag in ('execution_authorized','model_inference_allowed','training_submission_allowed'):
        if scope.get(flag) is not False:raise ValueError('review_only_authorization_required:'+flag)
    if type(value.get('new_experiment_observations')) is not bool:
        raise ValueError('observation_scope_boolean_required')
    matrix=value.get('gap_matrix',[])
    if not isinstance(matrix,list) or any(not isinstance(g,dict) or not isinstance(g.get('gap_id'),str) or g.get('status') not in {'open','partially-closed','closed'} for g in matrix):
        raise ValueError('typed_gap_matrix_required')
    if len({g['gap_id'] for g in matrix})!=len(matrix):raise ValueError('unique_gap_ids_required')
    if value.get('alignment_context') is not None:
        from orchestration import review_context
        review_context(value['alignment_context'])


def prepare(root,request,context,evidence,observed_url,expected_revision,supersedes_review_id=None):
    root=Path(root)
    with locked(root):
        session=check(root,observed_url,expected_revision)
        supplied=read_json(context);_context(supplied,session)
        packet=read_json(request)
        if packet.get('schema')!='sdm-loop-analysis-request/v1':raise ValueError('analysis_request_schema_required')
        if type(packet.get('remaining_rounds')) is not int or packet['remaining_rounds']<0:
            raise ValueError('nonnegative_review_budget_required')
        if not isinstance(packet.get('objective'),str) or not packet['objective'].strip():raise ValueError('review_objective_required')
        for field in ('review_binding','review_context','session_context','supersedes_review'):
            if field in packet:raise ValueError('fresh_source_request_required:'+field)
        state=_load(root)
        predecessor=None
        if supersedes_review_id:
            predecessor=_item(state,supersedes_review_id)
            _verify(root,predecessor)
            if predecessor['stage']!='cancelled':raise ValueError('cancelled_predecessor_required')
        source_packet=packet
        packet={**packet,'source_request_digest':digest(source_packet),
                'delivery_evidence':[{'name':Path(p).name,'sha256':sha(Path(p).read_bytes())} for p in evidence],
                'review_binding':{k:session[k] for k in ('project','workflow','chat_url')},
                'review_context':supplied,'current_authorization':supplied['current_authorization'],
                'session_context':{'revision':session['revision'],'digest':session.get('context_digest'),
                    'capsule':read_json(root/session['context_ref']) if session.get('context_ref') else None}}
        if supplied.get('alignment_context') is not None:
            packet['alignment_context']=supplied['alignment_context']
        if predecessor:
            packet['supersedes_review']={'review_id':predecessor['review_id'],
                'request_digest':predecessor['request_digest'],'cancellation_digest':predecessor['cancellation_digest']}
        review_id=digest(packet).removeprefix('sha256:')
        if state['active_review'] and state['active_review']!=review_id:raise ValueError('active_review_must_be_reconciled')
        if review_id in state['reviews']:
            prior=state['reviews'][review_id];bundle,_=_verify(root,prior)
            if prior['stage']=='cancelled':raise ValueError('cancelled_request_requires_new_request')
            _no_cancellation_pending(bundle);return prior
        bundle=root/'reviews'/review_id
        emit(bundle,'input-request.json',source_packet)
        emit(bundle,'source-request.json',packet)
        manifest=pack(bundle/'source-request.json',evidence,bundle)
        emit(bundle,'review-context.json',supplied)
        emit(bundle,'delivery-intent.json',{'schema':'sdm-pro-delivery-intent/v1','request_digest':manifest['request_digest'],
            'context_revision':session['revision'],'context_digest':session.get('context_digest'),
            'binding':packet['review_binding'],'model':'Pro','files':manifest['files'],'execution_allowed':False})
        from review_guidance import research_guidance
        prompt=('Use the bound existing Pro conversation. Read the immutable request and evidence; do not execute code or ML. '
                + research_guidance() +
                'When ready for a final decision, return the ordinary BMAD decision fields plus session_context_revision, session_context_digest, and '
                'continuation_acknowledgement {status, objective, fixed_conditions, excluded_explanations, remaining_unknowns, current_scope, no_new_observations}. '
                'Echo fixed_conditions and excluded_explanations exactly from review_context; no_new_observations must be the inverse of new_experiment_observations. '
                'Preserve gap IDs/statuses; without new supporting evidence do not upgrade closure. A decision is never execution authority. '
                'If supersedes_review is supplied, the previous request is locally cancelled; answer only this current request_digest. '
                'request_digest='+manifest['request_digest']+'; session_context_revision='+str(session['revision'])+
                '; session_context_digest='+json.dumps(session.get('context_digest')))
        if supplied.get('alignment_context') is not None:
            from orchestration import alignment_prompt
            prompt += alignment_prompt(supplied['alignment_context'])
        from model_review_bridge import save
        save(bundle,'continuation-prompt.txt',prompt.encode('utf-8'))
        item={'review_id':review_id,'request_digest':manifest['request_digest'],'stage':'prepared',
              'context_revision':session['revision'],'context_digest':session.get('context_digest'),
              'session_head_digest':session['head_digest'],'bundle':str(bundle.resolve()),'attempts':[]}
        if predecessor:item['supersedes_review_id']=predecessor['review_id']
        state['reviews'][review_id]=item;state['active_review']=review_id;_save(root,state)
        return item


def begin_delivery(root,review_id,expected_workflow_revision):
    """Persist transport intent before an outer agent can send anything."""
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state,review_id);bundle,packet=_verify(root,item)
        _no_cancellation_pending(bundle)
        if state['active_review']!=review_id:raise ValueError('active_review_required')
        check(root,packet['review_binding']['chat_url'],item['context_revision'])
        if item['stage']=='delivery-in-progress' or (bundle/'transport-intent.json').exists():
            raise ValueError('transport_intent_must_be_reconciled')
        if item['stage']!='prepared':raise ValueError('delivery_begin_transition_not_allowed')
        _workflow_revision(state,expected_workflow_revision)
        emit(bundle,'transport-intent.json',{'schema':'sdm-pro-transport-intent/v1',
            'request_digest':item['request_digest'],'binding':packet['review_binding'],
            'workflow_revision':state['revision'],'transport_performed_by':'outer-authorized-agent'})
        item['stage']='delivery-in-progress';_save(root,state);return item


def _workflow_revision(state,expected):
    if type(expected) is not int or expected<0:raise ValueError('integer_workflow_revision_required')
    if expected!=state['revision']:raise ValueError('stale_workflow_revision')


def mark_delivery(root,receipt=None,uncertain_reason=None):
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state);bundle,packet=_verify(root,item)
        _no_cancellation_pending(bundle)
        check(root,packet['review_binding']['chat_url'],item['context_revision'])
        if item['stage'] not in {'prepared','delivery-in-progress','waiting-for-review','delivery-uncertain'}:raise ValueError('delivery_transition_not_allowed')
        if uncertain_reason:
            item.update(stage='delivery-uncertain',uncertain_reason=uncertain_reason);_save(root,state);return item
        value=read_json(receipt)
        if value.get('url')!=packet['review_binding']['chat_url'] or value.get('model')!='Pro' or value.get('verified_visible') is not True or value.get('request_digest')!=item['request_digest']:
            raise ValueError('delivery_receipt_mismatch')
        ref='delivery-'+digest(value).removeprefix('sha256:')+'.json';emit(bundle,ref,value)
        if item.get('delivery_receipt')==ref and item['stage']=='waiting-for-review':return item
        item.update(stage='waiting-for-review',delivery_receipt=ref);item.pop('uncertain_reason',None);_save(root,state)
        return item


def _reconciliation(value,packet,item,now,root=None):
    if value.get('schema')!='sdm-pro-review-reconciliation/v1':raise ValueError('reconciliation_schema_required')
    if value.get('url')!=packet['review_binding']['chat_url'] or value.get('model')!='Pro' or value.get('verified_visible') is not True or value.get('request_digest')!=item['request_digest']:
        raise ValueError('reconciliation_receipt_mismatch')
    if not isinstance(value.get('evidence_ref'),str) or not value['evidence_ref'].strip():
        raise ValueError('reconciliation_evidence_required')
    try:
        observed=dt.datetime.fromisoformat(value['observed_at'])
        age=(now-observed).total_seconds()
        fresh=observed.utcoffset() is not None and 0<=age<=300
    except (ValueError,TypeError,KeyError):fresh=False
    if not fresh:raise ValueError('fresh_reconciliation_required')
    outcome=value.get('outcome')
    if outcome not in {'not-delivered','response-complete','generation-stopped'}:
        raise ValueError('remote_outcome_not_terminal')
    if outcome=='not-delivered' and (item.get('delivery_receipt') or item['attempts']):
        raise ValueError('reconciliation_contradicts_delivery')
    if root is not None:
        ref=Path(value['evidence_ref'])
        if ref.is_absolute() or any(part in {'.','..'} for part in ref.parts) or '\\' in value['evidence_ref'] or ':' in value['evidence_ref']:
            raise ValueError('project_relative_reconciliation_evidence_required')
        path=root/ref
        if not path.resolve().is_relative_to(root.resolve()):raise ValueError('project_relative_reconciliation_evidence_required')
        parents=[path,*path.parents[:len(ref.parts)]]
        if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in parents) or not path.is_file():
            raise ValueError('reconciliation_evidence_missing_or_linked')
        if sha(path.read_bytes())!=value.get('evidence_sha256'):raise ValueError('reconciliation_evidence_changed')
    return outcome


def _cancellation_evidence(bundle,value):
    if value.get('reconciliation') is None:return
    expected=value['reconciliation'].get('evidence_sha256')
    ref=value.get('reconciliation_evidence_ref')
    if not isinstance(expected,str) or not isinstance(ref,str) or ref!='reconciliation-evidence/'+expected.removeprefix('sha256:')+'.bin':
        raise ValueError('cancellation_evidence_binding_failed')
    if sha((bundle/ref).read_bytes())!=expected:raise ValueError('cancellation_evidence_changed')


def _finish_cancellation(root,state,item,bundle,packet):
    value=read_json(bundle/'cancellation.json')
    expected={'review_id':item['review_id'],'request_digest':item['request_digest'],
        'binding':packet['review_binding'],'previous_stage':item['stage'],
        'workflow_revision':state['revision'],'context_revision':item['context_revision'],
        'session_head_digest':item['session_head_digest']}
    if value.get('schema')!='sdm-pro-review-cancellation/v1' or any(value.get(k)!=v for k,v in expected.items()):
        raise ValueError('cancellation_intent_mismatch')
    if item['stage'] not in {'prepared','delivery-in-progress','waiting-for-review','delivery-uncertain','rejected'}:
        raise ValueError('cancellation_transition_not_allowed')
    current=check(root,packet['review_binding']['chat_url'],item['context_revision'])
    if current['head_digest']!=item['session_head_digest']:raise ValueError('stale_session_head')
    if state['active_review']!=item['review_id']:raise ValueError('active_review_required')
    proof=value.get('reconciliation')
    if value['remote_outcome']=='never-dispatched':
        if item['stage']!='prepared' or (bundle/'transport-intent.json').exists() or proof is not None:
            raise ValueError('unreconciled_transport_intent')
    elif not isinstance(proof,dict) or digest(proof)!=value.get('reconciliation_digest'):
        raise ValueError('cancellation_proof_changed')
    else:
        try:recorded=dt.datetime.fromisoformat(value['recorded_at'])
        except (KeyError,TypeError,ValueError) as error:raise ValueError('cancellation_timestamp_required') from error
        if _reconciliation(proof,packet,item,recorded)!=value['remote_outcome']:
            raise ValueError('cancellation_outcome_changed')
        _cancellation_evidence(bundle,value)
    if not isinstance(value.get('reason'),str) or not value['reason'].strip() or any(value.get(k) is not False for k in ('context_advanced','remote_stop_performed','execution_allowed')):
        raise ValueError('cancellation_scope_changed')
    item.update(stage='cancelled',cancellation_digest=digest(value),cancellation_ref='cancellation.json')
    state['active_review']=None;_save(root,state);return item


def cancel(root,review_id,reason,expected_workflow_revision,reconciliation=None,fault_hook=lambda stage:None):
    """Retire a local request only after terminal same-chat reconciliation."""
    if not isinstance(reason,str) or not reason.strip():raise ValueError('cancellation_reason_required')
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state,review_id);bundle,packet=_verify(root,item)
        proof=read_json(reconciliation) if reconciliation else None
        path=bundle/'cancellation.json'
        if path.exists():
            value=read_json(path)
            if value['reason']!=reason or value.get('reconciliation')!=proof:
                raise ValueError('immutable_cancellation_conflict')
            if item['stage']=='cancelled':return item
            return _finish_cancellation(root,state,item,bundle,packet)
        _workflow_revision(state,expected_workflow_revision)
        if state['active_review']!=review_id:raise ValueError('active_review_required')
        if item['stage'] not in {'prepared','delivery-in-progress','waiting-for-review','delivery-uncertain','rejected'}:
            raise ValueError('cancellation_transition_not_allowed')
        current=check(root,packet['review_binding']['chat_url'],item['context_revision'])
        if current['head_digest']!=item['session_head_digest']:raise ValueError('stale_session_head')
        now=dt.datetime.now(dt.timezone.utc)
        if item['stage']=='prepared' and not (bundle/'transport-intent.json').exists():
            if proof is not None:raise ValueError('unsent_cancellation_needs_no_receipt')
            outcome='never-dispatched'
        else:
            if proof is None:raise ValueError('same_chat_reconciliation_required')
            outcome=_reconciliation(proof,packet,item,now,root)
        evidence_ref=None
        if proof:
            evidence_ref='reconciliation-evidence/'+proof['evidence_sha256'].removeprefix('sha256:')+'.bin'
            from model_review_bridge import save
            observed=(root/proof['evidence_ref']).read_bytes()
            if sha(observed)!=proof['evidence_sha256']:raise ValueError('reconciliation_evidence_changed')
            save(bundle,evidence_ref,observed)
        value={'schema':'sdm-pro-review-cancellation/v1','review_id':review_id,
            'request_digest':item['request_digest'],'binding':packet['review_binding'],
            'previous_stage':item['stage'],'workflow_revision':state['revision'],
            'context_revision':item['context_revision'],'session_head_digest':item['session_head_digest'],
            'reason':reason,'recorded_at':now.isoformat(),'remote_outcome':outcome,'reconciliation':proof,
            'reconciliation_digest':digest(proof) if proof else None,
            'reconciliation_evidence_ref':evidence_ref,
            'context_advanced':False,'remote_stop_performed':False,'execution_allowed':False}
        emit(bundle,'cancellation.json',value);fault_hook('cancellation-recorded')
        return _finish_cancellation(root,state,item,bundle,packet)


def _decision(raw,packet,item):
    value=validate(raw,packet)
    if type(value.get('session_context_revision')) is not int or value['session_context_revision']!=item['context_revision'] or value.get('session_context_digest')!=item['context_digest']:
        raise ValueError('response_context_mismatch')
    ctx=packet['review_context'];ack=value.get('continuation_acknowledgement',{})
    if not isinstance(ack,dict) or any(not ack.get(k) for k in ('status','objective','current_scope')):
        raise ValueError('continuation_acknowledgement_required')
    for field in ('fixed_conditions','excluded_explanations'):
        if ack.get(field)!=ctx[field]:raise ValueError('acknowledgement_context_mismatch:'+field)
    if not isinstance(ack.get('remaining_unknowns'),list) or not all(isinstance(x,str) for x in ack['remaining_unknowns']):
        raise ValueError('typed_remaining_unknowns_required')
    if ack.get('no_new_observations') is not (not ctx['new_experiment_observations']):raise ValueError('observation_scope_mismatch')
    prior={g['gap_id']:g for g in ctx.get('gap_matrix',[])}
    current=value.get('evidence_gap_matrix',[])
    if prior:
        if not isinstance(current,list) or len(current)!=len(prior) or {g.get('gap_id') for g in current}!=set(prior):
            raise ValueError('stable_gap_ids_required')
        ranks={'open':0,'partially-closed':1,'closed':2}
        for gap in current:
            if gap.get('status') not in ranks:raise ValueError('typed_gap_status_required')
            if not ctx['new_experiment_observations'] and ranks[gap['status']]>ranks[prior[gap['gap_id']]['status']]:
                raise ValueError('gap_upgrade_without_evidence')
    return value


def ingest(root,response,receipt,review_id=None,fault_hook=lambda stage:None):
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state,review_id);bundle,packet=_verify(root,item)
        body=Path(response).read_bytes();body_hash=sha(body)
        if item['stage']=='cancelled':
            if state['active_review']:
                _no_cancellation_pending(_bundle(root,_item(state)))
            proof=read_json(receipt)
            if proof.get('url')!=packet['review_binding']['chat_url'] or proof.get('model')!='Pro' or proof.get('verified_visible') is not True or proof.get('request_digest')!=item['request_digest']:
                raise ValueError('return_receipt_mismatch')
            from model_review_bridge import save
            ref='late-responses/'+body_hash.removeprefix('sha256:')+'.json'
            save(bundle,ref,body)
            receipt_ref='late-responses/receipt-'+digest(proof).removeprefix('sha256:')+'.json'
            emit(bundle,receipt_ref,proof)
            attempt={'response_ref':ref,'receipt_ref':receipt_ref,'error':'cancelled_review_response_retained'}
            if attempt not in item['attempts']:
                item['attempts'].append(attempt);_save(root,state)
            raise ValueError('cancelled_review_response_retained')
        _no_cancellation_pending(bundle)
        if item['stage'] in {'validated','planned'}:
            if item['response_sha256']!=body_hash:raise ValueError('accepted_response_conflict')
            if sha((bundle/'analysis-response.json').read_bytes())!=body_hash:raise ValueError('accepted_response_changed')
            check(root,packet['review_binding']['chat_url']);return item
        if item['stage'] not in {'waiting-for-review','received','rejected','delivery-uncertain'}:raise ValueError('response_before_delivery')
        ref='attempts/'+body_hash.removeprefix('sha256:')+'.json'
        from model_review_bridge import save
        save(bundle,ref,body)
        proof=read_json(receipt)
        if proof.get('url')!=packet['review_binding']['chat_url'] or proof.get('model')!='Pro' or proof.get('verified_visible') is not True or proof.get('request_digest')!=item['request_digest']:
            raise ValueError('return_receipt_mismatch')
        if item['stage']=='received' and item.get('return_receipt'):
            if item.get('response_sha256')!=body_hash:raise ValueError('received_response_must_be_reconciled')
            proof=read_json(bundle/item['return_receipt'])
        try:
            decision=_decision(read_json(bundle/ref),packet,item)
        except (ValueError,TypeError,KeyError) as error:
            item.update(stage='rejected',rejection=str(error));item['attempts'].append({'response_ref':ref,'error':str(error)});_save(root,state)
            raise ValueError('review_rejected:'+str(error)) from error
        item.update(stage='received',response_ref=ref,response_sha256=body_hash)
        receipt_ref='return-'+digest(proof).removeprefix('sha256:')+'.json';emit(bundle,receipt_ref,proof)
        item['return_receipt']=receipt_ref;_save(root,state);fault_hook('received')
        session=check(root,proof['url'])
        if session['revision']==item['context_revision']:
            if session['head_digest']!=item['session_head_digest']:raise ValueError('stale_session_head')
            emit(bundle,'chat-receipt.json',proof)
            accept(bundle,bundle/ref);fault_hook('accepted-response')
            session=_record(root,bundle,proof['url'],item['context_revision'])
            fault_hook('session-recorded')
        elif session['revision']==item['context_revision']+1:
            event=read_json(root/'records'/f"{session['revision']:04d}.json")
            if event['request_digest']!=item['request_digest'] or event['decision_digest']!=digest(decision) or event['response_sha256']!=body_hash:
                raise ValueError('stale_session_revision')
        else:raise ValueError('stale_session_revision')
        report={'schema':'sdm-pro-review-validation/v1','status':'pass','validator':'loop_analysis.validate + session CAS + typed continuation checks',
                'request_digest':item['request_digest'],'decision_digest':digest(decision),'response_sha256':body_hash,
                'context_revision_in':item['context_revision'],'context_revision_out':session['revision'],
                'scientific_validity_certified':False,'execution_allowed':False}
        emit(bundle,'validation-report.json',report)
        item.update(stage='validated',decision_digest=digest(decision),accepted_context_revision=session['revision']);item.pop('rejection',None);_save(root,state)
        return item


def plan(root,review_id=None):
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state,review_id);bundle,packet=_verify(root,item)
        _no_cancellation_pending(bundle)
        if item['stage'] not in {'validated','planned'}:raise ValueError('validated_review_required')
        decision=read_json(bundle/'validated-decision.json')
        check(root,packet['review_binding']['chat_url'],item['accepted_context_revision'])
        if digest(decision)!=item['decision_digest']:raise ValueError('accepted_decision_changed')
        value={'schema':'sdm-pro-review-plan/v1','mode':'planning-only','project':state['project'],'workflow':state['workflow'],
               'request_digest':item['request_digest'],'decision_digest':item['decision_digest'],
               'context_revision':item['accepted_context_revision'],'fixed_conditions':packet['review_context']['fixed_conditions'],
               'next_parameters':decision.get('next_parameters'),'proposal':decision.get('next_experiment_proposal'),
               'remaining_rounds':packet['remaining_rounds'],'execution_allowed':False,'training_dispatched':False,
               'model_inference_dispatched':False,'readiness':'requires-current-authority-and-fresh-preflight',
               'blockers':['review_does_not_authorize_execution','fresh_execution_preflight_required']}
        if packet['remaining_rounds']<=0:value['blockers'].append('historical_budget_exhausted')
        if decision['action']!='run-next-round':value['blockers'].append('decision_requests_stop')
        emit(bundle,'execution-plan.json',value)
        if item['stage']!='planned':
            item['stage']='planned'
            if state['active_review']==item['review_id']:state['active_review']=None
            _save(root,state)
        return value


def status(root):
    root=Path(root)
    with locked(root):
        state=_load(root);session=read_json(root/'session.json');check(root,session['chat_url'])
        for item in state['reviews'].values():_verify(root,item)
        return state


def recover(root):
    root=Path(root)
    with locked(root):
        state=_load(root);current=read_json(root/'session.json');check(root,current['chat_url'])
        if not state['active_review']:return state
        item=_item(state);bundle,packet=_verify(root,item)
        if (bundle/'cancellation.json').exists():return _finish_cancellation(root,state,item,bundle,packet)
        if item['stage']=='delivery-in-progress' or (item['stage']=='prepared' and (bundle/'transport-intent.json').exists()):
            item.update(stage='delivery-uncertain',uncertain_reason='transport_intent_without_confirmed_receipt')
            _save(root,state);return item
        response=(bundle/item['response_ref'],bundle/item['return_receipt'],item['review_id']) if item['stage']=='received' else None
        if response is None:return item
    return ingest(root,*response)


def source_decision(root,review_id,source_request,*,allow_historical=False):
    """Return a bound validation view; retain original Pro response unchanged."""
    root=Path(root)
    with locked(root):
        state=_load(root);item=_item(state,review_id);bundle,packet=_verify(root,item)
        if item['stage'] not in {'validated','planned'}:raise ValueError('validated_review_required')
        if read_json(bundle/'input-request.json')!=source_request or packet['source_request_digest']!=digest(source_request):
            raise ValueError('external_source_request_mismatch')
        raw=read_json(bundle/'analysis-response.json')
        if sha((bundle/'analysis-response.json').read_bytes())!=item['response_sha256']:raise ValueError('accepted_response_changed')
        value=_decision(raw,packet,item)
        if digest(value)!=item['decision_digest']:raise ValueError('accepted_decision_changed')
        if allow_historical:
            # A later accepted turn does not erase earlier dialogue evidence.
            # Check the entire current chain AND the exact historical event.
            state_now=check(root,packet['review_binding']['chat_url'])
            revision=item['accepted_context_revision']
            if not 1<=revision<=state_now['revision']:raise ValueError('historical_review_revision_missing')
            event=read_json(root/'records'/f'{revision:04d}.json')
            for key,expected in {'request_digest':item['request_digest'],'decision_digest':item['decision_digest'],
                    'review_root':str(bundle),'response_sha256':item['response_sha256']}.items():
                if event.get(key)!=expected:raise ValueError('historical_review_event_mismatch')
        else:check(root,packet['review_binding']['chat_url'],item['accepted_context_revision'])
        projected={**value,'request_digest':digest(source_request),'external_review_binding':{
            'project':state['project'],'workflow':state['workflow'],'review_id':review_id,
            'review_request_digest':item['request_digest'],'review_decision_digest':item['decision_digest'],
            'review_root':str(bundle.resolve()),'original_response_sha256':item['response_sha256']}}
        return validate(projected,source_request)


def export_loop_result(root,review_id,source_request,output):
    source=read_json(source_request);decision=source_decision(root,review_id,source)
    value={'schema':'sdm-external-review-result/v1','source_request_digest':digest(source),
           'workflow_root':str(Path(root).resolve()),'review_id':review_id,
           'binding':decision['external_review_binding']}
    output=Path(output);emit(output.parent,output.name,value);return value


def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('prepare');p.add_argument('--root',required=True);p.add_argument('--request',required=True);p.add_argument('--context',required=True);p.add_argument('--evidence',action='append',default=[]);p.add_argument('--observed-url',required=True);p.add_argument('--expected-revision',type=int,required=True);p.add_argument('--supersedes-review-id')
    p=sub.add_parser('begin-delivery');p.add_argument('--root',required=True);p.add_argument('--review-id',required=True);p.add_argument('--expected-workflow-revision',type=int,required=True)
    p=sub.add_parser('cancel');p.add_argument('--root',required=True);p.add_argument('--review-id',required=True);p.add_argument('--reason',required=True);p.add_argument('--expected-workflow-revision',type=int,required=True);p.add_argument('--reconciliation')
    p=sub.add_parser('delivery');p.add_argument('--root',required=True);p.add_argument('--receipt');p.add_argument('--uncertain-reason')
    p=sub.add_parser('ingest');p.add_argument('--root',required=True);p.add_argument('--response',required=True);p.add_argument('--receipt',required=True);p.add_argument('--review-id')
    for name in ('plan','status','recover'):
        p=sub.add_parser(name);p.add_argument('--root',required=True)
        if name=='plan':p.add_argument('--review-id')
    p=sub.add_parser('export-loop-result');p.add_argument('--root',required=True);p.add_argument('--review-id',required=True);p.add_argument('--source-request',required=True);p.add_argument('--output',required=True)
    args=vars(parser.parse_args());action=args.pop('action')
    result={'prepare':prepare,'begin-delivery':begin_delivery,'cancel':cancel,'delivery':mark_delivery,'ingest':ingest,'plan':plan,'status':status,'recover':recover,'export-loop-result':export_loop_result}[action](**args)
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))

if __name__=='__main__':main()
