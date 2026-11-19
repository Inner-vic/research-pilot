"""Completed-output correction controls, not real model/ML/platform evidence."""
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/tests'))
from test_component_research_continuation import implementation_fixture,ref
import research_implementation as implementation
from review_storage import atomic_json,read_json
from task_pipeline import digest


def ready(tmp_path):
    cfg,reviewer,plan,workspace,contract=implementation_fixture(tmp_path)
    cfg['research_implementation']['version']=2
    root=tmp_path/'control';implementation.prepare(cfg,root,reviewer)
    native=tmp_path/'native-result.json'
    atomic_json(native,{'job_id':'synthetic-delivery','status':'completed','detail':{
        'terminal':'completed','thread_id':'synthetic-thread','turn_id':'synthetic-turn'}})
    cfg['research_implementation']['native_completion']={'job_id':'synthetic-delivery',
        'receipt':ref(native),'implementation_job':ref(root/'implementation-job.json')}
    return cfg,root,plan,workspace,contract,reviewer


def output(cfg,root,plan,workspace,contract,code=b'raise AssertionError("never execute locally")\n'):
    (workspace/'model.py').write_bytes(code)
    atomic_json(workspace/'parameters.json',{'comparison_identity':contract['comparison_identity'],
        'execution_scope':contract['execution_scope'],'platform_requirements':cfg['platform_requirements']})
    job=read_json(root/'implementation-job.json')
    atomic_json(workspace/'implementation.json',{'schema':'sdm-research-implementation-output/v1',
        'status':'implemented','job_id':job['job_id'],'parent_plan_digest':digest(plan),
        'contract_sha256':cfg['research_implementation']['execution_contract']['sha256'],
        'files':[{'path':name,'sha256':ref(workspace/name)['sha256']} for name in contract['allowed_files']]})


@pytest.mark.parametrize('defect',['syntax','json','digest','missing-output','missing-source','manifest'])
def test_completed_local_defects_return_bound_feedback_and_preserve_failed_bytes(tmp_path,defect):
    cfg,root,plan,workspace,contract,reviewer=ready(tmp_path)
    output(cfg,root,plan,workspace,contract,b'def broken(:\n' if defect=='syntax' else b'raise AssertionError("never run")\n')
    if defect=='json':(workspace/'implementation.json').write_text('{invalid')
    if defect=='digest':
        value=read_json(workspace/'implementation.json');value['files'][0]['sha256']='0'*64
        atomic_json(workspace/'implementation.json',value)
    if defect=='missing-output':(workspace/'implementation.json').unlink()
    if defect=='missing-source':(workspace/'model.py').unlink()
    if defect=='manifest':
        value=read_json(workspace/'implementation.json');value['files']=[None]
        atomic_json(workspace/'implementation.json',value)
    before={p.name:p.read_bytes() for p in workspace.iterdir()}
    result=implementation.freeze(cfg,root)
    assert result['status']=='implementation-correction-required' and not result['execution_authority']
    feedback=read_json(result['feedback']['path'])
    assert feedback['native_completion']==cfg['research_implementation']['native_completion']
    assert {name:Path(row['path']).read_bytes() for name,row in feedback['files'].items()}==before
    assert not (root/'implemented-profile.json').exists()
    # A crash after publication must replay the original validation receipt,
    # even if a later corrective turn has already changed the workspace.
    (workspace/'model.py').write_text('changed after original validation')
    assert implementation.freeze(cfg,root)==result
    assert {name:Path(row['path']).read_bytes() for name,row in feedback['files'].items()}==before


def test_correction_job_binds_same_plan_and_feedback_then_freezes_repaired_output(tmp_path):
    cfg,root,plan,workspace,contract,reviewer=ready(tmp_path)
    output(cfg,root,plan,workspace,contract,b'def broken(:\n')
    failure=implementation.freeze(cfg,root)
    original_job=read_json(root/'implementation-job.json')
    corrected=copy.deepcopy(cfg);selected=corrected['research_implementation'];selected.pop('native_completion')
    selected['validation_feedback']=failure['feedback']
    repair=tmp_path/'repair';prepared=implementation.prepare(corrected,repair,reviewer)
    assert prepared['job_id']!=original_job['job_id']
    assert read_json(prepared['job_path'])['approved_plan']==original_job['approved_plan']
    assert read_json(prepared['job_path'])['validation_feedback']==failure['feedback']
    output(corrected,repair,plan,workspace,contract)
    native=tmp_path/'repair-native.json'
    atomic_json(native,{'job_id':'synthetic-repair-delivery','status':'completed','detail':{
        'terminal':'completed','thread_id':'synthetic-thread','turn_id':'second-turn'}})
    selected['native_completion']={'job_id':'synthetic-repair-delivery','receipt':ref(native),
                                   'implementation_job':ref(repair/'implementation-job.json')}
    value=implementation.freeze(corrected,repair)
    assert value['syntax_verified'] and not value['model_functional_verification']
    assert value['code_review_required'] and not value['execution_authority']
    assert implementation.freeze(corrected,repair)==value


@pytest.mark.parametrize('defect',['unknown','failed','changed-receipt','protected-input','scope','symlink'])
def test_unknown_or_changed_protection_never_becomes_a_correction_prompt(tmp_path,defect):
    cfg,root,plan,workspace,contract,reviewer=ready(tmp_path)
    output(cfg,root,plan,workspace,contract)
    native=Path(cfg['research_implementation']['native_completion']['receipt']['path'])
    if defect in ('unknown','failed','changed-receipt'):
        value=read_json(native);value['status']=defect if defect!='changed-receipt' else 'completed'
        value['detail']['terminal']='unknown';atomic_json(native,value)
        if defect!='changed-receipt':cfg['research_implementation']['native_completion']['receipt']=ref(native)
    if defect=='protected-input':Path(contract['protected_inputs'][0]['path']).write_text('changed')
    if defect=='scope':
        value=read_json(workspace/'parameters.json');value['execution_scope']['namespace']='other'
        atomic_json(workspace/'parameters.json',value)
        value=read_json(workspace/'implementation.json');value['files'][1]['sha256']=ref(workspace/'parameters.json')['sha256']
        atomic_json(workspace/'implementation.json',value)
    if defect=='symlink':
        (workspace/'model.py').unlink()
        try:(workspace/'model.py').symlink_to(tmp_path/'evaluator.py')
        except OSError as error:
            if getattr(error,'winerror',None)==1314:pytest.skip('Host lacks file-symlink creation privilege; no rejection claim')
            raise
    with pytest.raises(ValueError):implementation.freeze(cfg,root)
    assert not (root/'validation-result.json').exists()


def test_feedback_file_tampering_blocks_before_reviewer_or_native_call(tmp_path):
    cfg,root,plan,workspace,contract,reviewer=ready(tmp_path)
    output(cfg,root,plan,workspace,contract,b'def broken(:\n')
    failure=implementation.freeze(cfg,root)
    cfg['research_implementation'].pop('native_completion')
    cfg['research_implementation']['validation_feedback']=failure['feedback']
    Path(failure['feedback']['path']).write_text('{}')
    with patch('proposal_review.gate') as gate,pytest.raises(ValueError):
        implementation.prepare(cfg,tmp_path/'repair')
    gate.assert_not_called()
