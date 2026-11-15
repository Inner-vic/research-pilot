"""Completed native output -> immutable local defect evidence, never execution.

Only opt-in v2 uses this adapter. Unknown calls and changed protected inputs do
not become repair prompts. The original planner/reviewer still owns science.
"""
import hashlib
import json
from pathlib import Path

from component_research import reference
from research_replan import once
from review_storage import read_json,native_path
from task_pipeline import digest

REPAIRABLE={'bound_implementation_output_required','bounded_implementation_file_manifest_required',
    'typed_implementation_file_reference_required','implementation_file_changed',
    'complete_implementation_file_set_required','nonfinite_implementation_json',
    'typed_implementation_parameters_required','implementation_required_file_missing','implementation_round_metadata_required'}


def ref(path):
    path=Path(path).resolve()
    return {'path':str(path),'sha256':hashlib.sha256(native_path(path).read_bytes()).hexdigest()}


def completion(selected,root,job):
    proof=selected.get('native_completion',{})
    if set(proof)!={'job_id','receipt','implementation_job'}:
        raise ValueError('completed_native_implementation_receipt_required')
    workspace=Path(job['workspace']).resolve()
    for value in (proof['receipt'],proof['implementation_job']):
        if reference(value).resolve().is_relative_to(workspace):
            raise ValueError('native_receipt_must_be_outside_implementation_workspace')
    if reference(proof['implementation_job']).resolve()!=(root/'implementation-job.json').resolve():
        raise ValueError('original_native_implementation_input_required')
    receipt=read_json(proof['receipt']['path']);detail=receipt.get('detail',{})
    if (receipt.get('job_id')!=proof['job_id'] or receipt.get('status')!='completed'
            or detail.get('terminal')!='completed' or not detail.get('thread_id') or not detail.get('turn_id')):
        raise ValueError('unknown_native_implementation_reconcile_only')
    return proof


class Snapshot(dict):
    def __missing__(self,name):raise ValueError('implementation_required_file_missing')


def capture(workspace,contract):
    result=Snapshot();workspace=workspace.resolve()
    names=['implementation.json',*contract['allowed_files']]
    for name in dict.fromkeys(names):
        path=workspace/name
        if (Path(name).is_absolute() or '..' in Path(name).parts
                or not path.resolve().is_relative_to(workspace) or path.is_symlink()
                or any(p.is_symlink() for p in path.parents if p!=workspace and p.is_relative_to(workspace))):
            raise ValueError('unsafe_implementation_snapshot_path')
        if not path.exists():continue
        if not path.is_file() or path.stat().st_size>2*1024*1024:
            raise ValueError('bounded_regular_implementation_snapshot_required')
        result[name]=path.read_bytes()
        if len(result[name])>2*1024*1024:raise ValueError('bounded_regular_implementation_snapshot_required')
    return result


def check_feedback(value,selected):
    path=reference(value);feedback=read_json(path)
    if feedback.get('schema')=='sdm-implementation-code-feedback/v1':
        if path.resolve().is_relative_to(Path(selected['workspace']).resolve()):
            raise ValueError('code_feedback_must_be_outside_implementation_workspace')
        from implementation_code_review import check
        return check(value,selected)
    if (path.resolve().is_relative_to(Path(selected['workspace']).resolve())
            or feedback.get('schema')!='sdm-implementation-validation-feedback/v1'
            or feedback.get('status')!='correctable-local-defect'
            or feedback.get('approved_plan')!=selected['approved_plan']
            or feedback.get('execution_contract')!=selected['execution_contract']
            or feedback.get('workspace')!=str(Path(selected['workspace']).resolve())
            or feedback.get('execution_authority') is not False):
        raise ValueError('bound_same_plan_validation_feedback_required')
    for row in feedback['files'].values():reference(row)
    previous=read_json(reference(feedback['native_completion']['receipt']))
    if previous.get('status')!='completed' or previous.get('job_id')!=feedback['native_completion']['job_id']:
        raise ValueError('feedback_requires_completed_original_native_call')
    return feedback


def reported_capability_gap(selected,native,root,job=None,snapshot=None):
    """A completed refusal is evidence for science, not a manifest repair.

    The implementer's text cannot authorize a contract extension. The original
    immutable interface and both reviewers must independently assess its claim.
    """
    receipt=read_json(reference(native['receipt']));detail=receipt.get('detail',{})
    if (receipt.get('status')!='completed' or receipt.get('job_id')!=native['job_id']
            or detail.get('terminal')!='completed'):
        raise ValueError('unknown_native_implementation_reconcile_only')
    response=detail.get('response','')
    format='legacy-prefix/v1';kind='implementer-reported'
    if selected.get('capability_feedback')=={'version':2}:
        if job is None or snapshot is None:return None
        try:output=json.loads(snapshot.get('implementation.json',b'{}'))
        except (ValueError,UnicodeError):return None
        if output.get('status')!='blocked':return None
        if (set(output)!={'schema','job_id','parent_plan_digest','contract_sha256','status','files','block_kind','reason','evidence_paths'}
                or output['schema']!='sdm-research-implementation-output/v1' or output['job_id']!=job['job_id']
                or output['parent_plan_digest']!=digest(read_json(reference(selected['approved_plan'])))
                or output['contract_sha256']!=selected['execution_contract']['sha256'] or output['files']!=[]
                or output['block_kind'] not in ('capability','known-recipe','needs-evidence')
                or not isinstance(output['reason'],str) or not 0<len(output['reason'])<=16000):
            raise ValueError('bound_structured_implementation_block_required')
        allowed={r['path'] for r in selected.get('sources',[])}|{selected['approved_plan']['path'],selected['execution_contract']['path']}
        if not isinstance(output['evidence_paths'],list) or not set(output['evidence_paths'])<=allowed:
            raise ValueError('registered_block_evidence_paths_required')
        response=output['reason'];kind=output['block_kind'];format='structured/v2'
    elif not isinstance(response,str) or not response.strip().lstrip('*').startswith(('needs-agent','needs-capability')):
        return None
    feedback={'schema':'sdm-implementation-capability-gap/v1',
        'approved_plan':selected['approved_plan'],'execution_contract':selected['execution_contract'],
        'native_completion':native,'implementer_claim':response,
        'response_format':format,'reported_kind':kind,
        'claim_status':'untrusted completed implementation report; independently review actual protected interface',
        'next':'Reassess the scientific plan under the actual interface or report a needed host capability extension; '
            'do not repeatedly repair the manifest or claim an unimplemented experiment ran.',
        'execution_authority':False,'platform_actions':0}
    path=Path(root)/'capability-gap.json';once(path,feedback)
    return {'status':'implementation-capability-gap','feedback':ref(path),
        'execution_authority':False,'platform_actions':0}


def validate_completed(config,root,freeze):
    from research_implementation import spec,execution_contract
    selected=spec(config);root=Path(root).resolve()
    if root.is_relative_to(Path(selected['workspace']).resolve()):
        raise ValueError('implementation_control_outside_model_workspace_required')
    job=read_json(root/'implementation-job.json')
    if (job.get('approved_plan')!=selected['approved_plan']
            or job.get('execution_contract')!=selected['execution_contract']
            or Path(job.get('workspace','')).resolve()!=Path(selected['workspace']).resolve()):
        raise ValueError('original_implementation_job_changed')
    contract=execution_contract(selected);plan=read_json(selected['approved_plan']['path'])
    reference(plan['accepted_review_ref'])
    native=completion(selected,root,job)
    result_path=root/'validation-result.json'
    if result_path.exists():
        saved=read_json(result_path)
        if saved.get('config_digest')!=digest(config) or saved.get('native_completion')!=native:
            raise ValueError('original_validation_receipt_changed')
        for row in saved['artifacts']:reference(row)
        return saved['result']
    snapshot=capture(Path(job['workspace']),contract)
    gap=(reported_capability_gap(selected,native,root,job,snapshot)
         if selected.get('capability_feedback') in ({'version':1},{'version':2}) else None)
    if gap:
        once(result_path,{'schema':'sdm-completed-implementation-validation/v1','config_digest':digest(config),
            'native_completion':native,'result':gap,'artifacts':[gap['feedback']]})
        return gap
    try:
        result=freeze(config,root,snapshot)
        artifacts=[ref(Path(result['root'])/name) for name in result['files']]
    except (ValueError,SyntaxError) as error:
        if isinstance(error,json.JSONDecodeError):
            reason='invalid_implementation_json';location={'line':error.lineno,'column':error.colno}
        elif isinstance(error,SyntaxError):
            reason='invalid_implementation_python';location={'file':error.filename,'line':error.lineno,'column':error.offset}
        elif str(error) in REPAIRABLE:reason=str(error);location={}
        else:raise  # authority/contract/integrity failures remain attention, not repair
        hashes={name:hashlib.sha256(raw).hexdigest() for name,raw in snapshot.items()}
        # Delivery/job binding fields legitimately change on a repair; they
        # must not make unchanged invalid source appear to be new progress.
        signature=digest({'reason':reason,'files':{k:v for k,v in hashes.items() if k!='implementation.json'}})
        version=digest({'job':job['job_id'],'files':hashes,'reason':reason})
        folder=root/'validation-feedback'/version.removeprefix('sha256:');native_path(folder).mkdir(parents=True,exist_ok=True)
        from component_event_source import publish_bytes
        files={}
        for name,raw in snapshot.items():
            path=folder/'files'/name;native_path(path.parent).mkdir(parents=True,exist_ok=True);publish_bytes(path,raw);files[name]=ref(path)
        feedback={'schema':'sdm-implementation-validation-feedback/v1','status':'correctable-local-defect',
            'reason':reason,'location':location,'failure_signature':signature,'implementation_job_id':job['job_id'],
            'approved_plan':selected['approved_plan'],'execution_contract':selected['execution_contract'],
            'workspace':str(Path(job['workspace']).resolve()),'native_completion':native,'files':files,
            'missing_files':[name for name in dict.fromkeys(['implementation.json',*contract['allowed_files']]) if name not in snapshot],
            'execution_authority':False,'ML_fits':0,'platform_actions':0,
            'next_action':'Correct the exact local defect under the same approved plan. Retain the failed evidence. '
                'Do not change evaluation, execution scope, authority or claim functional validation.'}
        once(folder/'feedback.json',feedback)
        result={'status':'implementation-correction-required','feedback':ref(folder/'feedback.json'),
                'failure_signature':signature,'execution_authority':False,'platform_actions':0}
        artifacts=[result['feedback'],*files.values()]
    once(result_path,{'schema':'sdm-completed-implementation-validation/v1','config_digest':digest(config),
        'native_completion':native,'result':result,'artifacts':artifacts})
    return result
