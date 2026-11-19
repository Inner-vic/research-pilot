"""Verified incumbents and goal-directed research decisions; never executes ML."""
import hashlib
import math
from pathlib import Path

from review_storage import read_json


def policy(value):
    if (not isinstance(value, dict) or value.get('version') not in (1,2,3,4,5)
            or set(value) != ({'version','target','budget'} if value['version'] in (1,4,5)
                             else {'version','target','budget','termination'} if value['version']==2
                             else {'version','target','budget','termination','verification'})):
        raise ValueError('explicit_candidate_decision_policy_required')
    target=value['target']; budget=value['budget']
    if (set(target) != {'metric', 'operator', 'value'} or (target['metric'] != 'validation.macro_f1' if value['version']!=5 else target['metric'] not in ('validation.accuracy','validation.macro_f1','validation.mean_iou','validation.mae','validation.mse'))
            or (target['operator'] != '>=' if value['version']!=5 else target['operator'] not in ('>=','<=')) or type(target['value']) not in (int,float)
            or not math.isfinite(target['value']) or not (0<=target['value'] if value['version']==5 and target['metric'] in ('validation.mae','validation.mse') else 0<=target['value']<=1)):
        raise ValueError('binary_adapter_explicit_metric_target_required')
    if (set(budget) != ({'known_task_uids','max_new_submissions'} if value['version'] in (1,4,5) else {'known_task_uids'})
            or not isinstance(budget['known_task_uids'], list)
            or (value['version']==1 and not budget['known_task_uids']) or len(set(budget['known_task_uids'])) != len(budget['known_task_uids'])
            or any(not isinstance(uid,str) or not uid for uid in budget['known_task_uids'])):
        raise ValueError('explicit_nonrenewing_submission_budget_required')
    if value['version'] in (1,4,5):
        if type(budget['max_new_submissions']) is not int or budget['max_new_submissions']<1:
            raise ValueError('explicit_nonrenewing_submission_budget_required')
    else:
        terminal=value['termination']
        if (not isinstance(terminal,dict) or set(terminal)!={'mode','source_ref'}
                or terminal['mode']!=('until-verification' if value['version']==3 else 'until-target') or not isinstance(terminal['source_ref'],str)
                or not terminal['source_ref'].strip()):
            raise ValueError('explicit_user_goal_driven_termination_required')
    if value['version']==3:
        from component_reproduction import settings
        settings(value['verification'])
    return value


def registry(rows, selected_policy, observed_task_uids):
    """Rebuild from immutable artifacts; a regression never evicts the incumbent."""
    from component_research import round_evidence, reference
    selected_policy=policy(selected_policy)
    actual=[round_evidence(row['result']) for row in rows]
    if (not actual or len({row['uid'] for row in actual}) != len(actual)
            or any(row['fair_comparison'] != actual[0]['fair_comparison'] for row in actual)):
        raise ValueError('unique_comparable_candidates_required')
    if (not isinstance(observed_task_uids,list) or any(not isinstance(uid,str) or not uid for uid in observed_task_uids)
            or not {row['uid'] for row in actual} <= set(observed_task_uids)):
        raise ValueError('all_observed_task_uids_required_for_budget')
    lower=selected_policy['target']['operator']=='<='
    if any(r.get('metric_name','validation.macro_f1')!=selected_policy['target']['metric'] or r.get('metric_operator','>=')!=selected_policy['target']['operator'] for r in actual):
        raise ValueError('candidate_metric_or_direction_changed')
    candidates=[]; best=None
    for index,(row,verified) in enumerate(zip(rows,actual)):
        ready=read_json(reference(row['result'])); root=Path(ready['artifact_root'])
        candidate={'uid':verified['uid'], 'result':row['result'], 'metric':verified['primary_metric'],
            'config':verified['result']['config'], 'selected_epoch':verified['result']['selected_epoch'],
            'checkpoint':{'path':str(root/('checkpoint.bin' if ready.get('schema')=='sdm-project-result-ready/v1' else 'checkpoint.pt')),'sha256':ready['artifact_sha256']['checkpoint.bin' if ready.get('schema')=='sdm-project-result-ready/v1' else 'checkpoint.pt']},
            'result_artifact':{'path':str(root/'result.json'),'sha256':ready['artifact_sha256']['result.json']},
            'accepted_plan':row.get('accepted_plan'),
            'delta_from_incumbent_before':None if best is None else verified['primary_metric']-best['metric']}
        # Manager ref is optional for legacy artifacts. Never reconstruct an
        # executable recipe from an aggregate score or generated narrative.
        manager=ready.get('submission_identity',{}).get('manager_plan')
        if manager:
            reference(manager)
            if manager['sha256'].removeprefix('sha256:') != ready['original_plan_sha256']:
                raise ValueError('candidate_original_manager_identity_changed')
            candidate['manager_plan']=manager
            candidate['recipe_available']=True
        else:candidate['recipe_available']=False
        candidates.append(candidate)
        if best is None or (candidate['metric'] < best['metric'] if lower else candidate['metric'] > best['metric']):best=candidate
    latest=candidates[-1]
    used=sorted(set(observed_task_uids)-set(selected_policy['budget']['known_task_uids']))
    continuous=selected_policy['version'] in (2,3)
    remaining=None if continuous else max(0,selected_policy['budget']['max_new_submissions']-len(used))
    met=(best['metric'] <= selected_policy['target']['value'] if lower else best['metric'] >= selected_policy['target']['value'])
    quality_met=met;verification=None
    if selected_policy['version']==3:
        from component_reproduction import check
        verification=check(rows,selected_policy['verification']);met=verification['met']
    return {'schema':'sdm-component-candidate-registry/v1', 'comparison_identity':actual[0]['fair_comparison'],
        'metric_scope':'adaptive fixed validation; no untouched-test or statistical superiority claim',
        'tie_rule':('strictly lower' if lower else 'strictly higher')+' verified metric replaces incumbent; earliest exact tie retained',
        'candidates':candidates, 'incumbent':best, 'latest':latest,
        'reference_policy':{'initial_uid':candidates[0]['uid'],'initial_role':'exploration-start',
            'comparison_role':'explicit candidate selected by the original accepted decision',
            'formal_baseline_qualification_inferred':False,
            'qualification':'Use original SDM baseline definition/reproduction/lock/replacement evidence when applicable; neither first nor best metric grants it.'},
        'latest_is_incumbent':latest['uid']==best['uid'],
        'latest_delta_from_incumbent':latest['metric']-best['metric'],
        'attempts_since_incumbent':len(candidates)-1-candidates.index(best),
        'goal':{'target':selected_policy['target'],'met':met,'quality_met':quality_met,
            'verification':verification,'gap':max(0,(best['metric']-selected_policy['target']['value']) if lower else (selected_policy['target']['value']-best['metric']))},
        'budget':{**selected_policy['budget'],'new_task_uids':used,'remaining_submissions':remaining,
            'mode':selected_policy['termination']['mode'] if continuous else 'bounded-submissions',
            'counts_failed_and_cancelled':True,'renewed':False},
        'progress_assessment':progress_assessment(candidates,best,selected_policy['target']['value'],lower),
        'required_exit':'goal-met' if met else 'stop-research' if remaining==0 else None,
        'execution_authority':False}


def decision_schema(registry):
    from research_model_runner import object_schema
    string={'type':'string'}
    return object_schema({'choice':{'type':'string','enum':['continue','rollback','redesign','stop']},
        'base_task_uid':{'type':'string','enum':[c['uid'] for c in registry['candidates']]},
        **{key:string for key in ('reason','why_not_alternatives','falsifier','cost_justification')}})


def recipe_history(rows,root):
    """Compact, complete historical model recipes, not just aggregate scores."""
    from component_research import round_evidence,reference
    from component_payload import executed_request_files,ref
    from research_replan import once
    records=[]
    for row in rows:
        actual=round_evidence(row['result']);ready=read_json(reference(row['result']))
        record={'uid':actual['uid'],'primary_metric':actual['primary_metric'],'metric_name':actual.get('metric_name','validation.macro_f1'),'training':actual['result']['config']}
        if record['metric_name']=='validation.macro_f1':record['macro_f1']=actual['primary_metric']
        if row.get('accepted_plan'):
            direction=read_json(reference(row['accepted_plan']))['research_revision']
            record['intervention']={k:direction[k] for k in ('action','change_level','intervention')}
        manager=ready.get('submission_identity',{}).get('manager_plan')
        if manager:
            files,proof=executed_request_files(read_json(reference(manager))['request'],actual['result']['code_sha256'])
            record.update(manager_plan=manager,code_sha256=actual['result']['code_sha256'])
            if '_researchpilot_project.json' in files:
                import json
                project=json.loads(files['_researchpilot_project.json'])['project']
                record['editable_sources']={n:files[n] for n in project['editable_files'] if n!=project['parameters_file']}
            else:record['model_source']=files.get('model.py')
        else:record['model_source_scope']='legacy original baseline; raw result retained'
        records.append(record)
    path=Path(root)/'historical-recipes.json'
    once(path,{'schema':'sdm-verified-recipe-history/v1','records':records,
        'rule':'These are completed recipes, including negative results. Reversing a previous change may replay an existing recipe. '
            'A new task number, description or renamed local symbols is not a new scientific intervention.',
        'execution_authority':False})
    index=Path(root)/'historical-recipe-index.json'
    once(index,{'schema':'sdm-verified-recipe-index/v1','records':[
        {'record':i,'uid':row['uid'],'primary_metric':row['primary_metric'],'metric_name':row['metric_name'],**({'macro_f1':row['macro_f1']} if 'macro_f1' in row else {}),
            'training':{k:v for k,v in row['training'].items() if k in ('lr','epochs','batch_size','seed')},
            'intervention':row.get('intervention'),'code_sha256':row.get('code_sha256')}
        for i,row in enumerate(records,1)],'full_records':ref(path),
        'scope':'Verbatim selected measured/accepted fields. Inspect relevant raw records for mechanism questions; original coordinator independently rejects exact recipe replays. This index does not establish causal superiority.'})
    return ref(path)


def review_view(registry):
    """Keep decision facts inline; full provenance remains a registered source.

    Duplicating every checkpoint/plan path both in context and its raw artifact
    crowds the incumbent's tiny actual model out of the bounded shared packet.
    """
    view={k:registry[k] for k in ('schema','metric_scope','tie_rule','latest_is_incumbent',
        'latest_delta_from_incumbent','attempts_since_incumbent','goal','budget','required_exit','execution_authority')}
    if 'progress_assessment' in registry:view['progress_assessment']=registry['progress_assessment']
    if 'reference_policy' in registry:view['reference_policy']=registry['reference_policy']
    view['candidates']=[{k:row[k] for k in ('uid','metric','recipe_available','result')}
        for row in registry['candidates']]
    for key in ('incumbent','latest'):
        view[key]={k:registry[key][k] for k in ('uid','metric')}
    return view


def validate_decision(revision, registry):
    decision=revision.get('candidate_decision',{})
    if set(decision) != {'choice','base_task_uid','reason','why_not_alternatives','falsifier','cost_justification'}:
        raise ValueError('reasoned_candidate_decision_required')
    if any(not isinstance(decision[k],str) or not decision[k].strip() for k in decision):
        raise ValueError('nonempty_candidate_decision_required')
    choices={'continue','rollback','redesign','stop'}
    candidates={row['uid']:row for row in registry['candidates']}
    if decision['choice'] not in choices or decision['base_task_uid'] not in candidates:
        raise ValueError('verified_candidate_base_required')
    terminal=revision['action'] in ('goal-met','stop-research')
    if terminal != (decision['choice']=='stop'):
        raise ValueError('candidate_decision_exit_must_match_research_action')
    required=registry['required_exit']
    if required and revision['action'] != required:
        raise ValueError('goal_or_exhausted_budget_requires_exit:'+required)
    if revision['action']=='goal-met' and not registry['goal']['met']:
        raise ValueError('incumbent_has_not_reached_target')
    if registry['budget'].get('mode') in ('until-target','until-verification') and revision['action']=='stop-research':
        raise ValueError('goal_driven_research_cannot_stop_below_target_for_trial_count')
    if terminal:
        if decision['base_task_uid'] != registry['incumbent']['uid']:
            raise ValueError('terminal_decision_must_retain_incumbent')
    elif not candidates[decision['base_task_uid']]['recipe_available']:
        raise ValueError('selected_candidate_requires_original_executable_recipe')
    if decision['choice']=='continue' and decision['base_task_uid'] != registry['latest']['uid']:
        raise ValueError('continue_must_name_latest_base')
    if decision['choice']=='rollback' and (decision['base_task_uid'] != registry['incumbent']['uid']
            or registry['latest_is_incumbent']):
        raise ValueError('rollback_must_restore_distinct_verified_incumbent')
    if decision['choice']=='redesign' and (revision['action']!='revise-plan'
            or revision['change_level'] in ('none','tuning')):
        raise ValueError('redesign_requires_substantive_plan_change')
    return decision


def selected_reference(plan):
    """Reuse original accepted direction/candidate rules before implementing a base.

    A selected comparable recipe is not a B02 lock or B03 reproduction claim.
    No new approval, authority, budget or candidate-selection criterion is added.
    """
    from component_research import reference,round_evidence
    from research_replan import validate_accepted_direction
    accepted=read_json(reference(plan['accepted_review_ref']))
    if accepted.get('approved') is not True or accepted.get('response',{}).get('verdict')!='approve':
        raise ValueError('original_reference_review_required')
    revision=validate_accepted_direction(plan,accepted)
    registry=plan['candidate_management'];decision=validate_decision(revision,registry)
    selected=next(row for row in registry['candidates'] if row['uid']==decision['base_task_uid'])
    if plan.get('selected_candidate')!=selected:
        raise ValueError('selected_reference_must_match_accepted_candidate')
    actual=round_evidence(selected['result'])
    if (actual['uid']!=selected['uid'] or actual['primary_metric']!=selected['metric']
            or actual['fair_comparison']!=registry['comparison_identity']):
        raise ValueError('selected_reference_original_evidence_changed')
    ready=read_json(reference(selected['result']))
    if not selected.get('manager_plan') or ready.get('submission_identity',{}).get('manager_plan')!=selected['manager_plan']:
        raise ValueError('selected_reference_original_recipe_changed')
    reference(selected['manager_plan'])
    return selected


def progress_assessment(candidates,best,target,lower=False):
    """Measured trajectory and negative evidence, not an automatic scientific verdict."""
    first=candidates[0];position=next(i for i,row in enumerate(candidates) if row['uid']==best['uid'])
    negative=[{'uid':row['uid'],'metric':row['metric'],'delta_from_best':row['metric']-best['metric'],
        'accepted_plan':row.get('accepted_plan')} for row in candidates[position+1:]]
    return {'initial_metric':first['metric'],'best_metric':best['metric'],
        'gain_over_initial':(first['metric']-best['metric']) if lower else (best['metric']-first['metric']),'remaining_gap':max(0,best['metric']-target if lower else target-best['metric']),
        'non_improving_since_best':negative,
        'decision_rule':'先解释当前最优候选之后的失败改变了哪些假设，再选择回退、继续或大改。'
            '区分测量/数据问题、训练欠收敛、表示与任务不契合及比较不公平；缺证据就保留未知。'
            '试验应能区分影响下一决策的竞争解释，不要求穷举方向；不能仅因最新一轮存在就沿它继续。'
            '多次失败不是自动更换模型或停止的阈值；小改要解释新增信息，大改要说明机制与公平比较。',
        'scope':'固定验证集上的自适应筛选；不构成未接触测试集结论或因果证明'}
