"""Condition-driven A/B loop. Model calls and project evidence are synthetic."""
from pathlib import Path
import pytest
import proposal_review as r
from review_storage import atomic_json,read_json
from task_pipeline import digest
from test_proposal_review import start,answer_local,proposal


def config():return {'proposal_review':{'version':3,'backend':'model','context_required':True}}


def answer(wait,verdict,issue=None,resolved=()):
    body=answer_local(wait,verdict)
    body['challenges']=([{'id':issue,'severity':'major','question':'Check a distinct unresolved assumption',
        'basis':'Synthetic configuration evidence','resolution_criterion':'Explicit comparable counterfactual'}] if issue else [])
    body['resolutions']=[{'challenge_id':x,'status':'resolved','reason':'Rechecked the revised comparison'} for x in resolved]
    atomic_json(Path(wait['response_path']),body)


def revise(cfg,root,revision,issue):
    value=proposal(cfg,revision);value['hypothesis']=f'A new falsifiable comparison with revision {revision}'
    value['addresses']=[{'challenge_id':issue,'reason':'Changed the comparison and its outcome rule'}]
    value['advice_dispositions']=[{'challenge_id':issue,'decision':'modify','applicability':'Matches the fixture recipe',
                                 'reason':'Use a comparable counterfactual'}]
    atomic_json(root/'proposal.json',value)


def test_ready_plan_can_exit_on_first_review_without_inventing_objections(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer(wait,'approve')
    result=r.gate(cfg,tmp_path)
    assert result['status']=='proposal-approved' and result['review_rounds']==1
    assert r.gate(cfg,tmp_path)['receipt']==result['receipt'] and not result['execution_authority']


def test_substantive_review_can_continue_beyond_old_twelve_turn_limit(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);issues=[]
    for number in range(1,15):
        issue=f'issue-{number}';answer(wait,'revise',issue,resolved=issues)
        assert r.gate(cfg,tmp_path)['status']=='proposal-revision-required';issues.append(issue)
        revise(cfg,tmp_path,number+1,issue);wait=r.gate(cfg,tmp_path)
        assert wait['status']=='awaiting-proposal-review'
    answer(wait,'approve',resolved=issues)
    result=r.gate(cfg,tmp_path)
    assert result['status']=='proposal-approved' and result['review_rounds']==15


def test_no_revision_waits_and_metadata_only_changes_cannot_spin_review(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);answer(wait,'revise','comparison');r.gate(cfg,tmp_path)
    assert r.gate(cfg,tmp_path)['status']=='awaiting-proposal-revision'
    value=read_json(tmp_path/'proposal.json');value['revision']=2;value['proposer']['call_ref']='synthetic-only/a-2'
    atomic_json(tmp_path/'proposal.json',value)
    assert r.gate(cfg,tmp_path)['status']=='proposal-review-stalled'
    revise(cfg,tmp_path,2,'comparison')
    assert r.gate(cfg,tmp_path)['status']=='awaiting-proposal-review'


@pytest.mark.parametrize(('verdict','status'),[('reject','proposal-rejected'),('stalled','proposal-review-stalled')])
def test_unsupported_plan_or_unresolvable_disagreement_pauses_without_resources(tmp_path,verdict,status):
    cfg=config();wait=start(cfg,tmp_path);answer(wait,verdict)
    result=r.gate(cfg,tmp_path)
    assert result['status']==status and not result['execution_authority']
    assert r.gate(cfg,tmp_path)['status']==status


def test_missing_evidence_waits_then_registered_supplement_resumes_same_loop(tmp_path):
    cfg=config();wait=start(cfg,tmp_path);packet=read_json(wait['request_path']);answer(wait,'needs-evidence')
    assert r.gate(cfg,tmp_path)['status']=='awaiting-proposal-evidence'
    assert r.gate(cfg,tmp_path)['status']=='awaiting-proposal-evidence'
    query={'request_digest':digest(packet),'queries':[{'source_id':'config','start_line':1,'line_count':20,
        'question':'What constraints were actually declared?'}]}
    path=tmp_path/'query.json';atomic_json(path,query)
    supplied=r.gate(cfg,tmp_path,evidence_query=path)
    assert supplied['status']=='review-evidence-supplied' and not supplied['execution_authority']
    wait=r.gate(cfg,tmp_path);next_packet=read_json(wait['request_path'])
    assert next_packet['prior_rounds'][-1]['supplements'][0]['value']['results'][0]['source_id']=='config'
    answer(wait,'approve');assert r.gate(cfg,tmp_path)['status']=='proposal-approved'
    Path(supplied['supplement_path']).write_text('{}')
    with pytest.raises(ValueError,match='supplement_changed'):r.gate(cfg,tmp_path)


def test_new_policy_has_no_hidden_round_limits():
    value=r.default_policy()
    assert value['version']==3 and 'min_rounds' not in value and 'max_rounds' not in value
