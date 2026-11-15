"""Official LPAI SDK operations plus official CLI Pod readback.

Invoked only by a configured external-operation driver. Imports/auth are lazy;
probe performs no platform request. No HTTP, credential export or monitoring.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import time

from external_operations import envelope


def runtime_path(config,path):
    """Explicit longest-prefix mapping for a configured cross-host interpreter."""
    text=str(path)
    matches=[row for row in config.get('runtime_path_map',[]) if text==row['local'] or text.startswith(row['local'].rstrip('/')+'/')]
    if not matches:return text
    row=max(matches,key=lambda r:len(r['local']))
    return row['runtime'].rstrip('/\\')+text[len(row['local']):].replace('\\','/')


def submission_metadata(config):
    for key in ('namespace','owner','cluster','component_uid','task_name'):
        if not isinstance(config.get(key),str) or not config[key].strip():raise ValueError('lpai_submission_scope_required:'+key)
    if type(config.get('total_gpus')) is not int or config['total_gpus']<1:raise ValueError('lpai_gpu_count_required')
    info=config.get('task_info',{})
    for field in ('problem_statement','solution_approach','expected_result','expected_duration_desc'):
        if not isinstance(info.get(field),str) or not info[field].strip():raise ValueError('lpai_task_info_required:'+field)
    if type(info.get('expected_duration')) is not int or info['expected_duration']<=0:raise ValueError('lpai_duration_metadata_required')
    return info


def operation_binding(config_path, sdk_python, action, request_key, authorization_ref, old_uid=None):
    """Build the node binding without importing the SDK or touching the platform."""
    path=Path(config_path).resolve();config=json.loads(path.read_bytes())
    if action in {'submit','switch'} and not config.get('submission_registry'):
        raise ValueError('project_shared_submission_registry_required')
    adapter=Path(__file__).resolve()
    files=[adapter,adapter.with_name('external_operations.py'),adapter.with_name('submission_guard.py'),
           adapter.with_name('review_storage.py'),path,Path(config['configuration_ref']['path']).resolve()]
    commands={name:[str(sdk_python),'-B',runtime_path(config,adapter),'--config',runtime_path(config,path),'--operation',name,
                    '--uid','{uid}','--request-key','{request_key}'] for name in
              ('probe','prepare','stop','read-old','read-new','submit','lookup','read')}
    return {'schema':'sdm-external-operation/v1','provider':'lpai','namespace':config['namespace'],
            'owner':config['owner'],'request_key':request_key,'action':action,'old_uid':old_uid,
            'component_uid':config['component_uid'],'configuration_digest':config['configuration_ref']['digest'],
            'submission_registry':str(Path(config.get('submission_registry',path.parent/'.sdm-submission-requests')).resolve()),
            'authorization_ref':authorization_ref,'routes':[{'id':'lpai-official-sdk','kind':'official-sdk',
                'adapter_refs':[{'path':str(p),'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest()} for p in files],
                'commands':commands}]}


class LpaiAdapter:
    def __init__(self, config, project, task, pods):
        self.config, self.project, self.task, self.pods = config, project, task, pods

    def component(self, verify_gate=True):
        p=self.project.get(self.config['component_uid'])
        if not isinstance(p,dict) or any(p.get(k)!=self.config[k] for k in ('namespace','owner','cluster')):
            raise ValueError('lpai_component_scope_mismatch')
        if p.get('uid')!=self.config['component_uid']:
            raise ValueError('lpai_component_uid_mismatch')
        if verify_gate and self.config.get('normal_gate_command'):
            # Submission must re-read the command; a checked boolean alone is
            # not evidence that this component cannot autostart model training.
            wanted=self.config['normal_gate_command']
            task=p.get('data',{}).get('lpai_task',{})
            if task.get('cmd')!=wanted or not task.get('roles') or any(r.get('cmd')!=wanted for r in task['roles']):
                raise ValueError('lpai_normal_gate_command_readback_mismatch')
        return p

    def prepare(self):
        p=self.component(verify_gate=False)
        ref=self.config.get('native_configuration_ref',self.config['configuration_ref']);path=Path(ref['path'])
        if 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()!=ref['digest']:
            raise ValueError('lpai_configuration_changed')
        cfg=json.loads(path.read_bytes())
        if p.get('last_task_uid') or p.get('run_times'):
            raise ValueError('fresh_lpai_component_required')
        if cfg['name']!=p['name'] or cfg['queue_code']!=p['queue_code']:
            raise ValueError('lpai_configuration_scope_mismatch')
        # The supported SDK update may print HTTP errors and return without
        # raising. Ignore its process status; verify actual provider state.
        self.project.update(p['uid'],str(path))
        p=self.component();roles=p['data']['lpai_task']['roles']
        requested=cfg['task_role_specs']
        if (p['data']['lpai_task']['cmd']!=cfg['cmd'] or len(roles)!=len(requested)
            or any(role['cmd']!=cfg['cmd'] for role in roles)
            or any(role.get('image_info',{}).get('image')!=cfg['image'] for role in roles)
            or bool(p.get('elastic_train'))!=bool(cfg.get('elastic_train_info'))
            or p.get('gems_project')!=cfg.get('gems_project')
            or p.get('max_retry',0)!=int(cfg.get('retry',0))
            or sum(v['replica']*v['resource']['Gpu'] for v in roles)!=self.config['total_gpus']
            or [(v['role'],v['replica'],v['resource']['Id'],v['resource']['replica']) for v in roles]
               !=[(v['rolename'],int(v['replica']),v['resource']['id'],v['resource'].get('replica',1)) for v in requested]):
            raise ValueError('lpai_configuration_readback_mismatch')
        envs={v['key']:v['value'] for v in p['data'].get('lpai_envs',[])}
        if envs!={k:str(v) for k,v in cfg.get('envs',{}).items()}:
            raise ValueError('lpai_environment_readback_mismatch')
        actual_code=p['data'].get('lpai_git') or [];wanted_code=cfg.get('code') or []
        if len(actual_code)!=len(wanted_code) or any(
                any(a.get(k,'')!=w.get(k,'') for k in ('name','commit_id','branch')) for a,w in zip(actual_code,wanted_code)):
            raise ValueError('lpai_source_readback_mismatch')
        for native,source in (('lpai_input_dataset','input_dataset'),('lpai_input_pvc','input_data_volume'),
                              ('lpai_model_download','input_model')):
            # Compare every declared mount field; provider may add defaults.
            actual=p['data'].get(native) or [];wanted=cfg.get(source) or []
            if len(actual)!=len(wanted) or any(any(a.get(k)!=v for k,v in w.items()) for a,w in zip(actual,wanted)):
                raise ValueError('lpai_mount_readback_mismatch:'+native)
        return {'configuration_verified':True,'component_uid':p['uid'],'configuration_digest':ref['digest']}

    def read(self,uid):
        value=self.task.get(uid)
        if not isinstance(value,dict) or value.get('uid')!=uid or any(
                value.get(k)!=self.config[k] for k in ('namespace','owner')):
            raise ValueError('lpai_task_scope_mismatch')
        pods=self.pods(uid)
        if not isinstance(pods,list) or any(v.get('task_uid')!=uid or v.get('namespace')!=self.config['namespace'] for v in pods):
            raise ValueError('lpai_pod_scope_mismatch')
        terminal={'stopped','failed','succeeded','completed','cancelled'}
        marker=value.get('describe','')
        key=marker.removeprefix('sdm-request:') if marker.startswith('sdm-request:') else None
        return {'provider':'lpai','namespace':value['namespace'],'owner':value['owner'],'uid':uid,
                'component_uid':value.get('project_uid'),'request_key':key,'status':value['status'],
                'observed_at':time.time(),'released':value['status'] in terminal and all(
                    p['status'] in {'Succeeded','Failed'} for p in pods),
                'pods':[{'status':p['status'],'uid':p['uid']} for p in pods]}

    def stop(self,uid):
        self.read(uid)  # Reject foreign identity before mutation.
        result=self.task.update(uid,status='stop')
        if result not in (1,True) and not (isinstance(result,dict) and result.get('status')=='ok'):
            raise ValueError('lpai_stop_not_acknowledged')
        return {'requested':True,'uid':uid}  # Still not release proof.

    def lookup(self,key):
        result=self.task.list(name_like=self.config['task_name'],owner=self.config['owner'],
                              page_size=100,page_num=1)
        if not isinstance(result,dict) or not isinstance(result.get('items'),list) or type(result.get('total')) is not int:
            raise ValueError('lpai_complete_lookup_required')
        if result['total']!=len(result['items']):
            raise ValueError('lpai_lookup_requires_pagination_adapter')
        return {'complete':True,'matches':[self.read(v['uid']) for v in result['items']
            if v.get('describe')=='sdm-request:'+key and v.get('project_uid')==self.config['component_uid']]}

    def submit(self,key):
        self.component(verify_gate=not self.config.get('prepare_before_submit'))
        info=submission_metadata(self.config)
        if self.config.get('prepare_before_submit'):self.prepare()
        result=self.task.submit_project_task(cluster=self.config['cluster'],project_uid=self.config['component_uid'],
            name=self.config['task_name'],describe='sdm-request:'+key,**info)
        if isinstance(result,dict) and result.get('status') in {'pending_approval','approval-pending'}:
            from submission_guard import PendingApproval
            raise PendingApproval(result)
        if not isinstance(result,str) or not result:
            raise ValueError('lpai_submission_uid_required')
        return {'uid':result}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--operation',required=True,choices=('probe','prepare','read','read-old','read-new','stop','submit','lookup'))
    parser.add_argument('--uid');parser.add_argument('--request-key')
    args=parser.parse_args()
    try:
        config=json.loads(Path(args.config).read_bytes())
        if args.operation=='probe':
            import importlib.metadata
            packages=importlib.metadata.packages_distributions().get('lpai',[])
            version=','.join(name+':'+importlib.metadata.version(name) for name in packages) or 'SDK-not-installed'
            value={'available':importlib.util.find_spec('lpai') is not None and bool(shutil.which(config.get('official_cli','lpai-agent'))),
                   'source_ref':'installed official lpai.pipelinev2 API and lpai-agent tasks pods', 'version':version,
                   'operations':['prepare','read','read-old','read-new','stop','submit','lookup']}
        else:
            # Prevent SDK diagnostics from corrupting the structured response or
            # leaking auth material. Failure is reported using a bounded type.
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                from lpai.conf.lipai_conf import LpaiConf
                from lpai.pipelinev2.project import Project
                from lpai.pipelinev2.task import Task
                conf=LpaiConf()
                if conf.get_namespace()!=config['namespace']:raise ValueError('lpai_namespace_mismatch')
                def pods(uid):
                    response=subprocess.run([config.get('official_cli','lpai-agent'),'tasks','pods','--namespace',config['namespace'],'--uid',uid],
                                            capture_output=True,text=True,timeout=30,shell=False, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    return envelope(json.loads(response.stdout),response.returncode)
                adapter=LpaiAdapter(config,Project(conf),Task(conf),pods)
                if args.operation=='prepare':value=adapter.prepare()
                elif args.operation=='stop':value=adapter.stop(args.uid)
                elif args.operation in {'submit','lookup'}:value=getattr(adapter,args.operation)(args.request_key)
                else:value=adapter.read(args.uid)
        print(json.dumps({'status':'ok','data':value}))
        return 0
    except Exception as error:
        from submission_guard import PendingApproval
        if isinstance(error,PendingApproval):
            print(json.dumps(error.approval));return 0
        reason=str(error) if isinstance(error,ValueError) else type(error).__name__
        print(json.dumps({'status':'error','error':reason}))
        return 1


if __name__=='__main__':raise SystemExit(main())
