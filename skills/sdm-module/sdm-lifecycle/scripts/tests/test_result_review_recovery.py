"""Released-only recovery and actual serialized request bounds; no ML/network."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from research_model_runner import shared_evidence
from model_runner import request
from result_review_recovery import validate,file_sha
from review_storage import atomic_json
from task_pipeline import digest


def fixture(tmp_path):
    root=tmp_path/'run';root.mkdir()
    project=tmp_path/'project.json';config={'research_worker':{'version':5,'project_path':str(project)}}
    atomic_json(project,config)
    atomic_json(root/'execution.json',{'status':'awaiting-review','process_terminal':True,'artifacts_collected':True,'stop_verified':True,'binding':'observed'})
    old=root/'research-decisions'/('a'*64);slot=old/'001-a';slot.mkdir(parents=True)
    intent=slot/'intent.json';atomic_json(intent,{'call_id':'sha256:'+'b'*64})
    atomic_json(old/'state.json',{'schema':'sdm-research-decision/v5','status':'blocked',
        'response_path':str(root/'review/analysis-response.json'),'reason':'ValueError:model_request_exceeds_bounded_input'})
    runtime=Path(__file__).resolve().parents[1]
    manifest=tmp_path/'manifest.json'
    value={'schema':'sdm-result-review-recovery/v1','project_digest':digest(config),'output':str(root),
        'execution_binding':'observed','execution_authority':False,'producer_runtime':str(runtime.parent),
        'producer_files':{str(runtime/name):file_sha(runtime/name) for name in
            ('run_project_pipeline.py','research_worker.py','research_model_runner.py','research_decision_store.py','result_review_recovery.py')},
        'unlaunched_intents':[{'path':str(intent),'sha256':file_sha(intent)}],'evidence':[]}
    atomic_json(manifest,value)
    return config,root,manifest


def test_recovery_requires_released_result_and_no_started_transport(tmp_path):
    config,root,manifest=fixture(tmp_path)
    assert validate(config,root,manifest)['execution_authority'] is False
    state=json.loads((root/'execution.json').read_bytes());state['stop_verified']=False
    atomic_json(root/'execution.json',state)
    with pytest.raises(ValueError,match='released_terminal'):validate(config,root,manifest)
    state['stop_verified']=True;atomic_json(root/'execution.json',state)
    transport=root/'model-calls'/('b'*24);transport.mkdir(parents=True)
    with pytest.raises(ValueError,match='unknown_or_completed'):validate(config,root,manifest)


def test_only_proven_unlaunched_claim_can_be_linked_to_new_producer(tmp_path):
    from research_decision_store import existing
    config,root,manifest=fixture(tmp_path)
    job=root/'research-decisions'/('c'*64);response=root/'review/analysis-response.json'
    with pytest.raises(ValueError,match='unreconciled_decision_claim'):existing(config,root,job,response)
    assert existing(config,root,job,response,review_recovery=manifest) is None
    transport=root/'model-calls'/('b'*24);transport.mkdir(parents=True)
    with pytest.raises(ValueError,match='unknown_or_completed'):existing(config,root,job,response,review_recovery=manifest)


def test_escaped_raw_sources_fit_both_real_model_request_contracts(tmp_path):
    root=tmp_path/'actual';root.mkdir();job=tmp_path/'job';job.mkdir();sources={}
    observed=root/'result.json';observed.write_text('{"validation_accuracy":0.8138}')
    sources[str(observed)]=file_sha(observed)
    for i in range(40):
        path=tmp_path/('metadata-%02d.json'%i)
        path.write_text(json.dumps({'text':'quoted\\path\n'*500},indent=2))
        sources[str(path)]=file_sha(path)
    project={'research_worker':{'output':str(root)},'background':'x'*90000}
    shared=shared_evidence(project,{}, {},sources,job)
    encoded=lambda x:len(json.dumps(x,ensure_ascii=False).encode())
    assert encoded(shared)<=160*1024 and len(shared['raw_sources'])==len(sources)
    assert next(row for row in shared['raw_sources'] if row['path']==str(observed))['text']==observed.read_text()
    assert any(row['omitted_bytes'] for row in shared['raw_sources'])
    for purpose,evidence in [('result-analysis',{'shared':shared}),
            ('result-critique',{'shared':shared,'draft':{'reason':'r'*(48*1024-100)}})]:
        value={'schema':'sdm-model-request/v1','purpose':purpose,'contract_id':'control','model':'control',
            'evidence':evidence,'prompt':'Read-only bounded control','output_schema':{'type':'object','properties':{}}}
        assert request(value)==value
