"""Offline native component payload construction using the existing SDK adapter."""
import base64
import json
from pathlib import Path
import shlex
import sys
import secrets

from execution_contracts import pinned_json
from project_driver import file_digest
from roadmodel_normal_contract import immutable, prepare, require, validate_project
from task_pipeline import digest


def build(config,output):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    prepare(config,output/'normal')
    project=json.loads((output/'normal/project.json').read_bytes());plan=validate_project(project)
    require(plan['evidence_kind']=='project-execution','native_project_execution_required')
    spec=config['normal_native'];control=spec['runtime_control_root']
    safety_version=spec.get('runtime_safety_version',1)
    require(type(safety_version) is int and safety_version in (1,2),'supported_runtime_safety_version_required')
    require(control.startswith('/') and '..' not in control.split('/'),'absolute_native_control_root_required')
    require(plan['distributed']['nodes']==1,'native_gate_single_node_only')
    require(spec.get('restricted_shared_control_mount') is True,'restricted_shared_control_mount_required')
    protocol=pinned_json(config['normal_protocol'])
    bundle={'plan':plan,'plan_digest':digest(plan),'source_files':protocol['source_files'],
        'runtime_assets':spec['runtime_assets'],'preflight_valid_seconds':spec.get('preflight_valid_seconds',300)}
    require(bundle['runtime_assets'],'runtime_assets_required')
    for asset in bundle['runtime_assets']:
        require(asset['path'].startswith('/') and asset['digest'].startswith('sha256:'),'pinned_runtime_asset_required')
    require(any(a.get('kind')=='data-index' and a['digest']==plan['data_digest'] for a in bundle['runtime_assets']),
        'normal_data_asset_binding_required')
    if plan['selected_checkpoint']:
        require(any(a.get('kind')=='initial-checkpoint' and a['digest']==plan['selected_checkpoint']['digest']
            and a['path']==protocol['runtime_checkpoint_root'].rstrip('/')+'/'+plan['selected_checkpoint']['relative_path']
            for a in bundle['runtime_assets']),'normal_checkpoint_asset_binding_required')
    gate=Path(__file__).with_name('roadmodel_component_gate.py')
    scripts=Path(__file__).parent
    connected=bool(spec.get('execution'))
    if connected:
        from lpai_operation_adapter import submission_metadata
        submission_metadata(spec['adapter'])
        require(spec['adapter']['total_gpus']==plan['world_size'],'normal_native_requested_gpu_count_mismatch')
        from normal_execution import authorization
        auth=authorization(project,plan)
        require((Path(config['normal_source_root'])/'eval.py').is_file() and 'eval.py' in protocol['source_files'],
            'normal_pinned_evaluation_entry_required')
        execution=spec['execution'];observer=scripts/'roadmodel_process_observer.py'
        if not Path(execution['workspace']).exists() and execution.get('workspace_payload'):
            from workspace import create_workspace
            create_workspace(Path(execution['workspace']),pinned_json(execution['workspace_payload']),
                expected_revision=0,operation_id='normal-workspace-register',assets_root=scripts.parent/'assets')
        bundle_path=output/'native-bundle.json'
        nonce=json.loads(bundle_path.read_bytes())['session_nonce'] if bundle_path.exists() else secrets.token_hex(24)
        bundle.update(normal_session=True,session_nonce=nonce,authorization_digest=__import__('canonical').digest(auth),
            component_uid=spec['adapter']['component_uid'],evaluation_metrics=execution['evaluation_metrics'],
            evaluation_wall_seconds=execution.get('evaluation_wall_seconds'))
        from normal_execution import allocation_budget_seconds
        bundle['allocation_wall_seconds']=allocation_budget_seconds(project,plan)
        argv=list(plan['argv']);entry=plan['runtime_source_root']+'/train.py';position=argv.index(entry)
        bundle['observed_argv']=argv[:position]+[control+'/observer.py','--entry',entry,'--control',control,
            '--output',plan['runtime_output_root'],'--kind','run','--']+argv[position+1:]
    immutable(output/'native-bundle.json',bundle)
    bundle_bytes=(output/'native-bundle.json').read_bytes()
    effective=Path(project['normal_effective_config']['path']).read_bytes()
    # All executable/config bytes are embedded. No network download or credential.
    writes={control+'/gate.py':gate.read_bytes(),control+'/bundle.json':bundle_bytes,
            plan['runtime_config_path']:effective}
    if connected:writes[control+'/observer.py']=observer.read_bytes()
    if connected and safety_version==2:
        writes[control+'/roadmodel_runtime_safety.py']=(scripts/'roadmodel_runtime_safety.py').read_bytes()
    bootstrap='import pathlib,base64;'+ ';'.join(
        'p=pathlib.Path('+repr(path)+');p.parent.mkdir(parents=True,exist_ok=True);p.open("xb").write(base64.b64decode('+repr(base64.b64encode(data).decode())+'))'
        for path,data in writes.items())
    cmd='set -eu\n'+('export SDM_RUNTIME_POLICY_VERSION=2\n' if safety_version==2 else '')+'python -I -B -c '+shlex.quote(bootstrap)+'\nexec python -I -B '+shlex.quote(control+'/gate.py')+' --bundle '+shlex.quote(control+'/bundle.json')+' --control '+shlex.quote(control)+'\n'
    native=pinned_json(spec['component_template']);native={**native,'cmd':cmd}
    if connected:
        from normal_shared import validate_mapping
        validate_mapping(spec,plan,native)
        for name in ('name','queue_code','image'):
            require(isinstance(native.get(name),str) and native[name].strip(),'normal_native_template_field_required:'+name)
        roles=native.get('task_role_specs',[])
        require(isinstance(roles,list) and roles and sum(int(r['replica']) for r in roles)==plan['distributed']['nodes'],
            'normal_native_node_allocation_mismatch')
        from pathlib import PurePosixPath
        source=PurePosixPath(plan['runtime_source_root'])
        staged=any(c.get('commit_id')==plan['source_commit'] for c in native.get('code',[]))
        staged=staged or any(source.is_relative_to(PurePosixPath(v['runtime_root'])) for v in spec['execution']['shared_volumes'])
        require(staged,'normal_source_must_be_bound_to_code_asset_or_shared_volume')
    immutable(output/'component-config.json',native)
    adapter={**spec['adapter'],'configuration_ref':{'path':str(output/'component-config.json'),'digest':file_digest(output/'component-config.json')},
        'normal_gate_command':cmd,'normal_plan_digest':project['normal_plan']['digest'],'prepare_before_submit':True}
    if adapter.get('runtime_path_map'):
        from lpai_operation_adapter import runtime_path
        adapter['native_configuration_ref']={**adapter['configuration_ref'],'path':runtime_path(adapter,output/'component-config.json')}
    immutable(output/'official-adapter.json',adapter)
    from lpai_operation_adapter import operation_binding
    binding=operation_binding(output/'official-adapter.json',spec['sdk_python'],'submit',spec['request_key'],spec['authorization_ref'])
    binding.update(normal_plan_digest=project['normal_plan']['digest'],normal_effective_config_digest=plan['effective_config_digest'],
        normal_argv=plan['argv'],allocation_only=True,allocation_semantics='gated-component-no-training-before-node15')
    project['external_operations']={**project.get('external_operations',{}),'14':binding}
    # This delivery is a preparation artifact, not a partially enabled submit
    # configuration. Public execution must reject it before any side effect.
    # Resource authorization binds the final platform payload digest, which is
    # now reviewable. Activation is a separate, read-only binding transaction.
    project['normal_native_preparation_only']=True
    if connected:
        project['normal_native']={**spec,'bundle_ref':{'path':str(output/'native-bundle.json'),'digest':file_digest(output/'native-bundle.json')},
            'adapter_config_ref':{'path':str(output/'official-adapter.json'),'digest':file_digest(output/'official-adapter.json')}}
        project['normal_native_session_version']=1
        project['normal_evaluation_checkpoint_selector']=config.get('normal_evaluation_checkpoint_selector',{'policy':'latest-complete','origin':plan['identity']})
        project['read_only_task_nodes']=[n for n in project['read_only_task_nodes'] if n!='13']
        from normal_session import validate
        validate(project,plan)
    immutable(output/'project.json',project)
    return {'status':'native-payload-prepared','project':str(output/'project.json'),'platform_calls':0,
        'execution_ready':False,'execution_chain_configured':connected,
        'qualification':'configured chain; resource authorization must bind final payload before activation',
        'remaining':['bind-resource-authorization'] if connected else ['shared mount, authenticated workspace, signer and training authorization configuration'],
        'component_semantics':'starts gate only; waits after preflight for node15 permit'}


def activate(config,output):
    project=pinned_json(config['prepared_project'])
    require(project.get('normal_native_session_version')==1,'normal_connected_session_required')
    spec={**project['external_operations']['14'],'authorization_ref':config['resource_authorization']}
    from external_operations import Operation
    Operation(spec,Path(output)/'authorization-check-state.json',60).authorization()
    project={**project,'external_operations':{'14':spec},'normal_native_preparation_only':False}
    plan=validate_project(project)
    from normal_session import validate
    validate(project,plan)
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    immutable(output/'project.json',project)
    return {'status':'normal-native-bindings-activated','project':str(output/'project.json'),
        'platform_calls':0,'execution_ready':True,'resource_started':False,
        'qualification':'authorized configured chain; runtime acceptance remains unverified'}
