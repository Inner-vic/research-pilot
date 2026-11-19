"""Project-owned training/evaluation facts; no account defaults or execution.

This module discovers and freezes inputs for the existing SDM lifecycle. It
does not schedule work, approve plans, or implement a second acceptance rule.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from review_storage import read_json, atomic_json

SCHEMA='researchpilot-project/v1'
FILE='researchpilot.project.json'


def relative(value):
    if (not isinstance(value,str) or not value or '\\' in value or ':' in value
            or value.startswith('/') or any(p in ('','..','.') for p in value.split('/'))):
        raise ValueError('portable_project_relative_path_required')
    return value


def source(root,name):
    path=Path(root)/relative(name)
    if (not path.is_file() or not path.resolve().is_relative_to(Path(root).resolve())
            or any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in (path,*path.parents))):
        raise ValueError('owned_project_source_required:'+name)
    if path.stat().st_size>2*1024*1024:raise ValueError('bounded_project_source_required')
    return path


def settings(value):
    required={'schema','project_id','source_files','editable_files','parameters_file','entrypoints',
              'data','tensor','evaluation','training_limits','preflight_checks','contract_contents','not_applicable_contracts'}
    if not isinstance(value,dict) or set(value)!=required or value['schema']!=SCHEMA:
        raise ValueError('typed_project_integration_required')
    if not isinstance(value['project_id'],str) or not value['project_id'].strip():
        raise ValueError('project_identity_required')
    files=value['source_files'];editable=value['editable_files']
    if (not isinstance(files,list) or not 1<=len(files)<=32 or len(set(files))!=len(files)
            or not isinstance(editable,list) or not editable or len(set(editable))!=len(editable)
            or not set(editable)<=set(files) or value['parameters_file'] not in editable):
        raise ValueError('explicit_project_source_and_edit_scope_required')
    for name in files:
        relative(name)
        if name.startswith('_researchpilot_'):raise ValueError('reserved_project_runtime_name')
    for kind in ('preflight','train','evaluate'):
        argv=value['entrypoints'].get(kind)
        if (not isinstance(argv,list) or not argv or not all(isinstance(a,str) and a for a in argv)
                or not any(arg in files for arg in argv)):
            raise ValueError('project_declared_entrypoint_required:'+kind)
        if any(arg in editable for arg in argv) and kind!='train':raise ValueError('protected_project_check_and_evaluation_required')
    if set(value['entrypoints'])!={'preflight','train','evaluate'}:
        raise ValueError('three_project_entrypoints_required')
    checks=value['preflight_checks']
    if (not isinstance(checks,list) or not checks or any(not isinstance(r,dict) or set(r)!={'name','applicable','rationale'} or not isinstance(r['name'],str) or not r['name'] or type(r['applicable']) is not bool or not isinstance(r['rationale'],str) or len(r['rationale'].strip())<10 for r in checks) or len({r['name'] for r in checks})!=len(checks)):
        raise ValueError('project_functional_check_applicability_required')
    data=value['data']
    if (not isinstance(data,dict) or set(data)!={'identity','partition_identity','description','labels','quality_policy'}
            or not all(isinstance(data[k],str) and data[k].strip() for k in ('identity','partition_identity','description'))
            or not isinstance(data['labels'],list) or not isinstance(data['quality_policy'],dict)):
        raise ValueError('project_data_and_partition_facts_required')
    tensor=value['tensor']
    if not isinstance(tensor,dict) or set(tensor)!={'inputs','outputs','shape','dtype','constraints'}:
        raise ValueError('project_tensor_contract_required')
    evaluation=value['evaluation']
    if (not isinstance(evaluation,dict) or set(evaluation)!={'kind','primary_metric','operator','target','selection','report','predictions'}
            or evaluation['kind'] not in ('classification','regression')
            or evaluation['operator'] not in ('>=','<=')
            or not isinstance(evaluation['primary_metric'],str)
            or type(evaluation['target']) not in (int,float) or not math.isfinite(evaluation['target'])
            or not isinstance(evaluation['selection'],str) or not evaluation['selection']):
        raise ValueError('explicit_project_evaluation_required')
    supported={'classification':{'accuracy','macro_f1','mean_iou'},'regression':{'mae','mse'}}
    if evaluation['primary_metric'] not in supported[evaluation['kind']]:
        raise ValueError('evaluation_recount_adapter_not_implemented')
    if evaluation['selection'] not in ('last','best-primary-earliest'):
        raise ValueError('project_selection_adapter_not_implemented')
    if evaluation['operator']!=('<=' if evaluation['kind']=='regression' else '>='):
        raise ValueError('metric_direction_must_match_declared_evaluator')
    for key in ('report','predictions'):relative(evaluation[key])
    contents=value['contract_contents'];omitted=value['not_applicable_contracts']
    if (not isinstance(contents,dict) or not {'G03','G07','G08','G09','G10'}<=set(contents)
            or not set(contents)<={'G%02d'%n for n in range(3,13)}
            or not isinstance(omitted,dict) or any(not isinstance(reason,str) or len(reason.strip())<20 for reason in omitted.values())):
        raise ValueError('project_original_contract_contents_and_applicability_required')
    if evaluation['kind']=='classification' and not {'G07','G08'}<=set(contents):
        raise ValueError('classification_ontology_and_transform_contracts_required')
    limits=value['training_limits']
    if (not isinstance(limits,dict) or set(limits)!={'min_epochs','max_epochs','default_epochs'}
            or any(type(v) is not int for v in limits.values())
            or not 1<=limits['min_epochs']<=limits['default_epochs']<=limits['max_epochs']):
        raise ValueError('project_training_limits_required')
    return value


def inspect(project):
    root=Path(project).resolve();path=root/FILE
    if not path.is_file():
        return {'schema':'researchpilot-project-discovery/v1','status':'needs-project-facts',
            'configuration':None,'missing':['training and preflight entrypoints','data and fixed partition identity',
                'evaluation entrypoint and primary metric','editable and protected source files'],
            'next':'Inspect current source/config/results and prepare project facts; ask only facts that cannot be discovered.',
            'project_registration_required':False,'model_calls':0,'platform_writes':0}
    value=settings(read_json(path));missing=[];identities={}
    for name in value['source_files']:
        try:identities[name]=hashlib.sha256(source(root,name).read_bytes()).hexdigest()
        except (OSError,ValueError):missing.append(name)
    return {'schema':'researchpilot-project-discovery/v1','status':'ready' if not missing else 'missing-source-files',
        'configuration':str(path),'project_id':value['project_id'],'source_files':identities,'missing':missing,
        'evaluation':value['evaluation'],'execution_configuration':'current project .bmad-ml/CAPABILITIES.json; external to portable project facts',
        'model_calls':0,'platform_writes':0}


def freeze(project,destination):
    root=Path(project).resolve();value=settings(read_json(root/FILE))
    raw={name:source(root,name).read_bytes() for name in value['source_files']}
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError('fresh_project_snapshot_required')
    destination.mkdir(parents=True)
    for name,content in raw.items():
        p=destination/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
    value={**value,'source_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in raw.items()}}
    atomic_json(destination/FILE,value)
    return {'path':str(destination/FILE),'sha256':hashlib.sha256((destination/FILE).read_bytes()).hexdigest()}


def snapshot(ref):
    from component_research import reference
    path=reference(ref);value=read_json(path);hashes=value.pop('source_sha256',None)
    settings(value)
    if not isinstance(hashes,dict) or set(hashes)!=set(value['source_files']):
        raise ValueError('complete_project_snapshot_required')
    for name,wanted in hashes.items():
        if hashlib.sha256(source(path.parent,name).read_bytes()).hexdigest()!=wanted:
            raise ValueError('project_snapshot_source_changed:'+name)
    return value,hashes,path.parent


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',required=True)
    args=p.parse_args();print(json.dumps(inspect(args.project_root),ensure_ascii=False))
