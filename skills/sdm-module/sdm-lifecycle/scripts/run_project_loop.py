"""Dispatch configured generic studies; retain immutable historical compatibility."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from loop_decision import analyze,changed
from run_project_pipeline import run as pipeline,windows
from task_pipeline import digest,digest_bytes,verified_s13
from loop_analysis import analyze as model_analyze,validate_context
from round_reporting import deliver
from project_driver import resolve as resolve_driver


def write_once(path,value):
    path=Path(path); raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
    if path.exists():
        if path.read_bytes()!=raw: raise ValueError('immutable_loop_record_changed:'+path.name)
    else:
        with path.open('xb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())


def accepted_observation(round_root,summary,node,name):
    row=summary.get('nodes',{}).get(node,{})
    if row.get('status') not in {'completed','failed'}: return {'status':row.get('status','missing')}
    observation=row.get('observation',row)
    ref=observation.get('signed_result')
    if not ref:
        return {'status':'failed','uncertain_effect':True,'reason':'execution_result_not_received'}
    status='successful' if observation['status']=='completed' else 'failed'
    if not verified_s13(ref,status): raise ValueError('loop_execution_receipt_not_verified')
    raw=(round_root/'attempt-01'/(name+'-signed.json')).read_bytes()
    if digest_bytes(raw)!=ref['result_digest']: raise ValueError('loop_signed_packet_changed')
    packet=json.loads(raw)
    evidence=packet['evidence'][0]['value']; actual=json.loads(evidence['actual_report'])
    if digest(actual)!=evidence['observation_digest'] or observation.get('actual')!=actual:
        raise ValueError('loop_actual_observation_changed')
    return {'status':observation['status'],'actual':actual,'receipt':ref,
            'uncertain_effect':observation.get('uncertain_effect',False)}


def run(config,output):
    if config.get('loop_adapter'):
        from generic_project_loop import run as generic_run
        return generic_run(config,output)
    # Compatibility for immutable historical RoadModel configs. New projects
    # select the byte-pinned project adapter, never the old AMP/LR experiment.
    if config.get('provider')!='roadmodel-devspace-v1':
        raise ValueError('project_loop_adapter_binding_required')
    if config.get('merge') or config.get('publish'): raise ValueError('loop_cannot_merge_or_publish')
    limit=config.get('max_rounds',3)
    if type(limit) is not int or not 1<=limit<=3: raise ValueError('bounded_loop_rounds_required')
    if not config.get('provider'): raise ValueError('configured_loop_provider_required')
    initial=config['initial_parameters']
    if set(initial)!={'amp','learning_rate'} or type(initial['amp']) is not bool or type(initial['learning_rate']) not in (int,float) or not 1e-7<=initial['learning_rate']<=4e-5:
        raise ValueError('bounded_loop_parameters_required')
    if config.get('max_attempts',1)!=1 or config.get('recovery_probe_node') or config.get('attempt_gpu_uuids'):
        raise ValueError('loop_rounds_must_not_hide_extra_attempts')
    if config.get('decision_mode','model-bridge')=='model-bridge': validate_context(config,initial)
    host=Path(__file__).resolve().parents[4]
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    write_once(output/'loop-config.json',config)
    fd=os.open(output/'loop.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    history=[]; result={'run_id':config['run_id'],'status':'running','rounds':history}; started=time.monotonic()
    original_locators=json.loads(Path(config['locators_linux']).read_bytes())
    cleanup_project=output/'cleanup-project.json'
    cleanup_cfg={**config,'linux_host':str(host),'root':windows(output),'workspace_id':config['run_id']+'-resources','attempt':1}
    write_once(cleanup_project,cleanup_cfg)
    try:
        params=dict(initial); fixed=None; gpu=None; parent=None; root=None
        for index in range(1,limit+1):
            if time.monotonic()-started>1800:
                result.update(status='stopped',decision={'action':'stop-time-budget','reason':'No new round after 30 minutes.'}); break
            root=output/f'round-{index:02d}'; root.mkdir(exist_ok=True)
            loc={**original_locators,'execution_output_root':config['remote_loop_root']+f'/r{index:02d}',
                 'fixed_subset_path':original_locators['execution_output_root']+'/subset.json',
                 'loop_parameters':json.dumps(params,sort_keys=True,separators=(',',':')),
                 'loop_parameters_digest':digest(params)}
            locfile=root/'locators.json'; write_once(locfile,loc)
            delta=changed(history[-1]['parameters'],params) if history else []
            if history and len(delta)!=1: raise ValueError('exactly_one_loop_variable_required')
            plan={'round':index,'parameters':params,'changed_variable':delta[0] if delta else None,
                  'parent_decision_digest':digest(parent) if parent else None,
                  'fixed':fixed,'baseline_checkpoint_digest':original_locators['checkpoint_digest'],
                  'hypothesis':parent['hypothesis'] if parent else 'Observe the bounded baseline with fixed data and evaluation.'}
            write_once(root/'round-plan.json',plan)
            round_config={**config,'run_id':config['run_id']+f'-r{index:02d}','max_attempts':1,'resource_owner':'loop',
                          'loop_parameters':params,'locators':windows(locfile),
                          'fixed_subset_digest':config['fixed_subset_digest']}
            if fixed: round_config['fixed_config_digest']=fixed['fixed_config_digest']
            if gpu: round_config['attempt_gpu_uuids']={'1':gpu}
            outcome=pipeline(round_config,root)
            summary=outcome['attempts'][0]
            if summary.get('status')!='complete': raise ValueError('loop_round_not_terminal')
            training=accepted_observation(root,summary,'15','train')
            evaluation=accepted_observation(root,summary,'16','eval')
            actual=training.get('actual',{})
            if actual.get('loop_parameters') and actual['loop_parameters']!=params:
                raise ValueError('observed_loop_parameters_changed')
            if actual.get('fixed_config_digest'):
                observed_fixed={k:actual[k] for k in ('source_commit','source_url','source_index_digest','subset_digest','fixed_config_digest','evaluator_source_digest','fixed_subset_path')}
                observed_fixed.update(baseline_checkpoint_digest=original_locators['checkpoint_digest'],seed=42,train_frames=8,val_frames=2,epochs=1,batch_size=1)
                if fixed is None: fixed=observed_fixed
                elif fixed!=observed_fixed: raise ValueError('fixed_loop_comparison_changed')
                if evaluation.get('actual'):
                    for key in ('subset_digest','fixed_config_digest','evaluator_source_digest'):
                        if evaluation['actual'].get(key)!=fixed[key]: raise ValueError('fixed_evaluation_changed')
                    if evaluation['actual']['checkpoint_digests']!=actual['checkpoint_digests']:
                        raise ValueError('evaluation_not_current_round_checkpoint')
            if actual.get('selected_gpu_uuid'):
                if gpu and actual['selected_gpu_uuid']!=gpu: raise ValueError('loop_gpu_changed')
                gpu=actual['selected_gpu_uuid']
                if evaluation.get('actual') and evaluation['actual'].get('selected_gpu_uuid')!=gpu:
                    raise ValueError('loop_evaluation_gpu_changed')
            record={'round':index,'parameters':params,'changed_variable':plan['changed_variable'],
                    'training':training,'evaluation':evaluation,'fixed':fixed}
            write_once(root/'round-observation.json',record); history.append(record)
            # Retention/delivery are independent of scientific qualification and
            # never cause a duplicate training attempt after a Git failure.
            if config.get('result_records'):
                archive=host/'.agents/skills/lpai-s12-platform/scripts/archive_round_artifacts.py'
                try:
                    argv=[config['windows_python_wsl'],'-B',windows(archive),
                          '--observation',windows(root/'round-observation.json'),
                          '--destination',windows(root/'artifacts'),
                          '--namespace',original_locators['dataset_namespace'],
                          '--space',original_locators['devspace_name'],
                          '--expected-uid',original_locators['devspace_uid'],
                          '--cluster',config['cluster'],'--ssh-identity',config['ssh_identity']]
                    invocation=subprocess.run(argv,capture_output=True,text=True,timeout=1200, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    write_once(root/'artifact-archive-invocation.json',{'exit_code':invocation.returncode,
                        'stdout':invocation.stdout,'stderr':invocation.stderr})
                except (OSError,subprocess.SubprocessError) as error:
                    write_once(root/'artifact-archive-problem.json',{'problem':type(error).__name__})
                deliver({**config,'result_records':{**config['result_records'],'sync':False}},root)
            if config.get('decision_mode','model-bridge')=='model-bridge':
                decision=model_analyze(history,{**config,'max_rounds':limit},root,write_once)
            elif config['decision_mode']=='legacy-rule':
                decision=analyze(history,limit)
            else: raise ValueError('configured_analysis_mode_required')
            write_once(root/'analysis-and-decision.json',decision)
            if config.get('result_records'): deliver(config,root,decision)
            result.update(status='stopped',decision=decision,fixed=fixed)
            print(json.dumps({'round':index,'training':training['status'],'evaluation':evaluation['status'],
                              'decision':decision['action'],'changed_variable':decision.get('changed_variable')},ensure_ascii=False),flush=True)
            if decision['action']!='run-next-round': break
            params=decision['next_parameters']; parent=decision
    except Exception as error:
        result.update(status='blocked',error_type=type(error).__name__,reason=str(error))
        if config.get('result_records') and root is not None:
            deliver(config,root,error=str(error))
    finally:
        try:
            receipt=output/'loop-cleanup.json'; provider=resolve_driver(config,host)
            if not receipt.exists():
                argv=[config['windows_python_wsl'],'-B',windows(provider),'--project',windows(cleanup_project),
                      '--node','cleanup','--receipt',windows(receipt)]
                invocation=subprocess.run(argv,capture_output=True,text=True,timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                write_once(output/'cleanup-invocation.json',{'exit_code':invocation.returncode,'stdout':invocation.stdout,'stderr':invocation.stderr})
            cleanup=json.loads(receipt.read_bytes())
            write_once(output/'cleanup-digest.json',{'digest':digest_bytes(receipt.read_bytes())})
            result['cleanup']=cleanup
            if cleanup['status']!='completed' or cleanup.get('resource',{}).get('phase')!='stopped': result['status']='cleanup-required'
        except Exception as error:
            result.update(status='cleanup-required',cleanup_error=str(error))
        finally:
            (output/'loop-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
            os.close(fd); (output/'loop.lock').unlink()
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--project',type=Path); p.add_argument('--output',type=Path)
    p.add_argument('--reconcile-session',type=Path);p.add_argument('--reconciliation-request',type=Path)
    p.add_argument('--history-request',type=Path);p.add_argument('--history-round',type=int)
    a=p.parse_args()
    if a.history_request:
        if a.reconcile_session or a.history_round is None:p.error('choose one history read operation')
        from history_context import read_round
        packet=json.loads(a.history_request.read_bytes())
        print(json.dumps(read_round(packet['history_archive'],a.history_round),ensure_ascii=False));return 0
    if not a.project or not a.output:p.error('--project and --output are required for execution or reconciliation')
    if a.reconcile_session:
        if not a.reconciliation_request:p.error('--reconciliation-request required')
        from session_reconciliation import reconcile
        result=reconcile(json.loads(a.project.read_bytes()),a.output,a.reconcile_session,json.loads(a.reconciliation_request.read_bytes()))
        print(json.dumps(result,ensure_ascii=False));return 0 if result['status']=='completed' else 3
    if a.reconciliation_request or a.history_round is not None:p.error('incomplete independent operation')
    result=run(json.loads(a.project.read_bytes()),a.output)
    print(json.dumps({'run_id':result['run_id'],'status':result['status'],'rounds':len(result['rounds']),
                      'decision':result.get('decision',{}).get('action'),'cleanup':result.get('cleanup',{}).get('status')}))
    return 3 if result['status'] in {'blocked','cleanup-required'} else 0
if __name__=='__main__': raise SystemExit(main())
