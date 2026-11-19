"""Synthetic CPU evidence investigation; no model/provider/platform calls."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import pytest
import proposal_review as r
from review_storage import atomic_json, read_json
from task_pipeline import digest
from test_proposal_review import config, proposal, response, revise


def fixture(tmp_path):
    cfg=config();cfg['proposal_review']['context_required']=True
    atomic_json(tmp_path/'proposal.json',proposal(cfg))
    assert r.gate(cfg,tmp_path)['status']=='awaiting-review-context'
    source=tmp_path/'raw.log';source.write_text('loss=2\nloss=1\n',encoding='utf-8')
    ctx={'schema':'sdm-review-context/v1','subject_digest':digest(r.subject(cfg)),
         **{k:'Synthetic project '+k for k in ('goal','constraints','data','code_revision','configuration','history','unknowns')},
         'sources':[{'id':'raw','kind':'log','path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}
    atomic_json(tmp_path/'review-context.json',ctx)
    wait=r.gate(cfg,tmp_path)
    return cfg,wait,source


def assessed(packet,verdict='revise'):
    v=response(packet,verdict)
    v['evidence_assessment']={'source_ids':['raw'],'judgment':'hypothesis','limitations':['Synthetic short trace only']}
    for c in v['challenges']:
        c.update(basis='Only two observed losses',resolution_criterion='Specify a comparable held-out outcome')
    return v


def test_public_query_and_two_round_advice_handling(tmp_path):
    cfg,wait,source=fixture(tmp_path);packet=read_json(wait['request_path'])
    query={'request_digest':digest(packet),'queries':[{'source_id':'raw','start_line':2,'line_count':1,'question':'Which loss was observed?'}]}
    atomic_json(tmp_path/'query.json',query);atomic_json(tmp_path/'project.json',cfg)
    entry=Path(r.__file__).with_name('run_project_pipeline.py')
    cmd=[sys.executable,'-B',str(entry),'--project',str(tmp_path/'project.json'),'--output',str(tmp_path),'--proposal-review','--review-evidence-query',str(tmp_path/'query.json')]
    first=subprocess.run(cmd,capture_output=True,text=True,check=True)
    result=json.loads(first.stdout);supp=read_json(result['supplement_path'])
    assert supp['results'][0]['text']=='loss=1' and not supp['execution_authority']
    assert json.loads(subprocess.run(cmd,capture_output=True,text=True,check=True).stdout)==result
    atomic_json(Path(wait['response_path']),assessed(packet));r.gate(cfg,tmp_path)
    revise(cfg,tmp_path)
    p=read_json(tmp_path/'proposal.json');p.pop('advice_dispositions',None);atomic_json(tmp_path/'proposal.json',p)
    with pytest.raises(ValueError,match='advice_applicability'):r.gate(cfg,tmp_path)
    p=read_json(tmp_path/'proposal.json');p['advice_dispositions']=[{'challenge_id':'comparison','decision':'accept','applicability':'Applies to current metric recipe','reason':'Add falsification outcome'}]
    atomic_json(tmp_path/'proposal.json',p)
    wait=r.gate(cfg,tmp_path);packet=read_json(wait['request_path'])
    atomic_json(Path(wait['response_path']),assessed(packet,'approve'))
    approved=r.gate(cfg,tmp_path)
    assert approved['status']=='proposal-approved' and not approved['execution_authority']
    source.write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='source_changed'):r.gate(cfg,tmp_path)


@pytest.mark.parametrize('bad',['wrong-binding','unknown-source','command','too-many-lines','missing-citation','insufficient','ungrounded'])
def test_invalid_investigation_and_review_are_rejected(tmp_path,bad):
    from review_context import supplement,validate_assessment
    cfg,wait,source=fixture(tmp_path);packet=read_json(wait['request_path'])
    q={'request_digest':digest(packet),'queries':[{'source_id':'raw','question':'Check evidence'}]}
    v=assessed(packet,'approve')
    if bad=='wrong-binding':q['request_digest']='stale'
    elif bad=='unknown-source':q['queries'][0]['source_id']='../../secret'
    elif bad=='command':q['queries'][0]={'command':'arbitrary shell'}
    elif bad=='too-many-lines':q['queries'][0]['line_count']=201
    elif bad=='missing-citation':v['evidence_assessment']['source_ids']=[]
    elif bad=='insufficient':v['evidence_assessment']['judgment']='insufficient'
    else:v['challenges'][0].pop('basis')
    with pytest.raises(ValueError):
        if bad in {'missing-citation','insufficient','ungrounded'}:validate_assessment(v,packet)
        else:supplement(packet,q)


def test_new_policy_requires_context_but_old_policy_unchanged():
    assert r.default_policy()['context_required'] is True
    assert 'context_required' not in r.settings(config())

def test_registered_long_path_keeps_original_type_size_and_digest_guards(tmp_path):
    from review_context import FIELDS,validate_context
    from review_storage import native_path
    folder=tmp_path
    while len(str(folder))<300:folder=folder/('review-evidence-'+'x'*40)
    native_path(folder).mkdir(parents=True)
    source=folder/'registered-source.py';native_path(source).write_bytes(b'# original evidence')
    value={'schema':'sdm-review-context/v1','subject_digest':'subject',
     **{key:'registered context' for key in FIELDS},
     'sources':[{'id':'original','kind':'code','path':str(source),
      'sha256':hashlib.sha256(b'# original evidence').hexdigest()}]}
    assert validate_context(value,'subject')==value
    native_path(source).write_bytes(b'# changed evidence')
    with pytest.raises(ValueError,match='review_source_changed'):validate_context(value,'subject')
