"""Kill the CPU coordinator; an independent local guard releases two mock allocations."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from paired_execution import contract
from paired_release_watch import proof, process_birth
from review_storage import atomic_json, read_json
from round_reporting import digest
from test_comparison_preflight import plan
from test_paired_execution import config, ref


FAKE='''import json,pathlib,time
from review_storage import atomic_json
from paired_release_watch import proof
class Adapter:
    is_live=False
    def __init__(self,s):self.s=s;self.path=pathlib.Path(s['backend'])
    def read(self):return json.loads(self.path.read_text()) if self.path.exists() else {'jobs':{},'submits':0,'stops':0}
    def save(self,v):atomic_json(self.path,v)
    def preflight(self,r):
        guard=proof(self.s['heartbeat'],r)
        return {'ready':True,'project':r['project'],'observed_at':time.time(),'external_allocations':[],
                'normal_preflight_ref':'SYNTHETIC CPU preflight','lease_guard':guard}
    def lookup(self,r):
        v=self.read();return {'complete':True,'matches':[j for j in v['jobs'].values() if j['request_key']==r['request_key']]}
    def submit(self,r):
        v=self.read();uid='cpu-'+r['arm_id'];v['submits']+=1
        j={'uid':uid,'job_name':r['job_name'],'request_key':r['request_key'],'phase':'running','released':False,
           'gpus':r['declared_arm']['allocation']['gpus'],'hourly_cost':r['declared_arm']['allocation']['hourly_cost']}
        v['jobs'][uid]=j;self.save(v);return {'outcome':'submitted','uid':uid,'observation':j}
    def observe(self,uid,r):return self.read()['jobs'][uid]
    def stop(self,uid,r,reason):
        v=self.read();v['stops']+=1;v['jobs'][uid].update(phase='stopped',released=True);self.save(v);return {'acknowledged':True}
def create_adapter(s):return Adapter(s)
'''


@pytest.mark.skipif(os.name!='posix',reason='Linux independent guard/process birth validation')
def test_independent_guard_survives_coordinator_death_and_preserves_deadline(tmp_path):
    core=Path(__file__).parents[1]; backend=tmp_path/'backend.json'; heartbeat=tmp_path/'guard.json'
    c=config(tmp_path)
    (tmp_path/'adapter.py').write_text(FAKE)
    import hashlib
    c['adapter']['digest']='sha256:'+hashlib.sha256((tmp_path/'adapter.py').read_bytes()).hexdigest()
    c['adapter_settings']={'backend':str(backend),'heartbeat':str(heartbeat)}
    c['monitoring'].update(poll_seconds=.05,call_timeout_seconds=.3,cleanup_reserve_seconds=.8,
                           stop_retry_seconds=.1,health_required=False)
    deadline=time.time()+9
    approval={'schema':'sdm-paired-authorization/v1','status':'confirmed','contract_digest':contract(c),
              'plan_digest':digest(plan()),'user_message_ref':'SYNTHETIC CPU guard survival test',
              'operations':['submit','stop-owned-until-released'],'submit_before':deadline-1,'resource_deadline':deadline}
    c['authorization_ref']=ref(tmp_path/'approval.json',approval)
    cref=ref(tmp_path/'config.json',c); ref(tmp_path/'config-ref.json',cref)
    state=tmp_path/'group'; env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(core)}
    guardian=subprocess.Popen([sys.executable,'-B',str(core/'paired_release_watch.py'),'--config-ref',str(tmp_path/'config-ref.json'),
                               '--state-root',str(state),'--heartbeat',str(heartbeat),'--apply'],
                              env=env,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    coordinator=None
    try:
        until=time.monotonic()+5
        while not heartbeat.exists() and time.monotonic()<until:time.sleep(.03)
        assert read_json(heartbeat)['status']=='armed'
        command='import json,sys,time;from paired_execution import advance;advance(json.load(open(sys.argv[1])),sys.argv[2]);print("READY",flush=True);time.sleep(30)'
        coordinator=subprocess.Popen([sys.executable,'-B','-c',command,str(tmp_path/'config.json'),str(state)],
                                     env=env,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        until=time.monotonic()+5
        while (not backend.exists() or read_json(backend)['submits']!=2) and time.monotonic()<until:time.sleep(.03)
        assert read_json(backend)['submits']==2
        initial=read_json(state/'state.json'); saved_deadlines={k:v['request']['deadline'] for k,v in initial['arms'].items()}
        coordinator.kill();coordinator.wait(timeout=3)
        stdout,stderr=guardian.communicate(timeout=12)
        final=read_json(state/'state.json'); result=read_json(heartbeat); b=read_json(backend)
        assert guardian.returncode==0,(stdout.decode(),stderr.decode(),result)
        assert result['status']=='released' and b['submits']==2 and b['stops']==2
        assert final['resource_deadline']==deadline and not final['unreleased_arms']
        assert {k:v['request']['deadline'] for k,v in final['arms'].items()}==saved_deadlines
    finally:
        for child in (coordinator,guardian):
            if child is not None and child.poll() is None:child.kill();child.wait(timeout=3)


@pytest.mark.skipif(os.name!='posix',reason='Linux process identity')
@pytest.mark.parametrize('fault',['stale','dead-pid','wrong-contract','too-short-deadline'])
def test_unhealthy_guard_never_admits_compute(tmp_path,fault):
    p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(10)'],start_new_session=True)
    try:
        r={'contract_digest':'fixture-contract','deadline':time.time()+5}
        record={'schema':'sdm-paired-release-watch/v1','status':'armed','pid':p.pid,'process_birth':process_birth(p.pid),
                'platform_mutations_enabled':True,'observed_at':time.time(),'resource_deadline':time.time()+7,
                'contract_digest':'fixture-contract'}
        if fault=='stale':record['observed_at']-=20
        if fault=='dead-pid':p.kill();p.wait(timeout=2)
        if fault=='wrong-contract':record['contract_digest']='foreign'
        if fault=='too-short-deadline':record['resource_deadline']=time.time()
        path=tmp_path/'guard.json';atomic_json(path,record)
        with pytest.raises(ValueError):proof(path,r)
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=2)


@pytest.mark.skipif(os.name!='posix',reason='Linux independent guard')
def test_guard_closes_after_released_partial_pair_without_waiting_full_lease(tmp_path,monkeypatch):
    import paired_release_watch as module
    c=config(tmp_path);cref=ref(tmp_path/'config.json',c);root=tmp_path/'state';root.mkdir();atomic_json(root/'state.json',{})
    monkeypatch.setattr(module.time,'time',lambda:1000)
    monkeypatch.setattr(module,'advance',lambda *a,**k:{'status':'incomplete','unreleased_arms':[],
                                                    'arms':{'control':{'submit_at':1000,'released_at':1001},'treatment':{}}})
    result=module.watch(cref,root,tmp_path/'heartbeat.json')
    assert result['status']=='released' and result['group_status']=='incomplete'
