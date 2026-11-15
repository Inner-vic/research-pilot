"""Consume grounded round decisions through the existing devspace executor.

This is a Study continuation route, not a workload adapter or a decision maker.
Only an explicitly authorized variable domain can produce another round.
"""
import copy
import hashlib
import json
import math
from pathlib import Path
import re

import devspace_execution as single
from loop_contract import parameter_values, field
from loop_decision import changed
from loop_analysis import validate_next_step
from review_storage import atomic_json
from task_event_collector import collector_lock
from submission_guard import Guard
from task_pipeline import digest
from task_event_wakeup import terminal_consumer


def binding(config):
    value = copy.deepcopy(config)
    value['devspace_study'].pop('authorization', None)
    return digest(value)


def validate(config):
    spec = config['devspace_study']
    if config.get('research_iteration'):
        from research_replan import policy
        policy(config)
        if config.get('research_worker',{}).get('version')!=5 or config.get('proposal_review',{}).get('version')!=4:
            raise ValueError('research_iteration_requires_original_v5_worker_and_v4_proposal_models')
    if spec.get('predecessor') is not None:
        from study_successor import validate_inheritance
        validate_inheritance(config)
    if spec.get('version') != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', spec.get('study_id', '')):
        raise ValueError('devspace_study_identity_required')
    if not all(isinstance(spec.get(k), str) and spec[k].strip() for k in ('objective', 'stop_criteria')):
        raise ValueError('devspace_study_objective_and_stop_criteria_required')
    if type(spec.get('max_rounds')) is not int or spec['max_rounds'] < 1:
        raise ValueError('explicit_study_round_scope_required')
    if type(spec.get('max_attempts_per_round', 1)) is not int or spec.get('max_attempts_per_round', 1) < 1:
        raise ValueError('explicit_study_attempt_scope_required')
    if type(spec.get('initial_plan_review_required', False)) is not bool:
        raise ValueError('boolean_initial_plan_review_required')
    if type(spec.get('event_driven_continuation', False)) is not bool:
        raise ValueError('boolean_event_driven_continuation_required')
    parameter_values(spec['initial_parameters'], spec['allowed_variables'])
    if config.get('research_worker',{}).get('version')==5:
        from research_model_runner import parameters as model_parameters
        model_parameters(spec['allowed_variables']) # reject wire/domain mismatch before any resource action
    if spec.get('review_context') is not None:
        from study_review_context import seed
        seed(spec)  # fail before acquisition, not at the next proposal
        if config.get('research_worker', {}).get('version', 0) < 4:
            raise ValueError('study_context_requires_durable_research_consumption')
    flags = spec.get('parameter_flags', {})
    if set(flags) != set(spec['initial_parameters']) or len(set(flags.values())) != len(flags):
        raise ValueError('unique_parameter_flags_required')
    argv = config['devspace_execution']['plan']['argv']
    if any(not isinstance(v, str) or not re.fullmatch(r'--[a-z][a-z0-9-]*', v) or v in argv for v in flags.values()):
        raise ValueError('declared_parameter_flags_required')
    if (not isinstance(spec.get('fixed_fields'), list) or not spec['fixed_fields']
        or not all(isinstance(k, str) and k for k in spec['fixed_fields'])
        or not isinstance(spec.get('metrics'), list) or not spec['metrics']
        or not all(isinstance(k, str) and k for k in spec['metrics'])):
        raise ValueError('explicit_study_comparison_fields_required')
    report = spec.get('result_artifact')
    if report not in config['devspace_execution']['plan']['artifacts']:
        raise ValueError('registered_study_result_artifact_required')
    resume = spec.get('checkpoint_resume')
    if resume is not None:
        plan = config['devspace_execution']['plan']
        if (not isinstance(resume, dict) or set(resume) != {'artifact','input_name','flag'}
            or resume['artifact'] not in plan['artifacts']
            or not isinstance(resume['input_name'], str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', resume['input_name'])
            or resume['input_name'] in {r['name'] for r in plan['inputs']}
            or not isinstance(resume['flag'], str) or not re.fullmatch(r'--[a-z][a-z0-9-]*', resume['flag'])
            or resume['flag'] in argv or resume['flag'] in flags.values()):
            raise ValueError('declared_checkpoint_resume_contract_required')
    auth = spec.get('authorization', {})
    if auth.get('confirmed') is not True or auth.get('study_digest') != binding(config) or not auth.get('source_ref'):
        raise ValueError('exact_study_scope_authorization_required')
    single.validate(config)  # also verify initial resource/plan authorization
    return spec


def once(path, value):
    if path.exists():
        if json.loads(path.read_bytes()) != value:
            raise ValueError('immutable_study_record_changed:' + path.name)
    else:
        atomic_json(path, value)


def round_config(config, number, parameters, parent, history=None, attempt=1, prior_attempts=None):
    spec = config['devspace_study']
    child = copy.deepcopy(config)
    child.pop('devspace_study')
    child['devspace_round_context'] = {'study_id': spec['study_id'], 'objective': spec['objective'],
        'stop_criteria': spec['stop_criteria'], 'round': number,
        'planned_parameters': json.dumps(parameters, sort_keys=True),
        'allowed_variables': json.dumps(spec['allowed_variables'], sort_keys=True),
        'comparison_fields': json.dumps(spec['fixed_fields']),
        'parent_decision': json.dumps(parent, sort_keys=True),
        'previous_results': json.dumps([{k: r[k] for k in ('round','parameters','metrics','result_path','result_digest')}
                                         for r in (history or [])], sort_keys=True)}
    if 'max_attempts_per_round' in spec:
        child['devspace_round_context'].update(attempt=attempt, previous_attempts=json.dumps([
            {k:r[k] for k in ('round','attempt','parameters','metrics','outcome','exit_code','execution_binding','failure')}
            for r in (prior_attempts or [])], sort_keys=True))
        if spec['max_attempts_per_round'] > 1:
            child['devspace_round_context'].update(failure_diagnostic_result=spec['result_artifact'],
                failure_diagnostic_metrics=json.dumps(spec['metrics']))
    plan = child['devspace_execution']['plan']
    plan['run_id'] = spec['study_id'] + '-r%02d' % number
    plan['remote_root'] = plan['remote_root'].rstrip('/\\') + '/r%02d' % number
    if attempt > 1:
        plan['run_id'] += '-a%02d' % attempt
        plan['remote_root'] += '/attempt-%02d' % attempt
    for key in sorted(parameters):
        value = parameters[key]
        plan['argv'] += [spec['parameter_flags'][key], str(value).lower() if type(value) is bool else str(value)]
    from study_review_context import history_materials
    history_materials(config, child, history or [])
    if parent and parent.get('resume'):
        source = (prior_attempts or [])[-1]['checkpoint']
        checkpoint = Path(source['path'])
        if checkpoint.is_symlink() or not checkpoint.is_file() or hashlib.sha256(checkpoint.read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('resume_checkpoint_changed_or_missing')
        resume = spec['checkpoint_resume']
        plan['inputs'].append({'name':resume['input_name'], 'path':str(checkpoint), 'sha256':source['sha256']})
        plan['argv'] += [resume['flag'], resume['input_name']]
    # Derive a bound execution grant from the user's frozen Study scope. This
    # records inheritance, never a new human confirmation or reviewer authority.
    child['devspace_execution']['authorization'] = {
        'confirmed': True, 'plan_digest': digest(plan),
        'source_ref': spec['authorization']['source_ref'],
        'inherited_study_digest': binding(config), 'parent_decision_digest': digest(parent) if parent else None}
    return child


def observed(spec, child, folder, parameters, previous):
    if not child.get('stop_verified') or not child.get('artifacts_collected') or not child.get('process_terminal'):
        raise ValueError('study_requires_complete_round_cleanup')
    report_path = folder / 'artifacts' / spec['result_artifact']
    diagnostic = (child.get('outcome') == 'failed' and type(child.get('process', {}).get('exit_code')) is int
                  and child['process']['exit_code'] != 0 and spec.get('max_attempts_per_round', 1) > 1)
    report_error = None
    try:
        report = json.loads(report_path.read_bytes())
        if not isinstance(report, dict): raise ValueError('structured_study_result_required')
    except (OSError, ValueError) as error:
        if not diagnostic: raise
        report = {}; report_error = type(error).__name__
    if (report.get('parameters') is not None or not diagnostic) and digest(report.get('parameters')) != digest(parameters):
        raise ValueError('study_observed_parameters_mismatch')
    unmet = []
    for name, expected in spec.get('required_values', {}).items():
        actual = field(report, name)
        if type(actual) is not type(expected) or actual != expected:
            if child.get('outcome') == 'failed' and spec.get('max_attempts_per_round', 1) > 1:
                unmet.append(name)  # diagnostic evidence, never a successful qualification
            else:
                raise ValueError('study_result_requirement_not_met:' + name)
    values = {name: field(report, name) for name in spec['fixed_fields']}
    if (not diagnostic and any(v is None for v in values.values())) or previous and any(
            v is not None and previous['fixed'].get(k) != v for k,v in values.items()):
        raise ValueError('study_comparison_identity_changed')
    metrics = {name: field(report, name) for name in spec['metrics']}
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in metrics.values()):
        if not diagnostic: raise ValueError('finite_study_metrics_required')
        metrics = {k:v if type(v) in (int,float) and math.isfinite(v) else None for k,v in metrics.items()}
    record = {'parameters': parameters, 'fixed': values, 'metrics': metrics,
            'result_path': str(report_path), 'result_digest': 'sha256:' + hashlib.sha256(report_path.read_bytes()).hexdigest() if report_path.is_file() else None,
            'exit_code': child.get('process', {}).get('exit_code'), 'outcome': child.get('outcome'),
            'stop_verified': child['stop_verified'],
            'review': {k: child['review'][k] for k in ('binding', 'decision', 'next_step')}}
    if 'max_attempts_per_round' in spec:
        record.update(execution_binding=child['binding'], requirement_failures=unmet,
                      failure=child.get('process', {}).get('failure'))
    if diagnostic:
        record.update(diagnostic_only=True, result_error=report_error,
                      missing_artifacts=child.get('missing_artifacts', []), parameters_source='authorized-round-plan')
    if spec.get('checkpoint_resume'):
        checkpoint = folder / 'artifacts' / spec['checkpoint_resume']['artifact']
        if checkpoint.is_symlink() or not checkpoint.is_file():
            if diagnostic: return record  # may repair from scratch, never claim resume
            raise ValueError('registered_resume_checkpoint_missing')
        record['checkpoint'] = {'path':str(checkpoint), 'sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        project = json.loads((folder / 'project.json').read_bytes())
        inputs = [r for r in project['devspace_execution']['plan']['inputs'] if r['name'] == spec['checkpoint_resume']['input_name']]
        if inputs:
            restored = report.get('resume', {})
            if (restored.get('input_sha256') != inputs[0]['sha256'] or restored.get('restored') is not True
                or type(restored.get('start_updates')) is not int or restored['start_updates'] <= 0
                or type(restored.get('end_updates')) is not int or restored['end_updates'] <= restored['start_updates']):
                raise ValueError('actual_checkpoint_restore_evidence_required')
            record['resume'] = restored
    return record


def continuation(spec, record, history):
    step = validate_next_step({'next_step': record['review'].get('next_step')})
    decision = step.get('iteration')
    if not isinstance(decision, dict) or decision.get('action') not in {'run-next-round', 'retry-round', 'stop'}:
        raise ValueError('structured_study_iteration_decision_required')
    if decision['action'] == 'stop':
        return decision
    if step['authorization'] != 'existing' or step['status'] != 'ready':
        raise ValueError('study_continuation_not_authorized_or_ready')
    return iteration_proposal(spec, record, history)


def iteration_proposal(spec, record, history):
    """Validate a proposal's experiment scope, without granting operational release."""
    step = validate_next_step({'next_step': record['review'].get('next_step')})
    decision = step.get('iteration')
    if not isinstance(decision, dict) or decision.get('action') not in {'run-next-round', 'retry-round', 'stop'}:
        raise ValueError('structured_study_iteration_decision_required')
    if decision['action'] == 'stop':
        return decision
    retry = decision['action'] == 'retry-round'
    resume = decision.get('resume')
    if resume:
        if (not retry or not record.get('checkpoint') or not spec.get('checkpoint_resume') or not isinstance(resume, dict)
            or set(resume) != {'from_execution_binding','artifact_sha256'}
            or resume['from_execution_binding'] != record.get('execution_binding')
            or resume['artifact_sha256'] != record.get('checkpoint', {}).get('sha256')):
            raise ValueError('exact_registered_checkpoint_resume_required')
    if retry:
        if record.get('outcome') != 'failed' or record.get('exit_code') in (None, 0) or not record.get('stop_verified'):
            raise ValueError('known_failed_released_attempt_required')
        if record.get('attempt', 1) >= spec.get('max_attempts_per_round', 1):
            raise ValueError('study_attempt_authorization_exhausted')
        if not record.get('execution_binding') or decision.get('failed_attempt_binding') != record['execution_binding']:
            raise ValueError('exact_failed_attempt_binding_required')
    else:
        if record['outcome'] != 'succeeded' or record['exit_code'] != 0:
            raise ValueError('failed_study_round_cannot_auto_continue')
        if len(history) >= spec['max_rounds']:
            raise ValueError('study_round_authorization_exhausted')
    params = decision.get('parameters', {})
    parameter_values(params, spec['allowed_variables'])
    delta = changed(record['parameters'], params)
    if not (resume and not delta and decision.get('changed_variable') is None) and (len(delta) != 1 or decision.get('changed_variable') != delta[0]):
        raise ValueError('exactly_one_study_variable_required')
    if not retry and any(row['parameters'] == params for row in history):
        raise ValueError('repeated_study_parameters')
    if not all(isinstance(decision.get(k), str) and decision[k].strip() for k in ('hypothesis', 'expected_observations')):
        raise ValueError('falsifiable_study_iteration_required')
    return decision


def initial_plan(config, root):
    """An Agent response may revise parameters inside the existing frozen grant."""
    spec = config['devspace_study']
    if not spec.get('initial_plan_review_required'):
        return spec['initial_parameters'], None
    request = {'schema':'sdm-study-plan-request/v1', 'study_digest':binding(config),
               'objective':spec['objective'], 'parameters':spec['initial_parameters'],
               'allowed_variables':spec['allowed_variables'], 'stop_criteria':spec['stop_criteria']}
    from review_guidance import bind_iteration_guidance
    prior=root/'study-plan-request.json'
    request=bind_iteration_guidance(request,json.loads(prior.read_bytes()) if prior.exists() else None,purpose='plan')
    once(root / 'study-plan-request.json', request)
    path = root / 'study-plan-response.json'
    if not path.is_file():
        return None, None
    response = json.loads(path.read_bytes())
    if (response.get('request_digest') != digest(request) or response.get('action') not in {'accept','revise'}
        or not isinstance(response.get('reason'), str) or not response['reason'].strip()):
        raise ValueError('bound_study_plan_response_required')
    parameters = response.get('parameters', {})
    parameter_values(parameters, spec['allowed_variables'])
    delta = changed(spec['initial_parameters'], parameters)
    if len(delta) != (1 if response['action'] == 'revise' else 0):
        raise ValueError('initial_plan_single_revision_required')
    once(root / 'study-plan-decision.json', response)
    return parameters, response


def attempt_folder(root, number, attempt):
    folder = root / ('round-%02d' % number)
    return folder if attempt == 1 else folder / ('attempt-%02d' % attempt)


def consume_round_review(config, root, folder, child):
    """Accept once before proposing a successor, under the existing outer guard."""
    if config.get('research_worker', {}).get('version', 0) < 4:
        return
    from research_decision_store import validate
    from research_consumption import recover, proof
    accepted = child.get('review', {}).get('research_acceptance')
    if not isinstance(accepted, dict):
        raise ValueError('durable_coordinator_acceptance_required')
    path = root / 'research-decisions' / accepted['decision_id'] / 'commit.json'
    value = recover(config, root, path, validate(config, root, path, check_control=False))
    expected = proof(config, root, path, value, child, json.loads((folder / 'project.json').read_bytes()))
    stable = ('decision_id', 'project_digest', 'execution_binding', 'uid',
              'execution_project_digest', 'control_revision', 'request_digest', 'response_digest')
    if (not value['consumed'] or accepted != expected or any(
            value['consumption_evidence']['acceptance'][k] != accepted[k] for k in stable)):
        raise ValueError('consumed_round_review_required_before_successor')
    once(folder / 'review-consumption.json', {'schema': 'sdm-study-review-consumption/v1',
        'decision_id': value['decision_id'], 'execution_binding': child['binding'],
        'request_digest': value['record']['request_digest'],
        'response_digest': value['record']['response_digest'],
        'consumption_evidence': value['consumption_evidence'], 'execution_authority': False})


def execution(config, output, action_receipt=None, allow_live=False):
    spec = validate(config)
    root = single.absolute(str(Path(output).absolute()))
    if config['devspace_execution']['plan']['transport'] != 'local-cpu' and not allow_live:
        raise ValueError('explicit_allow_devspace_live_required')
    root.mkdir(parents=True, exist_ok=True)
    with collector_lock(str(root / 'study')):
        once(root / 'study-config.json', config)
        guard = Guard({'provider':'sdm-devspace-study', 'namespace':config['devspace_execution']['plan']['resource']['namespace'],
                       'owner':config['devspace_execution']['owner'], 'request_key':spec['study_id'],
                       'component_uid':config['devspace_execution']['plan']['resource']['uid'],
                       'configuration_digest':binding(config), 'submission_registry':config['devspace_execution']['submission_registry']},
                      root / 'execution.json')
        with guard.lock():
            guard.load()
            if guard.state['phase'] == 'new': guard.before_submit(root / 'execution.json')
            elif guard.state.get('state_path') != str((root / 'execution.json').resolve()):
                raise ValueError('existing_study_resume_original_workspace')
        parameters, parent = initial_plan(config, root)
        if parameters is None:
            if action_receipt is not None: raise ValueError('study_plan_review_before_resource_action')
            state = {'schema':'sdm-devspace-study/v1', 'study_id':spec['study_id'],
                     'study_digest':binding(config), 'status':'awaiting-study-plan',
                     'plan_request':str(root / 'study-plan-request.json'),
                     'plan_response':str(root / 'study-plan-response.json'), 'history':[]}
            atomic_json(root / 'execution.json', state); return state
        receipt_target = None
        if action_receipt is not None:
            supplied = json.loads(Path(action_receipt).read_bytes())
            if supplied.get('action') not in {'start','stop'}:
                raise ValueError('bound_active_study_action_receipt_required')
            if (root / 'execution.json').is_file():
                current = json.loads((root / 'execution.json').read_bytes())
                receipt_target = (current['active_round'], current.get('active_attempt', 1))
            else:
                paths = list(root.glob('round-*/execution.json')) + list(root.glob('round-*/attempt-*/execution.json'))
                candidates = [p for p in paths if not json.loads(p.read_bytes()).get('stop_verified')]
                if len(candidates) != 1: raise ValueError('unique_unclosed_study_round_required')
                folder = candidates[0].parent
                receipt_target = ((int(folder.parent.name.split('-')[1]), int(folder.name.split('-')[1]))
                                  if folder.name.startswith('attempt-') else (int(folder.name.split('-')[1]), 1))
            request = attempt_folder(root, *receipt_target) / (supplied['action'] + '-request.json')
            if not request.is_file() or supplied.get('request_digest') != digest(json.loads(request.read_bytes())):
                raise ValueError('bound_active_study_action_receipt_required')
        history = []; attempts = []
        for number in range(1, spec['max_rounds'] + 1):
            prior_attempts = []
            for attempt in range(1, spec.get('max_attempts_per_round', 1) + 1):
                folder = attempt_folder(root, number, attempt); folder.mkdir(parents=True, exist_ok=True)
                child_config = round_config(config, number, parameters, parent, history, attempt, prior_attempts)
                once(folder / 'project.json', child_config)
                plan = {'study_digest':binding(config), 'round':number, 'parameters':parameters,
                        'parent_decision_digest':digest(parent) if parent else None}
                if 'max_attempts_per_round' in spec: plan['attempt'] = attempt
                once(folder / 'round-plan.json', plan)
                from study_review_context import produce
                produce(config, child_config, folder, history, parent, once)
                advance=single.execution
                if child_config.get('devspace_main_workflow'):
                    from devspace_main_workflow import execution as advance
                child = advance(child_config, folder,
                    action_receipt if receipt_target == (number, attempt) else None, allow_live, notification_config=config)
                state = {**child, 'schema':'sdm-devspace-study/v1', 'study_id':spec['study_id'],
                         'study_digest':binding(config), 'active_round':number, 'active_attempt':attempt,
                         'execution_root':str(folder), 'execution_project':str(folder / 'project.json'), 'history':history}
                if config.get('research_iteration'):
                    state['research_iteration']=config['research_iteration']
                    report_path=folder/'artifacts'/spec['result_artifact']
                    if child.get('required_artifacts_complete') and child.get('stop_verified') and report_path.is_file():
                        from research_replan import goal_check
                        state['research_goal_evaluation']=goal_check(config,json.loads(report_path.read_bytes()))
                if 'max_attempts_per_round' in spec: state['attempt_history'] = attempts
                from research_observation import capture_execution
                observation = capture_execution(config, state, root)
                if observation is not None: state['research_observation'] = observation
                if child['status'] != 'completed':
                    atomic_json(root / 'execution.json', state); return state
                record = None
                try:
                    consume_round_review(config, root, folder, child)
                    record = observed(spec, child, folder, parameters, history[-1] if history else None)
                    record['round'] = number
                    if 'max_attempts_per_round' in spec: record['attempt'] = attempt
                    if spec.get('event_driven_continuation'):
                        if config.get('research_worker'):
                            from research_worker import terminal_evidence
                            received = terminal_evidence(config, child['binding'])
                        else:
                            received = terminal_consumer(config, child['binding'])
                        if received is None:
                            state.update(status='awaiting-event-consumer', reason=(
                                'durable_bound_terminal_required' if config.get('research_worker')
                                else 'received_exact_terminal_dispatch_required'))
                            atomic_json(root / 'execution.json', state); return state
                        record['event_consumer'] = received
                    once(folder / 'round-observation.json', record)
                    decision = continuation(spec, record, history + [record])
                    once(folder / 'iteration-decision.json', decision)
                    once(folder / 'round-transition.json', {'round':number, 'attempt':attempt,
                        'actual_parameters':parameters, 'metrics':record['metrics'], 'outcome':record['outcome'],
                        'assessment':record['review']['decision'].get('previous_experiment_assessment'),
                        'next_action':decision, 'next_result_pending':decision['action']!='stop',
                        'event_consumer':record.get('event_consumer')})
                except ValueError as error:
                    state.update(status='study-blocked', reason=str(error), history=history + [record] if record is not None else history)
                    atomic_json(root / 'execution.json', state)
                    single.emit(child_config, folder, child, 'study_continuation_blocked',
                                {'reason':str(error), 'automatic_resubmit':False}, digest(str(error)), config)
                    return state
                attempts.append(record); prior_attempts.append(record)
                if decision['action'] != 'retry-round': history.append(record)
                state['history'] = history
                if 'max_attempts_per_round' in spec: state['attempt_history'] = attempts
                if decision['action'] == 'stop':
                    state.update(status='completed', study_complete=True,
                                 stop_reason=record['review']['next_step']['stop_reason'])
                    if config.get('research_iteration'):
                        from research_replan import request_after_review
                        direction=record['review']['next_step']['research_direction']
                        state.update(research_complete=direction['action']=='goal-met' and state.get('research_goal_evaluation',{}).get('met') is True,
                            research_status=direction['action'])
                        following=request_after_review(config,root,state)
                        if following:state['research_next']=following
                    atomic_json(root / 'execution.json', state)
                    keys = ['schema','study_id','study_digest','status','history','stop_reason',
                            'evidence_kind','stop_verified','billing_release_verified']
                    if 'max_attempts_per_round' in spec: keys.append('attempt_history')
                    if config.get('research_iteration'):keys+=['research_complete','research_status']+(['research_next'] if 'research_next' in state else [])
                    once(root / 'study-result.json', {k:state[k] for k in keys}); return state
                if (root / 'PAUSE').exists():
                    state.update(status='paused', reason='explicit_study_pause')
                    atomic_json(root / 'execution.json', state); return state
                parameters = decision['parameters']; parent = decision
                if decision['action'] != 'retry-round': break
        raise ValueError('explicit_final_study_decision_required')
