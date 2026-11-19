"""Project-independent artifact checks and deterministic metric recounts.

Recounting saved predictions is not model inference or training. Unsupported
metrics require an explicit evaluator adapter; no missing metric becomes zero.
"""
import hashlib,json,math
from pathlib import Path
from component_research import reference
from research_project import snapshot

FILES={'result.json','predictions.json','split.json','checkpoint.bin','preflight.json'}
def require(value,message):
    if not value:raise ValueError(message)
def number(value):return type(value) in (int,float) and math.isfinite(value)

def recount(predictions,evaluation,labels):
    y=predictions.get('y_true');p=predictions.get('y_pred')
    require(isinstance(y,list) and y and isinstance(p,list) and len(y)==len(p),'complete_project_predictions_required')
    if evaluation['kind']=='regression':
        require(all(number(v) for v in y+p),'finite_regression_predictions_required')
        return {'mae':sum(abs(a-b) for a,b in zip(y,p))/len(y),'mse':sum((a-b)**2 for a,b in zip(y,p))/len(y)}
    require(isinstance(labels,list) and len(labels)>=2 and len(set(labels))==len(labels)
        and set(y)<=set(labels) and set(p)<=set(labels),'project_ontology_prediction_mismatch')
    cm=[[sum(a==i and b==j for a,b in zip(y,p)) for j in labels] for i in labels]
    f1=[];iou=[]
    for i in range(len(labels)):
        tp=cm[i][i];fp=sum(row[i] for row in cm)-tp;fn=sum(cm[i])-tp
        f1.append(2*tp/max(1,2*tp+fp+fn));iou.append(tp/max(1,tp+fp+fn))
    return {'accuracy':sum(a==b for a,b in zip(y,p))/len(y),'macro_f1':sum(f1)/len(labels),
        'mean_iou':sum(iou)/len(labels),'confusion_matrix':cm}

def verify_saved(artifacts,expected):
    require(set(artifacts)==FILES,'complete_project_artifacts_required')
    project,_,_=snapshot(expected['project_contract']);evaluation=project['evaluation']
    result=json.loads(artifacts['result.json']);predictions=json.loads(artifacts['predictions.json']);split=json.loads(artifacts['split.json'])
    require(result.get('schema')=='sdm-project-result/v1' and result.get('evaluation')==evaluation,'project_result_evaluator_changed')
    for field in ('protocol','source_sha256','split_sha256','selection','code_sha256','config'):
        require(result.get(field)==expected[field],'project_result_identity_changed:'+field)
    require(hashlib.sha256(artifacts['split.json']).hexdigest()==expected['split_sha256'],'project_partition_bytes_changed')
    train=split.get('train_ids',[]);validation=split.get('validation_ids',[])
    require(train and validation and len(set(train))==len(train) and len(set(validation))==len(validation)
        and not set(train)&set(validation) and predictions.get('row_ids')==validation,'project_sample_membership_changed')
    require(result.get('test_used') is False and split.get('test_used') is False,'project_withheld_test_policy_required')
    metrics=recount(predictions,evaluation,project['data']['labels']);metric=evaluation['primary_metric']
    for name,value in metrics.items():
        reported=result.get('validation',{}).get(name)
        require((reported==value if isinstance(value,list) else number(reported) and abs(reported-value)<=1e-10),'project_metric_recount_mismatch:'+name)
    history=result.get('history',[])
    require([r.get('epoch') for r in history]==list(range(1,expected['config']['epochs']+1)),'complete_project_training_history_required')
    require(all(number(r.get('validation',{}).get(metric)) for r in history),'finite_project_metric_history_required')
    if evaluation['selection']=='last':selected=history[-1]
    elif evaluation['selection']=='best-primary-earliest':
        selected=(max if evaluation['operator']=='>=' else min)(history,key=lambda row:row['validation'][metric])
    else:raise ValueError('project_selection_adapter_not_implemented')
    require(result.get('selected_epoch')==selected['epoch'] and result['validation'][metric]==selected['validation'][metric], 'project_checkpoint_selection_mismatch')
    require(hashlib.sha256(artifacts['checkpoint.bin']).hexdigest()==result.get('checkpoint_sha256'),'project_checkpoint_bytes_changed')
    preflight=json.loads(artifacts['preflight.json'])
    require(preflight==result.get('engineering_preflight'),'actual_project_preflight_required')
    from project_preflight import verify_report
    verify_report(preflight,project['preflight_checks'])
    return {'valid_training_round':True,'primary_metric':metrics[metric],
        'metric_name':'validation.'+metric,'metric_operator':evaluation['operator'],
        'artifact_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in artifacts.items()},
        'epochs':expected['config']['epochs'],'selected_epoch':result['selected_epoch'],'project_contract':expected['project_contract']}

def verify(task,receipt,lines,expected):
    from component_results import unpack
    require(receipt.get('status')=='verified' and task.get('status')=='succeeded','verified_successful_project_task_required')
    for key in ('uid','namespace','owner','cluster'):
        require(task.get(key)==expected.get(key)==receipt.get('resource',{}).get(key),'project_task_identity_mismatch')
    require(task.get('project_uid',task.get('resource_uid'))==expected['component_uid'],'project_resource_identity_mismatch')
    from component_results import verify_submission_identity
    verify_submission_identity(task,receipt['resource'],expected.get('submission_identity'))
    artifacts,events=unpack(lines,FILES)
    for field in ('source_sha256','code_sha256','config','split_sha256'):
        require(events['preflight'].get(field)==expected[field],'project_remote_preflight_identity_changed')
    require(events['completed'].get('result_sha256')==hashlib.sha256(artifacts['result.json']).hexdigest(),'project_completed_artifact_changed')
    facts=verify_saved(artifacts,expected)
    return {**facts,'uid':task['uid'],'submission_identity':{'version':1,'manager_plan':expected['submission_identity']['manager_plan']}},artifacts

def round_evidence(ref):
    path=reference(ref);ready=json.loads(path.read_bytes());root=Path(ready['artifact_root'])
    require(ready.get('schema')=='sdm-project-result-ready/v1' and ready.get('valid_training_round') is True,'verified_project_result_required')
    require(root.is_absolute() and set(ready.get('artifact_sha256',{}))==FILES,'archived_project_artifacts_required')
    artifacts={n:(root/n).read_bytes() for n in FILES}
    require({n:hashlib.sha256(b).hexdigest() for n,b in artifacts.items()}==ready['artifact_sha256'],'archived_project_artifact_changed')
    result=json.loads(artifacts['result.json'])
    facts=verify_saved(artifacts,{**{k:result[k] for k in ('protocol','source_sha256','split_sha256','selection','code_sha256','config')},'project_contract':ready['project_contract']})
    require(ready['primary_metric']==facts['primary_metric'],'archived_project_primary_metric_changed')
    return {'uid':ready['uid'],'primary_metric':facts['primary_metric'],'metric_name':facts['metric_name'],
        'metric_operator':facts['metric_operator'],'fair_comparison':{k:result[k] for k in ('protocol','source_sha256','split_sha256','selection')},
        'manifest':ref,'result':result,'sources':[path,*[root/n for n in sorted(FILES) if n!='checkpoint.bin']]}

def analyze(rows):
    actual=[round_evidence(row['result']) for row in rows]
    require(actual and all(a['fair_comparison']==actual[0]['fair_comparison'] and a['metric_name']==actual[0]['metric_name'] and a['metric_operator']==actual[0]['metric_operator'] for a in actual),'comparable_project_results_required')
    best=(max if actual[0]['metric_operator']=='>=' else min)(actual,key=lambda a:a['primary_metric'])
    return {'schema':'sdm-verified-result-analysis/v1','primary_metric':actual[0]['metric_name'],
        'operator':actual[0]['metric_operator'],'comparison_identity':actual[0]['fair_comparison'],
        'rounds':[{'uid':a['uid'],'primary_metric':a['primary_metric'],'config':a['result']['config'],
                   'validation':a['result']['validation'],'history':a['result']['history']} for a in actual],
        'incumbent_uid':best['uid'],'interpretation_required':'Compare the actual project metrics, curves, errors and cost; mechanism and scientific support require independent review.',
        'execution_authority':False}
