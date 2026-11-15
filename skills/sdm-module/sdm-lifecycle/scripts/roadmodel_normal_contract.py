"""RoadModel normal-v2 business contracts; no model, SDK or resource imports."""
import ast
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from execution_contracts import pinned_json
from project_driver import file_digest
from review_storage import atomic_json
from task_pipeline import digest
from proposal_review import default_policy

PROVIDER='roadmodel-normal-v2'
NODES=('1','2','3','4','6','7','8','13','14','15','16','18','21','cleanup')
SOURCES=('train.py','misc/dist_utils.py','misc/ckpt_load_utils.py')
IMPLEMENTATION_FILES=('roadmodel_normal_driver.py','roadmodel_normal_contract.py','driver_handshake.py',
    'execution_contracts.py','project_driver.py','task_pipeline.py','canonical.py',
    'normal_execution.py','normal_shared.py','normal_session.py','handoffs.py')


def require(ok,reason):
    if not ok:raise ValueError(reason)


def plain(path):
    p=Path(path)
    require(p.is_absolute() and p.resolve()==p and not p.is_symlink(),'absolute_unlinked_normal_path_required')
    return p


def relative(value):
    require(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*',value)
            and not any(x in {'.','..'} for x in value.split('/')),'normal_relative_file_required')
    return value


def positive(value,name,zero=False):
    require(type(value) is int and value>=(0 if zero else 1),'normal_positive_integer_required:'+name)


def settings(protocol,resolved):
    """The same pure parameter validation is used by handshake and runtime."""
    require(isinstance(protocol,dict) and protocol.get('schema')=='roadmodel-normal-protocol/v2','normal_protocol_required')
    require(protocol.get('evidence_kind') in {'cpu-simulation','project-execution'},'normal_evidence_kind_required')
    identity=protocol.get('identity',{})
    require(all(isinstance(identity.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',identity[k]) for k in ('study','trial','attempt')),'normal_attempt_identity_required')
    train=protocol.get('training',{});dist=protocol.get('distributed',{})
    for k in ('epochs','batch_per_gpu','gradient_accumulation'):positive(train.get(k),k)
    positive(train.get('workers_per_gpu'),'workers_per_gpu',True);positive(train.get('seed'),'seed',True)
    require(type(train.get('amp')) is bool,'normal_amp_boolean_required')
    require(type(train.get('learning_rate')) in (int,float) and math.isfinite(train['learning_rate']) and train['learning_rate']>0,'normal_learning_rate_required')
    require(protocol.get('data_scope')=='full-declared-input','normal_full_declared_data_scope_required')
    require(isinstance(protocol.get('data_digest'),str) and re.fullmatch(r'sha256:[0-9a-f]{64}',protocol['data_digest']),'normal_data_digest_required')
    for k in ('nodes','gpus_per_node'):positive(dist.get(k),k)
    positive(dist.get('node_rank'),'node_rank',True)
    require(dist['node_rank']<dist['nodes'],'normal_node_rank_out_of_range')
    require(isinstance(dist.get('master_addr'),str) and re.fullmatch(r'[A-Za-z0-9_.:-]+',dist['master_addr']),'normal_master_address_required')
    require(type(dist.get('master_port')) is int and 1<=dist['master_port']<=65535,'normal_master_port_required')
    limit=protocol.get('training_wall_seconds')
    require(limit is None or type(limit) is int and limit>0,'normal_training_wall_limit_required')
    for k in ('runtime_source_root','runtime_output_root','runtime_config_path'):
        require(isinstance(protocol.get(k),str) and protocol[k].startswith('/') and '..' not in protocol[k].split('/'),'absolute_normal_runtime_path_required:'+k)
    require(protocol['runtime_config_path'].endswith('.py'),'normal_materialized_python_config_required')
    require(isinstance(protocol.get('source_commit'),str) and re.fullmatch(r'[0-9a-f]{40}',protocol['source_commit']),'normal_source_commit_required')
    require(isinstance(resolved,dict) and resolved and '_base_' not in resolved,'fully_resolved_normal_config_required')
    # Literal-only JSON export; no imports, eval, exec or MMEngine execution here.
    json.dumps(resolved,allow_nan=False)
    require(all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',k) and not k.startswith('__') for k in resolved),'normal_literal_config_keys_required')
    expected={'max_epochs':train['epochs'],'train_loader':{'batch_size':train['batch_per_gpu'],'num_workers':train['workers_per_gpu']},'amp':train['amp']}
    require(resolved.get('max_epochs')==expected['max_epochs'] and type(resolved.get('max_epochs')) is int,'normal_epochs_config_mismatch')
    for k,v in expected['train_loader'].items():
        require(type(resolved.get('train_loader',{}).get(k)) is int and resolved['train_loader'][k]==v,'normal_loader_config_mismatch:'+k)
    require(resolved.get('amp') is train['amp'],'normal_amp_config_mismatch')
    lr=resolved.get('optimizer',{}).get('optimizer',{}).get('lr')
    require(type(lr) in (int,float) and lr==train['learning_rate'],'normal_lr_config_mismatch')
    require(not resolved.get('resume_from') and not resolved.get('load_from'),'normal_implicit_checkpoint_config_forbidden')
    if 'warmup_epochs' in train:
        value=train['warmup_epochs']
        require(type(value) in (int,float) and math.isfinite(value) and value>=0,'normal_warmup_epochs_required')
        require(resolved.get('warmup_epochs')==value and resolved.get('warmup_iters') is None,'normal_warmup_config_mismatch')
    if resolved.get('warmup_iters') is not None:
        positive(resolved['warmup_iters'],'warmup_iters',True)
    if resolved.get('warmup_epochs') is not None:
        value=resolved['warmup_epochs']
        require(type(value) in (int,float) and math.isfinite(value) and value>=0,'normal_warmup_epochs_required')
    return {'epochs':train['epochs'],'world_size':dist['nodes']*dist['gpus_per_node'],
        'effective_batch':train['batch_per_gpu']*dist['nodes']*dist['gpus_per_node']*train['gradient_accumulation'],
        'training_wall_seconds':limit,'fresh_output_required':True}


def choose_checkpoint(index,choice,identity,mode):
    require(isinstance(index,dict) and index.get('schema')=='roadmodel-checkpoint-index/v1','normal_checkpoint_index_required')
    rows=index.get('checkpoints');require(isinstance(rows,list) and len(rows)<=4096,'bounded_normal_checkpoint_index_required')
    ids=[r.get('id') for r in rows if isinstance(r,dict)]
    require(len(ids)==len(rows) and all(isinstance(x,str) and x for x in ids) and len(set(ids))==len(ids),'unique_normal_checkpoint_ids_required')
    origin=choice.get('origin');require(isinstance(origin,dict) and set(origin)=={'study','trial','attempt'},'explicit_checkpoint_origin_required')
    if mode=='resume':require(origin['study']==identity['study'] and origin['trial']==identity['trial'] and origin['attempt']!=identity['attempt'],'resume_checkpoint_attempt_mismatch')
    candidates=[r for r in rows if r.get('origin')==origin and r.get('complete') is True and r.get('finite_state') is not False]
    policy=choice.get('policy')
    if policy=='exact':candidates=[r for r in candidates if r.get('id')==choice.get('id')]
    elif policy=='latest-complete':
        require(all(type(r.get('epoch')) is int and r['epoch']>=0 for r in candidates),'checkpoint_epoch_required')
        if candidates:
            epoch=max(r['epoch'] for r in candidates);candidates=[r for r in candidates if r['epoch']==epoch]
    elif policy=='best':
        metric=choice.get('metric');direction=choice.get('direction')
        require(isinstance(metric,str) and metric and direction in {'min','max'},'explicit_checkpoint_metric_direction_required')
        require(all(type(r.get('metrics',{}).get(metric)) in (int,float) and math.isfinite(r['metrics'][metric]) for r in candidates),'finite_checkpoint_selection_metric_required')
        if candidates:
            score=({'min':min,'max':max}[direction])(r['metrics'][metric] for r in candidates)
            candidates=[r for r in candidates if r['metrics'][metric]==score]
    else:raise ValueError('explicit_checkpoint_selection_policy_required')
    require(len(candidates)==1,'one_complete_checkpoint_required')
    selected=candidates[0];relative(selected.get('relative_path'))
    require(isinstance(selected.get('digest'),str) and re.fullmatch(r'sha256:[0-9a-f]{64}',selected['digest']),'checkpoint_content_digest_required')
    required={'model'} if mode=='weights' else {'model','optimizer','scheduler','epoch','global_iter'}
    require(required<=set(selected.get('components',[])),'checkpoint_declared_components_incomplete')
    return deepcopy(selected)


def business(node,inputs):
    """Offline challenges call actual plan/checkpoint validators, never platform paths."""
    if node not in NODES:return {'task_node':node,'status':'failed','reason':'unsupported_node'}
    try:
        meta=settings(inputs['protocol'],inputs['resolved'])
        mode=inputs['protocol'].get('initialization',{}).get('mode')
        require(mode in {'none','weights','resume'},'normal_initialization_mode_required')
        if mode=='resume':
            require(inputs['protocol']['initialization'].get('resume_kind')=='optimizer-schedule','full_resume_not_qualified')
        selected=None
        if mode!='none':selected=choose_checkpoint(inputs['checkpoint_index'],inputs['protocol']['initialization']['selector'],inputs['protocol']['identity'],mode)
        return {'task_node':node,'status':'completed','actual':{**meta,'checkpoint_id':selected['id'] if selected else None,
            'qualification':'offline-business-contract-only','model_executed':False}}
    except (ValueError,KeyError,TypeError,AttributeError) as e:
        return {'task_node':node,'status':'failed','reason':str(e) if isinstance(e,ValueError) else 'normal_business_input_required'}


def immutable(path,value):
    if path.exists():require(json.loads(path.read_bytes())==value,'normal_prepared_artifact_changed')
    else:atomic_json(path,value)


def compile_plan(config,output,*,_verify_checkpoint_content=True):
    protocol=pinned_json(config['normal_protocol']);resolved=pinned_json(config['normal_resolved_config'])
    meta=settings(protocol,resolved);root=plain(config['normal_source_root'])
    files=protocol.get('source_files',{})
    require(set(SOURCES)<=set(files) and len(files)<=64,'normal_pinned_source_files_required')
    for name,wanted in files.items():
        path=plain(root/relative(name));require(file_digest(path)==wanted,'normal_source_file_changed:'+name)
    tree=ast.parse((root/'train.py').read_text(encoding='utf-8'));flags=set()
    for n in ast.walk(tree):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_argument':
            flags.update(a.value for a in n.args if isinstance(a,ast.Constant) and isinstance(a.value,str))
    require({'--py-config','--work-dir','--amp','--gradient-accumulation','--resume-from','--load-from','--seed'}<=flags,'normal_source_cli_contract_missing')
    mode=protocol.get('initialization',{}).get('mode');require(mode in {'none','weights','resume'},'normal_initialization_mode_required')
    if mode=='resume':require(protocol['initialization'].get('resume_kind')=='optimizer-schedule','full_resume_not_qualified')
    selected=None;checkpoint=None
    if mode!='none':
        index=pinned_json(config['normal_checkpoint_index'])
        selected=choose_checkpoint(index,protocol['initialization']['selector'],protocol['identity'],mode)
        if mode=='resume' and protocol['evidence_kind']=='project-execution':
            require(selected.get('health_verified') is True and selected.get('finite_state') is True,
                'normal_resume_requires_verified_finite_checkpoint')
            if protocol['training']['amp']:
                require('amp_scaler' in selected.get('components',[]),'normal_amp_resume_requires_scaler')
        local=plain(plain(config['normal_checkpoint_root'])/relative(selected['relative_path']))
        if _verify_checkpoint_content:require(file_digest(local)==selected['digest'],'selected_checkpoint_content_changed')
        runtime_root=protocol.get('runtime_checkpoint_root')
        require(isinstance(runtime_root,str) and runtime_root.startswith('/') and '..' not in runtime_root.split('/'),'absolute_runtime_checkpoint_root_required')
        checkpoint=runtime_root.rstrip('/')+'/'+selected['relative_path']
    resolved=deepcopy(resolved)
    # Freeze the legacy fallback explicitly; declared epochs must never be lost
    # in inherited environment variables that the pinned config does not read.
    if resolved.get('warmup_iters') is None and not resolved.get('warmup_epochs',0):
        resolved['warmup_iters']=500
    text='\n'.join(k+' = '+repr(v) for k,v in sorted(resolved.items()))+'\n'
    config_digest='sha256:'+__import__('hashlib').sha256(text.encode()).hexdigest()
    dist=protocol['distributed'];train=protocol['training']
    argv=['python','-m','torch.distributed.run','--nnodes',str(dist['nodes']),'--nproc_per_node',str(dist['gpus_per_node']),
          '--node_rank',str(dist['node_rank']),'--master_addr',dist['master_addr'],'--master_port',str(dist['master_port']),
          protocol['runtime_source_root'].rstrip('/')+'/train.py','--py-config',protocol['runtime_config_path'],
          '--work-dir',protocol['runtime_output_root'],'--seed',str(train['seed']),'--gradient-accumulation',str(train['gradient_accumulation'])]
    if train['amp']:argv.append('--amp')
    if checkpoint:argv.extend(['--resume-from' if mode=='resume' else '--load-from',checkpoint])
    plan={'schema':'roadmodel-normal-plan/v2','provider':PROVIDER,'protocol_digest':config['normal_protocol']['digest'],
        'resolved_config_digest':config['normal_resolved_config']['digest'],'effective_config_digest':config_digest,
        'identity':protocol['identity'],'source_commit':protocol['source_commit'],'source_files':files,'data_digest':protocol['data_digest'],
        'evidence_kind':protocol['evidence_kind'],'training':train,'distributed':dist,**meta,'argv':argv,
        'runtime_source_root':protocol['runtime_source_root'].rstrip('/'),
        'runtime_output_root':protocol['runtime_output_root'],'runtime_config_path':protocol['runtime_config_path'],
        'initialization_mode':mode,'selected_checkpoint':selected,
        'resume_qualification':'optimizer/scheduler/epoch/iteration only; AMP scaler and RNG restoration NOT qualified' if mode=='resume' else 'not-resuming',
        'execution_authority_created':False,'configuration_staging_verified':False,
        'required_runtime_checks':['source_commit','data_digest','effective_config_digest','world_size','visible_gpus_per_node','output_empty']}
    output=plain(Path(output).absolute());output.mkdir(parents=True,exist_ok=True)
    target=output/'effective-config.py'
    if target.exists():require(target.read_text(encoding='utf-8')==text,'normal_effective_config_changed')
    else:
        with target.open('x',encoding='utf-8',newline='\n') as stream:stream.write(text)
    immutable(output/'normal-plan.json',plan)
    return plan


def fixture_inputs():
    protocol={'schema':'roadmodel-normal-protocol/v2','evidence_kind':'cpu-simulation','identity':{'study':'cpu','trial':'normal','attempt':'a2'},
        'data_scope':'full-declared-input','data_digest':'sha256:'+'a'*64,'source_commit':'b'*40,
        'runtime_source_root':'/cpu/roadmodel','runtime_output_root':'/cpu/output/a2','runtime_config_path':'/cpu/config/a2.py',
        'training':{'epochs':300,'batch_per_gpu':12,'workers_per_gpu':4,'learning_rate':4e-5,'gradient_accumulation':2,'amp':True,'seed':42},
        'distributed':{'nodes':1,'gpus_per_node':8,'node_rank':0,'master_addr':'localhost','master_port':29500},
        'training_wall_seconds':None,'initialization':{'mode':'none'}}
    resolved={'max_epochs':300,'train_loader':{'batch_size':12,'num_workers':4},'amp':True,'optimizer':{'optimizer':{'lr':4e-5}}}
    return {'protocol':protocol,'resolved':resolved}


def _prepare(config,output):
    require(config.get('provider')==PROVIDER,'normal_provider_required')
    require(config.get('objective','execute-and-learn')=='execute-and-learn'
        and not any(config.get(k,False) for k in ('code_change','baseline_required','robustness_required')),
        'normal_v2_frozen_training_scope_required')
    output=plain(Path(output).absolute());output.mkdir(parents=True,exist_ok=True)
    plan=compile_plan(config,output);scripts=Path(__file__).parent;driver=scripts/'roadmodel_normal_driver.py'
    impl={name:file_digest(scripts/name) for name in IMPLEMENTATION_FILES}
    inputs=fixture_inputs();invalid=deepcopy(inputs);invalid['protocol']['training']['batch_per_gpu']=0
    expected={'epochs':300,'world_size':8,'effective_batch':192,'training_wall_seconds':None,
        'fresh_output_required':True,'checkpoint_id':None,'qualification':'offline-business-contract-only','model_executed':False}
    cp=deepcopy(inputs);cp['protocol']['initialization']={'mode':'weights','selector':{'policy':'latest-complete','origin':{'study':'cpu','trial':'baseline','attempt':'a1'}}}
    cp['checkpoint_index']={'schema':'roadmodel-checkpoint-index/v1','checkpoints':[
        {'id':name,'origin':cp['protocol']['initialization']['selector']['origin'],'complete':True,'epoch':epoch,
         'relative_path':name+'.pth','digest':'sha256:'+'c'*64,'components':['model']} for name,epoch in [('old',1),('chosen',5)]]}
    bad_cp=deepcopy(cp);bad_cp['checkpoint_index']['checkpoints'][1]['id']='old'
    rows={node:{'input_contract':'roadmodel-normal-protocol/v2','output_contract':'roadmodel-normal-receipt/v2',
        'behavior_cases':[
            {'kind':'valid','input':inputs,'expected':{'task_node':node,'status':'completed','actual':expected}},
            {'kind':'invalid','input':invalid,'expected':{'task_node':node,'status':'failed','reason':'normal_positive_integer_required:batch_per_gpu'}},
            {'kind':'valid','input':cp,'expected':{'task_node':node,'status':'completed','actual':{**expected,'checkpoint_id':'chosen'}}},
            {'kind':'invalid','input':bad_cp,'expected':{'task_node':node,'status':'failed','reason':'unique_normal_checkpoint_ids_required'}}]} for node in NODES}
    manifest={'schema':'sdm-driver-capabilities/v2','provider':PROVIDER,'graph_revision':3,'handshake_protocol':'sdm-driver-handshake/v1',
        'driver_digest':file_digest(driver),'implementation_files':impl,'nodes':rows}
    immutable(output/'driver-capabilities.json',manifest)
    project={**config,'supported_graph_revisions':[3],'preparation_is_read_only':True,'max_attempts':1,
        'workflow_profile':config.get('workflow_profile','legacy'),
        'proposal_review':config.get('proposal_review',default_policy()),
        'driver_runtime':{'mode':'native','python':__import__('sys').executable},
        'project_driver':{'path':str(driver),'digest':file_digest(driver)},
        'driver_capabilities':{'path':str(output/'driver-capabilities.json'),'digest':file_digest(output/'driver-capabilities.json')},
        'normal_plan':{'path':str(output/'normal-plan.json'),'digest':file_digest(output/'normal-plan.json')},
        'normal_effective_config':{'path':str(output/'effective-config.py'),'digest':plan['effective_config_digest']},
        'read_only_task_nodes':['1','2','3','4','6','8','13','18','21']}
    from proposal_review import settings
    if settings(project) is None:raise ValueError('new_roadmodel_proposal_review_required')
    immutable(output/'project.json',project)
    return {'status':'prepared-for-offline-validation','provider':PROVIDER,'project':str(output/'project.json'),
        'plan_digest':project['normal_plan']['digest'],'execution_authority_created':False,'platform_calls':0,
        'qualification':'business contract migration; staging, platform execution and model correctness not verified'}


def prepare(config,output):
    from review_storage import locked
    output=plain(Path(output).absolute());output.mkdir(parents=True,exist_ok=True)
    with locked(output,'.normal-contract.lock','normal_preparation_busy'):
        return _prepare(config,output)


def validate_project(config,*,_verify_checkpoint_content=True):
    require(config.get('provider')==PROVIDER,'normal_provider_required')
    plan=pinned_json(config['normal_plan']);path=plain(config['normal_effective_config']['path'])
    require(file_digest(path)==config['normal_effective_config']['digest']==plan['effective_config_digest'],'normal_effective_config_digest_mismatch')
    observed=compile_plan(config,Path(config['normal_plan']['path']).parent,_verify_checkpoint_content=_verify_checkpoint_content)
    require(observed==plan,'normal_plan_changed')
    require(not set(config.get('external_operations',{}))-{'14','cleanup'},'normal_business_validation_cannot_be_overridden')
    require(plan['evidence_kind']!='cpu-simulation' or not config.get('external_operations'),'simulated_normal_protocol_cannot_dispatch_external_operations')
    for node,spec in config.get('external_operations',{}).items():
        require(spec.get('normal_plan_digest')==config['normal_plan']['digest'],'normal_external_plan_binding_required')
        if node=='14':
            require(spec.get('action')=='submit' and spec.get('normal_effective_config_digest',spec.get('configuration_digest'))==plan['effective_config_digest'],'normal_submit_configuration_binding_required')
            require(spec.get('normal_argv')==plan['argv'],'normal_submit_argv_binding_required')
            require(spec.get('allocation_only') is True,'normal_allocation_must_not_autostart_training')
    return plan


def check_runtime(plan,observed):
    require(isinstance(observed,dict),'normal_runtime_preflight_required')
    for key in ('source_commit','data_digest','effective_config_digest','world_size'):
        require(observed.get(key)==plan[key],'normal_runtime_mismatch:'+key)
    require(observed.get('visible_gpus_per_node')==plan['distributed']['gpus_per_node'],'normal_runtime_visible_gpu_mismatch')
    require(observed.get('output_empty') is True,'normal_fresh_output_required_prevent_implicit_resume')
    require(observed.get('runtime_output_root')==plan['runtime_output_root'],'normal_output_identity_mismatch')
    require(type(observed.get('observed_at')) in (int,float) and type(observed.get('valid_until')) in (int,float)
        and observed['observed_at']<=__import__('time').time()<=observed['valid_until'],'normal_runtime_observation_stale')
    return {'runtime_contract_verified':True,'qualification':'bound observation; not independent platform authentication'}


def check_training(plan,observed):
    require(isinstance(observed,dict),'normal_training_observation_required')
    for key in ('identity','source_commit','data_digest','effective_config_digest','runtime_output_root'):
        require(observed.get(key)==plan[key],'normal_training_identity_mismatch:'+key)
    require(observed.get('plan_digest')==digest(plan),'normal_training_plan_mismatch')
    require(observed.get('exit_code')==0 and type(observed.get('exit_code')) is int,'normal_training_exit_failed')
    require(type(observed.get('completed_epochs')) is int and observed['completed_epochs']==plan['training']['epochs'],'normal_target_epochs_not_completed')
    require(type(observed.get('optimizer_updates')) is int and observed['optimizer_updates']>0,'normal_effective_updates_required')
    require(type(observed.get('world_size')) is int and observed['world_size']==plan['world_size'],'normal_training_world_size_mismatch')
    require(observed.get('checkpoint_index') is not None,'normal_training_checkpoint_index_required')
    return {'completed_epochs':observed['completed_epochs'],'optimizer_updates':observed['optimizer_updates']}


def execution_request(config,plan,node):
    require(node in {'15','16'},'normal_execution_node_required')
    request={'schema':'roadmodel-normal-execution-request/v2','node':node,'plan_ref':config['normal_plan'],
        'plan_digest':digest(plan),'effective_config_ref':config['normal_effective_config'],
        'identity':plan['identity'],'evidence_kind':plan['evidence_kind'],'training_wall_seconds':plan['training_wall_seconds'],
        'authority':'existing SDM confirmation and signed S12 required by bound execution bridge'}
    if node=='15':request['argv']=plan['argv']
    else:
        require(config.get('normal_selected_evaluation_checkpoint') is not None,'normal_selected_evaluation_checkpoint_required')
        request['selected_checkpoint']=config['normal_selected_evaluation_checkpoint']
        request['runtime_checkpoint_path']=plan['runtime_output_root'].rstrip('/')+'/'+relative(request['selected_checkpoint']['relative_path'])
    return request
