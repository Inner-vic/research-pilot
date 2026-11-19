"""Provider-specific collection behind the original durable SDM event source."""
import hashlib,time
from pathlib import Path
from component_event_source import ComponentSource,file_ref
from component_results import marker_lines
from review_storage import atomic_json,read_json

def settings(spec,cfg,registered):
    if (spec.get('version')!=4 or spec.get('transport')!='project-command'
            or cfg.get('schema')!='sdm-event-service/v3' or not registered or registered.get('kind')!='component-source'):
        raise ValueError('registered_project_event_source_required')
    for key in ('manager_config','plan'):file_ref(spec[key])
    manager=read_json(spec['manager_config']['path']);plan=read_json(spec['plan']['path'])
    if manager.get('schema')!='sdm-project-manager/v1' or plan.get('route')!='project-command' or plan['request'].get('action')!='task.submit':
        raise ValueError('bound_project_manager_source_required')
    from task_event_collector import validate
    validate({'task_event_collector':{'version':2,'store':str(Path(cfg['root'])/'observations.sqlite'),
        'tasks':[],'phase_intervals_seconds':spec.get('phase_intervals_seconds')}})
    return manager,plan

def Source(inbox,spec,**kwargs):
    return ProjectSource(inbox,spec,**kwargs) if spec.get('transport')=='project-command' else ComponentSource(inbox,spec,**kwargs)

class ProjectSource(ComponentSource):
    """Only collection differs; original polling, backoff, outbox and Inbox remain."""
    def produce(self,manager,receipt,task):
        from task_event_collector import TERMINAL
        if task['status'] not in TERMINAL:return None
        uid=task['uid'];namespace=task['namespace']
        collection=manager.backend.call('collect',{'uid':uid,'request':self.plan['request']})
        if collection.get('uid')!=uid:raise ValueError('project_collection_uid_changed')
        if task['status']=='succeeded':
            from project_results import verify
            expected={**self.spec['expected'],**{k:task[k] for k in ('uid','namespace','owner','cluster')},
                'component_uid':self.plan['request']['uid'],'submission_identity':{'version':1,'manager_plan':self.spec['plan']}}
            facts,artifacts=verify(task,receipt,collection['lines'],expected)
            from component_event_source import publish_bytes
            for name,raw in artifacts.items():publish_bytes(self.root/name,raw)
            ready={'schema':'sdm-project-result-ready/v1',**facts,'artifact_root':str(self.root),
                'original_plan_sha256':self.spec['plan']['sha256']};phase='exited'
        else:
            logs=collection.get('logs',[])
            if not isinstance(logs,list) or any(not isinstance(row,str) for row in logs):raise ValueError('typed_project_failure_logs_required')
            ready={'schema':'sdm-component-diagnosis-ready/v1','uid':uid,'namespace':namespace,
                'platform_status':task['status'],'valid_training_round':False,
                'logs':[{'text':line} for line in logs],'original_plan_sha256':self.spec['plan']['sha256']};phase='worker_failed'
        path=self.root/'result-ready.json';atomic_json(path,ready)
        state={'binding':self.binding,'phase':phase,'task_uid':uid,'namespace':namespace,
            'platform_status':task['status'],'valid_training_round':ready['valid_training_round'],
            'result_ready':{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}}
        from research_event_service import digest
        return {'schema':'sdm-completion-event/v1','binding':self.binding,'phase':phase,'state':state,'state_sha256':digest(state)}
