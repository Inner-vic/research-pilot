"""Actual local collection, review import and coordinator gates; CPU fixtures only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
import execution_contracts as contracts
import task_pipeline as core
import test_orchestration as fixtures
from project_driver import file_digest
from review_storage import atomic_json


@pytest.fixture(params=['legacy','exploration'])
def f(request):
    value=fixtures.OrchestrationTests();value.setUp()
    value.task['workflow_profile']=request.param
    try:yield value
    finally:value.doCleanups()


def pinned(path,value):
    path.write_text(json.dumps(value),encoding='utf-8')
    return {'path':str(path),'digest':file_digest(path)}


def setup_collector(f,route=None):
    source=pinned(f.root/'snapshot.json',{'schema':'sdm-resource-inventory-export/v1','inventory':f.inventory,
        'source_route':route or {'kind':'official-sdk','operation':'CPU-export-fixture'}})
    driver=Path(contracts.__file__).with_name('collect_inventory_snapshot.py')
    spec={'kind':'local-collector','python':sys.executable,'driver':{'path':str(driver),'digest':file_digest(driver)},
        'inputs':{'snapshot':source},'timeout_seconds':15}
    f.task['orchestration']['record_producers']={'inventory':spec}
    Path(f.records['inventory']['path']).unlink()
    return spec


def test_actual_collector_flows_through_coordinator_and_resume_does_not_repeat(f):
    spec=setup_collector(f);root=f.root/'attempt';seen=[]
    first=core.coordinate(f.task,root,f.driver(seen))
    assert first['status']=='complete' and first['nodes']['26']['production']['kind']=='local-collector'
    raw=(root/'record-producer-inventory-invocation.json').read_bytes();calls=list(seen)
    second=core.coordinate(f.task,root,f.driver(seen))
    assert second['status']=='complete' and calls==seen
    assert (root/'record-producer-inventory-invocation.json').read_bytes()==raw
    assert all(key in core.graph(f.task) for key in ('1','2','3','4','6','8','14','7','13','15','16','30'))


def test_collector_output_recovery_reuses_receipt_after_crash_before_publication(f):
    setup_collector(f);root=f.root/'attempt'
    contracts.produce(f.task,'inventory',root)
    invocation=(root/'record-producer-inventory-invocation.json').read_bytes()
    Path(f.records['inventory']['path']).unlink()
    contracts.produce(f.task,'inventory',root)
    assert (root/'record-producer-inventory-invocation.json').read_bytes()==invocation


@pytest.mark.parametrize('change',['source','driver','request','receipt'])
def test_changed_collection_proof_cannot_be_reused(f,change):
    spec=setup_collector(f);root=f.root/'attempt';contracts.produce(f.task,'inventory',root)
    Path(f.records['inventory']['path']).unlink()
    if change=='source':Path(spec['inputs']['snapshot']['path']).write_text('{}')
    if change=='driver':spec['driver']['digest']='sha256:'+'0'*64
    if change=='request':(root/'record-producer-inventory-request.json').write_text('{}')
    if change=='receipt':(root/'record-producer-inventory.json').write_text('{}')
    with pytest.raises(ValueError):contracts.produce(f.task,'inventory',root)


def test_nonofficial_route_needs_documented_gap_and_blocks_resource_dispatch(f):
    setup_collector(f,{'kind':'browser'});seen=[]
    result=core.coordinate(f.task,f.root/'attempt',f.driver(seen))
    assert result['nodes']['26']['status']=='failed'
    assert not set(seen)&__import__('orchestration').COMPUTE_NODES


def test_raw_collector_does_not_bypass_inventory_semantics(f):
    setup_collector(f);spec=f.task['orchestration']['record_producers']['inventory']
    path=Path(spec['inputs']['snapshot']['path']);value=json.loads(path.read_bytes());value['inventory'].pop('account_ref')
    spec['inputs']['snapshot']=pinned(path,value)
    result=core.coordinate(f.task,f.root/'attempt',f.driver([]))
    assert result['nodes']['26']['status']=='failed'


def review_spec(f,name='method_review'):
    context=f.research if name=='method_review' else __import__('review_collaboration').plan_context(f.task) if name=='plan_review' else f.context
    record=f.method_review if name=='method_review' else f.plan_review if name=='plan_review' else f.response
    value={'schema':'sdm-reviewed-record/v1','record_kind':name,'status':'final','reviewer':'CPU-fixture',
        'mode':'local-agent','source_ref':'synthetic-review','context_digest':__import__('orchestration').value_digest(context),'record':record}
    ref=pinned(f.root/(name+'-review.json'),value)
    f.task['orchestration']['record_producers']={name:{'kind':'review-import','source':ref}}
    Path(f.records[name]['path']).unlink()
    return value,ref


@pytest.mark.parametrize('name',['method_review','plan_review','review_response'])
def test_context_bound_completed_review_import_enters_original_gate(f,name):
    review_spec(f,name);result=core.coordinate(f.task,f.root/'attempt',f.driver([]))
    node={'method_review':'24' if f.task['workflow_profile']=='exploration' else '25','plan_review':'34','review_response':'31'}[name]
    assert result['nodes'][node]['status']=='completed'
    observation=result['nodes'][node]
    production=observation.get('productions',{}).get(name,observation.get('production'))
    assert production['kind']=='review-import'


@pytest.mark.parametrize('field,value',[('status','pending'),('context_digest','wrong'),('mode','pro'),('reviewer','')])
def test_pending_stale_or_wrong_reviewer_import_fails(f,field,value):
    record,ref=review_spec(f);record[field]=value;ref.update(pinned(Path(ref['path']),record))
    result=core.coordinate(f.task,f.root/'attempt',f.driver([]))
    assert result['nodes']['24' if f.task['workflow_profile']=='exploration' else '25']['status']=='failed'


def test_mechanical_collection_cannot_generate_review_or_human_consent(f):
    spec=setup_collector(f)
    for name in ('method_review','confirmation'):
        f.task['orchestration']['record_producers']={name:deepcopy(spec)}
        with pytest.raises(ValueError):core.coordinate(f.task,f.root/name,f.driver([]))


def test_missing_review_semantics_still_block_after_successful_import(f):
    value,ref=review_spec(f);value['record'].pop('method_checks');ref.update(pinned(Path(ref['path']),value))
    result=core.coordinate(f.task,f.root/'attempt',f.driver([]))
    assert result['nodes']['24' if f.task['workflow_profile']=='exploration' else '25']['status']=='failed'


def test_public_project_entry_collects_inventory_and_imports_review(f,tmp_path):
    import run_project_pipeline as entry
    from test_project_driver_bundle import fixture
    _,project=fixture(tmp_path)
    spec=setup_collector(f)
    review_spec(f,'method_review')
    f.task['orchestration']['record_producers']['inventory']=spec
    project['orchestration']=deepcopy(f.task['orchestration'])
    project['workflow_profile']=f.task['workflow_profile']
    result=entry.run(project,tmp_path/'public')['attempts'][0]
    assert result['driver_handshake']['status']=='passed'
    assert result['nodes']['26']['production']['kind']=='local-collector'
    node='24' if f.task['workflow_profile']=='exploration' else '25'
    production=result['nodes'][node].get('productions',{}).get('method_review',result['nodes'][node].get('production'))
    assert production['kind']=='review-import'
    assert result['nodes']['16']['status']=='failed' # CPU fixture does not execute models


def test_interrupted_collector_with_no_receipt_is_not_dispatched_again(f):
    import record_collectors
    from unittest.mock import patch
    setup_collector(f);root=f.root/'attempt'
    with patch('task_pipeline.execute',return_value={'status':'failed','uncertain_effect':True}):
        with pytest.raises(ValueError,match='record_collection_failed'):contracts.produce(f.task,'inventory',root)
    # Explicit missing-receipt started marker uses the normal execute recovery rule.
    request=json.loads((root/'record-producer-inventory-request.json').read_bytes())
    argv=[sys.executable,'-B',str(Path(record_collectors.__file__).resolve()),'--request',str(root/'record-producer-inventory-request.json'),'--receipt',str(root/'record-producer-inventory.json')]
    driver={'argv':argv,'receipt':str(root/'record-producer-inventory.json'),'read_only':True,'timeout_seconds':15}
    atomic_json(root/'record-producer-inventory-started.json',{'driver_digest':core.digest(driver)})
    with patch('task_pipeline.subprocess.Popen',side_effect=AssertionError('redispatched')):
        with pytest.raises(ValueError,match='record_collection_failed'):contracts.produce(f.task,'inventory',root)
