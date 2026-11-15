"""Project actual signed execution evidence onto the practical task graph.

Read-only reconciliation, not a new training run and not a numbered-gate advance.
The selected workspaces and requests must be explicitly configured by the project.
"""
import argparse
import json
from pathlib import Path
from canonical import digest
from ledger import replay
from projection import materialize
from workspace import validate_workspace
from task_pipeline import graph


def observed(workspace,request_id):
    workspace=Path(workspace)
    if not validate_workspace(workspace)['valid']: raise ValueError('invalid_actual_workspace')
    state=materialize(replay(workspace))
    row=state['handoffs'][request_id]
    request=json.loads((workspace/'packets/S12'/f'{request_id}.json').read_bytes())
    if row['status']!='successful' or row['operation']!='ml.diagnostic-execution':
        raise ValueError('accepted_execution_required')
    # Locate the already-validated immutable result, not an exported summary.
    result=json.loads((workspace/'packets/S13'/f'{request_id}.json').read_bytes())
    if result.get('request_id')!=request_id or digest(result)!=row['result_digest']:
        raise ValueError('immutable_result_digest_mismatch')
    observation=next(e['value'] for e in result['evidence'] if e['type']=='execution_observation')
    actual=json.loads(observation['actual_report'])
    if digest(actual)!=observation['observation_digest']: raise ValueError('observation_digest_mismatch')
    return {'workspace':str(workspace),'request_id':request_id,'result_digest':row['result_digest'],
            'subjects':request['subjects'],'actual':actual,'prior_findings':observation['prior_findings']}


def reconcile(task):
    nodes=graph(task)
    training=observed(task['training_workspace'],task['training_request'])
    evaluation=observed(task['evaluation_workspace'],task['evaluation_request'])
    if training['subjects']['execution_kind']!='run' or evaluation['subjects']['execution_kind']!='evaluate':
        raise ValueError('execution_role_mismatch')
    for k in ('code_revision','dataset_uid','dataset_version','dataset_scope_digest','resolved_config_digest'):
        if training['subjects'][k]!=evaluation['subjects'][k]: raise ValueError('cross_run_identity_mismatch:'+k)
    if evaluation['subjects']['checkpoint_digest'] not in training['actual']['checkpoint_digests']:
        raise ValueError('evaluation_does_not_use_training_output')
    observed_nodes={}
    for k in ('3','4','6','7','8','13','14','15','16','18'):
        observed_nodes[k]={'status':'observed','meaning':nodes[k]['name'],
                           'source_request':evaluation['request_id'] if k=='16' else training['request_id']}
    for k,n in nodes.items():
        if n['policy']=='not-applicable': observed_nodes[k]={'status':'not-applicable','meaning':n['name']}
    return {'status':'reconciled','new_gpu_run':False,'numbered_gates_advanced':False,
            'observed_nodes':observed_nodes,'unreconciled':[k for k in nodes if k not in observed_nodes],
            'training':training,'evaluation':evaluation,
            'next_work':['fresh execution drivers for the full task graph','automatic bounded next-action decision',
                         'native start/save-stop delivery or explicit UI handoff']}


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--task',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    a=p.parse_args(); result=reconcile(json.loads(a.task.read_bytes()))
    with a.output.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'status':result['status'],'observed_nodes':list(result['observed_nodes']),
                      'unreconciled':result['unreconciled'],'new_gpu_run':False}))
if __name__=='__main__': main()
