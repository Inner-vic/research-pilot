"""Differential responsibility checks; CPU stubs, no real platform/model calls."""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import orchestration as o
import task_pipeline as pipeline
import execution_contracts as contracts
from exploration_workflow import GROUPS,GROUP_MEMBERS,record_refs
from project_driver import file_digest
from review_storage import atomic_json
from test_orchestration import OrchestrationTests


@contextmanager
def fixture(profile,*,sections=False,**flags):
    f=OrchestrationTests();f.setUp()
    try:
        task=deepcopy(f.task);task.update(workflow_profile=profile,**flags)
        if sections:
            values={name:o.needed(task,name) for name in task['orchestration']['records']}
            task['orchestration']['records']=record_refs(f.root/'composed')
            for name,value in values.items():contracts.store_record(task,name,value)
        task['drivers']={key:{'argv':['CPU-only-stub'],'receipt':'unused'}
                         for key,node in pipeline.graph(task).items() if not node.get('builtin')}
        yield f,task
    finally:f.doCleanups()


def run(task,root,seen,failed=None):
    def driver(spec,folder,key,budget):
        seen.append(key)
        return {'status':'failed' if key==failed else 'completed',
                **({'reason':'injected_preparation_failure','uncertain_effect':False} if key==failed else {})}
    return pipeline.coordinate(task,root,driver)


@pytest.mark.parametrize('goal',['execute-and-learn','evaluate-only','baseline-reproduction'])
@pytest.mark.parametrize('code,isolation',[(False,False),(False,True),(True,False),(True,True)])
def test_operational_contracts_and_conditional_policies_are_preserved(goal,code,isolation):
    plain={'objective':goal,'code_change':code,'orchestration':{'source_isolation':isolation}}
    old=pipeline.graph({**plain,'workflow_profile':'legacy'})
    new=pipeline.graph({**plain,'workflow_profile':'exploration'})
    operations={key for key,node in old.items() if not node.get('builtin') or node.get('builtin')=='release'}
    assert operations<={*new}
    for key in operations:
        assert new[key]['policy']==old[key]['policy']
        assert new[key].get('accept_failed_dependencies')==old[key].get('accept_failed_dependencies')
    assert set(old)-set(new)=={member for owner,members in GROUP_MEMBERS.items() for member in members if member!=owner}
    assert all(old[key].get('builtin') for key in set(old)-set(new))
    done=set()
    while len(done)<len(new):
        ready={key for key,node in new.items() if set(node['depends'])<=done}-done
        assert ready,'dependency cycle'
        done.update(ready)


def test_grouped_early_checks_have_no_earlier_external_consumer():
    """Merging must not shift an operation past a check it originally needed."""
    old=pipeline.graph({'objective':'execute-and-learn','code_change':True,'code_review_required':True})
    def ancestors(key):
        seen=set();todo=list(old[key]['depends'])
        while todo:
            parent=todo.pop()
            if parent not in seen:seen.add(parent);todo.extend(old[parent]['depends'])
        return seen
    for members in GROUP_MEMBERS.values():
        tail=members[-1]
        for early in members[:-1]:
            for key,node in old.items():
                if key not in members and early in node['depends']:assert tail in ancestors(key)


@pytest.mark.parametrize('failed',['1','2','3','4','6','8','9','23','10','11','12'])
def test_each_preparation_failure_prevents_submission_in_both_graphs(failed):
    observations=[]
    for profile in ('legacy','exploration'):
        flags={'code_change':failed in {'9','10','11','12'},'code_review_required':failed=='11'}
        with fixture(profile,**flags) as (f,task):
            task['orchestration']['source_isolation']=failed=='23'
            # Isolation can be explicitly required while code is reused.
            task['drivers'].setdefault('23',{'argv':['CPU-only-stub'],'receipt':'unused'})
            seen=[];result=run(task,f.root/'attempt',seen,failed)
            assert result['nodes'][failed]['status']=='failed'
            assert '14' not in seen and '15' not in seen and 'cleanup' not in seen
            observations.append(sorted(seen))
    assert observations[0]==observations[1]


MUTATIONS={'background':{'goal':''},'research':{'question':''},'method_review':{'method_checks':[]},
    'inventory':{'options':[]},'proposal':{'max_seconds':0},'plan_review':{'blocking_gaps':['unresolved fixture']},
    'confirmation':{'confirmed':False},'review_context':{'goal':''},'review_response':{'blocking_gaps':['unresolved fixture']},
    'evidence_consistency':{'status':'needs-revision'},'conclusion_quality':{'status':'needs-revision'},
    'next_action':{'execution_allowed':True},'transition':{'execution_allowed':True}}


@pytest.mark.parametrize('name',list(MUTATIONS))
def test_each_record_negative_keeps_failure_or_wait_and_no_acceptance(name):
    dispositions=[]
    before_compute=name in {'background','research','method_review','inventory','proposal','plan_review','confirmation'}
    for profile in ('legacy','exploration'):
        with fixture(profile) as (f,task):
            f.update(name,**MUTATIONS[name]);seen=[];root=f.root/'attempt'
            result=run(task,root,seen)
            assert ('14' not in seen)==before_compute
            if not before_compute:assert seen.count('cleanup')==1
            semantic_key=next(key for key,value in o.RECORDS.items() if value==name)
            owner=next((key for key,fields in GROUPS.items() if name in fields),semantic_key) if profile=='exploration' else semantic_key
            assert result['nodes'].get(owner,{}).get('status')!='completed'
            accepted=root/'record-groups'/owner/'accepted.json'
            if accepted.exists():assert name not in json.loads(accepted.read_bytes())['records']
            dispositions.append((result['status'],'14' in seen,seen.count('cleanup')))
    assert dispositions[0]==dispositions[1]


@pytest.mark.parametrize('profile',['legacy','exploration'])
@pytest.mark.parametrize('sections',[False,True])
def test_all_configured_bundle_producers_publish_once_and_resume_without_operations(profile,sections):
    with fixture(profile,sections=sections) as (f,task):
        values={name:o.needed(task,name) for name in task['orchestration']['records']}
        source=f.root/'source-bundle.json';atomic_json(source,{'schema':'sdm-evidence-bundle/v1','records':values})
        spec={'kind':'bundle-projection','source':{'path':str(source),'digest':file_digest(source)}}
        producers=task['orchestration']['record_producers']={name:deepcopy(spec) for name in values if name!='confirmation'}
        if sections:
            for path in {ref['path'] for ref in task['orchestration']['records'].values()}:Path(path).unlink()
            contracts.store_record(task,'confirmation',values['confirmation'])
        else:
            for name in producers:Path(task['orchestration']['records'][name]['path']).unlink()
        seen=[];root=f.root/'attempt'
        with patch.object(contracts,'produce',wraps=contracts.produce) as called:
            result=run(task,root,seen)
            assert all(row['status'] in {'completed','not-applicable','recorded'} for row in result['nodes'].values())
            assert sorted(call.args[1] for call in called.call_args_list)==sorted(producers)
        previous=list(seen)
        with patch.object(contracts,'produce',side_effect=AssertionError('accepted producer repeated')):
            assert run(task,root,seen)['status']==result['status']
        assert seen==previous
        assert json.loads((root/'recommendation.json').read_bytes())==result['nodes']['20']['recommendation']
        decision=result['nodes']['20']['decision'] if profile=='exploration' else result['nodes']['33']['decision']
        assert json.loads((root/'decision.json').read_bytes())==decision


@pytest.mark.parametrize('profile',['legacy','exploration'])
@pytest.mark.parametrize('sections',[False,True])
@pytest.mark.parametrize('missing',['method_review','review_response','conclusion_quality','transition'])
def test_wait_then_fill_keeps_accepted_prefix_and_never_repeats_compute(profile,sections,missing):
    with fixture(profile,sections=sections) as (f,task):
        saved=o.needed(task,missing);ref=task['orchestration']['records'][missing];path=Path(ref['path'])
        if sections:
            value=json.loads(path.read_bytes());del value['records'][missing];atomic_json(path,value)
        else:path.unlink()
        seen=[];root=f.root/'attempt';first=run(task,root,seen)
        assert first['status']=='awaiting-evidence'
        assert ('14' not in seen)==(missing=='method_review')
        if missing!='method_review':assert seen.count('cleanup')==1
        previous=list(seen);contracts.store_record(task,missing,saved)
        second=run(task,root,seen)
        assert second['status']=='complete'
        if missing!='method_review':assert seen==previous+['21']
        else:
            assert all(seen.count(key)==previous.count(key) for key in ('1','2','3','4','6') if key in previous)
        assert seen.count('14')==1 and seen.count('cleanup')==1


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_accepted_section_cannot_change_while_another_record_is_pending(profile):
    with fixture(profile,sections=True) as (f,task):
        method_ref=task['orchestration']['records']['method_review'];path=Path(method_ref['path'])
        value=json.loads(path.read_bytes());del value['records']['method_review'];atomic_json(path,value)
        seen=[];root=f.root/'attempt';assert run(task,root,seen)['status']=='awaiting-evidence'
        value=json.loads(path.read_bytes());value['records']['research']['hypothesis']='tampered after acceptance';atomic_json(path,value)
        previous=list(seen)
        expected='historical_grouped_record_changed' if profile=='exploration' else 'historical_receipt_changed'
        with pytest.raises(ValueError,match=expected):run(task,root,seen)
        assert '14' not in seen and seen==previous


def test_old_experimental_revision_is_not_silently_migrated(tmp_path):
    with fixture('exploration') as (f,task):
        task=pipeline.current_task(task,tmp_path)
        assert task['exploration_revision']==2
        old={key:value for key,value in task.items() if key!='exploration_revision'}
        (tmp_path/'events.jsonl').write_text(json.dumps({'task_digest':pipeline.digest(old),'node':'22','result':{'status':'completed'}})+'\n')
        with pytest.raises(ValueError,match='revision_changed_resume_frozen'):
            pipeline.coordinate(old,tmp_path,lambda *args:pytest.fail('old graph executed'))


@pytest.mark.parametrize('corrupt',[None,[],{'research':{}},{'research':{'receipt':None}}])
def test_corrupt_accepted_group_state_halts_before_any_driver(corrupt):
    with fixture('exploration') as (f,task):
        path=Path(task['orchestration']['records']['method_review']['path']);path.unlink()
        seen=[];root=f.root/'attempt'
        assert run(task,root,seen)['status']=='awaiting-evidence'
        accepted=root/'record-groups/24/accepted.json'
        atomic_json(accepted,{'task_digest':pipeline.digest(pipeline.current_task(task,root)),'records':corrupt})
        previous=list(seen)
        from exploration_workflow import RecordGroupIntegrityError
        with pytest.raises(RecordGroupIntegrityError):run(task,root,seen)
        assert seen==previous and '14' not in seen


@pytest.mark.parametrize('profile',['legacy','exploration'])
@pytest.mark.parametrize('sections',[False,True])
def test_work_package_production_replaces_only_declared_preparation_calls(profile,sections):
    import work_packages as packages
    with fixture(profile,sections=sections,code_change=True) as (f,task):
        record_names=set()
        for package in ('task-contract','experiment-plan','implementation','evaluation','result-review','action-choice'):
            names=packages.MATERIAL_RECORDS.get(package,())
            records={name:o.needed(task,name) for name in names};record_names.update(names)
            refs=['CPU-only-material:'+package]
            value={'schema':'sdm-work-package-material/v1','work_package':package,'evidence_refs':refs,
                'checks':{key:{'finding':'CPU fixture preparation inspected','evidence_refs':refs} for key in packages.MATERIAL_CHECKS[package]},
                'records':records}
            source=f.root/(package+'-material.json');atomic_json(source,value)
            task.setdefault('work_package_materials',{})[package]={'path':str(source),'digest':file_digest(source)}
            for key in packages.MATERIAL_CHECKS[package]:task['drivers'].pop(key,None)
        if sections:
            for path in {task['orchestration']['records'][name]['path'] for name in record_names}:
                value=json.loads(Path(path).read_bytes())
                for name in record_names:value['records'].pop(name,None)
                atomic_json(path,value)
        else:
            for name in record_names:Path(task['orchestration']['records'][name]['path']).unlink()
        seen=[];root=f.root/'attempt';result=run(task,root,seen)
        assert not {'1','2','6','9','18'}&set(seen)
        assert {'10','12','14','7','13','15','16'}<=set(seen)
        assert all(result['nodes'][key]['status']=='completed' for key in ('1','2','6','9','18'))
        assert seen.count('cleanup')==1
        previous=list(seen);assert run(task,root,seen)['status']==result['status'];assert seen==previous


@pytest.mark.parametrize('profile',['legacy','exploration'])
def test_automatic_confirmation_still_rejected_before_any_driver(profile):
    with fixture(profile) as (f,task):
        path=f.root/'fake-consent.json';atomic_json(path,{'schema':'sdm-evidence-bundle/v1','records':{'confirmation':f.confirmation}})
        task['orchestration']['record_producers']={'confirmation':{'kind':'bundle-projection','source':{'path':str(path),'digest':file_digest(path)}}}
        seen=[]
        with pytest.raises(ValueError,match='confirmation_cannot_have_automatic_producer'):run(task,f.root/'attempt',seen)
        assert not seen
