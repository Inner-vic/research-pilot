"""Compile reusable RoadModel inputs and five-phase/event contracts; no platform calls."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from review_storage import atomic_json
from task_pipeline import digest
from execution_contracts import pinned_json
from proposal_review import default_policy


def prepare(config,output,entry):
    s=config['roadmodel_devspace'];root=Path(output).resolve()
    from agent_roles import policy as role_policy
    if 'agent_roles' in config and 'agent_roles' in s and config['agent_roles']!=s['agent_roles']:
        raise ValueError('conflicting_devspace_role_policies')
    selected_roles=role_policy({'agent_roles':config.get('agent_roles',s.get('agent_roles'))})
    if s.get('version')!=1 or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',s.get('run_id','')):
        raise ValueError('roadmodel_devspace_profile_identity_required')
    grant=s['authorization']
    if grant.get('confirmed') is not True or not grant.get('source_ref') or grant.get('time_limit') is not None or grant.get('budget_limit') is not None:
        raise ValueError('explicit_unbounded_profile_user_grant_required')
    observation=pinned_json(s['resource_observation'])
    deferred=s.get('defer_until_stopped',False)
    if type(deferred) is not bool:raise ValueError('boolean_deferred_acquisition_required')
    if observation.get('source')!='official-devspaces-get' or (observation['phase']!='stopped' and
        not (deferred and observation['phase'] in {'ready','waiting'})):
        raise ValueError('fresh_official_stopped_original_space_required')
    observed=dt.datetime.fromisoformat(observation['observed_at']);now=dt.datetime.now(dt.timezone.utc)
    if observed.utcoffset() is None or not 0<=(now-observed).total_seconds()<=900:
        raise ValueError('fresh_official_profile_resource_observation_required')
    resources=observation['resources'];mounts=observation['mounts']
    if ('A100' not in resources['gpuType'] or 'SHARE' not in resources['gpuType']
        or {m.get('envKey') for m in mounts}!={'LPAI_INPUT_DATASET_0','LPAI_INPUT_MODEL_0','LPAI_INPUT_MODEL_1'}):
        raise ValueError('profile_resource_and_mount_identity_required')
    source={k:s[k] for k in ('source_root','commit','source_url')}
    if (not re.fullmatch(r'[0-9a-f]{40}',source['commit']) or not source['source_root'].startswith('/lpai/')
        or '..' in Path(source['source_root']).parts or not re.fullmatch(r'https://[^/@?]+/[^?]+\.git',source['source_url'])):
        raise ValueError('credential_free_exact_profile_source_required')
    train=s.get('train_samples',32);val=s.get('validation_samples',8)
    epochs=s.get('epochs',20)
    semantic_study=s.get('semantic_study',False)
    if type(semantic_study) is not bool:raise ValueError('boolean_semantic_study_required')
    if type(epochs) is not int or epochs<1:raise ValueError('positive_profile_epochs_required')
    if type(train) is not int or type(val) is not int or not 1<=train<=128 or not 1<=val<=32:
        raise ValueError('explicit_small_profile_frame_counts_required')
    if root.exists() and any(root.iterdir()):raise ValueError('new_profile_output_required')
    root.mkdir(parents=True,exist_ok=True)
    profile={**source,'train_samples':train,'validation_samples':val,'epochs':epochs}
    if semantic_study:profile.update(runtime_version=2,learning_rate=s.get('initial_learning_rate',1e-5),
        maximum_training_seconds=s.get('maximum_training_seconds',900),loader_workers=s.get('loader_workers',0))
    if semantic_study and (type(profile['loader_workers']) is not int or not 0<=profile['loader_workers']<=16):
        raise ValueError('bounded_profile_loader_workers_required')
    atomic_json(root/'roadmodel-spec.json',profile)
    assets=Path(__file__).resolve().parents[1]/'assets/roadmodel'
    inputs=[{'name':p.name,'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in [assets/'workload.py',assets/'training_entry.py',assets/'prepare_roadmodel_minimal.py',assets/'preflight.py',root/'roadmodel-spec.json']]
    if semantic_study:
        extra=[assets/'training_entry_v2.py',assets/'semantic_overlay.py',assets/'feature_semantic_residual.py',assets/'resource_sampler.py',
               Path(__file__).parent/'roadmodel_runtime_safety.py']
        inputs.extend({'name':p.name,'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in extra)
    fields={'score':'float','validation_loss':'float','surface_miou':'float','nonempty_iou':'float','optimizer_updates':'int',
        'checkpoint_verified':'bool','checkpoint_digest':'str','source_commit':'str','data_digest':'str',
        'baseline_digest':'str','execution_device':'str','elapsed_seconds':'float'}
    if semantic_study:fields.update(initial_parameter_digest='str',loader_workers='int')
    resource={k:observation[k] for k in ('uid','name','namespace','cluster','cli_path')};resource['route']='native-ui'
    ssh=s['ssh'];ssh={**ssh,'key':str(Path(ssh['key']).resolve())}
    if not Path(ssh['key']).is_file():raise ValueError('existing_profile_ssh_key_required')
    objective=s.get('objective','Real RoadModel GPU two-round event-driven analysis/change/result, full artifacts and cleanup')
    plan={'run_id':s['run_id'],'transport':'ssh','remote_root':'/lpai/sdm-'+s['run_id'],
        'deadline':None,'max_seconds':None,'budget':None,'hourly_rate':s.get('historical_hourly_rate',.59),
        'limit_policy':'user-authorized-unbounded','limit_authorization_ref':grant['source_ref'],
        'resource':resource,'ssh':ssh,'supervised_cleanup':True,'route_evidence':s['resource_observation']['path'],
        'argv':['python3','-B','workload.py'],'inputs':inputs,
        'preflight':{'input_name':'preflight.py','parameters':profile,'timeout_seconds':120},
        'artifacts':['result.json','train.log','run-manifest.json','checkpoint.pth','checkpoint-manifest.json','fixed-subset.json','prepared/preflight.json','prepared/minimal.py'],
        'max_artifact_bytes':512*1024*1024,'max_total_bytes':640*1024*1024,'artifact_transfer_timeout_seconds':300,
        'raw_materials':{'schema':'sdm-material-preparation/v1','objective':objective,
            'project_metrics':['evaluation.validation_loss','evaluation.surface_miou'],
            'roles':[{'id':'result','root':'$artifacts','json_globs':['result.json'],
                'json_fields':{k:{'field':k,'type':v} for k,v in fields.items()}}]}}
    if deferred:
        # Preparation/review can proceed while another owned Study uses the
        # space. The executor still requires its own start request/receipt and
        # refuses to adopt a ready allocation. No existing workload is stopped.
        plan['adopt_existing_ready']=False
    roles={'instances':1,'gpu_type':resources['gpuType'],'gpus_per_instance':None,'allocation_mode':'shared',
        'process_device_limit':1,'cpu_cores_per_instance':float(resources['limit']['cpu']),
        'memory_gib_per_instance':float(resources['limit']['memory'].removesuffix('Gi'))}
    request={'provider':'lpai-devspace','namespace':resource['namespace'],'cluster':resource['cluster'],
        'queue_id':resources['resourceQueue'],'roles':[roles]}
    protocol={'train_samples':train,'validation_samples':val,'batch_size':1,'epochs':epochs,'amp':False,'seed':42}
    if semantic_study:protocol.update(train_workers=profile['loader_workers'],validation_workers=0,
        train_persistent_workers=profile['loader_workers']>0,prefetch_factor=2 if profile['loader_workers'] else 1)
    evidence=[s['resource_observation']['path'],*s['evidence_paths']]
    cfg={'workflow_profile':s.get('workflow_profile','legacy'),
        **({'agent_roles':selected_roles} if selected_roles is not None else {}),
        'proposal_review':s.get('proposal_review',default_policy()),
        **({'external_review':config['external_review']} if 'external_review' in config else {}),
        **({'external_review':s['external_review']} if 'external_review' in s else {}),
        'devspace_execution':{'version':1,'owner':s['owner'],'submission_registry':s['submission_registry'],
        'plan':plan,'authorization':{'confirmed':True,'plan_digest':digest(plan),'source_ref':grant['source_ref']}},
        'devspace_main_workflow':{'version':1,'objective':objective,'protocol':protocol,'evidence_paths':evidence,
            'hypothesis':'A controlled LR-only successor can test small-run sensitivity; no assumed improvement',
            'baseline_ref':'Mounted weights-only baseline; full source/checkpoint protocol retained in profile',
            'resource_request':request,'inventory_observed_at':observation['observed_at'],
            'inventory_valid_until':(observed+dt.timedelta(hours=2)).isoformat(),'inventory_ref':s['resource_observation']['path'],
            'quote_source':'Historical rate only, actual invoice/price not verified; user explicitly unbounded',
            'method_checks':[{'id':name,'status':'supported' if name not in {'uncertainty','total-cost'} else 'unknown',
                'rationale':reason,'evidence_ref':str(root/'roadmodel-spec.json')} for name,reason in [
                ('factor-and-controls','Only LR may change; same real subset/source/initial checkpoint/seed/protocol'),
                ('actual-progress','Actual optimizer hooks and checkpoint step must match epochs times train frame count'),
                ('schedule-and-resume',f'{epochs} epochs, short warmup, independent weights-only starts; not optimizer resume'),
                ('evaluation-and-selection','Fixed held-out frames, actual finite loss/mIoU; no confirmation from screening'),
                ('uncertainty','One seed and small sample, CUDA nondeterminism; improvement not assumed'),
                ('total-cost','Unbounded user authorization; historical rate is not current invoice')]]},
        'task_event_collector':{'version':1,'store':str(root/'events.sqlite3'),'tasks':[{'uid':resource['uid'],'namespace':resource['namespace']}]},
        'task_event_service':{'version':1,'read_only':True,'evidence_root':str(root/'events'),
            'bindings':[{k:resource[k] for k in ('uid','namespace','cluster')}]},
        'devspace_study':{'version':1,'study_id':s['run_id'],'objective':objective,
            'stop_criteria':'Two actual main-DAG rounds, independently event-received continuation, full artifacts, comparison/archive and sameUID stopped',
            'max_rounds':2,'max_attempts_per_round':3,'event_driven_continuation':True,
            'initial_parameters':{'learning_rate':s.get('initial_learning_rate',1e-5)},
            'allowed_variables':{'learning_rate':{'type':'number','minimum':1e-7,'maximum':4e-5}},
            'parameter_flags':{'learning_rate':'--learning-rate'},'result_artifact':'result.json',
            'fixed_fields':['source_commit','data_digest','baseline_digest','train_samples','validation_samples','batch_size','epochs','seed','amp','execution_device','data_kind'],
            'metrics':['validation_loss','surface_miou','nonempty_iou','optimizer_updates'],
            'required_values':{'checkpoint_verified':True,'execution_device':'cuda','optimizer_updates':train*epochs}}}
    wake=s['wakeup'];cfg['task_event_wakeup']={'version':1,'enabled':True,**wake,'project_path':str(root/'project.json'),
        'coordinator_path':str(Path(entry).resolve()),'resource_kind':'devspace',
        'trigger_kinds':['process_terminal','cleanup_pending','review_ready','collection_recovery_exhausted','cleanup_failed','observer_recovery_exhausted']}
    if semantic_study:
        cfg['devspace_main_workflow'].update(
            hypothesis='Final encoder features may recover class distinctions lost by Gaussian semantic mixing; small-run effectiveness remains unknown',
            comparison={'fixed':{**protocol,'learning_rate':profile['learning_rate'],'runtime_version':2},
                        'variables':['semantic_residual'],'scope':'engineering smoke, not epoch-40 recovery or formal eight-class target qualification'})
        cfg['devspace_main_workflow']['method_checks'][0]['rationale']='Only final-feature semantic residual differs; same weights, fixed subset, initialization seed and execution repairs'
        cfg['devspace_study'].update(
            initial_parameters={'semantic_residual':False},
            allowed_variables={'semantic_residual':{'type':'boolean'}},
            parameter_flags={'semantic_residual':'--semantic-residual'},
            initial_plan_review_required=True,
            stop_criteria='At most two independent engineering runs; baseline then reviewed semantic candidate, full evidence and sameUID stop; no quality qualification from a small subset')
        cfg['devspace_study']['fixed_fields'].append('initial_parameter_digest')
        cfg['devspace_study']['fixed_fields'].append('loader_workers')
        plan['artifacts'].extend(['implementation-manifest.json','semantic-runtime.json','resource-samples.json','load-and-optimizer.json'])
        cfg['devspace_execution']['authorization']['plan_digest']=digest(plan)
    from devspace_study import binding,validate
    from proposal_review import settings,pro_binding
    if (pro_binding(cfg) is not None and 'external_review' in config and 'external_review' in s
        and config['external_review']!=s['external_review']):
        raise ValueError('conflicting_pro_bindings')
    if settings(cfg) is None:raise ValueError('new_roadmodel_proposal_review_required')
    cfg['devspace_study']['authorization']={'confirmed':True,'study_digest':binding(cfg),'source_ref':grant['source_ref']}
    atomic_json(root/'project.json',cfg);validate(cfg)
    return {'status':'prepared','project':str(root/'project.json'),'plan_digest':digest(plan),
        'study_digest':binding(cfg),'platform_calls':0,'training_submitted':False,'configuration_generated_by':'installed RoadModel profile'}
