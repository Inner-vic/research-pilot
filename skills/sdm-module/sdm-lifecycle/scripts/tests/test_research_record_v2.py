"""v2 lineage, provenance, atomic retries and compatibility; no cloud resources."""
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import sqlite3

import pytest
import research_observation as research
import research_web


@pytest.fixture
def cfg(tmp_path):
    config = {'research_observation':{'version':1,'project_id':'dashboard-contract','question_id':'q',
        'store':str(tmp_path/'research.sqlite3'),'run_roots':[str(tmp_path)]}}
    research.event(config, record('question','q'))
    return config


def record(kind, node, **extra):
    return {'schema':'sdm-research-event/v2','project_id':'dashboard-contract','question_id':'q',
        'kind':kind,'id':node,'request_id':'req-'+node,'text':'Contract fixture '+node,
        'recording':'live','actor':{'kind':'agent','source_ref':'isolated synthetic contract test'}, **extra}


def plan(tmp_path, node, version, **extra):
    path=tmp_path/(node+'.md');path.write_text('Actual plan material '+node)
    ref={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    return record('plan',node,plan={'id':'route','version':version,'content_ref':ref},evidence=[ref],**extra)


def test_v1_preserved_and_v2_requires_own_schema(cfg):
    old=record('hypothesis','legacy');old['schema']='sdm-research-event/v1'
    assert research.event(cfg,old)['status']=='recorded'
    with pytest.raises(ValueError,match='semantic_event'):research.event(cfg,{**old,'id':'x','request_id':'x','kind':'discussion'})
    with pytest.raises(ValueError,match='exact_project'):research.event(cfg,record('discussion','cross',project_id='other'))
    assert research.context(cfg)['total_nodes']==2


def test_exact_plan_versions_and_revision_preserve_original(cfg,tmp_path):
    p1=plan(tmp_path,'p1','1.0');research.event(cfg,p1)
    with pytest.raises(ValueError,match='parent_plan'):research.event(cfg,plan(tmp_path,'bad','1.1'))
    p2=plan(tmp_path,'p2','1.1',parent_plan_id='p1',relations=[{'relation':'revises','target':'p1','basis':'observed'}])
    research.event(cfg,p2)
    with pytest.raises(ValueError,match='version_immutable'):research.event(cfg,plan(tmp_path,'dup','1.1'))
    nodes=research.context(cfg)['nodes'];assert [n['id'] for n in nodes]==['q','p1','p2']
    assert nodes[1]['plan']['version']=='1.0'
    # Old Web must remain readable when new relations are present.
    research.event(cfg,record('discussion','d',relations=[{'relation':'cites','target':'p1','basis':'observed'}]))
    assert len(research_web.ResearchView(cfg).read()['nodes'])==4


def test_cross_question_parents_focus_and_plan_references_are_rejected(cfg):
    research.event(cfg,record('question','other-q',question_id='other-q'))
    for key,kind in [('parent_id','branch'),('target_id','focus'),('plan_id','discussion'),('parent_plan_id','plan')]:
        with pytest.raises(ValueError,match='other_question'):
            research.event(cfg,record(kind,key,**{key:'other-q'},session_id='session'))
    with pytest.raises(ValueError,match='plan_reference_type'):research.event(cfg,record('discussion','wrong-type',plan_id='q'))


def test_backfill_pause_and_semantic_reviews_cannot_claim_contract_acceptance(cfg):
    for body,error in [
        (record('discussion','backfill',recording='backfill'),'backfill_reason'),
        (record('focus','paused',session_id='s1',target_id='q',state='paused'),'wait_condition'),
        (record('review','forged',review_status='accepted',relations=[{'relation':'cites','target':'q','basis':'observed'}]),'semantic_not_contract'),
    ]:
        with pytest.raises(ValueError,match=error):research.event(cfg,body)
    body=record('focus','pause',session_id='s1',target_id='q',state='paused',waiting_for='Actual user input',recording='backfill',backfill_reason='Fixture only')
    assert research.event(cfg,body)['execution_authority'] is False
    assert research.context(cfg)['nodes'][-1]['state']=='paused'


def test_concurrent_writers_duplicate_conflict_and_restart(cfg):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:research.event(cfg,record('discussion','same')),range(12)))
    assert sum(not r['reused'] for r in results)==1
    revision=research.context(cfg)['revision']
    with pytest.raises(ValueError,match='request_conflict'):research.event(cfg,record('discussion','same',text='changed'))
    with pytest.raises(ValueError,match='revision_conflict'):research.event(cfg,record('discussion','stale',expected_revision=0))
    assert research.event(copy.deepcopy(cfg),record('discussion','same'))['reused']
    with sqlite3.connect(research.settings(cfg)['store']) as db:
        assert db.execute('SELECT COUNT(*) FROM changes').fetchone()[0]==revision
