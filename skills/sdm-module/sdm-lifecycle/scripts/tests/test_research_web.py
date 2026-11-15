"""Live HTTP/SSE + actual local CPU rounds, no browser automation or LPAI."""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
from urllib.parse import urlencode

import pytest
import research_observation as research
import research_web as web
import devspace_study as study
from task_pipeline import digest
from test_research_observation import config_at, change, record_public
from test_devspace_study import public, respond, ENTRY


def request(server,path,params=None,method='GET',headers=None,token=True):
    params=dict(params or {})
    if token:params['token']=server.token
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    conn.request(method,path+'?'+urlencode(params),headers=headers or {})
    response=conn.getresponse();raw=response.read();conn.close()
    return response.status,json.loads(raw)


@pytest.fixture
def running(tmp_path):
    config,root=config_at(tmp_path)
    research.event(config,change('question','q-rate'))
    server=web.Server(config)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield config,root,server
    finally:server.shutdown();server.server_close();thread.join(5)


def test_http_is_read_only_and_checks_local_origin_token_and_evidence(running,tmp_path):
    config,root,server=running
    assert request(server,'/api/snapshot')[0]==200
    assert request(server,'/api/snapshot',token=False)[0]==403
    assert request(server,'/api/snapshot',headers={'Origin':'https://foreign.example'})[0]==403
    assert request(server,'/api/snapshot',headers={'Host':'foreign.example'})[0]==403
    assert request(server,'/api/snapshot',method='POST')[0]==405
    assert request(server,'/api/evidence',{'id':'../../project.json'})[0]==404
    file=root/'evidence.json';file.parent.mkdir();file.write_text('{"value":1}')
    research.event(config,change('finding','f1',evidence=[{'path':str(file)}]))
    data=request(server,'/api/snapshot')[1];key=next(iter(data['evidence']))
    result=request(server,'/api/evidence',{'id':key})
    assert result[0]==200 and result[1]['verified'] and result[1]['text']=='{"value":1}'
    file.write_text('{"value":2}')
    assert request(server,'/api/evidence',{'id':key})[0]==409
    outside=tmp_path/'outside.json';outside.write_text('{}')
    research.event(config,change('finding','outside',evidence=[{'path':str(outside)}]))
    key=next(k for k,v in request(server,'/api/snapshot')[1]['evidence'].items() if v['path']==str(outside))
    assert request(server,'/api/evidence',{'id':key})[0]==404
    assert not (root/'execution.json').exists()


def sse(server):
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=8)
    conn.request('GET','/api/events?'+urlencode({'token':server.token}))
    return conn,conn.getresponse()


def next_revision(stream):
    for i in range(20):
        line=stream.readline()
        if line.startswith(b'data:'):return json.loads(line[5:])['revision']
    raise AssertionError('No revision received')


def test_sse_actual_two_rounds_results_decision_and_reconnect(tmp_path):
    config,root=config_at(tmp_path)
    source=tmp_path/'workload.py'
    source.write_text("""import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--learning-rate',type=float);a=p.parse_args()
w=0.0
for i in range(5):
 w-=a.learning_rate*2*(w-3)
 Path('progress.json').write_text(json.dumps({'optimizer_updates':i+1}))
Path('result.json').write_text(json.dumps({'score':-(w-3)**2,'loss':(w-3)**2,'parameters':{'learning_rate':a.learning_rate},'data_digest':'fixed-data','checkpoint_verified':True}))
""")
    plan=config['devspace_execution']['plan'];plan['inputs'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
    config['devspace_execution']['authorization']['plan_digest']=digest(plan)
    config['devspace_study']['initial_parameters']['learning_rate']=.05
    config['devspace_study']['authorization']['study_digest']=study.binding(config)
    record_public(config,root,change('question','q-rate'))
    record_public(config,root,change('hypothesis','h1',text='Try learning_rate=.05 for five actual CPU updates'))
    server=web.Server(config);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    conn,stream=sse(server)
    try:
        assert next_revision(stream)==2
        first=public(config,root,True)
        # Consume the revision stream until it includes the completed first run.
        observed=next_revision(stream)
        snapshot=request(server,'/api/snapshot')[1]
        assert snapshot['revision']>=observed and len(snapshot['runs'])==1
        r1=snapshot['runs'][0];assert r1['result']['loss']==pytest.approx(3.1381059609)
        record_public(config,root,change('finding','f-result',run_ids=[r1['id']],text='Measured loss above target; increase only learning rate to .1'))
        # Original review contract is still the sole path to successor execution.
        respond(first,params={'learning_rate':.1})
        second=public(config,root,True)
        snapshot=request(server,'/api/snapshot')[1];assert len(snapshot['runs'])==2
        r2=snapshot['runs'][1];assert r2['result']['loss']==pytest.approx(.9663676416)
        assert r2['parameter_changes']==[{'name':'learning_rate','previous':.05,'current':.1}]
        assert any(e['basis']=='verified-contract' and e['target']==r2['id'] for e in snapshot['edges'])
        assert request(server,'/api/evidence',{'id':r2['evidence_links'][1]})[1]['verified']
        conn.close()  # Updates while this browser connection is absent are retained.
        record_public(config,root,change('finding','offline-result',run_ids=[r2['id']],text='Actual second result below target; retain during browser disconnect'))
        conn,stream=sse(server);revision=next_revision(stream)
        assert revision==research.context(config)['revision']
        assert any(n['id']=='offline-result' for n in request(server,'/api/snapshot')[1]['nodes'])
        respond(second,'stop');done=public(config,root);assert done['status']=='completed'
        before=research.context(config)['revision'];public(config,root)
        assert research.context(config)['revision']==before and not (root/'round-03').exists()
        assert request(server,'/api/snapshot')[1]['totals']['runs']==2
    finally:
        conn.close();server.shutdown();server.server_close();thread.join(5)


def test_no_database_is_created_by_view_and_readback_never_writes(running,tmp_path):
    config,root,server=running
    db=server.view.db()
    with pytest.raises(sqlite3.OperationalError):db.execute('DELETE FROM changes')
    db.close()
    other=copy.deepcopy(config);other['research_observation']['store']=str(tmp_path/'absent.sqlite3')
    with pytest.raises(sqlite3.OperationalError):web.ResearchView(other).read()
    assert not Path(other['research_observation']['store']).exists()
    different=copy.deepcopy(config);different['research_observation']['project_id']='different'
    with pytest.raises(ValueError,match='binding_changed'):web.ResearchView(different).read()


def test_history_pagination_is_complete_and_question_scoped(running):
    config,root,server=running
    for i in range(105):research.event(config,change('hypothesis','h'+str(i)))
    research.event(config,change('question','other-q',question_id='other-q'))
    page=request(server,'/api/history',{'question':'q-rate'})[1]
    assert len(page['items'])==100 and page['has_more']
    older=request(server,'/api/history',{'question':'q-rate','before':page['next_before']})[1]
    assert len(older['items'])==6 and not older['has_more']
    assert all(n['question_id']=='q-rate' for n in page['items']+older['items'])


def test_public_web_mode_rejects_live_execution_flags(tmp_path):
    config,root=config_at(tmp_path);project=tmp_path/'project.json';project.write_text(json.dumps(config))
    for flags in [['--research-web','--allow-devspace-live'],['--research-web','--devspace-execution'],['--web-port','8917']]:
        result=subprocess.run([sys.executable,'-B',str(ENTRY),'--project',str(project)]+flags,capture_output=True,text=True,timeout=20)
        assert result.returncode!=0
    assert not root.exists()


def test_exact_port_cannot_have_two_project_instances(running):
    config,root,server=running
    with pytest.raises(OSError):web.Server(config,server.server_port)
