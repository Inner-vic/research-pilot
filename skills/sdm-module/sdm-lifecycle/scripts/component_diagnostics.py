"""Recover verified existing observations; never execute ML or change scope."""
import hashlib
import json
from pathlib import Path
from review_storage import read_json
from component_research import round_evidence, reference, analyze
from component_event_source import publish_bytes
from component_payload import ref
from research_replan import once


def prepare(rows, root, failure=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if failure is not None:
        actual=read_json(reference(failure))
        if (actual.get('schema')!='sdm-component-diagnosis-ready/v1' or actual.get('valid_training_round') is not False
                or not actual.get('uid') or actual.get('platform_status') not in {'failed','stopped','cancelled','canceled'}):
            raise ValueError('authenticated_terminal_failure_required_for_diagnosis')
        # Reorganize existing evidence only. A request to inspect cannot silently
        # become permission to modify/retrain or create a scored candidate.
        path=root/'failed-task-index.json'
        once(path,{'schema':'sdm-failed-task-evidence-diagnostic/v1','failure':failure,
            'uid':actual['uid'],'terminal_status':actual['platform_status'],'logs':actual.get('logs',[]),
            'valid_training_round':False,'primary_metric':None,'existing_valid_rounds':len(rows),
            'new_measurements':False,'execution_authority':False,
            'limits':'Archived failure evidence only. No fitted model or validation result. A repair needs its own explicit reviewed intervention; missing protected capabilities remain missing.'})
        return [ref(path),failure]
    actual=[round_evidence(row['result']) for row in rows]
    analysis=analyze(rows)
    best=(min if actual[0].get('metric_operator')=='<=' else max)(actual,key=lambda row:row['primary_metric'])
    latest=actual[-1];by_uid={row['uid']:row for row in actual}
    selected=[latest,best]
    for row in list(selected):
        point=next(p for p in analysis['rounds'] if p['uid']==row['uid'])
        base=point.get('experimental_base_uid')
        if base:selected.append(by_uid[base])
    selected=list({row['uid']:row for row in selected}.values())
    refs=[];observations=[]
    for row in selected:
        result=row['result'];prediction=read_json(next(p for p in row['sources'] if p.name=='predictions.json'))
        body={'uid':row['uid'],'result':row['manifest'],'selected_epoch':result['selected_epoch'],
            'history':result.get('history',[]),'prediction':prediction,
            'experimental_base_uid':next(p for p in analysis['rounds'] if p['uid']==row['uid']).get('experimental_base_uid')}
        path=root/(row['uid']+'.json');once(path,body);refs.append(ref(path))
        observations.append({'uid':row['uid'],'primary_metric':row['primary_metric'],'metric_name':row.get('metric_name','validation.macro_f1'),
            **({'macro_f1':row['primary_metric']} if row.get('metric_name','validation.macro_f1')=='validation.macro_f1' else {}),
            'experimental_base_uid':body['experimental_base_uid'],'complete_curve_and_predictions':ref(path)})
    index={'schema':'sdm-existing-evidence-diagnostic/v1','latest_uid':latest['uid'],'incumbent_uid':best['uid'],
        'observations':observations,'comparison':analysis['rounds'][-1].get('paired_comparison'),
        'authority':'Verified archived artifacts only; no retraining, evaluation, model change or new authorization.',
        'limits':'Existing observations cannot identify causal mechanisms uniquely. Any absent measurement needs an explicit supported execution adapter.'}
    path=root/'index.json';once(path,index)
    return [ref(path),*refs]
