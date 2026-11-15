"""Cross-profile reachability and ownership; all driver results are CPU simulations."""
from copy import deepcopy
import itertools,json
from pathlib import Path
from unittest.mock import patch
import pytest
import task_pipeline as core
from node_registry import registry
from trigger_matrix import inspect
import test_orchestration as fixtures
import run_project_pipeline as entry
from test_project_driver_bundle import fixture as bundle_fixture
from project_driver import file_digest


def profiles():
    for objective,code,isolate,baseline,robust in itertools.product(
        ('execute-and-learn','evaluate-only','baseline-reproduction'),(False,True),(False,True),(False,True),(False,True)):
        if objective=='baseline-reproduction' and robust:continue
        yield {'objective':objective,'code_change':code,'baseline_required':baseline,'robustness_required':robust,'isolate':isolate}


@pytest.mark.parametrize('profile',list(profiles()))
def test_every_supported_profile_has_reachable_unique_nodes_and_executable_cpu_journey(tmp_path,profile):
    f=fixtures.OrchestrationTests();f.setUp()
    try:
        task=deepcopy(f.task);task.update({k:v for k,v in profile.items() if k!='isolate'})
        task['orchestration']['source_isolation']=profile['isolate']
        if profile['isolate']:task['drivers']['23']={'argv':['CPU-workspace-fixture'],'receipt':'unused'}
        nodes=core.graph(task);resolved=set()
        while len(resolved)<34:
            ready={k for k,n in nodes.items() if set(n['depends'])<=resolved}-resolved
            assert ready,'unreachable nodes or cycle'
            resolved.update(ready)
        seen=[];result=core.coordinate(task,tmp_path/'attempt',f.driver(seen))
        assert result['status']=='complete'
        for k,node in nodes.items():
            observed=result['nodes'][k]
            assert observed['status']==('not-applicable' if node['policy']=='not-applicable' else 'completed')
            if node.get('builtin') or node['policy']=='not-applicable':assert k not in seen
            else:assert seen.count(k)==1
        assert seen.count('cleanup')==1
        assert len({r['display_id'] for r in inspect(task,nodes)['nodes']})==34
    finally:f.doCleanups()


@pytest.mark.parametrize('ignored',['19','15'])
def test_handshake_uses_graph_selected_operations_not_ignored_argv_entries(tmp_path,ignored):
    _,config=bundle_fixture(tmp_path);f=fixtures.OrchestrationTests();f.setUp()
    try:
        config['orchestration']=deepcopy(f.task['orchestration']);captured=[]
        with patch.object(entry,'coordinate',side_effect=lambda task,root:captured.append(task) or {'status':'blocked','nodes':{}}):
            entry.run(config,tmp_path/'run')
        task=captured[0];task['drivers'][ignored]={'argv':['MUST-NOT-RUN'],'receipt':'unused'}
        result=core.coordinate(task,tmp_path/'run/attempt-01')
        assert result['driver_handshake']['status']=='passed'
        assert result['nodes'][ignored]['status']==('completed' if ignored=='19' else 'not-applicable')
        assert not (tmp_path/'run/attempt-01'/f'{ignored}-started.json').exists()
    finally:f.doCleanups()


@pytest.mark.parametrize('malformed',[[],{'reviewer':[]},{'source_ref':{}}])
def test_malformed_review_import_is_record_failure_not_uncaught_exception(tmp_path,malformed):
    from test_record_collectors import review_spec,pinned
    f=fixtures.OrchestrationTests();f.setUp()
    try:
        value,ref=review_spec(f)
        if isinstance(malformed,dict):value.update(malformed)
        else:value=malformed
        ref.update(pinned(Path(ref['path']),value))
        result=core.coordinate(f.task,tmp_path/'attempt',f.driver([]))
        assert result['nodes']['25']['status']=='failed'
        assert result['nodes'].get('14',{}).get('status') in {None,'skipped'}
    finally:f.doCleanups()


def test_malformed_collection_route_is_explicit_failure_before_compute(tmp_path):
    from test_record_collectors import setup_collector,pinned
    f=fixtures.OrchestrationTests();f.setUp()
    try:
        spec=setup_collector(f);path=Path(spec['inputs']['snapshot']['path']);value=json.loads(path.read_bytes());value['source_route']=[]
        spec['inputs']['snapshot'].update(pinned(path,value));seen=[]
        result=core.coordinate(f.task,tmp_path/'attempt',f.driver(seen))
        assert result['nodes']['26']['status']=='failed'
        assert not set(seen)&__import__('orchestration').COMPUTE_NODES
    finally:f.doCleanups()
