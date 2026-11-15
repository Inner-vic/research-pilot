"""Recover verified existing observations; never execute ML or change scope."""
import hashlib
import json
from pathlib import Path
from review_storage import read_json
from component_research import round_evidence, reference, analyze
from component_event_source import publish_bytes
from component_payload import ref
from research_replan import once


def prepare(rows, root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    actual=[round_evidence(row['result']) for row in rows]
    analysis=analyze(rows)
    best=max(actual,key=lambda row:row['primary_metric'])
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
        observations.append({'uid':row['uid'],'macro_f1':row['primary_metric'],
            'experimental_base_uid':body['experimental_base_uid'],'complete_curve_and_predictions':ref(path)})
    index={'schema':'sdm-existing-evidence-diagnostic/v1','latest_uid':latest['uid'],'incumbent_uid':best['uid'],
        'observations':observations,'comparison':analysis['rounds'][-1].get('paired_comparison'),
        'authority':'Verified archived artifacts only; no retraining, evaluation, model change or new authorization.',
        'limits':'Existing observations cannot identify causal mechanisms uniquely. Any absent measurement needs an explicit supported execution adapter.'}
    path=root/'index.json';once(path,index)
    return [ref(path),*refs]
