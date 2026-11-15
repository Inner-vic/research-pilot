"""Prepare one pinned public dataset through the installed official asset SDK.

Normal user credentials only. No overwriting, sharing, cache operations or
training. Every external intent is journaled; unknown writes reconcile only.
"""
import argparse
import contextlib
import hashlib
import io
import json
from pathlib import Path
import re
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest


def prepare(spec_path,state_path):
    spec=read_json(spec_path)
    if (set(spec)!={'namespace','name','version','file','sha256','source_ref'} or not spec['source_ref']
            or not re.fullmatch(r'[a-z][a-z0-9-]{3,60}',spec['name'])
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',spec['version'])):
        raise ValueError('explicit_pinned_dataset_input_required')
    path=Path(spec['file']).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest()!=spec['sha256']:raise ValueError('local_public_input_changed')
    state_path=Path(state_path).resolve();binding=digest(spec)
    with locked(state_path.parent,'.'+state_path.name+'.lock','asset_input_already_owned'):
        state=read_json(state_path) if state_path.exists() else {'binding':binding,'intents':[],'status':'new'}
        if state['binding']!=binding:raise ValueError('asset_input_identity_changed')
        before_operations=len(state['intents'])
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            import asset
            from asset.core.config import RequestError
            from lpai.conf.lipai_conf import LpaiConf
            conf=LpaiConf();api=asset.config(env=conf.get_env(),jwt_token=conf.get_token())
            dataset=asset.resource('datasets/'+spec['name'],config=api)
            version=asset.resource('datasets/'+spec['name']+'/versions/'+spec['version'],config=api)
            def get(resource):
                try:return resource.get(force_refresh=True)
                except RequestError as error:
                    if error.status==404:return None
                    raise
            def intent(name):
                if name in state['intents']:raise ValueError('asset_operation_unknown_reconcile_only:'+name)
                state['intents'].append(name);state['status']=name+'-intent';atomic_json(state_path,state)
            def ensure(resource,label,**kwargs):
                info=get(resource)
                if info is None:
                    intent(label)
                    resource.create(annotations={'sdm.source_sha256':spec['sha256']},
                        describe='Pinned public study input; '+spec['source_ref'],**kwargs)
                    info=get(resource)
                if not info or info.get('metadata',{}).get('annotations',{}).get('sdm.source_sha256')!=spec['sha256']:
                    raise ValueError('asset_identity_or_provenance_mismatch')
                return info
            parent=ensure(dataset,'dataset-create',namespace=spec['namespace'])
            info=ensure(version,'version-create',format='dir')
            if not version.file_exists(key=path.name):
                intent('upload')
                version.upload_file(key=path.name,file_path=str(path))
            body=version.get_file(key=path.name)
            raw=body.read(path.stat().st_size+1)
            if hashlib.sha256(raw).hexdigest()!=spec['sha256']:raise ValueError('uploaded_public_input_readback_mismatch')
            info=version.get(force_refresh=True)
            state.update(status='verified',dataset_uid=parent['metadata']['uid'],version_uid=info['metadata']['uid'],
                namespace=spec['namespace'],name=spec['name'],version=spec['version'],file=path.name,
                sha256=spec['sha256'],bytes=len(raw),source_ref=spec['source_ref'],
                input={'env_key':'LPAI_INPUT_DATA_1','env_value':'/mnt/datasets/'+spec['name']+'/'+spec['version'].replace('.','-'),
                    'name':spec['name'],'version':spec['version'],'fsType':'ofs'})
            atomic_json(state_path,state)
    return {**state,'new_asset_operations':len(state['intents'])-before_operations}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--spec',required=True);parser.add_argument('--state',required=True)
    args=parser.parse_args()
    try:print(json.dumps(prepare(args.spec,args.state),ensure_ascii=False))
    except Exception as error:
        print(json.dumps({'status':'error','error_type':type(error).__name__,
            'next':'Read the original journal and reconcile exact named asset; do not blindly repeat unknown writes.'}));raise SystemExit(1)
