"""Audit real result -> reviewed revision -> native implementation -> actual UID.

No model calls, platform queries, training or trial quotas. Reproduction is a
separate qualification and never counts as adaptive research evidence.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from component_research import reference,round_evidence
from component_payload import ref
from review_storage import read_json
from task_pipeline import digest


def reviewed(cfg,project,folder):
    code=('import json,sys;sys.path.insert(0,sys.argv[1]);from proposal_model_runner import drive;'
        'r=drive(json.load(open(sys.argv[2],encoding="utf-8")),sys.argv[3],verify_only=True);'
        'print(json.dumps({"status":r["status"],"receipt":r.get("receipt")}))')
    process=subprocess.run([sys.executable,'-I','-S','-B','-c',code,cfg['runtime_root'],str(project),str(folder)],
        capture_output=True,encoding='utf-8',timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if process.returncode:raise ValueError('continuous_chain_original_review_invalid')
    value=json.loads(process.stdout)
    if value['status']!='proposal-approved':raise ValueError('continuous_chain_original_review_not_approved')
    receipt=read_json(value['receipt'])
    models=[receipt['proposal']['proposer']['model']['id'],receipt['response']['reviewer']['model']['id']]
    if len(set(models))!=2:raise ValueError('continuous_chain_requires_distinct_actual_models')
    return {'receipt':ref(value['receipt']),'models':models,'verify_only':True}


def audit(cfg,state):
    if state.get('config_digest')!=digest(cfg):raise ValueError('continuous_chain_host_identity_changed')
    actual=[round_evidence(row['result']) for row in state['rounds']]
    by_uid={r['uid']:r for r in actual}
    if len(by_uid)!=len(actual):raise ValueError('continuous_chain_duplicate_actual_uid')
    producers={a['manager_plan']['sha256']:a for a in state['attempts'] if a.get('manager_plan')}
    initial_sha=cfg['component_pipeline']['initial_source']['plan']['sha256']
    baseline=next((row for row in actual if read_json(reference(row['manifest']))['original_plan_sha256']==initial_sha),None)
    edges=[];preparations=[];threads=[]
    from component_execution_evidence import fingerprint
    for index,(row,child) in enumerate(zip(state['rounds'],actual)):
        ready=read_json(reference(row['result']));producer=producers.get(ready['original_plan_sha256'])
        if producer is None:continue  # historical or incoming work is not this host's submission
        plan_ref=row.get('accepted_plan') or row.get('preparation_plan');plan=read_json(reference(plan_ref))
        preparation=plan.get('preparation_recovery')=={'version':1}
        if preparation:
            diagnosis=read_json(reference(plan['parent_diagnosis_ref']))
            if (index!=0 or diagnosis.get('valid_training_round') is not False or diagnosis.get('uid')!=plan['parent_task_uid']
                    or diagnosis.get('original_plan_sha256')!=initial_sha):raise ValueError('continuous_chain_original_preparation_link_required')
            selected=None
            if baseline is None:baseline=child
        else:
            if not index or plan['parent_result_sha256']!=state['rounds'][index-1]['result']['sha256']:
                raise ValueError('continuous_chain_parent_result_link_broken')
            selected=plan['selected_candidate']['uid']
            if selected not in {r['uid'] for r in actual[:index]}:raise ValueError('continuous_chain_base_not_preexisting')
        if producer['science_plan']!=plan_ref:raise ValueError('continuous_chain_submitted_plan_changed')
        direction=plan['research_revision']
        original=read_json(reference(producer['manager_plan']))
        if preparation:base=None;changed=False
        else:
            base=by_uid[selected];base_ready=read_json(reference(base['manifest']))
            base_request=read_json(reference(base_ready['submission_identity']['manager_plan']))['request']
            signature=fingerprint(original['request'],True);before=fingerprint(base_request,True)
            changed=signature.get('recipe_sha256')!=before.get('recipe_sha256')
        freeze=read_json(producer['freeze_project'])['research_implementation']
        native=read_json(reference(freeze['native_completion']['receipt']))
        if native.get('status')!='completed' or native.get('detail',{}).get('terminal')!='completed':
            raise ValueError('continuous_chain_completed_native_turn_required')
        threads.append(native['detail']['thread_id'])
        science=reference(plan_ref).parent
        edge={'trigger_uid':plan['parent_task_uid'] if preparation else actual[index-1]['uid'],'base_uid':selected,'successor_uid':child['uid'],
            'parent_result':plan['parent_diagnosis_ref'] if preparation else state['rounds'][index-1]['result'],'approved_plan':plan_ref,
            'science_review':reviewed(cfg,science/'replan-config.json',science),
            'code_review':reviewed(cfg,producer['code_review_project'],producer['code_review_root']),
            'native_receipt':freeze['native_completion']['receipt'],'native_thread_id':native['detail']['thread_id'],
            'manager_plan':producer['manager_plan'],'result':row['result'],
            'intervention':direction['intervention'],'action':direction['action'],'change_level':direction['change_level'],
            'actual_recipe_changed':changed,'metric':child['primary_metric'],
            'delta_from_selected_base':None if preparation else child['primary_metric']-base['primary_metric']}
        if preparation:preparations.append(edge)
        else:edges.append(edge)
    policy=cfg['component_pipeline'].get('decision_policy',{})
    adaptive=[e for e in edges if e['actual_recipe_changed'] and e['change_level']!='none' and policy.get('version')!=3]
    with sqlite3.connect((Path(cfg['root'])/'delivery.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        counts=[list(row) for row in db.execute('select status,count(*) from jobs group by status')]
    metrics=[by_uid[e['successor_uid']]['primary_metric'] for e in edges]
    initial_folder=Path(cfg['root'])/'component-pipeline/initial'
    initial_owned=False
    if baseline and cfg['component_pipeline'].get('initial_submission') and (initial_folder/'initial-dispatch.json').exists():
        dispatch=read_json(initial_folder/'initial-dispatch.json')
        initial_owned=(dispatch['config_sha256']==digest(cfg) and dispatch['plan']==cfg['component_pipeline']['initial_source']['plan']
            and (initial_folder/'initial-submission-observation.json').exists())
    return {'schema':'sdm-core-continuous-acceptance/v1','project_id':cfg['project_id'],
        'config_sha256':digest(cfg),'runtime_root':cfg['runtime_root'],'pipeline_status':state['status'],
        'baseline_uid':baseline['uid'] if baseline else None,
        'single_round_from_confirmed_plan':initial_owned,
        'continuous_loop_demonstrated':bool(adaptive and len(set(threads))==1),
        'adaptive_successor_edges':len(adaptive),'preparation_recovery_edges':len(preparations),'preparations':preparations,'edges':edges,'native_thread_ids':sorted(set(threads)),
        'host_best_gain':max(metrics+[baseline['primary_metric']])-baseline['primary_metric'] if baseline else None,
        'jobs':counts,'qualified_reproduction_only':policy.get('version')==3,
        'iteration_budget_created':False,'new_model_calls':0,'platform_writes':0,
        'scope':'One frozen host. Evidence for an actual successor is not a trial count or quality termination rule.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();cfg=read_json(args.config)
    print(json.dumps(audit(cfg,read_json(Path(cfg['root'])/'component-pipeline/pipeline.json')),ensure_ascii=False))
