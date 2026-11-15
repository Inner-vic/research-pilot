"""Project parser inspection and open review budgets; no platform/model calls."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import pytest
from project_driver import inspect_interface,file_digest
from model_review_bridge import plan


def config(path):
    return {'provider':'project-owned','project_driver':{'path':str(path),'digest':file_digest(path)}}


def test_static_inspection_never_imports_or_executes_legacy_adapter(tmp_path):
    marker=tmp_path/'MUST-NOT-EXIST'
    driver=tmp_path/'legacy.py'
    driver.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("mutation")\nimport argparse\np=argparse.ArgumentParser()\np.add_argument("--node")\np.add_argument("--project")\np.add_argument("--receipt")\n')
    result=inspect_interface(config(driver),tmp_path)
    assert result['status']=='migration-required'
    assert result['missing_explicit_flags']==['--handshake','--handshake-context']
    assert not marker.exists() and not result['adapter_executed']


def test_complete_explicit_flags_still_require_behavioral_verification(tmp_path):
    driver=tmp_path/'driver.py'
    driver.write_text('import argparse\np=argparse.ArgumentParser()\n'+''.join(f'p.add_argument({flag!r})\n' for flag in ('--node','--project','--receipt','--handshake','--handshake-context')))
    result=inspect_interface(config(driver),tmp_path)
    assert result['status']=='requires-behavioral-verification' and not result['missing_explicit_flags']


def test_public_inspection_does_not_create_execution_output(tmp_path):
    driver=tmp_path/'driver.py';driver.write_text('raise RuntimeError("must not execute")')
    project=tmp_path/'project.json';project.write_text(json.dumps(config(driver)))
    output=tmp_path/'run'
    result=subprocess.run([sys.executable,'-B',str(Path(__file__).parents[1]/'run_project_pipeline.py'),
        '--project',str(project),'--output',str(output),'--inspect-driver'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0 and json.loads(result.stdout)['status']=='migration-required'
    assert not output.exists()


@pytest.mark.parametrize('limit',[None,3])
def test_open_review_plan_preserves_stop_and_human_authority_boundaries(limit):
    history=[{'parameters':{'lr':0.1},'fixed':{'data':'retained'}}]
    packet={'history':history}
    decision={'action':'stop-analysis'}
    context={'history':history,'max_rounds':limit,'execution_authorized':False,
        'fixed_conditions':history[-1]['fixed'],'provider':'project-owned','checks':{}}
    result=plan(packet,decision,context)
    assert result['remaining_rounds']==(None if limit is None else 2)
    assert result['readiness']=='blocked' and not result['training_dispatched']
    assert 'execution_not_authorized' in result['blockers'] and 'decision_requests_stop' in result['blockers']


@pytest.mark.parametrize('limit',[False,-1,'unlimited'])
def test_invalid_review_budget_is_explicitly_rejected(limit):
    history=[{'parameters':{},'fixed':{}}]
    with pytest.raises(ValueError,match='positive_or_open_review_round_budget_required'):
        plan({'history':history},{'action':'stop-analysis'},{'history':history,'max_rounds':limit})


def test_compact_review_plan_verifies_full_prefix_instead_of_summary_list(tmp_path):
    from test_history_context import fixture
    from loop_analysis import request
    from copy import deepcopy
    cfg,history,folder=fixture(tmp_path)
    packet=request(history,cfg,folder)
    context={'history':history,'max_rounds':None,'execution_authorized':False,
        'fixed_conditions':history[-1].get('fixed'),'provider':'project-owned','checks':{}}
    result=plan(packet,{'action':'stop-analysis'},context)
    assert 'current_history_does_not_extend_request' not in result['blockers']
    assert result['readiness']=='blocked' and not result['training_dispatched']
    changed=deepcopy(history);changed[49]['parameters']['depth']=999
    mismatch=plan(packet,{'action':'stop-analysis'},{**context,'history':changed})
    assert 'current_history_does_not_extend_request' in mismatch['blockers']
    (folder.parent/'round-99/round-observation.json').write_text('{}')
    with pytest.raises(ValueError,match='observation_digest_mismatch'):
        plan(packet,{'action':'stop-analysis'},context)


def test_stop_review_without_experiment_history_does_not_index_missing_round():
    context={'history':[{'parameters':{},'fixed':{}}],'max_rounds':None,'execution_authorized':False,'checks':{}}
    result=plan({'history':[]},{'action':'stop-analysis'},context)
    assert result['readiness']=='blocked' and not result['training_dispatched']


def test_static_navigation_inventory_has_locations_without_executing(tmp_path):
    marker=tmp_path/'never-created';driver=tmp_path/'legacy.py'
    driver.write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\ndef node(key,cfg):\n    if key=="15":return cfg["training_context"]\n')
    result=inspect_interface(config(driver),tmp_path)
    assert result['literal_dispatch_branches']=={'15':[4]}
    assert result['explicit_context_subscripts']=={'training_context':[4]}
    assert result['static_inventory_limitations'] and not marker.exists()
