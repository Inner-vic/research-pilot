"""Read retained terminal results only. No executor, platform or model imports."""
import hashlib
import json
import math
from pathlib import Path


def inspect(config, root):
    root = Path(root).resolve()
    worker = config.get('research_worker', {})
    project_path = Path(worker['project_path']).resolve()
    project_raw = project_path.read_bytes()
    if Path(worker['output']).resolve() != root or json.loads(project_raw) != config:
        raise ValueError('terminal_project_output_binding_changed')
    state_path = root/'execution.json'; state = json.loads(state_path.read_bytes())
    if state.get('status') != 'completed' or not state.get('study_complete'):
        raise ValueError('completed_study_required_for_terminal_view')
    latest = state['history'][-1]; result_path = Path(latest['result_path']).resolve()
    if not result_path.is_relative_to(root): raise ValueError('registered_result_root_required')
    raw = result_path.read_bytes()
    if 'sha256:'+hashlib.sha256(raw).hexdigest() != latest['result_digest']:
        raise ValueError('retained_result_digest_changed')
    result = json.loads(raw); criteria = []
    for target in config.get('research_iteration',{}).get('targets',[]):
        actual = result
        for key in target['field'].split('.'): actual = actual[key]
        if type(actual) not in (int,float) or not math.isfinite(actual):
            raise ValueError('actual_numeric_research_metric_required')
        if target['operator'] not in ('>=','<='): raise ValueError('numeric_goal_operator_required')
        passed = actual>=target['value'] if target['operator']=='>=' else actual<=target['value']
        criteria.append({**target,'actual':actual,'passed':passed})
    commits = []
    request = state.get('review',{}).get('binding',{}).get('request_digest')
    for path in (root/'research-decisions').glob('*/commit.json'):
        commit = json.loads(path.read_bytes())
        record = commit.get('record',{})
        sources = record.get('sources',{})
        project_match = any(Path(path).resolve()==project_path and ref=='sha256:'+hashlib.sha256(project_raw).hexdigest()
            for path,ref in sources.items())
        if commit.get('consumed') is True and record.get('request_digest') == request and project_match:
            commits.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    met = bool(criteria) and all(row['passed'] for row in criteria)
    complete = bool(met and state.get('research_complete') and state.get('stop_verified')
        and state.get('main_workflow',{}).get('status')=='complete' and commits)
    return {'schema':'sdm-research-terminal-view/v1','status':'research-goal-met' if complete else 'completed-unverified',
        'research_complete':complete,'goal_assessment':{'met':met,'criteria':criteria},
        'binding':state['binding'],'stop_verified':state.get('stop_verified',False),
        'result':{'path':str(result_path),'sha256':hashlib.sha256(raw).hexdigest()},'consumed_reviews':commits,
        'execution_state':str(state_path),'read_only':True,'platform_calls':False,'model_calls':False,
        'execution_authority':False,'new_training':False}
