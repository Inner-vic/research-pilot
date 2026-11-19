"""Project-specific evidence preparation for the ONE existing SDM controller.

No loop, submission, scientific acceptance or model client is implemented here.
The original coordinator/Generator-Critic/native implementation own those duties.
"""
import copy,time
from pathlib import Path
from component_payload import ref,contract,executed_request_files
from component_research import reference,progress,analyze,round_evidence
from component_candidates import registry,recipe_history
from component_event_source import publish_bytes
from research_replan import once
from review_storage import read_json

def replan(owner,state,current,folder):
    recorded=owner.record_result(state,current,folder)
    if recorded is None:return
    ready,rows=recorded;delegation=read_json(reference(owner.spec['delegation']))
    sources=[owner.spec['baseline_source'],owner.spec['initial_request'],current['source']['plan']]
    sources.extend(owner.spec.get('additional_evidence',[]))
    if owner.spec.get('sdm_governance'):sources.append(owner.spec['sdm_governance']['seed'])
    capabilities=contract(owner.spec['baseline_source'],folder/'project-capabilities',owner.spec['comparison_identity'],
        owner.manager_config,read_json(reference(owner.spec['initial_request']))['uid'],
        training_limits=delegation['training_limits'],next_round=len(rows)+1,workload=owner.spec['workload'])
    bound=read_json(reference(capabilities));sources.extend([capabilities,*bound['protected_inputs'],owner.spec['workload']['project_contract']])
    sources.extend([*owner.spec.get('previous_implementation_feedback',[]),*state.get('implementation_block_history',[]),*current.get('capability_feedback',[])])
    spec={'version':3 if rows else 2,'parent_route':'project','result_contract':'sdm-project-result/v1',
        'objective':owner.spec['objective'],'rounds':rows,'minimum_rounds':owner.spec.get('minimum_rounds',1),
        'minimum_revisions':owner.spec.get('minimum_revisions',0),'sources':sources,
        'constraints':'Use the supplied PROJECT entrypoints, editable-file list, parameters, protected evaluator/preflight and metric direction. '
        'Do not assume a binary tensor, PyTorch factory, model.py/trainer.py names or LPAI-specific project facts. '
        'Retain original SDM contracts, accepted plan, comparison, exact execution identity and non-renewed budget. '
        'All ML stays remote; review never grants execution authority. '+delegation['resources']}
    candidate=None
    if rows:
        observed=list(dict.fromkeys(state.get('observed_task_uids',[])+[round_evidence(r['result'])['uid'] for r in rows]+
            [read_json(reference(a['result']))['uid'] for a in state['attempts'] if a.get('result')]))
        state['observed_task_uids']=observed
        candidate=registry(rows,owner.spec['decision_policy'],observed)
        once(folder/'candidate-registry.json',candidate);sources.append(ref(folder/'candidate-registry.json'))
        once(folder/'result-analysis.json',analyze(rows));current['analysis']=ref(folder/'result-analysis.json');sources.append(current['analysis'])
        sources.extend([recipe_history(rows,folder),ref(folder/'historical-recipe-index.json')])
        spec.update(decision_policy=owner.spec['decision_policy'],observed_task_uids=observed)
        state.update(incumbent=candidate['incumbent'],candidate_goal=candidate['goal'],submission_budget=candidate['budget'])
        state['goal_assessment']={**state['goal_assessment'],'chain_qualification_met':state['goal_assessment']['goal_met'],
            'goal_met':candidate['goal']['met'],'candidate_goal':candidate['goal'],'submission_budget':candidate['budget']}
    if not ready.get('valid_training_round'):
        spec['parent_diagnosis']=current['result']
        spec['constraints']+=' This exact terminal failure has no quality score. Diagnose real evidence and form a concrete supported repair or explain the protected capability gap.'
        if not rows:spec['preparation_recovery']={'version':1}
    selected=[('executed-parent',current['source']['plan'],current['source']['expected'].get('code_sha256'))]
    if candidate and candidate['incumbent'].get('manager_plan'):
        incumbent=candidate['incumbent'];selected.append(('executed-incumbent',incumbent['manager_plan'],round_evidence(incumbent['result'])['result']['code_sha256']))
    for label,plan_ref,checksum in selected:
        files,proof=executed_request_files(read_json(reference(plan_ref))['request'],checksum)
        root=folder/label;root.mkdir(exist_ok=True)
        for name,text in files.items():
            path=root/name;publish_bytes(path,text.encode());sources.append(ref(path))
        once(root/'proof.json',{**proof,'manager_plan':plan_ref});sources.append(ref(root/'proof.json'))
    config=owner.base_project();config['research_replan']=spec
    if owner.spec.get('sdm_intent'):
        from research_intent import for_review
        config['sdm_intent']=for_review(owner.spec['sdm_intent'])
    config['proposal_review']=copy.deepcopy(config['proposal_review'])
    config['proposal_review']['source_budget']={'version':1,'serialized_bytes':96*1024,'max_file_bytes':32768,
        'priority':[str(reference(r)) for r in sources if Path(r['path']).parent.name in ('executed-parent','executed-incumbent')]+[str(reference(capabilities))]}
    path=folder/'replan-project.json';once(path,config);current['replan_root']=str(folder/'replan')
    current['job']=owner.coordinator('research-replan',path,folder/'replan')
    if current.get('result_handoff') is not None:current['result_handoff'].update(model_requested=True,analysis_enqueued_at=time.time(),job_id=current['job'])
    current['stage']='reviewing-plan'
