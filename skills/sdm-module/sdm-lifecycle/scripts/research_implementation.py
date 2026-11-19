"""Original reviewed replan -> native implementation job -> frozen preflight.

This adapter neither fabricates authorization nor runs platform/training code.
Its completion is an implementation artifact, not execution qualification.
"""
import hashlib
import json
from pathlib import Path

from component_research import reference
from research_replan import once, IMPLEMENTATION_ACTIONS
from required_skills import inspect, inherit
from review_storage import read_json, native_path
from task_pipeline import digest


def strict_json(raw):
    def invalid(value):raise ValueError('nonfinite_implementation_json')
    return json.loads(raw,parse_constant=invalid)


def spec(config):
    value=config.get('research_implementation',{})
    if value.get('version') not in (1,2):raise ValueError('research_implementation_version_required')
    if inspect(config)['status']!='required-skills-available':raise ValueError('configured_platform_capabilities_required')
    for name in ('approved_plan','implementation_request','review_project','execution_contract'):
        reference(value[name])
    if 'validation_feedback' in value:
        if value['version']!=2:raise ValueError('implementation_feedback_requires_v2')
        reference(value['validation_feedback'])
    if not Path(value['review_root']).is_absolute() or not Path(value['workspace']).is_absolute():
        raise ValueError('absolute_review_and_implementation_workspace_required')
    return value


def execution_contract(selected):
    contract=read_json(selected['execution_contract']['path'])
    identity=contract.get('comparison_identity',{})
    scope=contract.get('execution_scope',{})
    if (contract.get('schema')!='sdm-component-implementation-contract/v1'
            or set(identity)!={'protocol','source_sha256','split_sha256','selection'}
            or any(not isinstance(v,str) or not v for v in identity.values())
            or not isinstance(contract.get('protected_inputs'),list) or not contract['protected_inputs']
            or not isinstance(scope.get('provider'),str) or not scope['provider']
            or any(not isinstance(scope.get(k),str) or not scope[k] for k in ('namespace','owner','component_uid'))):
        raise ValueError('protected_component_evaluation_contract_required')
    for row in contract['protected_inputs']:reference(row)
    allowed=contract.get('allowed_files',[])
    if (not isinstance(allowed,list) or not 1<=len(allowed)<=32
            or any(not isinstance(p,str) for p in allowed) or len(set(allowed))!=len(allowed)
            or any(Path(p).is_absolute() or '..' in Path(p).parts for p in allowed)
            or contract.get('parameters_file') not in allowed):
        raise ValueError('explicit_implementation_file_contract_required')
    return contract


def prepare(config,root,reviewer=None):
    selected=spec(config);plan=read_json(selected['approved_plan']['path'])
    execution_contract(selected)
    request=read_json(selected['implementation_request']['path'])
    review_cfg=read_json(selected['review_project']['path'])
    if (plan.get('review_project_ref')!=selected['review_project']
            or Path(plan.get('review_root','')).resolve()!=Path(selected['review_root']).resolve()):
        raise ValueError('original_replan_review_configuration_required')
    if (plan.get('schema')!='sdm-research-plan/v1' or plan.get('execution_authority') is not False
            or plan.get('budget_renewed') is not False or plan.get('research_goal_met') is not False
            or request.get('schema')!='sdm-research-implementation-request/v1'
            or request.get('parent_plan_digest')!=digest(plan) or request.get('execution_authority') is not False):
        raise ValueError('exact_approved_implementation_request_required')
    for original in (plan,request,review_cfg):
        if original.get('platform_requirements')!=config.get('platform_requirements'):
            raise ValueError('implementation_cannot_drop_required_skill_policy')
    if plan.get('research_revision',{}).get('action') not in IMPLEMENTATION_ACTIONS:
        raise ValueError('accepted_plan_does_not_request_implementation')
    original_review=read_json(reference(plan['accepted_review_ref']))
    if (original_review.get('approved') is not True or original_review.get('response',{}).get('verdict')!='approve'
            or original_review.get('proposal')!=plan.get('proposal')):
        raise ValueError('original_accepted_review_required')
    if selected.get('validation_feedback'):
        from implementation_validation import check_feedback
        check_feedback(selected['validation_feedback'],selected)
    # Revalidate the original gate and its model receipts; a proposal file or
    # self-reported approved=true is not a handoff.
    if reviewer is None:
        from proposal_review import gate
        reviewer=gate
    checked=reviewer(review_cfg,Path(selected['review_root']))
    if checked.get('status')!='proposal-approved':return {**checked,'implementation_started':False}
    approved=Path(checked['receipt']).resolve()
    if approved!=Path(plan['accepted_review']).resolve() or hashlib.sha256(native_path(approved).read_bytes()).hexdigest()!=plan['accepted_review_ref']['sha256']:
        raise ValueError('implementation_review_receipt_changed')
    root=Path(root).resolve();workspace=Path(selected['workspace']).resolve()
    if root.is_relative_to(workspace):raise ValueError('implementation_control_outside_model_workspace_required')
    native_path(root).mkdir(parents=True,exist_ok=True);native_path(workspace).mkdir(parents=True,exist_ok=True)
    for row in selected.get('sources',[]):reference(row)
    identity={'plan':selected['approved_plan'],'request':selected['implementation_request'],
              'contract':selected['execution_contract'],'workspace':str(workspace)}
    if selected.get('validation_feedback'):
        identity['validation_feedback']=selected['validation_feedback']
    key=digest(identity)
    job={'schema':'sdm-research-implementation-job/v1','job_id':key,'approved_plan':selected['approved_plan'],
         'implementation_request':selected['implementation_request'],'execution_contract':selected['execution_contract'],
         'sources':selected.get('sources',[]),'workspace':str(workspace),
         'output':str(workspace/'implementation.json'),'execution_authority':False,
         'instructions':'Read the bound approved plan and original evidence. Implement its intervention only inside this workspace. '
             'Do not allocate resources, run ML locally, query/write platform resources, alter authorization or reset budgets. '
             'Retain the frozen evaluation identity and the exact platform capabilities declared by this job. '
             'The parameters file must preserve comparison_identity and execution_scope from the contract and platform_requirements from this job exactly. '
             'Write implementation.json with schema=sdm-research-implementation-output/v1, job_id, parent_plan_digest, contract_sha256, status=implemented, '
             'and files=[{path:relative workspace path,sha256:exact file hash}]. Do not claim code review or execution passed.'}
    if selected.get('validation_feedback'):
        job['validation_feedback']=selected['validation_feedback']
        job['instructions']+=' Read the bound validation_feedback and preserved failed files. Correct these concrete defects '
        job['instructions']+='while implementing the same approved plan and contract. This is a new corrective turn after '
        job['instructions']+='a completed, receipted turn, not a resend of an unknown call or a new scientific plan.'
    if selected.get('capability_feedback')=={'version':2}:
        job['instructions']+=(' If blocked, write implementation.json with the SAME schema/job_id/parent_plan_digest/contract_sha256, '
            'status="blocked", files=[], block_kind="capability"|"known-recipe"|"needs-evidence", reason=nonempty explanation, '
            'and evidence_paths=[only registered source paths, approved_plan or execution_contract]. '
            'Do not leave only a natural-language needs-agent message or claim implementation completed. '
            'The reason and evidence do not authorize scope or protected-file changes.')
    bound=execution_contract(selected)
    if 'next_round' in bound:
        job['instructions']+=' Set '+bound.get('parameters_file','parameters.json')+'.training.round='+str(bound['next_round'])+' exactly as host identity metadata, including exact scientific reproduction. Preserve the scientific settings independently.'
    inherit(config,job);once(root/'implementation-job.json',job)
    return {'status':'implementation-job-prepared','job_path':str(root/'implementation-job.json'),
            'job_id':key,'execution_authority':False,'platform_actions':0}


def freeze(config,root):
    if config.get('research_implementation',{}).get('version')==2:
        from implementation_validation import validate_completed
        return validate_completed(config,root,_freeze)
    return _freeze(config,root)


def _freeze(config,root,snapshot=None):
    selected=spec(config);root=Path(root).resolve();job=read_json(root/'implementation-job.json')
    if job.get('approved_plan')!=selected['approved_plan'] or job.get('execution_contract')!=selected['execution_contract']:
        raise ValueError('original_implementation_job_changed')
    workspace=Path(job['workspace'])
    output=(strict_json(snapshot['implementation.json']) if snapshot is not None else read_json(workspace/'implementation.json'))
    contract=read_json(selected['execution_contract']['path'])
    execution_contract(selected)
    plan=read_json(selected['approved_plan']['path'])
    reference(plan['accepted_review_ref'])
    if (not isinstance(output,dict) or output.get('schema')!='sdm-research-implementation-output/v1' or output.get('status')!='implemented'
            or output.get('job_id')!=job['job_id'] or output.get('parent_plan_digest')!=digest(plan)
            or output.get('contract_sha256')!=selected['execution_contract']['sha256']):
        raise ValueError('bound_implementation_output_required')
    if contract.get('schema')!='sdm-component-implementation-contract/v1':
        raise ValueError('explicit_component_implementation_contract_required')
    rows=output.get('files');allowed=contract.get('allowed_files')
    if not isinstance(rows,list) or not rows or len(rows)>32 or not isinstance(allowed,list) or not allowed:
        raise ValueError('bounded_implementation_file_manifest_required')
    files={}
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'path','sha256'}:
            raise ValueError('typed_implementation_file_reference_required')
        name=row.get('path');path=workspace/str(name)
        if (name not in allowed or name in files or Path(name).is_absolute() or '..' in Path(name).parts
                or not path.resolve().is_relative_to(workspace.resolve()) or native_path(path).is_symlink()
                or (snapshot is None and (not native_path(path).is_file() or native_path(path).stat().st_size>2*1024*1024))):
            raise ValueError('declared_implementation_files_only')
        raw=snapshot[name] if snapshot is not None else native_path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row.get('sha256'):raise ValueError('implementation_file_changed')
        if path.suffix=='.py':compile(raw,name,'exec')  # syntax only, no import/local fitting
        elif path.suffix=='.json':strict_json(raw)
        elif contract.get('integration')=='project/v1':raw.decode('utf-8')
        else:raise ValueError('implementation_source_or_json_required')
        files[name]=raw
    if set(files)!=set(allowed):raise ValueError('complete_implementation_file_set_required')
    parameters=strict_json(files[contract['parameters_file']])
    if not isinstance(parameters,dict):raise ValueError('typed_implementation_parameters_required')
    if 'next_round' in contract and parameters.get('training',{}).get('round')!=contract['next_round']:
        raise ValueError('implementation_round_metadata_required')
    if parameters.get('comparison_identity')!=contract.get('comparison_identity'):
        raise ValueError('implementation_evaluation_contract_changed')
    if parameters.get('platform_requirements')!=config['platform_requirements']:
        raise ValueError('implementation_required_skill_policy_changed')
    if parameters.get('execution_scope')!=contract['execution_scope']:
        raise ValueError('implementation_execution_scope_changed')
    manifest={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}
    version=digest({'job':job['job_id'],'files':manifest}).removeprefix('sha256:')
    frozen=root/'frozen'/version;native_path(frozen).mkdir(parents=True,exist_ok=True)
    from component_event_source import publish_bytes
    for name,raw in files.items():
        path=frozen/name;publish_bytes(path,raw)
    result={'schema':'sdm-implemented-profile/v1','status':'requires-original-code-and-execution-preflight',
            'job_id':job['job_id'],'parent_plan_digest':digest(plan),'files':manifest,'root':str(frozen),
            'execution_contract':selected['execution_contract'],'comparison_identity':contract['comparison_identity'],
            'execution_scope':contract['execution_scope'],
            'syntax_verified':True,'model_functional_verification':False,'code_review_required':True,
            'execution_authority':False,'budget_renewed':False,'platform_actions':0}
    inherit(config,result);once(root/'implemented-profile.json',result)
    return result
