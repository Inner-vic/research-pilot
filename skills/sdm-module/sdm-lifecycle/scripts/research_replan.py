"""Goal-level plan revision through the existing proposal Generator-Critic; never executes ML."""
import copy
import math
from pathlib import Path

from project_driver import file_digest
from review_storage import atomic_json, read_json, native_path
from task_pipeline import digest

IMPLEMENTATION_ACTIONS = ('continue-plan', 'refine-current', 'revise-plan', 'diagnose')
ACTIONS = (*IMPLEMENTATION_ACTIONS, 'goal-met', 'stop-research')
LEVELS = ('tuning', 'model', 'representation', 'training-method', 'data-measurement', 'none')
DIRECTION_STATUSES = {'revise-plan':'research-plan-approved-awaiting-implementation',
    'diagnose':'research-plan-approved-awaiting-diagnosis','refine-current':'research-refinement-approved',
    'continue-plan':'research-refinement-approved','goal-met':'research-goal-met','stop-research':'research-stopped'}


def validate_accepted_direction(plan, accepted):
    if (plan.get('schema')!='sdm-research-plan/v1' or plan.get('proposal')!=accepted.get('proposal')
            or plan.get('research_revision')!=accepted.get('proposal',{}).get('research_revision')):
        raise ValueError('original_accepted_scientific_direction_changed')
    revision=validate_direction(plan['research_revision'],plan.get('evidence_ids',[]),strategy=True)
    if plan.get('status')!=DIRECTION_STATUSES[revision['action']]:
        raise ValueError('original_scientific_direction_status_changed')
    return revision



def policy(config):
    value = config.get('research_iteration')
    if value is None:return None
    if (not isinstance(value, dict) or value.get('version') != 1
            or not isinstance(value.get('objective'), str) or not value['objective'].strip()):
        raise ValueError('explicit_research_iteration_objective_required')
    targets=value.get('targets',[])
    if not isinstance(targets,list) or len(targets)>16:raise ValueError('bounded_research_targets_required')
    for target in targets:
        if (not isinstance(target,dict) or not isinstance(target.get('field'),str) or not target['field']
                or target.get('operator') not in ('>=','<=') or type(target.get('value')) not in (int,float)
                or not math.isfinite(target['value'])):
            raise ValueError('explicit_numeric_research_target_required')
    return value


def goal_check(config, report):
    selected=policy(config)
    if selected is None or not selected.get('targets'):
        return {'status':'unspecified','met':False,'criteria':[],'execution_authority':False}
    from loop_contract import field
    values=[]
    for target in selected['targets']:
        actual=field(report,target['field'])
        if type(actual) not in (int,float) or not math.isfinite(actual):
            raise ValueError('actual_research_target_metric_required:'+target['field'])
        passed=actual>=target['value'] if target['operator']=='>=' else actual<=target['value']
        values.append({**target,'actual':actual,'passed':passed})
    return {'status':'met' if all(row['passed'] for row in values) else 'unmet',
        'met':all(row['passed'] for row in values),'criteria':values,'execution_authority':False}


def validate_goal_claim(spec, revision, report):
    assessment=goal_check({'research_iteration':{'version':1,'objective':spec['objective'],
        'targets':spec.get('targets',[])}},report)
    if revision['action']=='goal-met' and not assessment['met']:
        raise ValueError('replan_goal_not_reached_by_actual_metrics')
    return assessment


def validate_direction(value, evidence_ids, *, strategy=False):
    if not isinstance(value, dict) or value.get('action') not in ACTIONS or value.get('change_level') not in LEVELS:
        raise ValueError('typed_goal_level_research_direction_required')
    if value.get('goal_status') not in ('met', 'unmet', 'uncertain'):
        raise ValueError('research_goal_status_required')
    if (not isinstance(value.get('evidence_ids'), list) or not value['evidence_ids']
            or not set(value['evidence_ids']) <= set(evidence_ids)):
        raise ValueError('registered_research_direction_evidence_required')
    for key in ('assumption', 'intervention', 'expected_decision_value'):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError('reasoned_research_direction_required:' + key)
    if value['action'] == 'goal-met' and value['goal_status'] != 'met':
        raise ValueError('study_finished_is_not_research_goal_met')
    if value['action'] == 'refine-current' and value['change_level'] != 'tuning':
        raise ValueError('refinement_must_name_current_tuning')
    if value['action'] == 'revise-plan' and value['change_level'] in ('tuning', 'none'):
        raise ValueError('plan_revision_requires_different_intervention_level')
    return value


def direction_schema(ids):
    from research_model_runner import object_schema
    return object_schema({'action': {'type': 'string', 'enum': list(ACTIONS)},
        'change_level': {'type': 'string', 'enum': list(LEVELS)},
        'goal_status': {'type': 'string', 'enum': ['met', 'unmet', 'uncertain']},
        'evidence_ids': {'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'enum': list(ids)}},
        **{key: {'type': 'string'} for key in ('assumption', 'intervention', 'expected_decision_value')}})


def validate_result(value, packet, state=None):
    direction = validate_direction(value['next_step'].get('research_direction'), packet['evidence'])
    assessment=(state or {}).get('research_goal_evaluation',{})
    if direction['action']=='goal-met' and assessment.get('status') in ('unmet','unspecified'):
        raise ValueError('research_goal_not_reached_by_actual_metrics')
    if direction['action'] not in ('continue-plan','refine-current') and value['next_step'].get('iteration', {}).get('action') != 'stop':
        raise ValueError('new_research_plan_requires_stop_current_execution_scope')
    return direction


def once(path, value):
    if native_path(path).exists():
        if read_json(path) != value:raise ValueError('immutable_research_replan_changed')
    else:atomic_json(path, value)


def request_after_review(config, root, state):
    """Called only after the original result was consumed and Study archived."""
    selected = policy(config)
    if selected is None:return None
    step = state['history'][-1]['review']['next_step']
    direction = step.get('research_direction')
    if not isinstance(direction, dict):raise ValueError('accepted_research_direction_required')
    if direction['action'] not in ('revise-plan', 'diagnose', 'refine-current', 'continue-plan'):return None
    value = {'schema': 'sdm-research-replan-request/v1', 'objective': selected['objective'],
        'parent_project': config['research_worker']['project_path'], 'parent_output': str(Path(root).resolve()),
        'parent_execution_binding': state['history'][-1].get('execution_binding', state['binding']),
        'direction': direction, 'study_finished': True, 'research_goal_met': False,
        'execution_authority': False, 'budget_renewed': False}
    path = Path(root) / 'research-replan-request.json'
    once(path, value)
    return {'status': 'pending-plan-review', 'request_path': str(path), 'request_digest': digest(value),
        'research_complete': False, 'execution_authority': False}


def parent_evidence(project, output):
    from research_decision_store import validate
    cfg = read_json(project)
    root = Path(output).resolve()
    if read_json(root / 'study-config.json') != cfg:raise ValueError('exact_parent_research_study_required')
    state = read_json(root / 'execution.json')
    if not state.get('study_complete') or state.get('status') != 'completed' or not state.get('stop_verified'):
        raise ValueError('released_archived_parent_required_for_replan')
    number = state['active_round']
    folder = root / ('round-%02d' % number)
    child = read_json(folder / 'execution.json')
    accepted = child['review']['research_acceptance']
    commit = root / 'research-decisions' / accepted['decision_id'] / 'commit.json'
    decision = validate(cfg, root, commit)
    if not decision['consumed']:raise ValueError('consumed_parent_review_required_for_replan')
    consumption = folder / 'review-consumption.json'
    if read_json(consumption)['consumption_evidence'] != decision['consumption_evidence']:
        raise ValueError('exact_parent_review_consumption_required')
    # Reuse the actual numeric and curve sources already accepted by the parent.
    # Binary checkpoints and diagnostic dumps are not necessary planning input.
    files = [Path(project), folder / 'round-observation.json', consumption, root / 'study-result.json']
    names = {'result.json', 'curves.json', 'experiment-spec.json', 'workload.py', 'data-manifest.json',
             'method-manifest.json', 'errors.json'}
    files += [Path(name) for name in decision['record']['sources'] if Path(name).name in names]
    for row in cfg['devspace_execution']['plan']['inputs']:
        if row['name'].endswith(('.py','.json')):
            if file_digest(row['path']).removeprefix('sha256:') != row['sha256'].removeprefix('sha256:'):
                raise ValueError('parent_training_input_changed')
            files.append(Path(row['path']))
    files = list(dict.fromkeys(files))
    if len(files) > 48:raise ValueError('bounded_parent_replan_sources_required')
    return cfg, state, [{'id': 'retained-' + str(i), 'kind': 'metric' if path.name in {'result.json','curves.json'} else 'history',
        'path': str(path), 'sha256': file_digest(path)} for i, path in enumerate(files)]


def advance(config, root, evidence_query=None):
    """Prepare a shared context and use the original condition-driven Generator-Critic gate."""
    if config.get('research_replan',{}).get('version') in (2,3):
        from component_research import advance as component_advance
        return component_advance(config,root,evidence_query)
    from proposal_review import gate, subject
    from review_context import validate_context
    spec = config['research_replan']
    if spec.get('version') != 1 or not spec.get('objective'):
        raise ValueError('research_replan_v1_objective_required')
    if config.get('devspace_execution') or config.get('drivers'):
        raise ValueError('replan_cannot_carry_execution_drivers')
    from required_skills import inspect as inspect_skills
    inspect_skills(config)
    parent, state, sources = parent_evidence(spec['parent_project'], spec['parent_output'])
    declared = spec.get('sources', [])
    if not isinstance(declared, list):raise ValueError('declared_replan_sources_required')
    sources += declared
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    steering = spec.get('control_parent')
    if steering:
        from research_worker import check_control
        check_control(steering['root'], steering['revision'])
    once(root / 'replan-config.json', config)
    context = {'schema': 'sdm-review-context/v1', 'subject_digest': digest(subject(config)),
        'goal': spec['objective'],
        'constraints': {'scope': 'Reassess intervention scale beyond the old variable domain. Refinement needs evidence that it is more informative than a plan change.',
            'limits': spec.get('constraints', 'No new execution permission; original resource Guard applies'),
            'budget': 'Closing a Study does not reach its research goal or renew its execution allowance.'},
        'data': 'Comparable retained actual observations and original workload below; test/metric scope remains as recorded.',
        'code_revision': 'Original consumed parent evidence; a selected change requires a new reviewed frozen implementation.',
        'configuration': {'parent_protocol': parent.get('devspace_main_workflow', {}).get('protocol'),
            'goal_targets': spec.get('targets', []),
            'old_variable_domain': parent['devspace_study']['allowed_variables'],
            'change_levels': list(LEVELS), 'implementation_required_after_approval': True},
        'history': {'actual_parent_assessment': state['history'][-1]['review']['decision']['previous_experiment_assessment'],
            'actual_parent_next_step': state['history'][-1]['review']['next_step'],
            'note': 'The old stop exhausted a local experimental scope; it is not evidence that the global research objective was met.'},
        'unknowns': 'Cause of the remaining gap, effectiveness and cost of untested interventions, variation beyond the retained measurements.',
        'sources': sources}
    validate_context(context, context['subject_digest'])
    once(root / 'review-context.json', context)
    result = gate(config, root, evidence_query)
    if steering:check_control(steering['root'], steering['revision'])
    if result['status'] != 'proposal-approved':return result
    folder = root / 'proposal-review' / digest(subject(config)).removeprefix('sha256:')[:32]
    accepted = read_json(sorted(folder.glob('round-*/accepted.json'))[-1])
    proposal = accepted['proposal']
    revision = validate_direction(proposal['research_revision'], [row['id'] for row in sources], strategy=True)
    goal_assessment=validate_goal_claim(spec,revision,read_json(state['history'][-1]['result_path']))
    status = DIRECTION_STATUSES[revision['action']]
    plan = {'schema': 'sdm-research-plan/v1', 'status': status, 'parent_project': spec['parent_project'],
        'parent_output': spec['parent_output'], 'objective': spec['objective'], 'research_revision': revision,
        'proposal': proposal, 'accepted_review': str(sorted(folder.glob('round-*/accepted.json'))[-1]),
        'accepted_review_ref': {'path':str(sorted(folder.glob('round-*/accepted.json'))[-1]),
            'sha256':file_digest(sorted(folder.glob('round-*/accepted.json'))[-1]).removeprefix('sha256:')},
        'review_project_ref': {'path':str(root/'replan-config.json'), 'sha256':file_digest(root/'replan-config.json').removeprefix('sha256:')},
        'review_root':str(root),
        'research_goal_met': revision['action'] == 'goal-met', 'goal_assessment':goal_assessment,
        'execution_authority': False, 'budget_renewed': False}
    from required_skills import inherit as inherit_skills
    inherit_skills(config,plan)
    once(root / 'research-plan.json', plan)
    if revision['action'] in ('revise-plan', 'diagnose', 'refine-current','continue-plan'):
        once(root / 'implementation-request.json', {'schema': 'sdm-research-implementation-request/v1',
            **({'platform_requirements':copy.deepcopy(config['platform_requirements'])} if 'platform_requirements' in config else {}),
            'parent_plan_digest': digest(plan), 'change_level': revision['change_level'],
            'intervention': revision['intervention'], 'comparison': proposal['comparison'],
            'outcome_actions': proposal['outcome_actions'], 'next': 'Implement the selected plan in a new frozen profile; use original code/proposal/preflight/resource gates',
            'execution_authority': False})
    return {'status': status, 'plan_path': str(root / 'research-plan.json'), 'research_revision': revision,
        'research_complete':revision['action']=='goal-met','goal_assessment':goal_assessment,
        'execution_authority': False, 'platform_actions': 0,
        'automatic_iteration_active':False,'continuation_dispatch_owner':'caller-or-original-ResearchPipeline',
        'agent_continuation_required': revision['action'] in ('revise-plan','diagnose','refine-current','continue-plan'),
        'next_agent_action': 'Continue implementation/diagnosis, create a reviewed frozen execution profile and dispatch through the original coordinator; do not end the research merely because this proposal was produced'}


def continue_after_archive(config, root, state):
    if not state.get('research_next') or state['research_next']['status'] != 'pending-plan-review':return None
    child_root = Path(root) / 'research-replan'
    child_root.mkdir(exist_ok=True)
    from research_worker import control, check_control
    steering = control(root)
    check_control(root, steering['revision'])
    child = {'research_replan': {'version': 1, 'objective': policy(config)['objective'],
        'parent_project': config['research_worker']['project_path'], 'parent_output': str(Path(root).resolve()),
        'constraints': policy(config).get('constraints', 'Original human scope, original execution Guard'),
        'targets': policy(config).get('targets', []),
        'control_parent': {'root': str(Path(root).resolve()), 'revision': steering['revision']}},
        'proposal_review': copy.deepcopy(config['proposal_review'])}
    if 'agent_roles' in config:
        child['agent_roles']=copy.deepcopy(config['agent_roles'])
    from required_skills import inherit as inherit_skills
    inherit_skills(config,child)
    once(child_root / 'project.json', child)
    return advance(child, child_root)
