"""Trusted local successor registration for event-service v3, no HTTP API."""
import copy
from pathlib import Path
from component_research import reference

MODES={'research-replan','prepare-implementation','freeze-implementation','proposal-review'}


def validate(cfg,binding,value):
    if cfg.get('schema')!='sdm-event-service/v3' or not binding or len(binding)>240:
        raise ValueError('explicit_v3_registered_task_identity_required')
    if not isinstance(value,dict) or set(value)!={'template','origin','data'}:
        raise ValueError('typed_local_successor_registration_required')
    template=cfg.get('task_templates',{}).get(value['template'])
    if not isinstance(template,dict) or template.get('kind') not in {'coordinator','implementation','component-source','report'}:
        raise ValueError('frozen_successor_template_required')
    origin=reference(value['origin'])
    if origin.resolve().is_relative_to(Path(cfg['cwd']).resolve()):
        raise ValueError('model_output_cannot_register_its_own_execution')
    task=copy.deepcopy(template);data=value['data'];kind=task['kind']
    if kind=='coordinator':
        if (not isinstance(data,dict) or set(data)!={'mode','project','output'}
                or data['mode'] not in MODES or data['mode'] not in template.get('modes',[])):
            raise ValueError('allowlisted_original_coordinator_mode_required')
        project=reference(data['project']);output=Path(data['output'])
        if (project.resolve().is_relative_to(Path(cfg['cwd']).resolve()) or not output.is_absolute()
                or not output.resolve().is_relative_to(Path(cfg['root']).resolve())
                or output.resolve().is_relative_to(Path(cfg['runtime_root']).resolve())):
            raise ValueError('registered_coordinator_control_paths_required')
        task['coordinator']={'project':str(project),'project_sha256':data['project']['sha256'],
            'entry':str(Path(cfg['runtime_root'])/'run_project_pipeline.py'),'output':str(output),
            'runtime_manifest':cfg['runtime_manifest'],'allow_devspace_live':False,'mode':data['mode']}
    elif kind=='implementation':
        if not isinstance(data,dict) or set(data)!={'job'}:raise ValueError('bound_implementation_job_required')
        job=reference(data['job'])
        if job.resolve().is_relative_to(Path(cfg['cwd']).resolve()):raise ValueError('trusted_implementation_job_required')
        task['evidence']=[data['job']]
    else:
        if not isinstance(data,dict) or set(data)!={'evidence'}:raise ValueError('bound_successor_source_required')
        reference(data['evidence']);task['evidence']=[data['evidence']]
    if not task.get('instructions') or not task.get('evidence_kind'):
        raise ValueError('registered_template_instructions_required')
    return task
