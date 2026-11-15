"""Public fresh-study preparation. Task writes occur only in the resident.

Inputs bind an authorized initial plan, existing component request, data/split,
source and baseline model. An explicit dataset_input may prepare one pinned
official data asset; its writes are journaled separately. No local ML, hidden
target or automatic scope change.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from component_payload import contract,ref,task_request,verify_request_payload
from component_research import reference
from lpai_manager import Manager
from research_replan import once
from research_runtime import freeze
from review_storage import atomic_json,locked,read_json
from task_pipeline import digest


def training_limits(spec):
    """Validate explicitly narrowed epoch authority before any side effect."""
    limits=spec.get('training_limits',{'min_epochs':10,'max_epochs':30,'default_epochs':20})
    if (not isinstance(limits,dict) or set(limits)!={'min_epochs','max_epochs','default_epochs'}
            or any(type(v) is not int for v in limits.values())
            or not 10<=limits['min_epochs']<=limits['default_epochs']<=limits['max_epochs']<=30
            or type(spec['training'].get('epochs')) is not int
            or not limits['min_epochs']<=spec['training']['epochs']<=limits['max_epochs']):
        raise ValueError('explicit_training_within_delegated_epoch_limits_required')
    return copy.deepcopy(limits)


def prepare(spec_path,destination,store,workspace,output):
    spec=read_json(spec_path)
    required={'schema','project_id','source_ref','objective','template','manager_config','request_template',
        'baseline_source','baseline_model','comparison_identity','training','target','workload'}
    if (set(spec)-{'prior_diagnoses','dataset_input','training_limits','recovery_exercise'}!=required or spec['schema']!='sdm-research-initialization/v1'
            or not re.fullmatch(r'[a-z][a-z0-9-]{3,60}',spec['project_id'])
            or not spec['source_ref'] or not spec['objective']):
        raise ValueError('explicit_authorized_initial_study_required')
    limits=training_limits(spec)
    output=Path(output).resolve();store=Path(store).resolve();workspace=Path(workspace).resolve()
    bound=digest({'spec':ref(spec_path),'destination':str(Path(destination).resolve()),
        'store':str(store),'workspace':str(workspace),'output':str(output)})
    journal=output.with_suffix('.initialization.json')
    with locked(output.parent,'.'+output.name+'.initialization.lock','initialization_already_owned'):
        if journal.exists():
            receipt=read_json(journal)
            if receipt['binding']!=bound:raise ValueError('initialization_inputs_changed')
            if output.exists():
                if ref(output)!=receipt.get('output'):raise ValueError('initialized_configuration_changed')
                return {'status':'already-prepared','config':str(output),'platform_writes':0,'model_calls':0}
        else:
            if output.exists() or store.exists() or workspace.exists():raise ValueError('fresh_study_roots_required')
            if store.is_relative_to(workspace):raise ValueError('control_state_outside_model_workspace_required')
            manifest=freeze(destination)
            once(journal,{'binding':bound,'status':'frozen','manifest':manifest})
        receipt=read_json(journal);manifest=receipt['manifest']
        cfg=copy.deepcopy(read_json(reference(spec['template'])))
        cfg.update(project_id=spec['project_id'],root=str(store),cwd=str(workspace),
            **{k:v for k,v in manifest.items() if k!='schema'})
        workspace.mkdir(parents=True,exist_ok=True)
        cfg['research_context']['root']=str(output.parent/'context')
        cfg.pop('recovery_exercise',None);cfg.pop('component_source',None);cfg.pop('source_argv',None)
        cfg['runtime_readiness']={'version':1};cfg['completion_shutdown']={'version':1}
        cfg['continuation_policy']={'version':1}
        if spec.get('recovery_exercise'):
            from research_recovery import exercise_settings
            cfg['recovery_exercise']=exercise_settings(spec['recovery_exercise'])
        pipeline=cfg['component_pipeline']
        pipeline['proposal_review']['model_runner']['sdk_recovery']={'version':2}
        if pipeline['proposal_review'].get('evidence_policy')=={'version':1,'mode':'registered-supplements'}:
            pipeline['proposal_review']['evidence_policy']={'version':2,'mode':'registered-supplements'}
        for name in ('verified_history','observed_task_uids','initial_science_plan','initial_submission',
                'previous_implementation_feedback','resume_approved_plan','resume_result','additional_evidence'):
            pipeline.pop(name,None)
        pipeline.update(version=4,minimum_rounds=1,minimum_revisions=0,objective=spec['objective'],
            baseline_source=spec['baseline_source'],workload=spec['workload'],implementation_feedback={'version':2})
        for value in spec.get('prior_diagnoses',[]):
            diagnosis=read_json(reference(value))
            if (diagnosis.get('schema')!='sdm-component-diagnosis-ready/v1'
                    or diagnosis.get('valid_training_round') is not False or not diagnosis.get('uid')):
                raise ValueError('prior_diagnosis_must_be_actual_failed_attempt')
            pipeline.setdefault('observed_task_uids',[]).append(diagnosis['uid'])
            pipeline.setdefault('additional_evidence',[]).append(value)
        review_workspace=output.parent/'review-workspace';review_workspace.mkdir(exist_ok=True)
        pipeline['proposal_review']['model_runner']['cwd']=str(review_workspace)
        manager_cfg=read_json(reference(spec['manager_config']));manager=Manager(manager_cfg)
        request=read_json(reference(spec['request_template']))
        asset_operations=0
        if spec.get('dataset_input'):
            data=spec['dataset_input'];input_spec=read_json(reference(data['spec']))
            if (set(data)!={'spec','state'} or not Path(data['state']).is_absolute()
                    or input_spec['namespace']!=manager_cfg['namespace']
                    or input_spec['sha256']!=spec['comparison_identity']['source_sha256']):
                raise ValueError('dataset_input_must_match_authorized_study_identity')
            operation=subprocess.run([manager_cfg['sdk_python'],'-B',str(Path(cfg['runtime_root'])/'lpai_dataset_input.py'),
                '--spec',str(reference(data['spec'])),'--state',data['state']],capture_output=True,encoding='utf-8',
                timeout=1200,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if operation.returncode:raise ValueError('official_dataset_input_requires_original_journal_reconciliation')
            prepared=json.loads(operation.stdout)
            if prepared['status']!='verified' or prepared['sha256']!=input_spec['sha256']:
                raise ValueError('verified_dataset_input_required')
            asset_operations=prepared['new_asset_operations']
            request['data']['lpai_input_dataset']=[prepared['input']]
            pipeline.setdefault('additional_evidence',[]).append(ref(data['state']))
        comparison=spec['comparison_identity'];pipeline['comparison_identity']=comparison
        scope={'provider':'lpai','namespace':manager_cfg['namespace'],'owner':manager_cfg['owner'],'component_uid':request['uid']}
        delegation={'schema':'sdm-research-delegation/v1','project_id':spec['project_id'],
            'source_ref':spec['source_ref'],'allow_successor_submission':True,'namespace':manager_cfg['namespace'],
            'owner':manager_cfg['owner'],'component_uid':request['uid'],'comparison_identity':comparison,
            'training_limits':limits,
            'resources':'Preserve original owned component, image, queue, mounts and declared roles/resources. All ML remote. '+
                json.dumps([{'resource':role['resource'],'replica':role.get('replica',1)} for role in request['data']['lpai_task']['roles']],sort_keys=True)}
        root=output.parent/'initial';root.mkdir(exist_ok=True)
        initial_contract=contract(spec['baseline_source'],root/'protected',comparison,manager_cfg,request['uid'],
            training_limits=delegation['training_limits'],next_round=1,workload=spec['workload'])
        profile_root=root/'profile';profile_root.mkdir(exist_ok=True)
        model=reference(spec['baseline_model']).read_bytes()
        from component_event_source import publish_bytes
        publish_bytes(profile_root/'model.py',model)
        parameters={'comparison_identity':comparison,'execution_scope':scope,
            'platform_requirements':manager_cfg['platform_requirements'],'training':spec['training']}
        once(profile_root/'parameters.json',parameters)
        files={name:ref(profile_root/name)['sha256'] for name in ('model.py','parameters.json')}
        candidate,expected,payload=task_request({'root':str(profile_root),'files':files},initial_contract,
            request,spec['project_id'],1)
        verify_request_payload(candidate,payload)
        request_path=root/'request.json';once(request_path,candidate)
        plan_path=root/'manager-plan.json'
        # A repeated GET/preflight is safe; never writes a platform object here.
        if not plan_path.exists():once(plan_path,manager.plan(candidate))
        plan=read_json(plan_path)
        if plan['request']!={**candidate,'component_settings':plan['request']['component_settings']}:
            raise ValueError('initial_manager_request_changed')
        once(root/'normalized-request.json',plan['request'])
        once(output.parent/'delegation.json',delegation);pipeline['delegation']=ref(output.parent/'delegation.json')
        confirmed={'schema':'sdm-confirmed-initial-study/v1','source_ref':spec['source_ref'],
            'objective':spec['objective'],'comparison_identity':comparison,'target':spec['target'],
            'baseline_source':spec['baseline_source'],'baseline_model':spec['baseline_model'],
            'training':spec['training'],'payload_manifest':payload,
            'scope':'Agent-prepared study under standing user delegation; not a fabricated UI confirmation or platform approval.'}
        once(root/'confirmed-plan.json',confirmed)
        authorization={'schema':'lpai-manager-authorization/v1','plan_sha256':'sha256:'+ref(plan_path)['sha256'],
            'direct_user_confirmation':True,'source_ref':spec['source_ref'],'delegation':pipeline['delegation'],
            'actor':'Codex under explicit standing research delegation, no new user click claimed'}
        once(root/'authorization.json',authorization)
        pipeline.update(initial_request=ref(root/'normalized-request.json'),initial_source={
            'version':3,'transport':manager_cfg['component_transport'],'binding':'component-source:'+ref(plan_path)['sha256'],
            'manager_config':spec['manager_config'],'plan':ref(plan_path),'expected':expected,
            'phase_intervals_seconds':{'queued':60,'running':30,'finishing':15,'terminal':300,'unknown':60},
            'result_identity':{'version':1,'mode':'original-manager-native-config'}},
            initial_submission={'version':1,'authorization':ref(root/'authorization.json'),'confirmed_plan':ref(root/'confirmed-plan.json')},
            decision_policy={'version':2,'target':spec['target'],'budget':{'known_task_uids':[]},
                'termination':{'mode':'until-target','source_ref':spec['source_ref']}})
        code='import sys,json;sys.path.insert(0,sys.argv[1]);from research_event_service import configuration;configuration(json.load(sys.stdin))'
        checked=subprocess.run([sys.executable,'-I','-S','-B','-c',code,cfg['runtime_root']],
            input=json.dumps(cfg),capture_output=True,encoding='utf-8',timeout=60,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if checked.returncode:raise ValueError('initialized_frozen_validation_failed:'+checked.stderr.splitlines()[-1][:180])
        atomic_json(output,cfg);receipt.update(status='prepared',output=ref(output));atomic_json(journal,receipt)
        return {'status':'prepared','config':str(output),'platform_writes':asset_operations,
            'platform_task_writes':0,'asset_operations_started':asset_operations,'model_calls':0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('spec','destination','store','workspace','output'):parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args();print(json.dumps(prepare(args.spec,args.destination,args.store,args.workspace,args.output)))
