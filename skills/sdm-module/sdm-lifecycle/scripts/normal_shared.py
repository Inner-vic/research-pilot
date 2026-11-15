"""Explicit, restricted shared-volume mapping; no inferred drive translation."""
import json
from pathlib import Path
from canonical import digest
from project_driver import file_digest
from roadmodel_normal_contract import require


def directory(path):
    path=Path(path)
    require(path.is_absolute() and path.resolve()==path and not path.is_symlink(),'normal_plain_shared_root_required')
    return path


def validate_mapping(spec,plan,template):
    """Bind coordinator paths to declared native PVC mounts, not guesses."""
    from pathlib import PurePosixPath
    execution=spec['execution'];volumes=execution.get('shared_volumes',[])
    require(isinstance(volumes,list) and volumes,'normal_explicit_shared_volumes_required')
    mounts=template.get('input_data_volume',[]);mappings=[]
    for row in volumes:
        local=directory(row['local_root']);runtime=PurePosixPath(row['runtime_root'])
        require(local.is_dir(),'normal_controller_shared_volume_not_accessible')
        require(runtime.is_absolute() and '..' not in runtime.parts,'normal_runtime_volume_root_required')
        require(sum(v.get('name')==row['name'] and v.get('env_value')==str(runtime) for v in mounts)==1,
            'normal_shared_volume_not_bound_to_native_mount')
        mappings.append((local,runtime))
    for local_name,remote in [('local_control_root',spec['runtime_control_root']),('local_output_root',plan['runtime_output_root'])]:
        choices=[]
        for local,runtime in mappings:
            path=PurePosixPath(remote)
            if path.is_relative_to(runtime):choices.append(local/path.relative_to(runtime).as_posix())
        require(len(choices)==1 and directory(execution[local_name])==choices[0],'normal_explicit_local_runtime_mapping_mismatch')
    require(any(PurePosixPath(plan['runtime_config_path']).is_relative_to(runtime) for _,runtime in mappings),
        'normal_configuration_must_use_declared_shared_volume')


def read(root,name,limit=8388608):
    root=directory(root);path=root/name
    require(path.resolve()==path and not path.is_symlink(),'normal_shared_symlink_forbidden')
    if not path.exists():return None
    require(path.is_file() and path.stat().st_size<=limit,'normal_shared_artifact_size_invalid')
    raw=path.read_bytes();require(len(raw)<=limit,'normal_shared_artifact_size_invalid')
    return json.loads(raw)


def publish(root,name,value):
    from roadmodel_component_gate import publish as exclusive
    root=directory(root);root.mkdir(parents=True,exist_ok=True)
    path=root/name;require(path.resolve()==path,'normal_shared_path_invalid')
    if path.exists():
        require(read(root,name)==value,'normal_shared_command_changed')
    else:exclusive(path,value)
    return file_digest(path)


def ready(config,plan):
    spec=config['normal_native'];execution=spec['execution']
    value=read(execution['local_control_root'],'ready.json')
    if value is None:return None
    bundle=json.loads(Path(spec['bundle_ref']['path']).read_bytes())
    require(file_digest(Path(spec['bundle_ref']['path']))==spec['bundle_ref']['digest'],'normal_bundle_changed')
    for k,want in {'bundle_digest':spec['bundle_ref']['digest'],'session_nonce':bundle['session_nonce'],
        'runtime_control_root':spec['runtime_control_root'],'runtime_output_root':plan['runtime_output_root']}.items():
        require(value.get(k)==want,'normal_shared_deployment_readback_mismatch:'+k)
    value['_shared_receipt_digest']=file_digest(Path(execution['local_control_root'])/'ready.json')
    return value


def output_file(config,relative):
    from roadmodel_normal_contract import relative as safe_relative
    root=directory(config['normal_native']['execution']['local_output_root'])
    path=root/safe_relative(relative)
    require(path.resolve()==path and path.is_file(),'normal_shared_output_missing_or_linked')
    return path
