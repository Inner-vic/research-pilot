"""Pending-review retirement without changing chats or accepting late decisions."""
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import pytest

from test_pro_review_workflow import project, URL, OTHER, flow, session
from review_storage import atomic_json, read_json
from model_review_bridge import sha


def revision(root):
    return flow.status(root)['revision']


def proof(root,item,outcome='response-complete',**changes):
    evidence=root/'transport/observed-chat.txt';evidence.parent.mkdir(exist_ok=True)
    evidence.write_text('Synthetic transport observation: '+outcome)
    value={'schema':'sdm-pro-review-reconciliation/v1','url':URL,'model':'Pro',
        'verified_visible':True,'request_digest':item['request_digest'],
        'observed_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'evidence_ref':'transport/observed-chat.txt','evidence_sha256':sha(evidence.read_bytes()),'outcome':outcome,**changes}
    path=root/'reconciliation.json';atomic_json(path,value);return path


def test_unsent_cancel_keeps_context_and_requires_linked_fresh_request(tmp_path):
    root,item,_,_=project(tmp_path,sent=False)
    before=session.check(root,URL,0)
    old_revision=revision(root)
    result=flow.cancel(root,item['review_id'],'Evidence needs correction',old_revision)
    assert result['stage']=='cancelled' and flow.status(root)['active_review'] is None
    assert session.check(root,URL,0)==before
    assert flow.cancel(root,item['review_id'],'Evidence needs correction',old_revision)==result
    assert revision(root)==old_revision+1
    with pytest.raises(ValueError,match='cancelled_request_requires_new_request'):
        flow.prepare(root,root/'input.json',root/'context.json',[],URL,0)
    replacement=flow.prepare(root,root/'input.json',root/'context.json',[],URL,0,item['review_id'])
    assert replacement['review_id']!=item['review_id']
    packet=read_json(Path(replacement['bundle'])/'analysis-request.json')
    assert packet['supersedes_review']['cancellation_digest']==result['cancellation_digest']
    assert packet['review_binding']['chat_url']==URL


def test_transport_intent_recovery_blocks_unobserved_cancellation(tmp_path):
    root,item,_,_=project(tmp_path,sent=False)
    stage=flow.begin_delivery(root,item['review_id'],revision(root))
    assert stage['stage']=='delivery-in-progress'
    with pytest.raises(ValueError,match='transport_intent_must_be_reconciled'):
        flow.begin_delivery(root,item['review_id'],revision(root))
    assert flow.recover(root)['stage']=='delivery-uncertain'
    with pytest.raises(ValueError,match='same_chat_reconciliation_required'):
        flow.cancel(root,item['review_id'],'Retry later',revision(root))
    value=flow.cancel(root,item['review_id'],'Retry later',revision(root),proof(root,item,'not-delivered'))
    assert value['stage']=='cancelled'
    assert session.check(root,URL,0)['head_digest'] is None


def test_partial_transport_intent_never_allows_a_blind_resend(tmp_path):
    root,item,_,_=project(tmp_path,sent=False)
    original=flow._save
    def crash(*args):raise RuntimeError('Before workflow pointer')
    flow._save=crash
    try:
        with pytest.raises(RuntimeError):flow.begin_delivery(root,item['review_id'],revision(root))
    finally:flow._save=original
    assert flow.status(root)['reviews'][item['review_id']]['stage']=='prepared'
    with pytest.raises(ValueError,match='transport_intent_must_be_reconciled'):
        flow.begin_delivery(root,item['review_id'],revision(root))
    with pytest.raises(ValueError,match='same_chat_reconciliation_required'):
        flow.cancel(root,item['review_id'],'Retire',revision(root))
    assert flow.recover(root)['stage']=='delivery-uncertain'


@pytest.mark.parametrize('change,error',[
    ({'url':OTHER},'receipt_mismatch'),
    ({'request_digest':'sha256:'+'0'*64},'receipt_mismatch'),
    ({'model':'Thinking'},'receipt_mismatch'),
    ({'verified_visible':False},'receipt_mismatch'),
    ({'evidence_ref':''},'evidence_required'),
    ({'evidence_ref':'../outside.txt'},'project_relative'),
    ({'evidence_sha256':'sha256:'+'0'*64},'evidence_changed'),
    ({'observed_at':'2020-01-01T00:00:00+00:00'},'fresh_reconciliation'),
    ({'observed_at':'2026-10-12T00:00:00'},'fresh_reconciliation'),
    ({'observed_at':'2999-01-01T00:00:00+00:00'},'fresh_reconciliation'),
    ({'outcome':'still-running'},'not_terminal'),
    ({'outcome':'unknown'},'not_terminal'),
    ({'outcome':'not-delivered'},'contradicts_delivery'),
])
def test_delivered_request_needs_fresh_consistent_terminal_observation(tmp_path,change,error):
    root,item,_,_=project(tmp_path)
    current=flow.status(root)
    with pytest.raises(ValueError,match=error):
        flow.cancel(root,item['review_id'],'Change scope',current['revision'],proof(root,item,**change))
    assert flow.status(root)==current
    assert not (Path(item['bundle'])/'cancellation.json').exists()


@pytest.mark.parametrize('outcome',['response-complete','generation-stopped'])
def test_delivered_cancel_retains_late_response_without_affecting_successor(tmp_path,outcome):
    root,item,_,_=project(tmp_path)
    flow.cancel(root,item['review_id'],'Retire request',revision(root),proof(root,item,outcome))
    replacement=flow.prepare(root,root/'input.json',root/'context.json',[],URL,0,item['review_id'])
    for _ in range(2):
        with pytest.raises(ValueError,match='cancelled_review_response_retained'):
            flow.ingest(root,root/'response.json',root/'receipt.json',item['review_id'])
    state=flow.status(root)
    assert state['active_review']==replacement['review_id']
    assert len(state['reviews'][item['review_id']]['attempts'])==1
    assert session.check(root,URL,0)['revision']==0
    with pytest.raises(ValueError,match='validated_review_required'):
        flow.export_loop_result(root,item['review_id'],root/'input.json',root/'loop-result.json')
    assert not (root/'loop-result.json').exists()


def test_received_response_must_recover_before_any_retirement(tmp_path):
    root,item,_,_=project(tmp_path)
    def crash(stage):
        if stage=='received':raise RuntimeError('interruption')
    with pytest.raises(RuntimeError):flow.ingest(root,root/'response.json',root/'receipt.json',fault_hook=crash)
    with pytest.raises(ValueError,match='cancellation_transition_not_allowed'):
        flow.cancel(root,item['review_id'],'Discard',revision(root),proof(root,item))
    assert flow.recover(root)['stage']=='validated'
    with pytest.raises(ValueError,match='cancellation_transition_not_allowed'):
        flow.cancel(root,item['review_id'],'Discard',revision(root),proof(root,item))
    flow.plan(root)
    assert session.check(root,URL,1)['revision']==1


def test_cancellation_freezes_observation_and_detects_retained_evidence_tampering(tmp_path):
    root,item,_,_=project(tmp_path)
    flow.cancel(root,item['review_id'],'Retire',revision(root),proof(root,item))
    (root/'transport/observed-chat.txt').unlink()
    assert flow.status(root)['reviews'][item['review_id']]['stage']=='cancelled'
    bundle=Path(item['bundle']);record=read_json(bundle/'cancellation.json')
    (bundle/record['reconciliation_evidence_ref']).write_text('Changed observation')
    with pytest.raises(ValueError,match='cancellation_evidence_changed'):flow.status(root)


@pytest.mark.parametrize('outside',[False,True])
def test_reconciliation_does_not_follow_linked_observations(tmp_path,outside):
    root,item,_,_=project(tmp_path)
    receipt=proof(root,item);value=read_json(receipt)
    target=tmp_path/'outside.txt' if outside else root/'inside.txt'
    target.write_text('Synthetic linked observation')
    link=root/'transport/link.txt';link.symlink_to(target)
    value.update(evidence_ref='transport/link.txt',evidence_sha256=sha(target.read_bytes()))
    atomic_json(receipt,value)
    with pytest.raises(ValueError,match='project_relative|missing_or_linked'):
        flow.cancel(root,item['review_id'],'Retire',revision(root),receipt)


def test_late_response_cannot_break_another_pending_cancellation_checkpoint(tmp_path):
    root,old,_,_=project(tmp_path)
    flow.cancel(root,old['review_id'],'Old request',revision(root),proof(root,old))
    current=flow.prepare(root,root/'input.json',root/'context.json',[],URL,0,old['review_id'])
    def crash(stage):raise RuntimeError('Interrupted cancellation')
    with pytest.raises(RuntimeError):
        flow.cancel(root,current['review_id'],'Current request',revision(root),fault_hook=crash)
    before=revision(root)
    with pytest.raises(ValueError,match='cancellation_pending_recovery_required'):
        flow.ingest(root,root/'response.json',root/'receipt.json',old['review_id'])
    assert revision(root)==before
    assert flow.recover(root)['stage']=='cancelled'
    assert session.check(root,URL,0)['revision']==0


def test_workflow_cas_and_predecessor_state_are_checked(tmp_path):
    root,item,_,_=project(tmp_path,sent=False)
    for expected in (False,revision(root)-1):
        with pytest.raises(ValueError,match='workflow_revision'):
            flow.cancel(root,item['review_id'],'Discard',expected)
    with pytest.raises(ValueError,match='cancelled_predecessor_required'):
        flow.prepare(root,root/'input.json',root/'context.json',[],URL,0,item['review_id'])
    flow.begin_delivery(root,item['review_id'],revision(root))
    flow.mark_delivery(root,root/'receipt.json')
    assert flow.status(root)['reviews'][item['review_id']]['stage']=='waiting-for-review'


def test_abrupt_cancellation_exit_recovers_once_without_new_dispatch(tmp_path):
    root,item,_,_=project(tmp_path)
    receipt=proof(root,item)
    expected=revision(root)
    code=('import os,sys\nfrom pathlib import Path\nimport pro_review_workflow as flow\n'
          'def crash(stage):\n if stage=="cancellation-recorded": os._exit(17)\n'
          'root=Path(sys.argv[1])\n'
          'flow.cancel(root,sys.argv[2],"Retire",int(sys.argv[3]),root/"reconciliation.json",fault_hook=crash)\n')
    result=subprocess.run([sys.executable,'-B','-c',code,str(root),item['review_id'],str(expected)],
        cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=20)
    assert result.returncode==17,result.stderr
    with pytest.raises(ValueError,match='cancellation_pending_recovery_required'):
        flow.ingest(root,root/'response.json',root/'receipt.json')
    assert flow.recover(root)['stage']=='cancelled'
    state=flow.status(root)
    assert state['revision']==expected+1 and state['active_review'] is None
    assert flow.recover(root)==state
    assert flow.cancel(root,item['review_id'],'Retire',expected,receipt)['stage']=='cancelled'
    assert session.check(root,URL,0)['revision']==0


def test_public_cli_can_retire_and_prepare_linked_request(tmp_path):
    root,item,_,_=project(tmp_path,sent=False)
    script=Path(__file__).parents[1]/'pro_review_workflow.py'
    result=subprocess.run([sys.executable,'-B',str(script),'cancel','--root',str(root),
        '--review-id',item['review_id'],'--reason','Fixture retirement',
        '--expected-workflow-revision',str(revision(root))],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['stage']=='cancelled'
    result=subprocess.run([sys.executable,'-B',str(script),'prepare','--root',str(root),
        '--request',str(root/'input.json'),'--context',str(root/'context.json'),
        '--observed-url',URL,'--expected-revision','0','--supersedes-review-id',item['review_id']],
        capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['supersedes_review_id']==item['review_id']
