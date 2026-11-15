"""Bounded Loop with project-owned variables, observations and comparison rules."""
import json
import os
from pathlib import Path
import subprocess
import time
import itertools
from history_context import sequence_digest

from loop_contract import compare, field, load_adapter, parameter_values, validate_comparisons
from loop_analysis import AnalysisPending, analyze as model_analyze, validate_context
from loop_decision import changed
from project_driver import resolve
from round_reporting import deliver
from run_project_pipeline import run as pipeline
from task_pipeline import digest, digest_bytes, verified_s13, execute


def write_once(path, value):
    path = Path(path)
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    if path.exists():
        if path.read_bytes() != raw: raise ValueError('immutable_loop_record_changed:' + path.name)
    else:
        with path.open('xb') as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def observation(adapter, root, summary, stage, node):
    row = summary.get('nodes', {}).get(node, {})
    status = row.get('status', 'missing')
    if status not in {'completed', 'failed'}: return {'status': status}
    payload = row.get('observation', row)
    ref = payload.get('signed_result') or payload.get('accepted_s13')
    if not ref:
        return {'status': 'failed', 'uncertain_effect': True, 'reason': 'execution_result_not_received'}
    if not verified_s13(ref, 'successful' if status == 'completed' else 'failed'):
        raise ValueError('loop_execution_receipt_not_verified')
    witness = adapter.observation_witness(root, summary, stage)
    raw = Path(witness['packet_path']).read_bytes()
    if digest_bytes(raw) != ref['result_digest']: raise ValueError('loop_signed_packet_changed')
    packet = json.loads(raw)
    report = field(packet, witness['report_pointer'])
    actual = json.loads(report) if isinstance(report, str) else report
    if not isinstance(actual, dict): raise ValueError('actual_report_object_required')
    if digest(actual) != field(packet, witness['digest_pointer']):
        raise ValueError('loop_actual_observation_changed')
    if payload.get('actual') is not None and payload['actual'] != actual:
        raise ValueError('task_observation_not_signed_report')
    return {'status': status, 'actual': actual, 'receipt': ref,
            'uncertain_effect': payload.get('uncertain_effect', False)}


def read_cleanup(path):
    if not Path(path).exists():
        from session_reconciliation import effective
        value=effective(path,{'status':'failed','reason':'missing_cleanup_receipt'})
        if value['status']!='completed':raise ValueError('missing_cleanup_requires_reconciliation')
        return value
    receipt=json.loads(Path(path).read_bytes())
    if not isinstance(receipt,dict) or receipt.get('status') not in {'completed','failed'}:
        raise ValueError('cleanup_result_contract_invalid')
    if receipt.get('receipt') and digest_bytes(Path(receipt['receipt']).read_bytes())!=receipt.get('receipt_digest'):
        raise ValueError('cleanup_receipt_changed')
    from session_reconciliation import effective
    return effective(path,receipt)


def check_config(config):
    from history_context import policy as context_policy
    context_policy(config)
    from long_loop import policy
    segmented=policy(config)
    if config.get('merge') or config.get('publish'): raise ValueError('loop_cannot_merge_or_publish')
    if config.get('objective', 'execute-and-learn') != 'execute-and-learn':
        raise ValueError('training_loop_objective_required')
    limit = config.get('max_rounds', 3)
    if limit is None:
        if segmented is None or not isinstance(config.get('study_stop_criteria'),str) or not config['study_stop_criteria'].strip():
            raise ValueError('open_study_requires_segmented_stop_criteria')
    elif type(limit) is not int or limit<1:raise ValueError('bounded_loop_rounds_required')
    if config.get('max_attempts', 1) != 1 or config.get('recovery_probe_node') or config.get('attempt_gpu_uuids'):
        raise ValueError('loop_rounds_must_not_hide_extra_attempts')
    if config.get('decision_mode', 'model-bridge') != 'model-bridge':
        raise ValueError('generic_loop_requires_model_analysis')
    if config.get('analysis_mode','synchronous') not in {'synchronous','external-review'}:
        raise ValueError('supported_analysis_mode_required')
    validate_context(config, config['initial_parameters'])
    if not config.get('project_driver'): raise ValueError('explicit_project_driver_required')
    validate_comparisons(config)
    metrics = config.get('project_metrics', [])
    if not isinstance(metrics, list) or not all(isinstance(x, str) for x in metrics):
        raise ValueError('project_metric_names_required')
    for requirement in config.get('continuation_requirements', []):
        if requirement.get('stage') not in {'training', 'evaluation'} or not isinstance(requirement.get('field'), str) or 'equals' not in requirement:
            raise ValueError('project_continuation_requirement_required')
    budget = config.get('loop_timeout_seconds', 1800)
    if budget is not None and (type(budget) is not int or budget<1): raise ValueError('bounded_loop_time_required')
    return limit, budget


def run(config, output):
    if config.get('long_loop') is not None:
        check_config(config)
        from long_loop import run as segmented_run
        return segmented_run(config,output,_run)
    return _run(config,output)


def _run(config, output, session=None):
    limit, budget = check_config(config)
    adapter = load_adapter(config['loop_adapter'])
    host = Path(__file__).resolve().parents[4]
    driver = resolve(config, host)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    write_once(output / 'loop-config.json', config)
    lock = output / 'loop.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600) if session is None else None
    history = []; result = {'run_id': config['run_id'], 'status': 'running', 'rounds': history}
    started = time.monotonic(); root = None
    try:
        prior_cleanup=None
        prior_path=Path(session['prior_cleanup']) if session and session.get('prior_cleanup') else output/'cleanup-result.json'
        if prior_path.exists() or (prior_path.parent/'cleanup-reconciled.json').exists():
            prior_cleanup=read_cleanup(prior_path)
            release=prior_cleanup.get('observation',prior_cleanup)
            if prior_cleanup['status']!='completed' or any(
                field(release,condition['field'])!=condition['equals']
                for condition in config.get('cleanup_requirements',[])):
                previous=output/'loop-result.json'
                if previous.exists():
                    result=json.loads(previous.read_bytes())
                    if result.get('run_id')!=config['run_id']: raise ValueError('loop_result_identity_changed')
                result.update(status='cleanup-required',reason='prior_cleanup_requires_platform_reconciliation')
                return result
        parameters = dict(config['initial_parameters']); fixed = None; parent = None
        for index in (itertools.count(1) if limit is None else range(1,limit+1)):
            if session and (output/'PAUSE').exists():
                result.update(status='paused',reason='explicit_study_pause');break
            if budget is not None and time.monotonic() - started > budget:
                result.update(status='paused' if session else 'stopped',reason='invocation_time_budget',
                    decision={'action':'stop-time-budget','reason':'No new round after this invocation budget.'});break
            # Re-check project code at every round; a changed adapter needs a new run.
            adapter = load_adapter(config['loop_adapter']); resolve(config, host)
            root = output / f'round-{index:02d}'
            if prior_cleanup is not None and not (root/'round-observation.json').exists() and (session is None or (root/'attempt-01').exists()):
                result.update(status='planning-required',reason='Released resources cannot be reacquired by resume; prepare a fresh authorized attempt.')
                break
            root.mkdir(exist_ok=True)
            delta = changed(history[-1]['parameters'], parameters) if history else []
            if history and len(delta) != 1: raise ValueError('exactly_one_loop_variable_required')
            parameter_values(parameters, config['allowed_variables'])
            plan = {'round': index, 'parameters': parameters, 'changed_variable': delta[0] if delta else None,
                    'parent_decision_digest': digest(parent) if parent else None, 'fixed': fixed,
                    'comparison_conditions': config['comparison_conditions'],
                    'hypothesis': parent['hypothesis'] if parent else config.get('initial_hypothesis', 'Observe the initial project configuration.')}
            write_once(root / 'round-plan.json', plan)
            runtime_path = root / 'round-runtime-config.json'
            binding = {'adapter': config['loop_adapter'], 'configuration_digest': digest(config),
                       'round': index, 'parameters': parameters}
            if runtime_path.exists():
                runtime = json.loads(runtime_path.read_bytes())
                if any(runtime.get(key) != value for key, value in binding.items()):
                    raise ValueError('round_runtime_binding_changed')
                patch = runtime['patch']
            else:
                patch = adapter.prepare_round(config, parameters, index, root, fixed)
                runtime = {**binding, 'patch': patch}
            forbidden = {'run_id', 'resource_owner', 'max_attempts', 'loop_parameters', 'loop_adapter',
                         'project_driver', 'result_records', 'allowed_variables', 'comparison_conditions', 'merge', 'publish',
                         'objective', 'max_rounds', 'loop_timeout_seconds', 'decision_mode', 'initial_parameters',
                         'analysis_objective', 'project_metrics', 'metric_definitions', 'continuation_requirements',
                         'evaluation_required', 'parameter_observation_field', 'observation_nodes', 'cleanup_requirements'}
            forbidden.update({'analysis_mode','external_review','driver_runtime',
                              'task_timeout_seconds','node_timeout_seconds',
                              'task_wall_time_seconds','cleanup_timeout_seconds'})
            forbidden.update({'study_id','trial_id','trial_inputs','attempt_kind','parent_identity',
                              'resume_checkpoint','restore_evidence','required_resume_states','driver_capabilities'})
            forbidden.update({'long_loop','segmented_cleanup_supported','dependency_inputs','check_dependencies','reuse_from','study_stop_criteria','history_context'})
            if not isinstance(patch, dict) or forbidden & patch.keys():
                raise ValueError('round_adapter_cannot_change_execution_authority')
            write_once(runtime_path, runtime)
            if (root / 'round-observation.json').exists():
                saved = json.loads((root / 'round-observation.json').read_bytes())
                if saved.get('runtime_configuration_digest') != digest(runtime):
                    raise ValueError('round_runtime_configuration_changed')
            round_config = {**config, **patch, 'run_id': config['run_id'] + f'-r{index:02d}',
                            'study_id':config.get('study_id',config['run_id']),
                            'trial_id':config['run_id']+f'-r{index:02d}',
                            'resource_owner': 'loop', 'max_attempts': 1, 'loop_parameters': parameters}
            if session and config.get('dependency_inputs') and index>1:
                prior_attempt=output/f'round-{index-1:02d}'/'attempt-01'
                if (prior_attempt/'coordinator-task.json').exists():round_config['reuse_from']=str(prior_attempt)
            nodes = config.get('observation_nodes', {'training': '15', 'evaluation': '16'})
            replayed_observation=session is not None and (root/'round-observation.json').exists()
            if (session is not None or prior_cleanup is not None or config.get('analysis_mode')=='external-review') and (root/'round-observation.json').exists():
                saved=json.loads((root/'round-observation.json').read_bytes())
                summary={'nodes':{}}
                for stage,node in nodes.items():
                    observed=saved[stage]
                    summary['nodes'][node]={'status':observed['status'],'observation':{
                        'signed_result':observed.get('receipt'),'actual':observed.get('actual'),
                        'uncertain_effect':observed.get('uncertain_effect',False)}}
                for finding in saved.get('execution_findings',[]):
                    summary['nodes'].setdefault(finding['node'],{}).update(finding)
            else:
                if session:
                    from long_loop import authority
                    if not authority(round_config):
                        result.update(status='awaiting-user-confirmation',reason='fresh_round_resource_records_required');break
                    from review_storage import atomic_json
                    intents_path=session['root']/'dispatch-intents.json'
                    intents=json.loads(intents_path.read_bytes()) if intents_path.exists() else []
                    intent={'round':index,'round_root':str(root)}
                    if intent not in intents:intents.append(intent)
                    atomic_json(intents_path,intents)
                    atomic_json(session['root']/'dispatch-intent.json',{'round':index,'round_root':str(root)})
                    session['new_rounds']+=1
                outcome = pipeline(round_config, root)
                summary = outcome['attempts'][0]
                if summary.get('status') != 'complete': raise ValueError('loop_round_not_terminal')
            training = observation(adapter, root, summary, 'training', nodes['training'])
            evaluation = observation(adapter, root, summary, 'evaluation', nodes['evaluation'])
            comparison = compare(config, training, evaluation, fixed)
            observed_parameters = field(training.get('actual', {}), config.get('parameter_observation_field', 'loop_parameters'))
            if training['status'] == 'completed' and observed_parameters != parameters:
                comparison['comparable'] = False
                comparison['findings'].append({'condition': 'effective-parameters', 'problem': 'runtime-parameters-mismatch', 'required': True})
            if fixed is None and comparison['comparable']: fixed = comparison['values']
            record = {'round': index, 'parameters': parameters, 'changed_variable': plan['changed_variable'],
                      'training': training, 'evaluation': evaluation, 'fixed': fixed, 'comparison': comparison,
                      'runtime_configuration_digest': digest(runtime),
                      'execution_findings': [
                          {'node': key, 'status': value['status'], 'reason': value.get('reason', value.get('observation', {}).get('reason')),
                           'receipt': value.get('receipt'), 'receipt_digest': value.get('receipt_digest'),
                           **({'uncertain_effect':True} if value.get('uncertain_effect') else {})}
                          for key, value in summary.get('nodes', {}).items()
                          if value.get('status') in {'failed', 'needs-driver', 'reconciliation-required'}]}
            write_once(root / 'round-observation.json', record); history.append(record)
            if config.get('result_records') and not replayed_observation:
                try:
                    retention = None if (root / 'artifact-archive-invocation.json').exists() else adapter.retention_driver(config, root, host)
                    if retention:
                        argv = retention['argv']
                        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
                            raise ValueError('explicit_retention_driver_required')
                        invocation = subprocess.run(argv, capture_output=True, text=True,
                                                    timeout=min(1200, retention.get('timeout_seconds', 1200)), shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        # Do not overwrite an earlier transfer receipt on resume.
                        path = root / 'artifact-archive-invocation.json'
                        if not path.exists(): write_once(path, {'exit_code': invocation.returncode, 'stdout': invocation.stdout, 'stderr': invocation.stderr})
                except (ValueError, OSError, subprocess.SubprocessError) as error:
                    path = root / 'artifact-archive-problem.json'
                    if not path.exists(): write_once(path, {'problem': str(error)})
                deliver({**config, 'result_records': {**config['result_records'], 'sync': False}}, root)
            binding_path=root/'decision-binding.json'
            replayed_decision=session is not None and binding_path.exists()
            if session and binding_path.exists():
                binding=json.loads(binding_path.read_bytes())
                decision=json.loads((root/'analysis-and-decision.json').read_bytes())
                if binding!={'history_digest':sequence_digest(history),'decision_digest':digest(decision)}:
                    raise ValueError('long_loop_decision_changed')
            else:
                decision = model_analyze(history, {**config, 'max_rounds': limit}, root, write_once)
            write_once(root / 'analysis-and-decision.json', decision)
            if session:write_once(binding_path,{'history_digest':sequence_digest(history),'decision_digest':digest(decision)})
            if config.get('result_records') and not replayed_decision: deliver(config, root, decision)
            result.update(status='stopped', decision=decision, fixed=fixed)
            print(json.dumps({'round': index, 'training': training['status'], 'evaluation': evaluation['status'],
                              'comparison_passed': comparison['comparable'], 'decision': decision['action']}, ensure_ascii=False), flush=True)
            if decision['action'] != 'run-next-round': break
            if config.get('analysis_mode')=='external-review' and session is None:
                result['status']='planning-required'
                result['reason']='External review does not authorize another dispatch; prepare a fresh authorized handoff.'
                break
            parameters = decision['next_parameters']; parent = decision
            if session and session['new_rounds']>=config['long_loop']['rounds_per_invocation']:
                result.update(status='paused',reason='invocation_slice_complete');break
    except AnalysisPending as error:
        result.update(status='awaiting-analysis',reason=str(error),pending_request=str(root/'analysis-request.json'))
    except Exception as error:
        result.update(status='blocked', error_type=type(error).__name__, reason=str(error))
        if config.get('result_records') and root is not None: deliver(config, root, error=str(error))
    finally:
        try:
            adapter = load_adapter(config['loop_adapter']); driver = resolve(config, host)
            cleanup_root=session['root'] if session else output
            state = cleanup_root / 'cleanup-result.json'
            if session and not session['new_rounds']:
                receipt=prior_cleanup or {'status':'completed','observation':{'resource':{'phase':'stopped'},'reason':'no_new_dispatch'}}
                cleanup=None
            else:
                cleanup = adapter.cleanup_driver({**config,'loop_output_root':str(output)} if session else config, cleanup_root, host, driver)
            if state.exists():
                receipt = read_cleanup(state)
            elif cleanup is not None:
                receipt = execute(cleanup, output, 'cleanup', 600)
                write_once(state, receipt)
            result['cleanup'] = receipt.get('observation', receipt)
            if receipt['status'] != 'completed': result['status'] = 'cleanup-required'
            for condition in config.get('cleanup_requirements', []):
                if field(result['cleanup'], condition['field']) != condition['equals']:
                    result['status'] = 'cleanup-required'
        except Exception as error:
            result.update(status='cleanup-required', cleanup_error=str(error))
        finally:
            (output / 'loop-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
            if fd is not None:os.close(fd); lock.unlink()
    return result
