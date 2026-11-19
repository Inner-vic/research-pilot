"""Execution selection for the same SDM Manager/Guard effect boundary.

LPAI components are one provider. A project command provider supplies only
probe/submit/lookup/read/collect operations; it never owns scientific decisions.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time
from lpai_manager import ManagerCore as LpaiManager, require, runtime_digest, check_readonly_runtime
from external_operations import sha
from review_storage import read_json
from submission_guard import Guard


def Manager(config,backend=None):
    if config.get('schema')=='sdm-project-manager/v1':return ProjectManager(config,backend)
    from lpai_manager import Manager as OriginalManager
    return OriginalManager(config) if backend is None else OriginalManager(config,backend)


class CommandBackend:
    def __init__(self,config):
        self.config=config
        spec=config.get('execution_adapter',{})
        require(spec.get('version')==1 and spec.get('location') in ('remote','local-engineering'), 'explicit_execution_location_required')
        require(set(spec.get('commands',{}))=={'probe','submit','lookup','read','collect'},'project_execution_operations_required')
        require(isinstance(spec.get('files'),list) and spec['files'],'pinned_project_execution_adapter_required')
        self.spec=spec
        self.verify()

    def verify(self):
        for ref in self.spec['files']:
            p=Path(ref['path'])
            require(p.is_absolute() and p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==ref['sha256'].removeprefix('sha256:'), 'execution_adapter_bytes_changed')
        for argv in self.spec['commands'].values():
            require(isinstance(argv,list) and argv and all(isinstance(a,str) and a for a in argv),'explicit_execution_argv_required')
            require(any(str(Path(r['path'])) in argv for r in self.spec['files']),'command_requires_pinned_driver')

    def call(self,operation,data):
        self.verify()
        result=subprocess.run(self.spec['commands'][operation],input=json.dumps(data,allow_nan=False),
            capture_output=True,encoding='utf-8',timeout=self.config.get('operation_timeout_seconds',60),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        # Do not echo stderr: provider programs can print local account details.
        require(result.returncode==0,'project_execution_operation_failed:'+operation)
        require(len(result.stdout)<=48*1024*1024,'bounded_execution_response_required')
        value=json.loads(result.stdout)
        require(isinstance(value,dict),'typed_execution_response_required')
        return value

    def mutate(self,request,plan_path):
        if self.spec['location']=='local-engineering':
            require(request['data'].get('purpose')=='engineering','local_ml_execution_forbidden')
        return self.call('submit',{'request':request,'plan':str(plan_path)})


class ProjectManager(LpaiManager):
    """Reuse original apply transaction and Guard, adapt provider facts only."""
    def __init__(self,config,backend=None):
        for key in ('provider','namespace','owner','state_root','submission_registry'):
            require(isinstance(config.get(key),str) and config[key].strip(),'execution_scope_required:'+key)
        require(config['provider']!='lpai','lpai_requires_official_component_adapter')
        require(config.get('platform_requirements',{}).get('platform')==config['provider'],'same_project_provider_and_capability_required')
        self.config=config;self.backend=backend or CommandBackend(config)

    def checked(self,kind,uid=None,name=None):
        require(kind=='task' and isinstance(uid,str) and uid,'exact_project_task_read_required')
        value=self.backend.call('read',{'uid':uid})
        require(value.get('uid')==uid and value.get('namespace')==self.config['namespace']
            and value.get('owner')==self.config['owner'],'project_task_read_scope_mismatch')
        return value

    def plan(self,request):
        from required_skills import inspect
        require(inspect(self.config)['status']=='required-skills-available','configured_platform_capability_required')
        request=copy.deepcopy(request)
        require(set(request)<= {'action','request_key','name','uid','cluster','data','task_info','component_settings'},'unknown_project_request_fields')
        require(request.get('action')=='task.submit' and all(isinstance(request.get(k),str) and request[k] for k in ('request_key','name','uid','cluster')),'typed_project_submission_required')
        require(isinstance(request.get('data'),dict),'project_execution_payload_required')
        if self.config['execution_adapter']['location']=='local-engineering':
            require(request['data'].get('purpose')=='engineering','local_ml_execution_forbidden')
        probe=self.backend.call('probe',{'request':request})
        require(probe.get('namespace')==self.config['namespace'] and probe.get('owner')==self.config['owner']
                and probe.get('resource_uid')==request['uid'],'execution_probe_scope_mismatch')
        require(isinstance(probe.get('configuration'),dict),'execution_probe_configuration_required')
        wanted=request.get('component_settings',probe['configuration'])
        require(wanted==probe['configuration'],'execution_configuration_changed')
        request['component_settings']=wanted
        return {'schema':'lpai-manager-plan/v1','created_at':time.time(),'request':request,
            'runtime_digest':runtime_digest(),'manager_digest':sha(self.config),'baseline':probe,
            'baseline_digest':sha(probe),'namespace':self.config['namespace'],'owner':self.config['owner'],
            'route':'project-command','preview':{'platform_writes':0,'execution_requires_bound_authorization':True}}

    def guard(self,plan):
        request=plan['request'];spec={'provider':self.config['provider'],'namespace':self.config['namespace'],
            'owner':self.config['owner'],'request_key':request['request_key'],'component_uid':request['uid'],
            'configuration_digest':sha(plan),'submission_registry':str(Path(self.config['submission_registry']).resolve())}
        return Guard(spec,Path(self.config['state_root']).resolve()/(sha(plan).split(':')[1]+'.json'))

    def finish(self,plan,guard):
        uid=guard.state.get('candidate_uid');require(bool(uid),'candidate_uid_missing_no_resubmit')
        value=self.backend.call('read',{'uid':uid,'request':plan['request']})
        request=plan['request']
        require(value.get('uid')==uid and value.get('namespace')==self.config['namespace']
            and value.get('owner')==self.config['owner'] and value.get('request_key')==request['request_key']
            and value.get('resource_uid')==request['uid'] and value.get('payload_digest')==sha(request['data']),
            'submitted_project_task_identity_or_payload_mismatch')
        require(value.get('cluster')==request['cluster'],'project_task_execution_environment_changed')
        guard.completed(uid)
        return {'status':'verified','resource':{**value,'data_digest':sha(request['data'])},'training_completed':False,'physical_resource_release_verified':False}

    def reconcile(self,plan_path,runtime_repair=None):
        plan=read_json(plan_path);require(plan.get('manager_digest')==sha(self.config),'manager_configuration_changed')
        check_readonly_runtime(plan_path,runtime_repair)
        guard=self.guard(plan)
        with guard.lock():
            guard.load()
            if guard.state['phase']=='completed':return {'status':'already-completed','uid':guard.state['uid'],'new_platform_writes':0}
            require(guard.state['phase'] in ('intent','pending-approval'),'existing_write_intent_required')
            if not guard.state.get('candidate_uid'):
                response=self.backend.call('lookup',{'request':plan['request']})
                candidates=response.get('candidates')
                require(isinstance(candidates,list) and len(candidates)<=1,'ambiguous_project_task_discovery_reconcile_only')
                if candidates:
                    require(isinstance(candidates[0],str) and candidates[0],'task_uid_required')
                    guard.state['candidate_uid']=candidates[0];guard.save()
            if not guard.state.get('candidate_uid'):
                return {'status':'submission-outcome-unresolved','new_platform_writes':0,'resubmit_allowed':False}
            return self.finish(plan,guard)
