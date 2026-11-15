"""Original ModelRunner code-revision handoff with explicit software replies."""
import copy
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/tests'))
import test_proposal_model_runner as model_fixtures
from test_implementation_feedback import ready,output
from component_payload import ref
import implementation_code_review as code_review
import research_implementation as implementation
import proposal_model_runner
from review_storage import read_json,atomic_json


@pytest.fixture
def reviewed(tmp_path):
    cfg,root,plan,workspace,contract,reviewer=ready(tmp_path)
    output(cfg,root,plan,workspace,contract);implementation.freeze(cfg,root)
    case=model_fixtures.ProposalRunnerTests();case.setUp()
    case.cfg.update(platform_requirements=cfg['platform_requirements'],component_code_review={
        'implemented_profile':ref(root/'implemented-profile.json'),'accepted_plan':cfg['research_implementation']['approved_plan'],
        'execution_contract':cfg['research_implementation']['execution_contract'],
        'request':cfg['research_implementation']['implementation_request']})
    case.cfg['proposal_review']['revision_target']='implementation';case.context()
    try:yield case,cfg,root,workspace,reviewer
    finally:case.doCleanups()


def test_code_revision_hands_off_after_actual_bound_critic_slot_and_replays_without_calls(reviewed,tmp_path):
    case,cfg,root,workspace,reviewer=reviewed;case.verdicts=['revise','approve']
    result=case.gate()
    assert result['status']=='proposal-revision-required' and result['revision_target']=='implementation'
    assert len(case.calls)==2 and not result['execution_authority']
    assert case.gate()['receipt']==result['receipt'] and len(case.calls)==2
    repair=code_review.feedback(case.cfg,case.root,result,workspace,cfg['research_implementation']['native_completion'],tmp_path/'feedback')
    config=copy.deepcopy(cfg);config['research_implementation'].pop('native_completion')
    config['research_implementation']['validation_feedback']=repair['feedback']
    prepared=implementation.prepare(config,tmp_path/'repair',reviewer)
    assert read_json(prepared['job_path'])['validation_feedback']==repair['feedback']
    assert len(case.calls)==2  # read-only validation must not ask another model
    first=read_json(result['receipt'])
    assert first['approved'] is False and first['response']['verdict']=='revise'


def test_verify_only_never_creates_a_model_call_for_missing_review(reviewed):
    case,*unused=reviewed
    result=proposal_model_runner.drive(case.cfg,case.root,verify_only=True)
    assert result['status']=='awaiting-proposal' and case.calls==[]


@pytest.mark.parametrize('verdict',['reject','needs-evidence','stalled'])
def test_non_revision_verdicts_cannot_become_implementation_repair(reviewed,tmp_path,verdict):
    case,cfg,root,workspace,reviewer=reviewed;case.verdicts=[verdict]
    result=case.gate()
    assert len(case.calls)==2
    # These decisions are not a receipt granting a corrective handoff.
    receipt=next(case.root.glob('proposal-review/*/round-01/accepted.json'))
    with pytest.raises(ValueError,match='original_code_revision_receipt_required'):
        code_review.feedback(case.cfg,case.root,{'receipt':str(receipt)},workspace,
            cfg['research_implementation']['native_completion'],tmp_path/'feedback')
    assert len(case.calls)==2


def test_changed_critic_view_or_frozen_source_blocks_replay_before_any_new_call(reviewed):
    case,*unused=reviewed;case.verdicts=['revise']
    result=case.gate();path=Path(result['receipt']).with_name('review-response.json')
    value=read_json(path);value['rationale']='tampered critique';atomic_json(path,value)
    with pytest.raises(ValueError):code_review.replay(case.cfg,case.root,ref(result['receipt']))
    assert len(case.calls)==2


def test_code_feedback_cannot_change_plan_scope_or_original_completed_call(reviewed,tmp_path):
    case,cfg,root,workspace,reviewer=reviewed;case.verdicts=['revise'];result=case.gate()
    repair=code_review.feedback(case.cfg,case.root,result,workspace,cfg['research_implementation']['native_completion'],tmp_path/'feedback')
    selected=copy.deepcopy(cfg['research_implementation'])
    selected['approved_plan']=cfg['research_implementation']['implementation_request']
    with pytest.raises(ValueError,match='bound_same_plan'):code_review.check(repair['feedback'],selected)
    native=Path(cfg['research_implementation']['native_completion']['receipt']['path'])
    changed=read_json(native);changed['status']='unknown';atomic_json(native,changed)
    with pytest.raises(ValueError):code_review.check(repair['feedback'],cfg['research_implementation'])
    assert len(case.calls)==2


def test_unknown_target_cannot_relabel_general_plan_review(reviewed):
    case,*unused=reviewed;case.cfg['proposal_review']['revision_target']='anything'
    case.context()
    with pytest.raises(ValueError,match='explicit_original_implementation_review_target'):case.gate()
    assert case.calls==[]
