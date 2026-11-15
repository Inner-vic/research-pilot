"""Local fixed-chat binding and versioned context capsules; no browser/model calls."""
import argparse
import json
import re
from pathlib import Path
from loop_analysis import validate
from model_review_bridge import sha, read, emit
from task_pipeline import digest
from review_storage import atomic_json, locked

URL=re.compile(r'https://chatgpt\.com/c/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})')

def atomic(path,value):
    atomic_json(path,value)

def review(bundle):
    bundle=Path(bundle).resolve();manifest=read(bundle/'manifest.json')
    for name,expected in manifest['files'].items():
        if Path(name).name!=name or sha((bundle/name).read_bytes())!=expected:
            raise ValueError('review_integrity_failed')
    packet=read(bundle/'analysis-request.json');raw=read(bundle/'analysis-response.json')
    if digest(packet)!=manifest['request_digest']: raise ValueError('request_digest_mismatch')
    decision=validate(raw,packet)
    return bundle,packet,decision,read(bundle/'chat-receipt.json')

def bind(root,project,workflow,url):
    with locked(root):
        return _bind(root,project,workflow,url)

def _bind(root,project,workflow,url):
    if not all(isinstance(x,str) and x.strip() for x in (project,workflow)):
        raise ValueError('project_workflow_identity_required')
    match=URL.fullmatch(url)
    if not match: raise ValueError('canonical_chat_url_required')
    root=Path(root);path=root/'session.json'
    value={'schema':'sdm-pro-review-session/v1','project':project,'workflow':workflow,
           'chat_url':url,'conversation_id':match[1],'model':'Pro','epoch':1,
           'new_chat_policy':'human-authorized-rollover-only','revision':0,
           'head_digest':None,'context_ref':None}
    if path.exists():
        old=read(path)
        for key in ['project','workflow','chat_url','model']:
            if old.get(key)!=value[key]: raise ValueError('session_binding_conflict')
        return old
    atomic(path,value);return value

def check(root,observed_url,expected_revision=None):
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision<0):
        raise ValueError('integer_session_revision_required')
    root=Path(root);state=read(root/'session.json')
    if type(state.get('revision')) is not int or state['revision']<0:
        raise ValueError('integer_session_revision_required')
    if observed_url!=state['chat_url']: raise ValueError('wrong_conversation')
    if expected_revision is not None and state['revision']!=expected_revision:
        raise ValueError('stale_session_revision')
    previous=None
    for revision in range(1,state['revision']+1):
        event=read(root/'records'/f'{revision:04d}.json')
        if event['revision']!=revision or event['previous_digest']!=previous:
            raise ValueError('session_history_chain_failed')
        if event['chat_url']!=state['chat_url']: raise ValueError('history_chat_mismatch')
        previous=digest(event)
    if previous!=state['head_digest']: raise ValueError('session_head_mismatch')
    if state['context_ref']:
        context=read(root/state['context_ref'])
        if context['revision']!=state['revision'] or context['head_digest']!=previous:
            raise ValueError('context_revision_mismatch')
        if digest(context)!=state['context_digest']: raise ValueError('context_integrity_failed')
    return state

def record(root,bundle,observed_url,expected_revision,bootstrap=False):
    with locked(root):
        return _record(root,bundle,observed_url,expected_revision,bootstrap)

def _record(root,bundle,observed_url,expected_revision,bootstrap=False):
    root=Path(root);state=check(root,observed_url,expected_revision)
    bundle,packet,decision,receipt=review(bundle)
    binding=packet.get('review_binding')
    if binding and any(binding.get(k)!=state[k] for k in ('project','workflow','chat_url')):
        raise ValueError('review_project_binding_mismatch')
    if receipt.get('url')!=state['chat_url'] or receipt.get('model')!='Pro' or receipt.get('verified_visible') is not True:
        raise ValueError('review_chat_or_model_mismatch')
    if bootstrap and state['revision']!=0:
        raise ValueError('bootstrap_only_for_initial_adoption')
    current=digest(decision)
    if state['revision']:
        last=read(root/'records'/f"{state['revision']:04d}.json")
        if last['decision_digest']==current:return state
    if not bootstrap and (type(decision.get('session_context_revision')) is not int or decision.get('session_context_revision')!=state['revision'] or decision.get('session_context_digest')!=state.get('context_digest')):
        raise ValueError('response_context_mismatch')
    revision=state['revision']+1
    event={'schema':'sdm-pro-session-record/v1','revision':revision,'previous_digest':state['head_digest'],
           'chat_url':state['chat_url'],'request_digest':digest(packet),'decision_digest':current,
           'review_root':str(bundle),'response_sha256':sha((bundle/'analysis-response.json').read_bytes())}
    emit(root/'records',f'{revision:04d}.json',event)
    supplied=packet.get('review_context',{})
    acknowledgement=decision.get('continuation_acknowledgement',{})
    prior=read(root/state['context_ref']) if state.get('context_ref') else {}
    context={'schema':'sdm-pro-context-capsule/v2','revision':revision,'head_digest':digest(event),
             'project':state['project'],'workflow':state['workflow'],'chat_url':state['chat_url'],
             'objective':packet['objective'],'request_digest':digest(packet),'decision_digest':current,
             'facts':decision['facts'],'hypotheses':decision['hypotheses'],
             'assessment':decision['previous_experiment_assessment'],
             'gap_matrix':decision.get('evidence_gap_matrix',[]),
             'next_plan':decision.get('next_experiment_proposal',{}),
             'fixed_conditions':supplied.get('fixed_conditions',acknowledgement.get('fixed_conditions',prior.get('fixed_conditions',{}))),
             'excluded_explanations':supplied.get('excluded_explanations',acknowledgement.get('excluded_explanations',prior.get('excluded_explanations',[]))),
             'remaining_unknowns':acknowledgement.get('remaining_unknowns',supplied.get('remaining_unknowns',prior.get('remaining_unknowns',[]))),
             'continuation_acknowledgement':acknowledgement,
             'current_authorization':packet.get('current_authorization',supplied.get('current_authorization',prior.get('current_authorization',{}))),
             'authorization_semantics':'Historical scope snapshot; recheck current user authority and fresh preflight before any execution.',
             'gap_lineage':read(bundle/'gap-lineage.json') if (bundle/'gap-lineage.json').exists() else None,
             'gap_ids':'Preserve project-owned historical IDs; short IDs from independent reviews require an explicit topic correspondence.',
             'evidence_refs':{'review_root':str(bundle),'manifest':str(bundle/'manifest.json')},
             'execution_scope':'This capsule is review context, never execution authorization.',
             'historical_remaining_rounds':packet['remaining_rounds'],
             'memory_limit':'A summary does not remove prior ChatGPT messages or guarantee remembered content.'}
    ref=f'contexts/{revision:04d}.json';emit(root,ref,context)
    state={**state,'revision':revision,'head_digest':digest(event),'context_ref':ref,'context_digest':digest(context)}
    atomic(root/'session.json',state)
    check(root,observed_url,revision);return state

def main():
    parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='command',required=True)
    b=sub.add_parser('bind');b.add_argument('--root',required=True);b.add_argument('--project',required=True);b.add_argument('--workflow',required=True);b.add_argument('--url',required=True)
    c=sub.add_parser('check');c.add_argument('--root',required=True);c.add_argument('--observed-url',required=True);c.add_argument('--expected-revision',type=int)
    r=sub.add_parser('record');r.add_argument('--root',required=True);r.add_argument('--bundle',required=True);r.add_argument('--observed-url',required=True);r.add_argument('--expected-revision',required=True,type=int);r.add_argument('--bootstrap',action='store_true')
    args=vars(parser.parse_args());command=args.pop('command')
    print(json.dumps({'status':'pass','session':{'bind':bind,'check':check,'record':record}[command](**args)},ensure_ascii=False))
if __name__=='__main__':main()
