"""One SDM acceptance record for dispatch, recovery and read-only projections.

The normal component adapter retains original object/row validation. It does
not issue formal lifecycle transitions, signed handoffs, or D05 qualification.
Missing operational facts never become successful scientific row checks.
"""
import argparse
import copy
import json
from pathlib import Path

from canonical import digest
from component_payload import ref
from component_research import reference, round_evidence
from contracts import AssetBundle, evaluate_gate, evaluate_scientific_decision
from lifecycle_rules import check_records, check_experiment_kernel
from research_replan import once
from review_storage import read_json


SCHEMA = 'sdm-component-governance/v1'
REQUIRED_CONTRACTS = {'G01', 'G02', 'G03', 'G07', 'G08', 'G09', 'G10', 'G13'}
# These are bindings to original duties, not replacement original gate trees.
DISPATCH = {'all_of': [{'check': name} for name in
    ('intent', 'contracts', 'baseline_spec', 'experiment_spec', 'fixed_comparison',
     'review_and_authority', 'actual_payload', 'baseline_available')]}


def records(value, bundle, project_id, required=REQUIRED_CONTRACTS):
    if not isinstance(value, dict) or set(value) != {'contracts', 'baseline', 'experiment'}:
        raise ValueError('original_sdm_contract_baseline_experiment_records_required')
    contracts = value['contracts']
    if (not isinstance(contracts, dict) or not required <= set(contracts)
            or any(k not in {'G%02d' % n for n in range(1, 14)} for k in contracts)):
        raise ValueError('applicable_original_data_label_tensor_evaluation_contracts_required')
    for kind, artifact in [*contracts.items(), ('B01', value['baseline'])]:
        if artifact.get('subjects', {}).get('project_id') != project_id:
            raise ValueError('governance_record_project_changed')
        findings = bundle.validate_artifact(kind, artifact)
        if findings:
            raise ValueError('original_sdm_record_invalid:' + kind + ':' + ','.join(f['code'] for f in findings))
    checked = [check_records(bundle, 4, {'B01': [value['baseline']]}),
               check_experiment_kernel(bundle, value['experiment'])]
    if not all(row['passed'] for row in checked):
        raise ValueError('original_sdm_baseline_or_experiment_row_blocked')
    return checked


def prepare(spec, intent):
    """Original records, applicability and bound comparison, before any effect."""
    value = copy.deepcopy(spec.get('sdm_governance'))
    if isinstance(value,dict) and 'records' not in value:
        from component_governance_input import assemble
        if spec['workload'].get('adapter')=='project/v1':
            from project_governance import assemble
        value=assemble(spec,intent)
    if (not isinstance(value, dict) or set(value) != {'schema', 'records', 'not_applicable', 'data_source', 'max_submissions'}
            or value['schema'] != SCHEMA or type(value['max_submissions']) is not int
            or not 1 <= value['max_submissions'] <= 100):
        raise ValueError('explicit_bounded_sdm_component_governance_required')
    from research_workloads import settings
    if settings(spec['workload'])['version'] not in (2,3):
        raise ValueError('new_governed_studies_require_remote_tiny_fit_and_resume_preflight')
    bundle = AssetBundle.load()
    applicable=REQUIRED_CONTRACTS
    if spec['workload'].get('adapter')=='project/v1':
        from project_governance import required as project_required
        applicable=project_required(spec['workload'])
    checks = records(value['records'], bundle, spec['project_id'],applicable)
    required = set(intent['brief']['content'].get('ml_contract_applicability', {}).get('required_contract_ids', []))
    supplied = set(value['records']['contracts'])
    if not required or required != supplied or supplied != applicable:
        raise ValueError('original_brief_applicability_must_equal_actual_contract_set')
    omitted = {'G%02d' % n for n in range(1, 14)} - supplied
    if (not isinstance(value['not_applicable'], dict) or set(value['not_applicable']) != omitted
            or any(not isinstance(reason, str) or len(reason.strip()) < 20 for reason in value['not_applicable'].values())):
        raise ValueError('each_omitted_contract_requires_explicit_scope_reason')
    # Only the implemented univariate binary exploratory adapter is qualified.
    # Other workloads must gain real adapters rather than blanket exemptions.
    experiment = value['records']['experiment']
    if experiment.get('budget',{}).get('max_submissions') != value['max_submissions']:
        raise ValueError('scientific_plan_and_dispatch_budget_must_match')
    if (experiment.get('protocol_channel') != 'exploratory' or experiment.get('promotable') is not False
            or experiment['hypothesis'] != intent['brief']['content']['hypothesis']):
        raise ValueError('component_profile_requires_exact_exploratory_hypothesis')
    if spec['workload'].get('adapter')=='project/v1':
        from project_governance import bind_facts
        return bind_facts(spec,intent,value,bundle,checks)
    from prepare_public_ts import metadata
    raw = reference(value['data_source']).read_bytes()
    comparison = spec['comparison_identity']
    import hashlib
    info = metadata(raw, comparison['protocol'])
    if (hashlib.sha256(raw).hexdigest() != comparison['source_sha256']
            or hashlib.sha256(json.dumps(info['split'], sort_keys=True).encode()).hexdigest() != comparison['split_sha256']):
        raise ValueError('governance_actual_data_or_split_changed')
    import ast
    baseline_raw=reference(spec['baseline_source']).read_bytes()
    from component_payload import verify_model_api
    verify_model_api(reference(spec['baseline_model']).read_text(encoding='utf-8'),spec['workload'])
    constants={}
    for node in ast.parse(baseline_raw).body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try:constants[node.targets[0].id]=ast.literal_eval(node.value)
            except (ValueError,TypeError):pass
    required_constants={'DATA_SHA':comparison['source_sha256'],'PROTOCOL':comparison['protocol'],
        'LENGTH':info['length'],'SAMPLES':info['samples'],'CLASS_LABELS':info['class_labels'],
        'TRAIN_SAMPLES':info['train_samples'],'VAL_SAMPLES':info['validation_samples'],
        'INPUT_FILE':Path(value['data_source']['path']).name}
    if any(constants.get(k)!=v for k,v in required_constants.items()):
        raise ValueError('baseline_actual_data_and_evaluation_binding_changed')
    from prepare_public_ts import render
    qualified={key:constants[key] for key in ('SOURCE','DATA_SHA','PROTOCOL','REMOTE_ROOT','INPUT_FILE','SAMPLES','LENGTH',
                                              'CLASS_LABELS','TRAIN_SAMPLES','VAL_SAMPLES')}
    if baseline_raw.replace(b'\r\n',b'\n') != render(qualified).replace(b'\r\n',b'\n'):
        raise ValueError('qualified_baseline_producer_required_for_current_adapter')
    from component_payload import harness
    trainer_sha256=hashlib.sha256(harness(baseline_raw,comparison,spec['workload'])).hexdigest()
    # Bind original G03/G09/G10 claims to the actual qualified adapter inputs.
    contract = value['records']['contracts']
    if contract['G02']['content']['references'] != sorted(k+':'+v['instance_key'] for k,v in contract.items() if k!='G02'):
        raise ValueError('original_contract_bundle_must_close_exact_applicable_set')
    for k,v in contract.items():
        if k!='G02' and contract['G02']['content']['source_refs'].get(k+':'+v['instance_key']) != digest(v):
            raise ValueError('original_contract_bundle_requires_exact_source_digests')
    baseline=value['records']['baseline']['content']
    if baseline['model']!='sha256:'+spec['baseline_model']['sha256'].removeprefix('sha256:'):
        raise ValueError('baseline_requires_exact_initial_model_source')
    if baseline['data']!='G03:'+contract['G03']['instance_key'] or baseline['evaluator']!='G10:'+contract['G10']['instance_key']:
        raise ValueError('baseline_requires_exact_original_data_and_evaluator')
    if contract['G03']['content']['version'] != comparison['source_sha256']:
        raise ValueError('original_data_contract_requires_exact_source_version')
    tensor = contract['G09']['content']
    if (tensor['shape'].get('signal') != [1, info['length']] or tensor['shape'].get('logits') != [2]
            or tensor['inputs'] != ['signal'] or tensor['outputs'] != ['logits']):
        raise ValueError('original_tensor_contract_must_bind_binary_adapter')
    if (len(contract['G07']['content']['classes']) != 2
            or contract['G10']['content']['evaluator_identity'] != comparison['protocol']):
        raise ValueError('original_label_or_evaluation_contract_changed')
    return {**value, 'project_id': spec['project_id'], 'comparison_identity': comparison,
            'engine_contract_digest': bundle.engine_contract_digest, 'record_checks': checks,
            'workload': spec['workload'], 'baseline_source':spec['baseline_source'],'baseline_model':spec['baseline_model'],
            'protected_trainer_sha256':trainer_sha256,
            'data_facts': info, 'intent_digest': digest(intent), 'intent': intent,
            'scope': 'Exploratory component acceptance; original schemas/semantics and row 4/6 gates. '
                     'Projected E01 scientific kernel is not full E01 artifact qualification. '
                     'Original external-operation rows and signed formal transitions are not asserted.'}


def publish(value, path):
    once(path, value)
    return {'version': 1, 'seed': ref(path), 'engine_contract_digest': value['engine_contract_digest']}


def load(policy, spec=None):
    if (not isinstance(policy, dict) or set(policy) != {'version', 'seed', 'engine_contract_digest'}
            or type(policy['version']) is not int or policy['version'] != 1):
        raise ValueError('pinned_sdm_component_governance_required')
    value = read_json(reference(policy['seed']))
    bundle = AssetBundle.load()
    if (value.get('schema') != SCHEMA or value.get('engine_contract_digest') != bundle.engine_contract_digest
            or policy['engine_contract_digest'] != bundle.engine_contract_digest):
        raise ValueError('governance_original_engine_changed')
    applicable=REQUIRED_CONTRACTS
    if value['workload'].get('adapter')=='project/v1':
        from project_governance import required as project_required
        applicable=project_required(value['workload'])
    records(value['records'], bundle, value['project_id'],applicable)
    from research_intent import check as check_intent
    check_intent(bundle,value['intent']['requirement'],value['intent']['brief'])
    input_value={key:value[key] for key in ('schema','records','not_applicable','data_source','max_submissions')}
    replay=prepare({'project_id':value['project_id'],'comparison_identity':value['comparison_identity'],
                    'workload':value['workload'],'baseline_source':value['baseline_source'],'baseline_model':value['baseline_model'],
                    'sdm_governance':input_value},value['intent'])
    if replay != value:raise ValueError('governance_core_receipt_replay_changed')
    if spec is not None:
        from research_intent import load as intent_load
        _, intent = intent_load(spec['sdm_intent'])
        if (intent['project_id'] != value['project_id'] or digest(intent) != value['intent_digest']
                or spec['comparison_identity'] != value['comparison_identity']
                or spec.get('workload') != value['workload']):
            raise ValueError('governance_intent_or_comparison_changed')
    return value


def run_kernel(value, acceptance):
    """Freeze successor framing from the actual accepted proposal, preserving its predecessor."""
    accepted=read_json(reference(acceptance['reviews']['science']['receipt']))
    proposal=accepted['proposal'];kernel=copy.deepcopy(value['records']['experiment'])
    kernel['experiment_id']='experiment-'+acceptance['manager_plan']['sha256'].removeprefix('sha256:')[:32]
    kernel['hypothesis']=proposal['sdm_brief']['hypothesis']
    if acceptance.get('science_plan'):
        direction=proposal['research_revision']
        kernel['variables']={'independent':direction['intervention'],
                             'dependent':value['records']['experiment']['variables']['dependent']}
    checked=check_experiment_kernel(AssetBundle.load(),kernel)
    return kernel,checked


def baseline_preparation(spec, acceptance, state, manager_config):
    """Continue original baseline establishment from one accounted failed Run.

    The shared submission verifier has already replayed this repair's science,
    code and authority. This only binds baseline readiness to the original
    failed predecessor; it neither creates a successful baseline nor a retry.
    """
    if state['rounds'] or not acceptance.get('science_plan'):return None
    plan=read_json(reference(acceptance['science_plan']))
    if plan.get('preparation_recovery') is None:return None
    from research_replan import IMPLEMENTATION_ACTIONS
    if (plan['preparation_recovery']!={'version':1} or plan.get('parent_result_sha256') is not None
            or plan.get('research_goal_met') is not False or plan.get('execution_authority') is not False
            or plan.get('budget_renewed') is not False
            or plan.get('research_revision',{}).get('action') not in IMPLEMENTATION_ACTIONS
            or plan['research_revision'].get('change_level') in (None,'none')):
        raise ValueError('reviewed_initial_baseline_repair_required')
    diagnosis_ref=plan.get('parent_diagnosis_ref',{});diagnosis=read_json(reference(diagnosis_ref))
    if (diagnosis.get('schema')!='sdm-component-diagnosis-ready/v1' or diagnosis.get('valid_training_round') is not False
            or diagnosis.get('platform_status') not in {'failed','stopped','cancelled','canceled'}
            or not diagnosis.get('uid') or diagnosis['uid']!=plan.get('parent_task_uid')):
        raise ValueError('baseline_repair_requires_exact_terminal_failure')
    matches=[a for a in state['attempts'] if a.get('result')==diagnosis_ref]
    if (len(matches)!=1 or diagnosis.get('original_plan_sha256')!=matches[0]['source']['plan']['sha256'].removeprefix('sha256:')):
        raise ValueError('baseline_repair_requires_original_failed_attempt')
    parents=[r for r in state.get('governance_runs',[])
             if read_json(reference(r))['manager_plan']==matches[0]['source']['plan']]
    if len(parents)!=1:raise ValueError('baseline_repair_requires_registered_parent_run')
    verified_registration(spec,read_json(reference(parents[0])),manager_config)
    return {'approved_repair':acceptance['science_plan'],'failed_parent':diagnosis_ref,
        'registered_parent':parents[0],'valid_training_round':False,
        'scope':'Continued baseline establishment under original shared gate; no successful baseline or refunded budget.'}


def submission(spec, acceptance, state, initial):
    """No external calls. Consumes original shared submission replay results."""
    value = load(spec['sdm_governance'], spec)
    identities=acceptance.get('payload_manifest',{}).get('identities',{})
    if value['workload'].get('adapter')=='project/v1':
        from project_acceptance_facts import payload as project_payload_checks
        project_payload_checks(value,acceptance,initial)
    elif (identities.get('trainer.py')!=value['protected_trainer_sha256']
            or initial and identities.get('model.py')!=value['baseline_model']['sha256'].removeprefix('sha256:')):
        raise ValueError('actual_payload_requires_fixed_trainer_and_initial_baseline')
    from component_payload import executed_request_files
    actual_plan=read_json(reference(acceptance['manager_plan']))
    files,_=executed_request_files(actual_plan['request'],acceptance['payload_manifest']['code_sha256'])
    from component_payload import verify_model_api
    if value['workload'].get('adapter')!='project/v1':verify_model_api(files['model.py'],value['workload'])
    for name,text in files.items():
        if name.endswith('.py'):compile(text,name,'exec')
    rows = [round_evidence(row['result']) for row in state['rounds']]
    if any(row['fair_comparison'] != spec['comparison_identity'] for row in rows):
        raise ValueError('governance_baseline_comparison_changed')
    # Register the planned Attempt before Manager.apply; unknown never frees it.
    planned = state.get('governance_runs', [])
    existing = [r for r in planned if read_json(reference(r))['manager_plan'] == acceptance['manager_plan']]
    kernel,kernel_check=run_kernel(value,acceptance)
    preparation=(baseline_preparation(spec,acceptance,state,
        read_json(reference(spec['initial_source']['manager_config'])))
        if not initial and not rows and acceptance.get('science_plan') else None)
    checks = {'intent': all(r['passed'] for r in acceptance.get('sdm_intent_checks', {}).get('record_checks', []))
                        and bool(acceptance.get('sdm_intent_checks')),
              'contracts': True, 'baseline_spec': value['record_checks'][0]['passed'],
              'experiment_spec': kernel_check['passed'],
              'fixed_comparison': True, 'review_and_authority': True,
              'actual_payload': bool(acceptance.get('decoded_proof')),
              'baseline_available': bool(rows) or initial or preparation is not None}
    passed, missing, _ = evaluate_gate(DISPATCH, {'checks': checks})
    if not passed:
        raise ValueError('sdm_core_dispatch_blocked:' + ','.join(missing))
    if not existing and len(planned) >= value['max_submissions']:
        raise ValueError('sdm_core_nonrenewing_submission_budget_exhausted')
    identity = digest(acceptance['manager_plan']).removeprefix('sha256:')
    return {'schema': 'sdm-core-submission/v1', 'governance': spec['sdm_governance'],
            'submission_acceptance_digest': digest(acceptance), 'gate_digest': digest(DISPATCH),
            'checks': checks, 'run_id': 'run-' + identity, 'attempt_id': 'attempt-' + identity,
            'manager_plan': acceptance['manager_plan'], 'baseline_results': [row['result'] for row in state['rounds']],
            'initial_baseline_establishment': initial or preparation is not None,
            **({'baseline_preparation_recovery':preparation} if preparation is not None else {}),
            'experiment_kernel':kernel, 'kernel_check':kernel_check,
            'predecessor_science_plan':acceptance.get('science_plan'), 'scientific_rule': value['records']['experiment']['decision_rule'],
            'execution_authority': 'Original Manager/Guard and user delegation only.'}


def verified_registration(spec, registered, manager_config):
    from component_submission import verify
    accepted = read_json(reference(registered['submission_receipt']))
    actual = verify(spec, manager_config, accepted['manager_plan'], accepted['authorization'],
                    accepted['reviews'], accepted['payload_manifest'], accepted['science_plan'])
    if actual != accepted or digest(actual) != registered['submission_acceptance_digest']:
        raise ValueError('governance_original_submission_replay_changed')
    kernel,checked=run_kernel(load(spec['sdm_governance'],spec),accepted)
    if registered.get('experiment_kernel')!=kernel or registered.get('kernel_check')!=checked:
        raise ValueError('immutable_run_scientific_kernel_changed')
    if registered['governance'] != spec['sdm_governance']:
        raise ValueError('governance_submission_policy_changed')
    return accepted


def evidence_review(policy, response, evidence_digest):
    """Adapt the actual independent critic to original S10 schema/semantic checks."""
    value=load(policy)
    material=response.get('sdm_evidence_review',{})
    names={'protocol','statistics','failure_cases','reproducibility','deployment'}
    reasons=material.get('reasons',{})
    if (set(reasons)!=names or any(not isinstance(r,str) or len(r.strip())<8 for r in reasons.values())
            or material.get('deviation_disposition') not in ('none','resolved','unresolved','not_applicable')):
        raise ValueError('original_five_lens_evidence_and_resolved_deviations_required')
    if material.get('five_lenses',{}).get('deployment') != 'inconclusive':
        raise ValueError('no_deployment_pass_without_applicable_deployment_evidence')
    actor=response.get('reviewer',{})
    # Original model transport owns and verifies this completion provenance.
    from model_runner_transport import checked
    completed=checked(Path(actor['source_ref']),'sdm-model-backend-completed/v1')
    if completed['status']!='completed':raise ValueError('completed_independent_critic_required')
    from datetime import datetime,timezone
    created=datetime.fromtimestamp(completed['completed_at'],timezone.utc).isoformat().replace('+00:00','Z')
    from research_intent import _record
    content={'evidence_digest':evidence_digest if evidence_digest.startswith('sha256:') else 'sha256:'+evidence_digest,
             'reviewers':[actor['identity']], 'roles':['independent-evidence-reviewer'],
             'five_lenses':material.get('five_lenses'), 'deviation_disposition':material['deviation_disposition']}
    record=_record('S10','review-'+digest(response).removeprefix('sha256:')[:32],content,value['project_id'],created)
    checked_record=check_records(AssetBundle.load(),19,{'S10':[record]})
    if checked_record['findings'] or response.get('verdict')=='approve' and not checked_record['passed']:
        raise ValueError('original_sdm_independent_evidence_review_blocked')
    return {'record':record,'record_check':checked_record,'reasons':reasons,
            'scope':'Original S10 object and row checks from independent model provenance; not a signed formal handoff.'}


def completion(spec, state, plan_ref):
    """Full terminal accounting + independent replay, then original decision rule.

    The current adapter has adaptive validation and no confirmatory seed/test
    protocol. Its scientific conclusion is therefore inconclusive, even at the
    operational target. No caller or model can convert that into D05 support.
    """
    load(spec['sdm_governance'], spec)
    from component_submission import verified_review
    plan = read_json(reference(plan_ref))
    config, accepted = verified_review({'project': plan['review_project_ref'], 'root': plan['review_root'],
                                'receipt': plan['accepted_review_ref']})
    from research_replan import validate_accepted_direction
    validate_accepted_direction(plan,accepted)
    if config.get('research_replan', {}).get('rounds') != state['rounds']:
        raise ValueError('scientific_review_must_cover_exact_complete_result_set')
    runs, attempts, evidence = [], [], []
    seen = set()
    manager = read_json(reference(spec['initial_source']['manager_config']))
    for registered_ref in state.get('governance_runs', []):
        registered = read_json(reference(registered_ref))
        verified_registration(spec, registered, manager)
        if registered['attempt_id'] in seen:
            raise ValueError('duplicate_registered_attempt')
        seen.add(registered['attempt_id'])
        matches = [a for a in state['attempts'] if a.get('source', {}).get('plan') == registered['manager_plan']]
        if len(matches) != 1 or not matches[0].get('result'):
            raise ValueError('all_registered_attempts_require_terminal_evidence')
        attempt = matches[0]
        ready = read_json(reference(attempt['result']))
        if ready.get('original_plan_sha256') != registered['manager_plan']['sha256']:
            raise ValueError('attempt_result_requires_exact_original_manager_plan')
        success = ready.get('valid_training_round') is True
        if success:
            row = round_evidence(attempt['result'])
            if row['fair_comparison'] != spec['comparison_identity']:
                raise ValueError('scientific_completion_comparison_changed')
            from component_results import verify_engineering_preflight
            if spec.get('workload',{}).get('adapter')!='project/v1':
                verify_engineering_preflight(row['result'],load(spec['sdm_governance'],spec)['workload']['engineering_preflight'])
            terminal = 'successful'
        else:
            # A diagnosis needs actual provider terminal status, never a reason
            # string or a wall-clock timeout classified by the host.
            terminal = {'failed':'failed','cancelled':'cancelled','stopped':'cancelled'}.get(ready.get('platform_status'))
            if terminal not in ('failed', 'cancelled', 'invalid'):
                raise ValueError('failed_attempt_needs_authenticated_terminal_status')
        runs.append({'run_id': registered['run_id'], 'required_attempt_ids': [registered['attempt_id']]})
        attempts.append({'attempt_id': registered['attempt_id'], 'run_id': registered['run_id'],
                         'terminal_status': terminal, 'accounted': True,
                         'validity': 'valid' if success else 'invalid', 'system_failure': False})
        evidence.append(attempt['result'])
    if not runs:
        raise ValueError('scientific_completion_requires_registered_runs')
    value = load(spec['sdm_governance'], spec)
    rule = value['records']['experiment']['decision_rule']
    decision = evaluate_scientific_decision(rule, runs, attempts,
        {'protocol_invalid': False, 'underpowered': True,
         'predicates': {rule['support_predicate']: False, rule['rejection_predicate']: False}})
    reviewed=evidence_review(spec['sdm_governance'],accepted['response'],digest(evidence))
    return {'schema': 'sdm-core-completion/v1', 'governance': spec['sdm_governance'],
            'reviewed_plan': plan_ref, 'evidence': evidence, 'planned_runs': runs, 'attempts': attempts,
            'independent_evidence_review':reviewed, 'scientific_decision': decision, 'operational_status': plan['status'],
            'limitations': ['Adaptive fixed-validation selection; no confirmatory protocol.',
                           'No formal D05 authority, signed handoff or external ledger anchor is asserted.'],
            'execution_authority': False}


def project(cfg, state):
    """Same core checker used on recovery and in all read-only projections."""
    spec = cfg.get('component_pipeline', {})
    if not spec.get('sdm_governance'):
        return None  # Never reinterpret old frozen studies under new rules.
    value = load(spec['sdm_governance'], spec)
    # This field is owned by ComponentPipeline/task_pipeline, whose frozen
    # JSON encoding differs from normative canonical numbers (e.g. 1e-12).
    # Keep normative record digests unchanged; check this runtime field with
    # its one original producer, never accept either hash interchangeably.
    from task_pipeline import digest as runtime_digest
    if state.get('config_digest') != runtime_digest(cfg):
        raise ValueError('governance_state_configuration_changed')
    from lifecycle_view import catalog
    steps = [{**row, 'status': 'evidence-pending', 'evidence': []} for row in catalog()['steps']]
    steps[2]['detail']='比较起点定义与实际比较依据分别核验；首轮结果或当前最优不自动取得基线复现、锁定资格。'
    def mark(number, status, evidence):
        steps[number-1].update(status=status, evidence=evidence)
    mark(1, 'verified', [spec['sdm_intent']['seed']])
    for number in (2, 4):
        mark(number, 'verified', [spec['sdm_governance']['seed']])
    manager = read_json(reference(spec['initial_source']['manager_config']))
    for registered_ref in state.get('governance_runs', []):
        registered = read_json(reference(registered_ref))
        verified_registration(spec, registered, manager)
        for number in (5, 6, 7):
            mark(number, 'operational-verified', [registered_ref])
        matching=[a for a in state['attempts'] if a.get('source',{}).get('plan')==registered['manager_plan']]
        submitted=any(a.get('result') or a.get('initial_submission_status') in ('verified','already-completed') for a in matching)
        mark(9,'operational-verified' if submitted else 'registered',[registered_ref])
    for attempt in state['attempts']:
        if attempt.get('result'):
            ready = read_json(reference(attempt['result']))
            if ready.get('valid_training_round') is True:
                round_evidence(attempt['result'])
                for number in (3, 10, 11):
                    mark(number, 'operational-verified', [attempt['result']])
                from component_results import verify_engineering_preflight
                actual=round_evidence(attempt['result'])
                if value['workload'].get('adapter')!='project/v1':
                    verify_engineering_preflight(actual['result'],value['workload']['engineering_preflight'])
                # Project round_evidence already replays the same declared functional checks.
                mark(8, 'operational-verified', [attempt['result']])
    if state.get('governance_completion'):
        completed = read_json(reference(state['governance_completion']))
        actual = completion(spec, state, completed['reviewed_plan'])
        if completed != actual:
            raise ValueError('governance_completion_changed')
        for number in (12, 13):
            mark(number, 'operational-verified', [state['governance_completion']])
    return {'schema': 'sdm-core-acceptance-view/v1', 'governance': spec['sdm_governance'],
            'steps': steps, 'not_applicable_contracts': value['not_applicable'],
            'scope': value['scope']+' Cumulative evidence coverage: each reference applies only to its bound Run, never an unexecuted successor.', 'scientific_qualification': False,
            'authority': 'Core-owned verified references; no acceptance inferred from runtime position.'}


def publish_projection(cfg, state, value):
    """The owning frozen core publishes; a newer Web must not rejudge its studies."""
    from review_storage import atomic_json
    if value is None:return
    atomic_json(Path(cfg['root'])/'component-pipeline/acceptance.json',
        {'schema':'sdm-core-acceptance-projection/v1','config_digest':digest(cfg),
         'state_digest':digest(state),'runtime_manifest_digest':digest(cfg['runtime_manifest']), 'view':value})


def read_projection(cfg, state):
    if not cfg.get('component_pipeline',{}).get('sdm_governance'):return None
    value=read_json(Path(cfg['root'])/'component-pipeline/acceptance.json')
    if (value.get('schema')!='sdm-core-acceptance-projection/v1' or value.get('config_digest')!=digest(cfg)
            or value.get('state_digest')!=digest(state)
            or value.get('runtime_manifest_digest')!=digest(cfg['runtime_manifest'])
            or value.get('view',{}).get('governance')!=cfg['component_pipeline']['sdm_governance']):
        raise ValueError('core_acceptance_projection_stale_or_changed')
    seed=read_json(reference(value['view']['governance']['seed']))
    for key in ('data_source','baseline_source','baseline_model'):reference(seed[key])
    reference(cfg['component_pipeline']['sdm_intent']['seed'])
    for row in value['view']['steps']:
        for source in row['evidence']:reference(source)
    return value['view']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    cfg = read_json(args.config)
    state = read_json(Path(cfg['root'])/'component-pipeline/pipeline.json')
    print(json.dumps(read_projection(cfg, state), ensure_ascii=False, indent=2))
