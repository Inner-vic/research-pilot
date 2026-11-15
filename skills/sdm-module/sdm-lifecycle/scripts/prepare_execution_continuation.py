"""Build a bounded execution request from authenticated reusable data evidence.

Does not advance the numbered cursor, lock a baseline, approve a merge or issue
scientific support. The outer coordinator invokes handoff create/dispatch/ingest.
"""
import argparse, hashlib, json, re, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from canonical import canonical_bytes, digest
from ledger import replay
from projection import materialize
from workspace import validate_workspace

def loop_parameter_digest(controls):
    """Platform parameter wire digest: sorted JSON, distinct from ledger numbers."""
    raw=json.dumps(controls,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    return 'sha256:'+hashlib.sha256(raw).hexdigest()

def prepare(workspace, data_workspace, preparation, locators, execution_id, attempt_id, kind, timeout, authorization):
    if kind not in {'run','evaluate'} or not 60<=timeout<=1800: raise ValueError('bounded_execution_required')
    if not all(re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]{0,100}',value) for value in (execution_id,attempt_id)):
        raise ValueError('portable_execution_identity_required')
    for path in (workspace,data_workspace):
        report=validate_workspace(path)
        if report.get('valid') is not True: raise ValueError('authenticated_workspace_required')
    state=materialize(replay(workspace)); previous=materialize(replay(data_workspace))
    if preparation.get('status')!='prepared' or preparation.get('gpu_compute_invoked') is not False:
        raise ValueError('actual_non_gpu_preparation_required')
    if preparation.get('source_commit')!=locators['code_revision'] or preparation.get('source_url')!=locators['repository_identity']:
        raise ValueError('actual_source_preparation_mismatch')
    if 'loop_parameters' in locators:
        controls=json.loads(locators['loop_parameters'])
        if preparation.get('loop_parameters')!=controls or preparation.get('loop_parameters_digest')!=locators.get('loop_parameters_digest') or loop_parameter_digest(controls)!=locators['loop_parameters_digest']:
            raise ValueError('actual_loop_preparation_mismatch')
        if preparation.get('fixed_subset_path')!=locators.get('fixed_subset_path'):
            raise ValueError('fixed_loop_subset_mismatch')
    if locators.get('execution_scope')!='branch-diagnostic' or not locators.get('execution_provider'): raise ValueError('configured_diagnostic_provider_required')
    if not re.fullmatch(r'[0-9a-f]{40}',locators['code_revision']): raise ValueError('exact_source_commit_required')
    if timeout>int(authorization.get('timeout_limit_seconds',900)): raise ValueError('authorized_budget_exceeded')
    if locators['branch_identity'] in authorization.get('protected_branches',['main','master']): raise ValueError('personal_branch_required')
    if authorization.get('execution_scope')!='branch-diagnostic' or authorization.get('repository_identity')!=locators['repository_identity'] or authorization.get('branch_identity')!=locators['branch_identity'] or not authorization.get('user_request'): raise ValueError('explicit_case_authorization_required')
    locators={**locators,'authorization_digest':digest(authorization)}
    matching=[]
    for row in previous['handoffs'].values():
        s=row.get('subjects',{})
        if row.get('operation') in {'data.labeling-status','data.quality-check'} and row.get('status')=='successful' and s.get('dataset_scope_digest')==preparation['subset_digest'] and s.get('dataset_version')==locators['dataset_version'] and s.get('dataset_version_uid')==locators['dataset_uid']:
            matching.append({'request_id':row['request_id'],'operation':row['operation'],'result_digest':row['result_digest']})
    if {r['operation'] for r in matching}!={'data.labeling-status','data.quality-check'}:
        raise ValueError('matching_accepted_data_receipts_required')
    records={}
    for kind_id in ('R04','R05','R06'):
        choices=previous.get('artifacts',{}).get(kind_id,{})
        selected=[]
        for key,entry in choices.items():
            record=json.loads((data_workspace/entry['path']).read_bytes())
            if digest(record)!=entry['digest']: raise ValueError('data_record_digest_mismatch')
            if record['content'].get('receipt',{}).get('request_id') in {r['request_id'] for r in matching}:
                selected.append((key,entry,record))
        if kind_id in {'R04','R05'} and len(selected)!=1: raise ValueError('exact_reusable_data_manifest_required')
        if selected: records[kind_id]=selected[0]
    if preparation['subset_digest'].split(':')[1] not in records['R04'][2]['content']['dataset_identity']:
        raise ValueError('audited_subset_identity_mismatch')
    evidence={'workspace':str(data_workspace),'record_digests':{k:v[1]['digest'] for k,v in records.items()},'receipts':matching}
    prior=[{'code':'baseline-not-locked','message':'Baseline qualification is separate from permission for diagnostic execution.'},
           {'code':'unmeasured-calibration','message':'Physical precision is unmeasured; no deployment or effectiveness qualification.'}]
    subjects={**locators,'execution_id':execution_id,'execution_attempt_id':attempt_id,'execution_kind':kind,
              'resolved_config_digest':preparation['resolved_config_digest'],'dataset_scope_digest':preparation['subset_digest'],
              'data_evidence_digest':digest(evidence),'timeout_seconds':str(timeout)}
    required=['artifact_id','branch_identity','dataset_namespace','devspace_name','devspace_uid','source_root','execution_output_root','checkpoint_digest']
    if any(not subjects.get(k) for k in required): raise ValueError('execution_locators_required')
    logical={'operation':'ml.diagnostic-execution','subjects':subjects,'gate':'execution.observation','step':5,'portable_paths':[]}
    logical['request_id']='REQ-'+digest(logical).split(':')[1][:40]
    return logical,{'data_evidence':evidence,'prior_findings':prior,'authorization':authorization}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('workspace','data-workspace','preparation','locators','authorization','output','context-output'): p.add_argument('--'+name,type=Path,required=True)
    for name in ('execution-id','attempt-id'): p.add_argument('--'+name,required=True)
    p.add_argument('--kind',choices=['run','evaluate'],required=True); p.add_argument('--timeout',type=int,default=900)
    a=p.parse_args()
    request,context=prepare(a.workspace,a.data_workspace,json.loads(a.preparation.read_bytes()),json.loads(a.locators.read_bytes()),a.execution_id,a.attempt_id,a.kind,a.timeout,json.loads(a.authorization.read_bytes()))
    for path,doc in [(a.output,request),(a.context_output,context)]:
        with path.open('xb') as f: f.write(canonical_bytes(doc))
    print(json.dumps({'status':'prepared','operation':request['operation'],'request_id':request['request_id'],'reused_data_receipts':len(context['data_evidence']['receipts']),'cursor_advanced':False}))
if __name__=='__main__': main()
