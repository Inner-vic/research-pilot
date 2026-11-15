"""Declarative profile compilation only; no SDK, queue, SSH or GPU calls."""
import datetime as dt
import hashlib,json
from pathlib import Path
import pytest
import roadmodel_devspace_profile as p
from task_pipeline import digest


def config(tmp_path):
    cli=tmp_path/'lpai-agent';cli.write_text('fixture-not-executable')
    codex=tmp_path/'codex';codex.write_text('fixture-not-executable')
    key=tmp_path/'key';key.write_text('synthetic fixture key, no credential')
    proof=tmp_path/'retained.txt';proof.write_text('retained CPU fixture')
    observed=tmp_path/'resource.json';now=dt.datetime.now(dt.timezone.utc).isoformat()
    raw={'source':'official-devspaces-get','observed_at':now,'phase':'stopped','name':'fixture-space',
        'uid':'00000000-0000-4000-8000-000000000002','namespace':'fixture','cluster':'fixture','cli_path':str(cli),
        'resources':{'gpuType':'NVIDIA-A100-SXM4-40GB-SHARE','resourceQueue':'fixture-queue','limit':{'cpu':'72','memory':'560Gi'}},
        'mounts':[{'envKey':n} for n in ('LPAI_INPUT_DATASET_0','LPAI_INPUT_MODEL_0','LPAI_INPUT_MODEL_1')]}
    observed.write_text(json.dumps(raw))
    return {'roadmodel_devspace':{'version':1,'run_id':'fixture-study','owner':'fixture',
        'source_root':'/lpai/other-source','commit':'a'*40,'source_url':'https://gitlab.example/user/roadmodel.git',
        'resource_observation':{'path':str(observed),'digest':'sha256:'+hashlib.sha256(observed.read_bytes()).hexdigest()},
        'ssh':{'host':'ssh.example.invalid','port':2200,'user':'root','key':str(key)},
        'submission_registry':str(tmp_path/'registry'),'evidence_paths':[str(proof)],
        'authorization':{'confirmed':True,'source_ref':'CPU fixture grant, not genuine platform authorization','time_limit':None,'budget_limit':None},
        'wakeup':{'thread_id':'00000000-0000-4000-8000-000000000003','codex_home':str(tmp_path),'cli_path':str(codex)}}}


def test_profile_generates_pinned_default_workload_and_main_dag_without_calls(tmp_path):
    cfg=config(tmp_path);out=tmp_path/'project';entry=Path(p.__file__).with_name('run_project_pipeline.py')
    result=p.prepare(cfg,out,entry)
    assert result['platform_calls']==0 and not result['training_submitted']
    compiled=json.loads((out/'project.json').read_bytes());plan=compiled['devspace_execution']['plan']
    assert compiled['workflow_profile']=='legacy'  # No automatic rollout of the unqualified compact DAG.
    assert compiled['proposal_review']['version']==3 and compiled['proposal_review']['backend']=='model'
    assert compiled['devspace_main_workflow']['protocol']['epochs']==20
    assert compiled['devspace_study']['required_values']['optimizer_updates']==32*20
    assert plan['resource']['uid']=='00000000-0000-4000-8000-000000000002'
    assert plan['preflight']['parameters']['source_root']=='/lpai/other-source'
    assert plan['deadline'] is None and plan['budget'] is None
    assert len(plan['inputs'])==5 and (out/'roadmodel-spec.json').is_file()
    assert not (out/'workload.py').exists()
    assert compiled['devspace_main_workflow']['version']==1
    assert compiled['devspace_study']['event_driven_continuation']
    for row in plan['inputs']:assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']
    with pytest.raises(ValueError,match='new_profile_output'):p.prepare(cfg,out,entry)


def test_profile_retains_top_level_role_policy_without_platform_calls(tmp_path):
    from agent_roles import DEFAULT_MODELS
    cfg=config(tmp_path);s=cfg['roadmodel_devspace'];cli=Path(s['wakeup']['cli_path'])
    cfg['agent_roles']={'version':1,'models':dict(DEFAULT_MODELS)}
    s['proposal_review']={'version':4,'backend':'model','context_required':True,
        'model_runner':{'version':1,'backend':'cli','cli_path':str(cli),
            'cli_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),'codex_home':str(tmp_path),
            'cwd':str(tmp_path),'timeout_seconds':30}}
    result=p.prepare(cfg,tmp_path/'roles',Path(p.__file__).with_name('run_project_pipeline.py'))
    compiled=json.loads(Path(result['project']).read_bytes())
    assert compiled['agent_roles']==cfg['agent_roles']
    assert result['platform_calls']==0 and not result['training_submitted']


def test_profile_never_invents_user_approval_or_overwrites_unknown_output(tmp_path):
    cfg=config(tmp_path);cfg['roadmodel_devspace']['authorization']['confirmed']=False
    out=tmp_path/'project'
    with pytest.raises(ValueError,match='user_grant'):p.prepare(cfg,out,Path(p.__file__))
    assert not out.exists()


def test_semantic_profile_pins_new_runtime_and_single_structural_variable(tmp_path):
    cfg=config(tmp_path);cfg['roadmodel_devspace']['semantic_study']=True
    out=tmp_path/'semantic';p.prepare(cfg,out,Path(p.__file__).with_name('run_project_pipeline.py'))
    compiled=json.loads((out/'project.json').read_bytes());plan=compiled['devspace_execution']['plan']
    assert compiled['devspace_study']['allowed_variables']=={'semantic_residual':{'type':'boolean'}}
    assert compiled['devspace_study']['initial_parameters']=={'semantic_residual':False}
    assert {'training_entry_v2.py','semantic_overlay.py','feature_semantic_residual.py','roadmodel_runtime_safety.py'} <= {r['name'] for r in plan['inputs']}
    assert json.loads((out/'roadmodel-spec.json').read_bytes())['runtime_version']==2
    assert 'semantic-runtime.json' in plan['artifacts']


def test_new_profile_uses_model_review_without_requiring_stored_optional_pro(tmp_path):
    cfg=config(tmp_path);cfg['external_review']={'old':'unavailable'}
    cfg['roadmodel_devspace']['external_review']={'another':'unused binding'}
    out=tmp_path/'project';p.prepare(cfg,out,Path(p.__file__).with_name('run_project_pipeline.py'))
    compiled=json.loads((out/'project.json').read_bytes())
    from proposal_review import mode
    assert compiled['proposal_review']['version']==3 and mode(compiled)=='local-agent'


@pytest.mark.parametrize('epochs',[1,10,20,30])
def test_epoch_target_is_pinned_in_workload_protocol_and_actual_update_gate(tmp_path,epochs):
    cfg=config(tmp_path);cfg['roadmodel_devspace']['epochs']=epochs
    out=tmp_path/'project';p.prepare(cfg,out,Path(p.__file__).with_name('run_project_pipeline.py'))
    compiled=json.loads((out/'project.json').read_bytes());spec=json.loads((out/'roadmodel-spec.json').read_bytes())
    assert spec['epochs']==epochs and compiled['devspace_main_workflow']['protocol']['epochs']==epochs
    assert compiled['devspace_study']['required_values']['optimizer_updates']==32*epochs
