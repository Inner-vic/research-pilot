"""Explicit workload ABI; executable support is distinct from future adapters."""
import argparse
import json
from review_storage import read_json


def settings(value):
    if value is None:return None
    if isinstance(value,dict) and value.get('adapter')=='project/v1':
        if set(value)!={'version','adapter','project_contract','source_ref'} or value['version']!=3 or not value['source_ref']:
            raise ValueError('typed_project_workload_required')
        from research_project import snapshot
        snapshot(value['project_contract'])
        return value
    if (not isinstance(value,dict) or set(value)!={'version','adapter','training_loss_hook','seed_values','source_ref'} | ({'engineering_preflight'} if value.get('version')==2 else set())
            or type(value['version']) is not int or value['version'] not in (1,2) or value['adapter']!='binary-component/v2'
            or type(value['training_loss_hook']) is not bool or not isinstance(value['source_ref'],str) or not value['source_ref'].strip()
            or not isinstance(value['seed_values'],list) or not value['seed_values']
            or len(set(value['seed_values']))!=len(value['seed_values'])
            or any(type(seed) is not int or not 0<=seed<=2**31-1 for seed in value['seed_values'])):
        raise ValueError('explicit_qualified_workload_contract_required')
    if value['version']==2:
        p=value['engineering_preflight']
        if (not isinstance(p,dict) or set(p)!={'version','max_steps','examples_per_class'}
                or type(p['version']) is not int or p['version']!=1
                or type(p['max_steps']) is not int or not 8<=p['max_steps']<=64
                or type(p['examples_per_class']) is not int or not 2<=p['examples_per_class']<=4):
            raise ValueError('bounded_remote_engineering_preflight_required')
    return value


def capabilities(value=None):
    value=settings(value)
    if value and value['adapter']=='project/v1':
        from research_project import snapshot
        project,_,_=snapshot(value['project_contract'])
        return {'schema':'sdm-workload-capabilities/v1','adapter':'project/v1','editable':project['editable_files'],
            'protected':['data/partition/evaluator identity','project preflight','original execution authority'],
            'result_contract':'sdm-project-result/v1','evaluation':project['evaluation'],
            'training_location':'configured remote provider','execution_authority':False}
    return {**({'engineering_preflight':value['engineering_preflight']} if value and value['version']==2 else {}),
        'schema':'sdm-workload-capabilities/v1','adapter':value['adapter'] if value else 'legacy-binary-component/v1',
        'editable':['model architecture','optimizer','training hyperparameters']+
            (['training loss'] if value and value['training_loss_hook'] else []),
        'seed_values':value['seed_values'] if value else [42],
        'protected':['data/source identity','train/validation membership','validation criterion',
            'metric arithmetic','checkpoint selection','platform identity','required official Skill'],
        'result_contract':'sdm-binary-classification/v1','training_location':'official remote component only',
        'unsupported':['RoadModel continuation (deferred by user)','multi-class result ABI','multi-node result ABI'],
        'execution_authority':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();cfg=read_json(args.config)
    print(json.dumps(capabilities(cfg['component_pipeline'].get('workload')),ensure_ascii=False))
