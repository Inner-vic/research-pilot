"""Pruned DAG and real CPU continuation, never a platform/model qualification."""
import json
from pathlib import Path
import pytest
from task_pipeline import current_task,graph
import devspace_study as study
from test_devspace_main_workflow import fixture,until_wait,result_reply
from test_proposal_review import complete_local


def task(**overrides):return current_task({'objective':'execute-and-learn','workflow_profile':'exploration',**overrides})


def test_repaired_profile_keeps_original_preparation_and_release_dependencies():
    nodes=graph(task())
    assert {'1','2','3','4','6','8','9','23','14','7','13','15','16','18','30','31','21'}<=set(nodes)
    assert nodes['24']['builtin']=='record-group' and nodes['31']['builtin']=='record-group'
    assert nodes['30']['depends']==['18'] and '31' not in nodes['30']['depends']
    assert all(dep in nodes for node in nodes.values() for dep in node['depends'])
    assert len(graph(current_task({'objective':'execute-and-learn'})))==34


@pytest.mark.parametrize('options',[
    {'objective':'evaluate-only'},
    {'objective':'baseline-reproduction'},
    {'code_change':True},
    {'code_change':True,'code_review_required':True},
    {'baseline_required':True,'robustness_required':True},
])
def test_original_optional_and_not_applicable_policies_are_preserved(options):
    nodes=graph(task(**options))
    original=graph({**task(**options),'workflow_profile':'legacy'})
    assert all(nodes[key]['policy']==original[key]['policy'] for key in ('5','9','10','11','12','15','16','17','23'))
    assert all(dep in nodes for node in nodes.values() for dep in node['depends'])


def test_new_task_uses_two_bundles_not_thirteen_manual_record_files(tmp_path):
    normalized=current_task(task(),tmp_path)
    refs=normalized['orchestration']['records']
    assert len({row['path'] for row in refs.values()})==2
    assert all(row['section']==name for name,row in refs.items())


def test_tampered_preparation_record_does_not_skip_owned_allocation_cleanup(tmp_path):
    from test_orchestration import OrchestrationTests
    import task_pipeline as pipeline
    fixture=OrchestrationTests();fixture.setUp()
    try:
        cfg=current_task({**fixture.task,'workflow_profile':'exploration'},tmp_path)
        record=tmp_path/'changed-plan.json';record.write_text('changed bytes')
        events=[{'task_digest':pipeline.digest(cfg),'node':'22','result':{'status':'completed',
            'source_receipts':[{'path':str(record),'digest':'sha256:old'}]}},
            {'task_digest':pipeline.digest(cfg),'node':'14','result':{'status':'completed'}}]
        (tmp_path/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\n')
        calls=[]
        def cleanup(driver,root,key,budget):calls.append(key);return {'status':'completed'}
        with pytest.raises(ValueError,match='grouped_record_changed'):pipeline.coordinate(cfg,tmp_path,cleanup)
        assert calls==['cleanup']
    finally:fixture.doCleanups()


def accept_child(state):
    cfg=json.loads(Path(state['execution_project']).read_bytes())
    assert complete_local(cfg,Path(state['execution_root']))['status']=='proposal-approved'


def test_two_actual_cpu_rounds_preserve_preparation_groups_and_resume_without_relaunch(tmp_path):
    cfg,root=fixture(tmp_path);cfg.update(workflow_profile='exploration',proposal_review={'version':1})
    cfg['devspace_study']['authorization']['study_digest']=study.binding(cfg)
    state=study.execution(cfg,root)
    assert state['status']=='awaiting-proposal' and not (tmp_path/'remote').exists()
    accept_child(state);state=until_wait(cfg,root)
    assert state['status']=='awaiting-review' and state['stop_verified']
    result_reply(state);state=until_wait(cfg,root)
    assert state['active_round']==2 and state['status']=='awaiting-proposal'
    accept_child(state);state=until_wait(cfg,root);assert state['status']=='awaiting-review'
    result_reply(state,'stop');done=until_wait(cfg,root)
    assert done['status']=='completed' and len(done['history'])==2
    for i in (1,2):
        folder=root/f'round-{i:02d}/main'
        summary=json.loads((folder/'summary.json').read_bytes())
        assert len(summary['work_packages'])==5
        assert all(summary['nodes'][key]['status']=='completed' for key in ('1','2','3','4','6','8','13','15','16','18','19','20','21','24','30','31','34'))
        assert len(list((folder/'orchestration-records').glob('*.json')))==2
    before=[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]
    assert study.execution(cfg,root)['status']=='completed'
    assert before==[(tmp_path/f'remote/r{i:02d}/process.json').read_bytes() for i in (1,2)]
    # Accepted grouped document cannot be changed unnoticed on resume.
    path=root/'round-01/main/orchestration-records/exploration-plan.json'
    value=json.loads(path.read_bytes());value['records']['background']['goal']='tampered';path.write_text(json.dumps(value))
    import devspace_main_workflow as main
    child=json.loads((root/'round-01/project.json').read_bytes())
    with pytest.raises(ValueError,match='record_changed|grouped_record_changed'):
        main.execution(child,root/'round-01')
