"""Component evidence adapter for original SDM proposal/research semantics.

Current producer adapter is explicitly binary-classification/v1, not a claim of
multi-class/IoU support. No model loop or platform driver. A finished training is evidence for the next
plan; it does not by itself satisfy the multi-round research objective.
"""
import hashlib
import json
import math
from pathlib import Path

from review_storage import atomic_json, read_json, native_path
from task_pipeline import digest


def reference(ref):
    path = Path(ref.get('path',''))
    if not path.is_absolute() or not native_path(path).is_file():
        raise ValueError('absolute_component_research_evidence_required')
    if hashlib.sha256(native_path(path).read_bytes()).hexdigest() != ref.get('sha256','').removeprefix('sha256:'):
        raise ValueError('component_research_evidence_changed')
    return path


def round_evidence(ref):
    path = reference(ref); ready = read_json(path)
    if ready.get('schema') != 'sdm-component-result-ready/v1' or ready.get('valid_training_round') is not True:
        raise ValueError('verified_component_training_round_required')
    root = Path(ready.get('artifact_root',''))
    if not root.is_absolute() or set(ready.get('artifact_sha256',{})) != {'split.json','predictions.json','result.json','checkpoint.pt'}:
        raise ValueError('complete_archived_component_artifacts_required')
    sources=[path]
    for name, expected in ready['artifact_sha256'].items():
        artifact=root/name
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
            raise ValueError('archived_component_artifact_changed')
        if name!='checkpoint.pt':sources.append(artifact)
    result=read_json(root/'result.json'); predictions=read_json(root/'predictions.json')
    split=read_json(root/'split.json')
    if result.get('schema')!='sdm-component-result/v1' or result.get('test_used') is not False or split.get('test_used') is not False:
        raise ValueError('fixed_component_evaluation_scope_required')
    y,p=predictions.get('y_true',[]),predictions.get('y_pred',[])
    if (not y or len(y)!=len(p) or set(y)!={0,1} or not set(p)<={0,1}
            or predictions.get('row_ids')!=split.get('validation_ids')):
        raise ValueError('archived_component_predictions_required')
    cm=[[sum(a==i and b==j for a,b in zip(y,p)) for j in (0,1)] for i in (0,1)]
    actual=sum(2*cm[i][i]/max(1,2*cm[i][i]+cm[i][1-i]+cm[1-i][i]) for i in (0,1))/2
    reported=result.get('validation',{}).get('macro_f1')
    if (type(reported) not in (int,float) or not math.isfinite(reported)
            or abs(reported-actual)>1e-10 or abs(ready.get('primary_metric',-1)-actual)>1e-10
            or hashlib.sha256((root/'split.json').read_bytes()).hexdigest()!=result.get('split_sha256')):
        raise ValueError('component_research_metric_evidence_mismatch')
    fair={k:result.get(k) for k in ('protocol','source_sha256','split_sha256','selection')}
    if any(not isinstance(v,str) or not v for v in fair.values()):
        raise ValueError('component_fair_comparison_identity_required')
    return {'uid':ready['uid'],'primary_metric':actual,'fair_comparison':fair,
            'manifest':ref,'result':result,'sources':sources}


def analyze(rows):
    """Recount verified results before a model proposes another experiment.

    This is descriptive evidence, not a hard-coded scientific decision. The
    author and critic still see the original curves, predictions and code.
    """
    actual = [round_evidence(row['result']) for row in rows]
    if not actual or any(row['fair_comparison'] != actual[0]['fair_comparison'] for row in actual):
        raise ValueError('verified_comparable_results_required_for_analysis')
    summary = []
    for index, row in enumerate(actual):
        result = row['result']; history = result.get('history', [])
        base=actual[index-1] if index else None
        base_kind='legacy chronological predecessor' if base else 'initial baseline'
        if rows[index].get('accepted_plan'):
            accepted=read_json(reference(rows[index]['accepted_plan']))
            selected=accepted.get('selected_candidate')
            if selected:
                matches=[previous for previous in actual[:index] if previous['uid']==selected['uid']]
                selected_result=round_evidence(selected['result'])
                if (len(matches)!=1 or selected_result['uid']!=selected['uid']
                        or selected_result['primary_metric']!=matches[0]['primary_metric']
                        or selected_result['fair_comparison']!=row['fair_comparison']):
                    raise ValueError('verified_prior_experimental_base_required')
                base=matches[0];base_kind='explicit reviewed candidate selection'
        point = {'uid': row['uid'], 'result': row['manifest'], 'config': result['config'],
            'validation': result['validation'], 'train': result.get('train'),
            'selected_epoch': result['selected_epoch'],
            'delta_from_baseline': row['primary_metric'] - actual[0]['primary_metric'],
            'delta_from_parent': None if not index else row['primary_metric'] - actual[index-1]['primary_metric'],
            'trigger_predecessor_uid':None if not index else actual[index-1]['uid'],
            'experimental_base_uid':None if base is None else base['uid'],
            'experimental_base_kind':base_kind,
            'delta_from_experimental_base':None if base is None else row['primary_metric']-base['primary_metric'],
            'reported_elapsed_seconds':result.get('elapsed_seconds'),
            'curve_points': len(history)}
        if isinstance(result.get('train'), dict):
            train_metric = result['train'].get('macro_f1')
            if type(train_metric) in (int, float) and math.isfinite(train_metric):
                point['train_validation_gap'] = train_metric - row['primary_metric']
        if base:
            previous=read_json(next(p for p in base['sources'] if p.name=='predictions.json'))
            current=read_json(next(p for p in row['sources'] if p.name=='predictions.json'))
            if previous['row_ids']!=current['row_ids'] or previous['y_true']!=current['y_true']:
                raise ValueError('paired_candidate_row_identity_changed')
            labels=previous['y_true'];before=previous['y_pred'];after=current['y_pred']
            prefix=0
            for a,b in zip(base['result'].get('history',[]),history):
                if a!=b:break
                prefix+=1
            point['paired_comparison']={
                'base_uid':base['uid'],'base_result':base['manifest'],
                'corrected_errors':sum(a!=y and b==y for a,b,y in zip(before,after,labels)),
                'introduced_errors':sum(a==y and b!=y for a,b,y in zip(before,after,labels)),
                'shared_errors':sum(a!=y and b!=y for a,b,y in zip(before,after,labels)),
                'identical_prefix_epochs':prefix,
                'first_different_epoch':history[prefix]['epoch'] if prefix<len(history) else None,
                'declared_lr_decay_epoch':result['config'].get('lr_decay_epoch'),
                'base_final_epoch':base['result'].get('history',[])[-1] if base['result'].get('history') else None,
                'candidate_final_epoch':history[-1] if history else None,
                'interpretation':'Observed paired outcomes and declared configuration, not proof of a unique causal mechanism.'}
        summary.append(point)
    best_index=max(range(len(actual)),key=lambda i:actual[i]['primary_metric'])
    best=actual[best_index];tail=actual[best_index+1:]
    error_sets=[];supports=None
    for row in actual[best_index:]:
        prediction=read_json(next(p for p in row['sources'] if p.name=='predictions.json'))
        labels=prediction['y_true'];predicted=prediction['y_pred']
        error_sets.append({i for i,(a,b) in enumerate(zip(labels,predicted)) if a!=b})
        supports=labels
    persistent=set.intersection(*error_sets)
    trajectory={'incumbent_uid':best['uid'],'incumbent_metric':best['primary_metric'],
        'last_improvement_round':best_index+1,'non_improving_trials_since_best':len(tail),
        'latest_delta_from_incumbent':actual[-1]['primary_metric']-best['primary_metric'],
        'incumbent_errors_never_corrected_by_later_candidates':len(persistent),
        'persistent_error_class_counts':{str(y):sum(supports[i]==y for i in persistent) for y in (0,1)},
        'interpretation':'Observed plateau/error overlap, not a trial quota or causal diagnosis. '
            'Use failed interventions to update competing hypotheses. Novel code alone is not decision value; '
            'defend another training run against obtaining discriminating evidence or revising assumptions. '
            'Do not infer that a failed intervention disproves its entire mechanism, or stop solely by this count.'}
    from research_diagnostics import summarize
    return {'schema': 'sdm-verified-result-analysis/v1', 'rounds': summary,'diagnostics':summarize(actual),
        'incumbent_trajectory':trajectory,
        'comparison_identity': actual[0]['fair_comparison'],
        'primary_metric': 'validation.macro_f1', 'metric_scope': 'fixed adaptive validation, not untouched test',
        'parent_delta_semantics':'delta_from_parent is the chronological trigger predecessor for compatibility. '
            'Use delta_from_experimental_base for the actually selected recipe; rollback can make them different.',
        'interpretation_required': 'Explain the observed gain or regression against raw curves and predictions. '
            'Separate optimization, representation, data and measurement hypotheses. A negative result is not an improvement; '
            'choose one informative next intervention with a falsifier, rather than repeating an exhausted parameter search.',
        'execution_authority': False}


def progress(rows, minimum_rounds=3, minimum_revisions=2):
    """The final accepted round must improve over the first, not a cherry pick."""
    if not isinstance(rows,list) or not rows:
        raise ValueError('actual_component_round_history_required')
    if type(minimum_rounds) is not int or minimum_rounds<1 or type(minimum_revisions) is not int or minimum_revisions<0:
        raise ValueError('explicit_component_goal_requirements_required')
    rounds=[];revisions=0;seen=set()
    for row in rows:
        current=round_evidence(row['result'])
        if current['uid'] in seen:raise ValueError('same_component_task_cannot_count_twice')
        seen.add(current['uid'])
        if rounds:
            if current['fair_comparison']!=rounds[0]['fair_comparison']:
                raise ValueError('component_evaluation_protocol_changed')
            plan_path=reference(row.get('accepted_plan',{}));plan=read_json(plan_path)
            if (plan.get('schema')!='sdm-research-plan/v1' or plan.get('research_goal_met') is not False
                    or plan.get('parent_result_sha256')!=rows[len(rounds)-1]['result']['sha256'].removeprefix('sha256:')):
                raise ValueError('result_driven_parent_plan_link_required')
            from research_replan import validate_direction
            direction=plan.get('research_revision',{})
            validate_direction(direction,plan.get('evidence_ids',[]),strategy=True)
            if direction['action'] not in ('continue-plan','refine-current','revise-plan','diagnose'):
                raise ValueError('trained_successor_requires_accepted_intervention')
            if plan_path in [r.get('accepted_plan_path') for r in rounds]:
                raise ValueError('same_plan_cannot_count_two_improvements')
            # Acceptance is still revalidated at execution by the original gate;
            # this immutable link prevents counting a repair or unreviewed idea.
            receipt=read_json(reference(plan.get('accepted_review_ref',{})))
            if receipt.get('approved') is not True or receipt.get('response',{}).get('verdict')!='approve':
                raise ValueError('accepted_successor_review_required')
            if receipt.get('proposal')!=plan.get('proposal') or plan.get('proposal',{}).get('research_revision')!=direction:
                raise ValueError('accepted_successor_proposal_changed')
            current['accepted_plan_path']=plan_path
            revisions+=int(direction['action']!='diagnose')
        rounds.append(current)
    delta=rounds[-1]['primary_metric']-rounds[0]['primary_metric']
    return {'valid_training_rounds':len(rounds),'result_driven_plan_revisions':revisions,
            'baseline_primary_metric':rounds[0]['primary_metric'],'final_primary_metric':rounds[-1]['primary_metric'],
            'primary_metric_delta':delta,'goal_met':len(rounds)>=minimum_rounds and revisions>=minimum_revisions and delta>0,
            'fair_comparison':rounds[0]['fair_comparison'],'metric_scope':'fixed validation, not untouched test',
            'execution_authority':False}


def advance(config, root, evidence_query=None):
    """Use the original condition-driven A/B gate, with component raw evidence."""
    from proposal_review import gate, subject
    from research_replan import once, validate_direction, validate_goal_claim
    from required_skills import inspect, inherit
    from review_context import validate_context
    spec=config['research_replan']
    if (spec.get('version') not in (2,3) or spec.get('parent_route')!='lpai-component'
            or spec.get('result_contract')!='sdm-binary-classification/v1'
            or not isinstance(spec.get('objective'),str) or not spec['objective'].strip()):
        raise ValueError('component_research_replan_v2_required')
    if config.get('devspace_execution') or config.get('drivers'):
        raise ValueError('component_replan_cannot_carry_execution_drivers')
    if inspect(config)['status']!='required-skills-available':raise ValueError('official_lpai_skill_required')
    rows=spec.get('rounds',[]);diagnosis=None
    if spec.get('parent_diagnosis'):
        path=reference(spec['parent_diagnosis']);diagnosis=read_json(path)
        if (diagnosis.get('schema')!='sdm-component-diagnosis-ready/v1' or diagnosis.get('valid_training_round') is not False
                or not diagnosis.get('uid') or diagnosis.get('platform_status') not in {'failed','stopped','cancelled','canceled'}):
            raise ValueError('actual_component_failure_evidence_required')
    if not rows and diagnosis is None:raise ValueError('actual_component_result_or_diagnosis_required')
    preparation=spec.get('preparation_recovery')
    if preparation is not None and (preparation!={'version':1} or rows or diagnosis is None):
        raise ValueError('explicit_initial_preparation_diagnosis_required')
    if not rows and diagnosis is not None and preparation is None:
        return {'status':'initial-baseline-execution-failed','diagnosis':spec['parent_diagnosis'],
            'agent_continuation_required':False,'model_calls':0,'platform_writes':0,
            'next':'Repair the evidenced input/runtime problem under the original delegation. A failed pre-fit task is not a candidate or a trained baseline.'}
    assessment=progress(rows,spec.get('minimum_rounds',3),spec.get('minimum_revisions',2)) if rows else {
        'valid_training_rounds':0,'result_driven_plan_revisions':0,'baseline_primary_metric':None,
        'final_primary_metric':None,'primary_metric_delta':None,'goal_met':False,'status':'no-valid-training-result',
        'execution_authority':False}
    candidates=None
    if spec['version']==3 and rows:
        from component_candidates import registry
        candidates=registry(rows,spec['decision_policy'],spec['observed_task_uids'])
        # Scientific quality and a nonrenewing submission budget replace the
        # legacy chain-qualification count as the research stopping condition.
        assessment={**assessment,'chain_qualification_met':assessment['goal_met'],
            'goal_met':candidates['goal']['met'],'candidate_goal':candidates['goal'],
            'submission_budget':candidates['budget']}
    parent=round_evidence(rows[-1]['result']) if rows else {'uid':diagnosis['uid']}
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    continuous=bool(candidates and spec['decision_policy']['version'] in (2,3))
    files=[]
    if continuous:
        # Growing research history is not a growing inline checklist. Keep
        # every verified raw result in a line-addressable registered archive,
        # and full sample-level artifacts for baseline/incumbent/latest.
        from component_event_source import publish_bytes
        history=root/'historical-results.jsonl'
        records=[{'uid':round_evidence(row['result'])['uid'],'result_ref':row['result'],
            'result':round_evidence(row['result'])['result']} for row in rows]
        publish_bytes(history,('\n'.join(json.dumps(row,ensure_ascii=False,allow_nan=False) for row in records)+'\n').encode('utf-8'))
        files.append(history)
        important={candidates['incumbent']['uid'],candidates['latest']['uid'],round_evidence(rows[0]['result'])['uid']}
        for row in rows:
            actual=round_evidence(row['result'])
            if actual['uid'] in important:files.extend(actual['sources'])
    else:
        for row in rows:files.extend(round_evidence(row['result'])['sources'])
    for ref in spec.get('sources',[]):files.append(reference(ref))
    if diagnosis:files.append(reference(spec['parent_diagnosis']))
    files=list(dict.fromkeys(files))
    if len(files)>(64 if spec['version']==3 else 48):raise ValueError('bounded_component_replan_sources_required')
    sources=[{'id':'component-source-'+str(i),'kind':'metric' if p.name=='result.json' else 'history',
              'path':str(p),'sha256':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()} for i,p in enumerate(files)]
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    context={'schema':'sdm-review-context/v1','subject_digest':digest(subject(config)),'goal':spec['objective'],
        'constraints':{'scope':spec.get('constraints','Original user scope and Manager/Guard; no local training'),
                       'comparison':'Keep the registered evaluation identity and report all attempts/costs; no validation/test substitution',
                       'evaluation':'The declared primary metric determines goal progress, not the entire scientific judgment. '
                           'Compare class precision/recall/F1, confusion counts, support, curves, costs and any registered slice or IoU evidence '
                           'against the incumbent on the same evaluation scope. State tradeoffs and missing metrics explicitly; '
                           'never equate an absent metric with zero, invent class meanings, or promote a candidate past a registered guardrail. '
                           'If no guardrail was agreed, report regressions without inventing a new veto or changing the target.',
                       'reasoning':'Use observed failure patterns to question model/representation, data/measurement, and training assumptions as relevant. '
                           'Do not always start with the model or keep making parameter tweaks after a comparable plateau. '
                           'Select one informative main-line intervention, name alternatives/counterevidence and its falsifying observation.'},
        'data':'Original component results and predictions, not a generated narrative only',
        'code_revision':'New accepted intervention requires separate implementation/code review and a fresh frozen attempt',
        'configuration':{'goal_progress':assessment,'goal_targets':spec.get('targets',[]),'implementation_required_after_approval':True},
        'history':{'actual_component_task_uids':[round_evidence(row['result'])['uid'] for row in rows],
                   'current_failure':diagnosis or 'none; retained failures stay in supplied evidence'},
        'unknowns':'Untested mechanisms, seed variation, cost and remaining failures. Queue waits are not training evidence.',
        'sources':sources}
    if candidates:
        from component_candidates import review_view
        context['configuration']['candidate_management']=review_view(candidates)
        context['constraints']['candidate_decision']=(
            'Compare latest and incumbent, goal gap, negative attempts and costs. Explicitly choose continue, rollback, '
            'redesign or stop with a verified base_task_uid, falsifier and rejected alternatives. Rollback restores a '
            'historical recipe for a NEW informative intervention, never reruns an old UID or restores platform state. '
            'Do not keep the latest inferior branch by default. A metric cluster alone does not prove a model ceiling. '
            'A reached target requires goal-met; exhausted submission budget requires stop-research with goal unmet. '
            'Counts qualify automation only; they never prove scientific success. Retain the incumbent on every exit.')
        if continuous:
            context['constraints']['candidate_decision']+=(
                ' USER DIRECTIVE: continue result-driven research until the registered target is reached, without a '
                'submission-count budget. remaining_submissions=null means no numerical cap, not exhausted. '
                'Do not select stop-research because several trials failed or a qualification count was reached. '
                'Keep costs visible and choose the next informative intervention. User pause/withdrawal and '
                'unresolved execution identity still block actions through the original runtime gates. '
                'Historical-results.jsonl contains every original verified result, one UID per line; '
                'use bounded queries for specific historical records rather than requesting every prediction file.')
            if spec['decision_policy']['version']==3:
                context['constraints']['candidate_decision']+=(
                    ' OPERATIONAL VERIFICATION OBJECTIVE: the quality target is already achieved; do not raise it. '
                    'The user now requests actual continuation/recovery verification. Exactly reproduce the specified '
                    'baseline recipe with a fresh task UID, unchanged scientific settings and original evaluation. '
                    'This is an explicitly scoped reproducibility intent, not an invented improvement or trial count. '
                    'Use continue-plan for that intent, name it as reproduction and retain the original best. '
                    'Exit goal-met only after the host compares complete histories and row-aligned predictions. '
                    'A mismatch requires diagnosis of that actual UID, never blind repeat submission.')
    validate_context(context,context['subject_digest']);once(root/'replan-config.json',config);once(root/'review-context.json',context)
    result=gate(config,root,evidence_query)
    if result['status']!='proposal-approved':return result
    accepted_path=Path(result['receipt']);accepted=read_json(accepted_path);proposal=accepted['proposal']
    ids=[s['id'] for s in sources];revision=validate_direction(proposal['research_revision'],ids,strategy=True)
    decision=None
    if candidates:
        from component_candidates import validate_decision
        decision=validate_decision(revision,candidates)
    if revision['action']=='goal-met' and not assessment['goal_met']:
        raise ValueError('three_round_component_goal_not_reached')
    # Absolute project targets, if declared, remain additionally required.
    if spec.get('targets') and rows:validate_goal_claim(spec,revision,assessment)
    if preparation and revision['action']!='diagnose':
        raise ValueError('initial_preparation_requires_diagnosis_not_scientific_exit')
    if diagnosis and revision['action'] not in ('diagnose','stop-research'):
        raise ValueError('failed_component_requires_diagnosis_before_new_scientific_claim')
    status={'revise-plan':'research-plan-approved-awaiting-implementation','diagnose':'research-plan-approved-awaiting-diagnosis',
            'refine-current':'research-refinement-approved','continue-plan':'research-refinement-approved',
            'goal-met':'research-goal-met','stop-research':'research-stopped'}[revision['action']]
    plan={'schema':'sdm-research-plan/v1','status':status,'parent_route':'lpai-component','objective':spec['objective'],
          'parent_result_sha256':rows[-1]['result']['sha256'].removeprefix('sha256:') if rows else None,
          'parent_task_uid':diagnosis['uid'] if diagnosis else parent['uid'],
          **({'parent_diagnosis_ref':spec['parent_diagnosis']} if diagnosis else {}),
          **({'preparation_recovery':preparation} if preparation else {}),
          'research_revision':revision,'proposal':proposal,'accepted_review':str(accepted_path),
          'accepted_review_ref':{'path':str(accepted_path),'sha256':hashlib.sha256(accepted_path.read_bytes()).hexdigest()},
          'review_project_ref':{'path':str(root/'replan-config.json'),'sha256':hashlib.sha256((root/'replan-config.json').read_bytes()).hexdigest()},
          'review_root':str(root),
          'evidence_ids':ids,'research_goal_met':revision['action']=='goal-met','goal_assessment':assessment,
          'execution_authority':False,'budget_renewed':False}
    if candidates:
        selected=next(row for row in candidates['candidates'] if row['uid']==decision['base_task_uid'])
        plan.update(candidate_management=candidates,selected_candidate=selected)
    inherit(config,plan);once(root/'research-plan.json',plan)
    if revision['action'] in ('revise-plan','diagnose','refine-current','continue-plan'):
        request={'schema':'sdm-research-implementation-request/v1','parent_plan_digest':digest(plan),
                 'change_level':revision['change_level'],'intervention':revision['intervention'],
                 'comparison':proposal['comparison'],'outcome_actions':proposal['outcome_actions'],
                 'parent_route':'lpai-component','parent_result_sha256':plan['parent_result_sha256'],
                 'next':'Implement in a new frozen profile, then original code/proposal/preflight/Manager Guard; never resubmit parent',
                 'execution_authority':False}
        inherit(config,request);once(root/'implementation-request.json',request)
    return {'status':status,'plan_path':str(root/'research-plan.json'),'research_complete':revision['action']=='goal-met',
            'goal_assessment':assessment,'agent_continuation_required':revision['action'] in ('revise-plan','diagnose','refine-current','continue-plan'),
            'execution_authority':False,'platform_actions':0}
