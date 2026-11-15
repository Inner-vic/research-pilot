"""Two-project, no-platform review lifecycle and crash/identity regression."""
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
import pro_review_workflow as flow
import pro_review_session as session
from review_storage import atomic_json, locked, read_json
from task_pipeline import digest

URL='https://chatgpt.com/c/11111111-1111-1111-1111-111111111111'
OTHER='https://chatgpt.com/c/22222222-2222-2222-2222-222222222222'


def project(tmp_path,name='forecast',url=URL,sent=True):
    root=tmp_path/name
    session.bind(root,name,'review',url)
    metric,value=('accuracy',0.8) if name=='classifier' else ('rmse',0.3)
    domain={'threshold':{'type':'number','minimum':0.1,'maximum':0.9},'mode':{'type':'enum','choices':['balanced','strict']}} if name=='classifier' else {'depth':{'type':'integer','minimum':1,'maximum':5}}
    packet={'schema':'sdm-loop-analysis-request/v1','objective':'Review '+name,'remaining_rounds':0,'history':[], 'evidence':{metric:value},'allowed_variables':domain,'project_metrics':[metric]}
    context={'schema':'sdm-pro-review-context/v1','project':name,'workflow':'review',
        'fixed_conditions':{'code':'c1','dataset':'d1','window':24},
        'excluded_explanations':['No missing input'],'remaining_unknowns':['Runtime contribution'],
        'current_authorization':{'execution_authorized':False,'model_inference_allowed':False,'training_submission_allowed':False},
        'new_experiment_observations':False,'gap_matrix':[{'gap_id':'runtime','status':'open'}]}
    if name=='classifier':context['fixed_conditions']={'code':'classifier-c2','dataset':'images-d2','split':'validation'}
    atomic_json(root/'input.json',packet);atomic_json(root/'context.json',context)
    item=flow.prepare(root,root/'input.json',root/'context.json',[],url,0)
    receipt={'url':url,'model':'Pro','verified_visible':True,'request_digest':item['request_digest']}
    atomic_json(root/'receipt.json',receipt)
    if sent:flow.mark_delivery(root,root/'receipt.json')
    body={'request_digest':item['request_digest'],'session_context_revision':0,'session_context_digest':None,
        'facts':[{'evidence_id':metric,'value':value}],
        'hypotheses':[{'statement':'Capacity may matter','evidence_ids':[metric],'limitations':'Synthetic evidence cannot establish a cause'}],
        'previous_experiment_assessment':'Fixture only','reason':'No execution budget','action':'stop-budget',
        'continuation_acknowledgement':{'status':'continued','objective':packet['objective'],
            'fixed_conditions':context['fixed_conditions'],'excluded_explanations':context['excluded_explanations'],
            'remaining_unknowns':context['remaining_unknowns'],'current_scope':'review only','no_new_observations':True},
        'evidence_gap_matrix':context['gap_matrix']}
    atomic_json(root/'response.json',body)
    return root,item,packet,body


def test_two_projects_complete_independently_and_retries_are_idempotent(tmp_path):
    for name,url in [('forecast',URL),('classifier',OTHER)]:
        root,item,source,body=project(tmp_path,name,url)
        first=flow.ingest(root,root/'response.json',root/'receipt.json')
        assert first['stage']=='validated'
        plan=flow.plan(root);assert plan['execution_allowed'] is False
        before=read_json(root/'session.json')
        assert flow.ingest(root,root/'response.json',root/'receipt.json',item['review_id'])['stage']=='planned'
        assert flow.plan(root,item['review_id'])==plan
        assert read_json(root/'session.json')==before
        capsule=read_json(root/before['context_ref'])
        assert capsule['schema']=='sdm-pro-context-capsule/v2'
        assert capsule['fixed_conditions']==body['continuation_acknowledgement']['fixed_conditions']
        assert capsule['excluded_explanations']==['No missing input']
        assert capsule['current_authorization']['execution_authorized'] is False
        receipt=flow.export_loop_result(root,item['review_id'],root/'input.json',root/'loop-result.json')
        assert receipt['source_request_digest']==digest(source)
        assert flow.status(root)['active_review'] is None


def test_project_receipt_and_pending_request_boundaries(tmp_path):
    root,item,source,body=project(tmp_path)
    other,_,_,_=project(tmp_path,'other',OTHER)
    with pytest.raises(ValueError,match='return_receipt_mismatch'):
        flow.ingest(root,root/'response.json',other/'receipt.json')
    context=read_json(root/'context.json');context['project']='other';atomic_json(root/'bad-context.json',context)
    with pytest.raises(ValueError,match='project_mismatch'):
        flow.prepare(root,root/'input.json',root/'bad-context.json',[],URL,0)
    source['objective']='Different review';atomic_json(root/'other-input.json',source)
    with pytest.raises(ValueError,match='active_review'):
        flow.prepare(root,root/'other-input.json',root/'context.json',[],URL,0)
    flow.mark_delivery(root,uncertain_reason='UI disconnected after send')
    assert flow.recover(root)['stage']=='delivery-uncertain'
    assert session.check(root,URL,0)['revision']==0


@pytest.mark.parametrize('bad', ['revision-type','invented-fact','gap-upgrade','frozen-context'])
def test_rejected_response_does_not_advance_context_and_can_be_corrected(tmp_path,bad):
    root,item,source,body=project(tmp_path)
    altered=json.loads(json.dumps(body))
    if bad=='revision-type':altered['session_context_revision']=False
    if bad=='invented-fact':altered['facts'][0]['value']=9
    if bad=='gap-upgrade':altered['evidence_gap_matrix'][0]['status']='closed'
    if bad=='frozen-context':altered['continuation_acknowledgement']['fixed_conditions']['window']=99
    atomic_json(root/'bad-response.json',altered)
    with pytest.raises(ValueError,match='review_rejected'):
        flow.ingest(root,root/'bad-response.json',root/'receipt.json')
    assert session.check(root,URL,0)['revision']==0
    assert flow.status(root)['reviews'][item['review_id']]['stage']=='rejected'
    assert flow.ingest(root,root/'response.json',root/'receipt.json')['stage']=='validated'


@pytest.mark.parametrize('stage',['received','accepted-response','session-recorded'])
def test_recovery_after_each_acceptance_boundary_does_not_duplicate(tmp_path,stage):
    root,item,_,_=project(tmp_path)
    def crash(at):
        if at==stage:raise RuntimeError('simulated interruption')
    with pytest.raises(RuntimeError,match='interruption'):
        flow.ingest(root,root/'response.json',root/'receipt.json',fault_hook=crash)
    assert flow.recover(root)['stage']=='validated'
    assert session.check(root,URL,1)['revision']==1
    assert len(list((root/'records').glob('*.json')))==1


@pytest.mark.parametrize('directory',['records','contexts'])
def test_session_partial_writes_are_replayed_without_history_rewrite(tmp_path,directory):
    root,item,_,_=project(tmp_path);original=session.emit
    def interrupted(output,name,value):
        original(output,name,value)
        path=Path(output)/name
        if directory in path.parts:raise RuntimeError('simulated write interruption')
    with patch.object(session,'emit',side_effect=interrupted),pytest.raises(RuntimeError):
        flow.ingest(root,root/'response.json',root/'receipt.json')
    assert session.check(root,URL,0)['revision']==0
    assert flow.recover(root)['stage']=='validated'
    assert session.check(root,URL,1)['revision']==1


def test_process_lock_prevents_a_second_writer(tmp_path):
    root=tmp_path/'locked'
    script=Path(__file__).parents[1]
    code='from review_storage import locked\nimport sys\nwith locked(sys.argv[1]):\n print("unexpected")\n'
    with locked(root):
        process=subprocess.run([sys.executable,'-B','-c',code,str(root)],cwd=script,capture_output=True,text=True)
    assert process.returncode!=0 and 'review_workflow_busy' in process.stderr
    with locked(root):pass


@pytest.mark.parametrize('boundary',['received','accepted-response','session-recorded'])
def test_abrupt_process_exit_releases_lock_and_recovers_exactly_once(tmp_path,boundary):
    root,item,_,_=project(tmp_path)
    code=('import os,sys\nfrom pathlib import Path\nimport pro_review_workflow as flow\n'
          'def crash(stage):\n if stage==sys.argv[2]: os._exit(17)\n'
          'root=Path(sys.argv[1])\n'
          'flow.ingest(root,root/"response.json",root/"receipt.json",fault_hook=crash)\n')
    process=subprocess.run([sys.executable,'-B','-c',code,str(root),boundary],
        cwd=Path(__file__).parents[1],capture_output=True,text=True,timeout=20)
    assert process.returncode==17,process.stderr
    assert flow.recover(root)['stage']=='validated'
    assert session.check(root,URL,1)['revision']==1
    assert len(list((root/'records').glob('*.json')))==1


def test_evidence_changes_and_source_mixing_cannot_be_silently_reused(tmp_path):
    root,item,source,_=project(tmp_path)
    evidence=root/'extra.txt';evidence.write_text('changed evidence')
    with pytest.raises(ValueError,match='active_review'):
        flow.prepare(root,root/'input.json',root/'context.json',[evidence],URL,0)
    flow.ingest(root,root/'response.json',root/'receipt.json');flow.plan(root)
    source['objective']='wrong project source'
    with pytest.raises(ValueError,match='source_request_mismatch'):
        flow.source_decision(root,item['review_id'],source)


def test_public_cli_status_reads_the_retained_workflow(tmp_path):
    root,item,_,_=project(tmp_path)
    script=Path(__file__).parents[1]/'pro_review_workflow.py'
    before={str(p.relative_to(script.parent)):p.read_bytes() for p in script.parent.rglob('*.pyc')}
    result=subprocess.run([sys.executable,'-B',str(script),'status','--root',str(root)],capture_output=True,text=True)
    assert result.returncode==0
    assert json.loads(result.stdout)['active_review']==item['review_id']
    assert {str(p.relative_to(script.parent)):p.read_bytes() for p in script.parent.rglob('*.pyc')}==before
