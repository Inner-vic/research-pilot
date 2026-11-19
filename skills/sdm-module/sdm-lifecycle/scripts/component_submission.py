"""Adapt component submissions to existing SDM review, gate and authority logic.

This is a submission precondition, not a replacement lifecycle or D05 issuer.
Review replay is verify-only; original Manager/Guard still own every effect.
"""
import copy
import json
from pathlib import Path

from component_payload import ref, executed_request_files, verify_request_payload
from component_research import reference
from research_replan import once
from review_storage import read_json
from task_pipeline import digest


# Retained transport/review/authority controls for compatible submissions.
# Scientific S01/S02 checks use the original schemas, semantics and row gates
# through research_intent; this legacy control tree is not the SDM lifecycle.
GATE = {'sequence': [
    {'check': 'science_review'}, {'check': 'code_review'},
    {'check': 'reviewed_payload'}, {'check': 'comparison_and_scope'},
    {'check': 'manager_preflight'}, {'authority': 'original_delegation'},
]}


def enabled(spec):
    value = spec.get('submission_gate')
    if value is None:
        return False
    if value not in ({'version': 1}, {'version': 2}) or type(value['version']) is not int:
        raise ValueError('explicit_sdm_submission_gate_version_required')
    return True


def binding(project, root, receipt):
    return {'project': ref(project), 'root': str(Path(root).resolve()), 'receipt': ref(receipt)}


def verified_review(value):
    """Replay original Generator-Critic receipts and provenance without invoking a model."""
    if not isinstance(value, dict) or set(value) != {'project', 'root', 'receipt'}:
        raise ValueError('bound_original_submission_review_required')
    project = read_json(reference(value['project']))
    receipt = reference(value['receipt'])
    root = Path(value['root'])
    if not root.is_absolute() or not receipt.resolve().is_relative_to(root.resolve()):
        raise ValueError('original_submission_review_root_required')
    policy = project.get('proposal_review', {})
    if policy.get('version') != 4 or policy.get('backend') != 'model':
        raise ValueError('original_dual_model_submission_review_required')
    from proposal_model_runner import drive
    checked = drive(project, root, verify_only=True)
    if (checked.get('status') != 'proposal-approved'
            or not checked.get('receipt') or ref(checked['receipt']) != value['receipt']):
        raise ValueError('original_submission_review_not_approved')
    return project, read_json(receipt)


def execution_binding_view(original_ref,current_path,folder):
    """Loss-aware presentation only; complete exact plans remain registered.

    Long encoded commands obscure resources and mounts in model context. Their
    hashes/byte lengths are shown here; decoded code and raw plans are separate
    original sources. This function cannot approve a plan or submit a task.
    """
    import hashlib
    original=read_json(reference(original_ref));current=read_json(current_path)
    commands={}
    def visible(value,path):
        if isinstance(value,dict):
            return {k:visible(v,path+'/'+str(k)) for k,v in value.items()}
        if isinstance(value,list):return [visible(v,path+'/'+str(i)) for i,v in enumerate(value)]
        if isinstance(value,str) and path.rsplit('/',1)[-1] in ('cmd','command'):
            fact={'sha256':hashlib.sha256(value.encode()).hexdigest(),'bytes':len(value.encode()),'text_omitted':True}
            commands[path]=fact;return fact
        return value
    view={'schema':'sdm-execution-binding-view/v1','original_plan':original_ref,
          'current_manager_preflight':ref(current_path),
          'original_request':visible(original['request'],'/original_request'),
          'current_request':visible(current['request'],'/current_request'),
          'current_provider_baseline':visible(current['baseline'],'/current_provider_baseline'),
          'namespace':current['namespace'],'owner':current['owner'],
          'command_summaries':commands,'projection':'Only command strings omitted. Full exact plans and decoded payload remain registered; no acceptance is inferred.',
          'execution_authority':False,'platform_calls':0}
    path=Path(folder)/'execution-binding-view.json';once(path,view);return path


def initial_review(cfg, spec, manager_config, phase, folder, science=None):
    """Prepare ordinary original proposal-review jobs over the exact first intent."""
    if phase not in ('science', 'code') or (phase == 'code') != (science is not None):
        raise ValueError('initial_science_then_code_review_required')
    initial = spec['initial_submission']
    plan_ref = spec['initial_source']['plan']
    confirmed_ref = initial['confirmed_plan']
    plan = read_json(reference(plan_ref)); confirmed = read_json(reference(confirmed_ref))
    request = plan['request']; manifest = confirmed['payload_manifest']
    proof = verify_request_payload(request, manifest)
    files, _ = executed_request_files(request, manifest['code_sha256'])
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    from component_event_source import publish_bytes
    sources = [reference(confirmed_ref), reference(plan_ref), reference(spec['delegation']),
               reference(spec['baseline_source'])]
    sources.append(reference(spec['initial_source']['manager_config']))
    sources.extend(reference(r) for r in manager_config.get('execution_adapter',{}).get('files',[]))
    (folder/'payload').mkdir(exist_ok=True)
    for name, text in files.items():
        path = folder/'payload'/name
        publish_bytes(path, text.encode('utf-8')); sources.append(path)
    proof_path = folder/'payload-proof.json'; once(proof_path, proof); sources.append(proof_path)
    acceptance_view=None
    if science is not None:
        _,accepted=verified_review(science)
        acceptance_view=folder/'scientific-acceptance-view.json'
        once(acceptance_view,{'schema':'sdm-science-acceptance-view/v1','original_review':science,
            'approved':accepted.get('approved'),'response':accepted.get('response'),
            'projection':'Exact accepted review response; complete receipt remains registered. Not execution authority.',
            'execution_authority':False})
        sources.extend([reference(science['project']), reference(science['receipt']),acceptance_view])
    from research_execution import Manager
    preflight_path = folder/'manager-preflight.json'
    if not preflight_path.exists():
        once(preflight_path, Manager(manager_config).plan(copy.deepcopy(request)))
    current = read_json(preflight_path)
    if any(current.get(key) != plan.get(key) for key in
           ('request', 'baseline_digest', 'manager_digest', 'runtime_digest', 'namespace', 'owner')):
        raise ValueError('initial_review_manager_preflight_changed')
    binding_view=execution_binding_view(plan_ref,preflight_path,folder)
    sources.extend([preflight_path,binding_view])
    config = {'platform_requirements': manager_config['platform_requirements'],
              'proposal_review': copy.deepcopy(spec['proposal_review']),
              'component_submission_review': {
                  'version': 1, 'phase': phase, 'plan': plan_ref, 'confirmed_plan': confirmed_ref,
                  'delegation': spec['delegation'], 'payload_manifest': manifest,
                  **({'science_review': science} if science else {})}}
    # Initial code is already fixed, not a new implementation workspace. A
    # critique cannot silently alter it or use successor-implementation feedback.
    config['proposal_review'].pop('revision_target', None)
    # Reuse the same bounded raw-source view as successor code review. The
    # complete files remain registered for original supplement/replay checks.
    config['proposal_review']['source_budget'] = {'version': 1, 'serialized_bytes': 96*1024,
        'max_file_bytes': 32768, 'priority': [str(binding_view)]+([str(acceptance_view)] if acceptance_view else [])+[str(folder/'payload'/name)
            for name in files] + [str(proof_path), str(reference(confirmed_ref))]}
    if spec.get('sdm_intent') is not None:
        from research_intent import for_review, load
        _, seed = load(spec['sdm_intent'])
        config['sdm_intent'] = for_review(spec['sdm_intent'], seed['brief']['content'])
        sources.append(reference(spec['sdm_intent']['seed']))
    if spec.get('sdm_governance'):
        from research_acceptance import load as governance_load
        governance_load(spec['sdm_governance'],spec)
        config['sdm_governance']=copy.deepcopy(spec['sdm_governance'])
        sources.append(reference(spec['sdm_governance']['seed']))
    from proposal_review import subject
    context = {'schema': 'sdm-review-context/v1', 'subject_digest': digest(subject(config)),
        'goal': ('Review the scientific merit of the exact declared first experiment, its falsifiable '
                 'comparison, baseline, evidence, cost and stop conditions.' if phase == 'science' else
                 'Review the exact first payload against the accepted scientific proposal, protected '
                 'evaluation and current official execution preflight.'),
        'constraints': {
            'scope': 'Original confirmed plan, user delegation and component resources. Review never authorizes execution.',
            'fidelity': 'The complete decoded payload is the actual proposed execution. Explain and critique that exact intent; '
                        'do not invent measurements, rewrite the intervention in prose, or approve different code.',
            'evaluation': 'Check leakage and fair comparison; retain fixed split/selection. Syntax is not remote functional verification.',
            'changes': 'Revisions may clarify justification. A needed code/plan/resource change blocks this frozen intent; '
                       'it requires new bound inputs, not an implicit edit or a duplicate submission.',
            'local_ml': 'Do not import, execute, fit or evaluate the decoded ML code locally.'},
        'data': spec['comparison_identity'], 'code_revision': manifest,
        'configuration': {'objective': spec['objective'], 'confirmed_plan': confirmed_ref,
                          'target': spec['decision_policy']['target'], 'preflight': ref(preflight_path)},
        'history': {'valid_training_rounds': 0, 'initial_review_phase': phase,
                    'science_review': science or 'No accepted initial scientific review yet.'},
        'unknowns': 'No trained baseline or new result is asserted. Remote behavior, seed variation and effectiveness remain untested.',
        'sources': [{'id': 'initial-'+str(i), 'kind': 'code' if p.suffix == '.py' else 'config', **ref(p)}
                    for i, p in enumerate(dict.fromkeys(sources))]}
    from review_context import validate_context
    validate_context(context, context['subject_digest'])
    project = folder/'project.json'; once(project, config); once(folder/'review-context.json', context)
    return project


def successor_reviews(current, code_receipt):
    plan = read_json(reference(current['science_plan']))
    science = {'project': plan['review_project_ref'], 'root': plan['review_root'],
               'receipt': plan['accepted_review_ref']}
    return {'science': science, 'code': binding(current['code_review_project'], current['code_review_root'], code_receipt)}


def verify(spec, manager_config, plan_ref, authorization_ref, reviews, payload, science_plan=None):
    """One gate for first and successor intents; no model or platform calls."""
    retained=spec.get('prior_submission_verifiers',{}).get(plan_ref['sha256'])
    if retained:
        # Old reviews include their original guidance paths. Replay with their
        # frozen owner rather than reinterpret/retag them under a new runtime.
        original=read_json(reference(retained))
        if {k:v for k,v in spec.items() if k!='prior_submission_verifiers'}!=original['component_pipeline']:
            raise ValueError('retained_submission_verifier_scope_changed')
        import subprocess,sys
        code=('import json,sys;sys.path.insert(0,sys.argv[1]);'
              'from review_storage import read_json;from research_event_service import configuration;'
              'from component_submission import verify;cfg=configuration(read_json(sys.argv[2]));'
              'args=json.load(sys.stdin);print(json.dumps(verify(cfg["component_pipeline"],**args)))')
        checked=subprocess.run([sys.executable,'-I','-S','-B','-c',code,original['runtime_root'],str(reference(retained))],
            input=json.dumps({'manager_config':manager_config,'plan_ref':plan_ref,'authorization_ref':authorization_ref,
                              'reviews':reviews,'payload':payload,'science_plan':science_plan}),
            capture_output=True,encoding='utf-8',timeout=120,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if checked.returncode:raise ValueError('retained_original_submission_replay_failed:'+checked.stderr.splitlines()[-1][:180])
        return json.loads(checked.stdout)
    if not enabled(spec):
        raise ValueError('sdm_submission_gate_policy_required')
    if not isinstance(reviews, dict) or set(reviews) != {'science', 'code'}:
        raise ValueError('both_original_submission_reviews_required')
    from lpai_manager import authorized_plan
    plan, auth = authorized_plan(reference(plan_ref), reference(authorization_ref), manager_config)
    science_cfg, science_receipt = verified_review(reviews['science'])
    code_cfg, _ = verified_review(reviews['code'])
    if science_plan is None:
        initial = spec['initial_submission']
        for phase, config in (('science', science_cfg), ('code', code_cfg)):
            selected = config.get('component_submission_review', {})
            if (selected.get('phase') != phase or selected.get('plan') != plan_ref
                    or selected.get('confirmed_plan') != initial['confirmed_plan']
                    or selected.get('delegation') != spec['delegation'] or selected.get('payload_manifest') != payload):
                raise ValueError('initial_review_does_not_bind_exact_submission')
        if code_cfg['component_submission_review'].get('science_review') != reviews['science']:
            raise ValueError('initial_code_review_requires_original_scientific_review')
    else:
        approved = read_json(reference(science_plan))
        if (approved.get('review_project_ref') != reviews['science']['project']
                or approved.get('accepted_review_ref') != reviews['science']['receipt']
                or approved.get('review_root') != reviews['science']['root']
                or approved.get('proposal') != science_receipt.get('proposal')):
            raise ValueError('successor_requires_original_scientific_plan')
        selected = code_cfg.get('component_code_review', {})
        request = read_json(reference(selected['request']))
        if (selected.get('accepted_plan') != science_plan or selected.get('payload_manifest') != payload
                or any(plan['request'].get(k) != v for k, v in request.items())
                or set(plan['request'])-set(request)-{'component_settings'}):
            raise ValueError('successor_review_does_not_bind_exact_submission')
    if spec.get('sdm_governance'):
        from research_acceptance import load as governance_load
        governance_load(spec['sdm_governance'],spec)
        if any(c.get('sdm_governance') != spec['sdm_governance'] for c in (science_cfg,code_cfg)):
            raise ValueError('both_reviews_require_same_sdm_governance')
    proof = verify_request_payload(plan['request'], payload)
    files, _ = executed_request_files(plan['request'], payload['code_sha256'])
    parameters_name=(json.loads(files['_researchpilot_project.json'])['parameters_file'] if '_researchpilot_project.json' in files else 'parameters.json')
    parameters = json.loads(files[parameters_name])
    delegation = read_json(reference(spec['delegation']))
    scope = {'provider': manager_config.get('provider','lpai'), 'namespace': manager_config['namespace'],
             'owner': manager_config['owner'], 'component_uid': plan['request']['uid']}
    checks = {'science_review': True, 'code_review': True, 'reviewed_payload': bool(proof),
              'comparison_and_scope': parameters.get('comparison_identity') == spec['comparison_identity']
                  and parameters.get('execution_scope') == scope
                  and parameters.get('platform_requirements') == manager_config.get('platform_requirements'),
              'manager_preflight': plan['request'].get('action') == 'task.submit'}
    limits = delegation.get('training_limits', {})
    epochs = parameters.get('training', {}).get('epochs')
    authority = (delegation.get('schema') == 'sdm-research-delegation/v1'
        and delegation.get('allow_successor_submission') is True
        and delegation.get('comparison_identity') == spec['comparison_identity']
        and all(delegation.get(k) == scope[k] for k in ('namespace', 'owner', 'component_uid'))
        and auth.get('delegation') == spec['delegation'] and auth.get('source_ref') == delegation.get('source_ref')
        and type(epochs) is int and type(limits.get('min_epochs')) is int and type(limits.get('max_epochs')) is int
        and limits['min_epochs'] <= epochs <= limits['max_epochs'])
    from research_intent import verify_submission
    intent_checks = verify_submission(spec, science_cfg, code_cfg, science_receipt['proposal'])
    if spec['submission_gate']['version'] == 2 and intent_checks is None:
        raise ValueError('submission_requires_original_sdm_intent')
    from contracts import evaluate_gate
    passed, missing, _ = evaluate_gate(GATE, {'checks': checks, 'authorities': {'original_delegation': authority}})
    if not passed:
        raise ValueError('sdm_submission_gate_blocked:'+','.join(missing))
    return {'schema': 'sdm-component-submission-acceptance/v1', 'gate_digest': digest(GATE),
            'manager_plan': plan_ref, 'authorization': authorization_ref, 'reviews': reviews,
            'science_plan': science_plan, 'payload_manifest': payload, 'decoded_proof': proof,
            **({'sdm_intent_checks': intent_checks} if intent_checks is not None else {}),
            'checks': checks, 'authority': 'Original Manager authorization and scoped user delegation; not review approval.',
            'scope': 'SDM submission duties only; not full 21-step qualification, D05, functional verification or platform approval.',
            'model_calls': 0, 'platform_calls': 0}
