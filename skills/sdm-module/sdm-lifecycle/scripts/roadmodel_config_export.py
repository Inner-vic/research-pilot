"""Export the reviewed standalone RoadModel Python configuration in a child.

This executes trusted project configuration, not an untrusted expression sandbox.
No model/torch/SDK import. Inherited experiment environment is discarded.
"""
import argparse
import ast
import decimal
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import types


def export(spec, output):
    from execution_contracts import pinned_json
    from project_driver import file_digest
    from review_storage import atomic_json
    source=Path(spec['source']['path']).resolve()
    if file_digest(source)!=spec['source']['digest']:raise ValueError('configuration_source_changed')
    tree=ast.parse(source.read_text(encoding='utf-8'))
    imports={n.names[0].name for n in ast.walk(tree) if isinstance(n,ast.Import)}
    if imports-{'os','decimal'} or any(isinstance(n,ast.ImportFrom) for n in ast.walk(tree)):
        raise ValueError('standalone_roadmodel_config_required')
    if any(isinstance(n,ast.Name) and n.id=='_base_' for n in ast.walk(tree)):
        raise ValueError('merged_config_export_required')
    env=spec.get('environment')
    if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()):
        raise ValueError('explicit_config_environment_required')
    if any(k in {'PATH','PYTHONPATH','PYTHONHOME','SYSTEMROOT','HOME'} or k.startswith('LPAI_') for k in env):
        raise ValueError('experiment_environment_only')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    target=output/'resolved-config.json'
    if target.exists():raise ValueError('fresh_configuration_export_required')
    child_env={k:v for k,v in os.environ.items() if k in {'PATH','SystemRoot','SYSTEMROOT','TEMP','TMP','WINDIR'}}
    child_env.update(env,PYTHONDONTWRITEBYTECODE='1')
    result=subprocess.run([sys.executable,'-I','-B',str(Path(__file__).resolve()),'--child',str(source)],
        env=child_env,capture_output=True,text=True,timeout=spec.get('timeout_seconds',60), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:raise ValueError('configuration_export_failed:'+result.stderr[-1000:])
    value=json.loads(result.stdout)
    # AMP is a CLI option in this source. Its value is pinned separately by protocol.
    protocol=pinned_json(spec['protocol'])
    value['amp']=protocol['training']['amp']
    from roadmodel_normal_contract import settings
    settings(protocol,value)
    if file_digest(source)!=spec['source']['digest']:raise ValueError('configuration_source_changed_during_export')
    atomic_json(target,value)
    receipt={'schema':'roadmodel-config-export/v1','source':spec['source'],'environment':env,
        'resolved_config':{'path':str(target),'digest':file_digest(target)},'keys':len(value),
        'model_type':value.get('model',{}).get('type'),'model_imported':False,
        'qualification':'real source configuration export; runtime data/model execution not verified'}
    atomic_json(output/'configuration-export.json',receipt)
    return receipt


def child(path):
    with decimal.localcontext():value=runpy.run_path(path)
    # Match MMEngine's __name filtering; single-underscore config values are
    # legitimate and must not silently disappear from a complete export.
    value={k:v for k,v in value.items() if not k.startswith('__') and not isinstance(v,(types.ModuleType,types.FunctionType))}
    print(json.dumps(value,allow_nan=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--child');a=p.parse_args()
    if not a.child:p.error('internal child operation required')
    child(a.child)
