"""Prepare a portable, explicitly authorized local CPU Study, never live resources."""
import argparse
import getpass
import hashlib
import json
from pathlib import Path
import sys
import uuid

from task_pipeline import digest
from devspace_study import binding, validate


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--authorize-local-cpu', action='store_true')
    p.add_argument('--authorization-ref', required=True)
    p.add_argument('--mode', choices=['normal', 'collapse'], default='normal')
    a = p.parse_args()
    if not a.authorize_local_cpu or not a.authorization_ref.strip(): p.error('explicit local CPU authorization and source reference required')
    root = a.output.absolute()
    if root.exists() or root.resolve() != root: p.error('fresh absolute unlinked trial directory required')
    workload = Path(__file__).resolve().parents[1] / 'assets/cpu-trial/workload.py'
    uid = str(uuid.uuid4()); study_id = 'cpu-' + uuid.uuid4().hex[:12]
    plan = {'run_id':study_id,'transport':'local-cpu','remote_root':str(root/'work'),
        'deadline':None,'max_seconds':None,'hourly_rate':0,'budget':None,
        'limit_policy':'user-authorized-unbounded','limit_authorization_ref':a.authorization_ref,
        'resource':{'uid':uid,'namespace':'local-cpu','route':'local-cpu'},
        'argv':[sys.executable,'-B','workload.py','--mode',a.mode],
        'inputs':[{'name':'workload.py','path':str(workload),'sha256':hashlib.sha256(workload.read_bytes()).hexdigest()}],
        'artifacts':['result.json','checkpoint.json','predictions.json'],
        'health_policy':{'version':1,'stop_authorized':True,'protocol':'cpu-regression-v1','metric':'validation_mse',
            'direction':'minimize','baseline':20,'failure_boundary':100,'minimum_change':10,'minimum_samples':64,
            'bad_evaluations':2,'bad_updates':2,'max_overflow_skips':8,'interval_seconds':.02,'sample_timeout_seconds':10,
            'checkpoint_file':'checkpoint.json','predictions_file':'predictions.json'},
        'raw_materials':{'schema':'sdm-material-preparation/v1','objective':'Evaluate actual synthetic CPU regression and decide one bounded LR change or stop; no GPU/model claim',
            'project_metrics':['evaluation.validation_mse'],'roles':[{'id':'result','root':'$artifacts','json_globs':['result.json'],
                'json_fields':{k:{'field':k,'type':t} for k,t in [('score','float'),('validation_mse','float'),('optimizer_updates','int'),('epochs','int'),('checkpoint_verified','bool')]}}]}}
    config = {'devspace_execution':{'version':1,'owner':getpass.getuser(),'submission_registry':str(root/'registry'),
        'plan':plan,'authorization':{'confirmed':True,'plan_digest':digest(plan),'source_ref':a.authorization_ref}},
        'task_event_collector':{'version':1,'store':str(root/'events.sqlite3'),'tasks':[{'uid':uid,'namespace':'local-cpu'}]},
        'task_event_service':{'version':1,'read_only':True,'evidence_root':str(root/'events'),
            'bindings':[{'uid':uid,'namespace':'local-cpu','cluster':'local-cpu'}]},
        'devspace_study':{'version':1,'study_id':study_id,'objective':'Complete two finite actual CPU rounds with grounded Agent review; compare LR and clean up.',
            'stop_criteria':'Stop after two rounds; health failure stops that workload and requires actual diagnosis, no automatic retry.',
            'max_rounds':2,'initial_parameters':{'learning_rate':.001},'allowed_variables':{'learning_rate':{'type':'number','minimum':.00001,'maximum':.1}},
            'parameter_flags':{'learning_rate':'--learning-rate'},'result_artifact':'result.json',
            'fixed_fields':['epochs','data_digest','execution_device','case_kind'], 'metrics':['validation_mse','optimizer_updates'],
            'required_values':{'checkpoint_verified':True}}}
    config['devspace_study']['authorization']={'confirmed':True,'study_digest':binding(config),'source_ref':a.authorization_ref}
    validate(config)
    root.mkdir(parents=True); (root/'project.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    print(json.dumps({'project':str(root/'project.json'),'output':str(root/'attempt'),
        'evidence_kind':'local-cpu-simulation','live_resources':False,'next':'Use run_project_pipeline.py --devspace-execution --watch-devspace; review is intentionally not prewritten.'}))


if __name__ == '__main__': main()
