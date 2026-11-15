"""Real CPU CLI subprocess journey with a file-backed mock backend, not the live LPAI CLI."""
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from lpai_paired_adapter import LpaiAdapter
from paired_execution import advance, contract
from round_reporting import digest
from test_comparison_preflight import plan
from test_paired_execution import config, ref
from test_lpai_paired_adapter import settings, request


CLI = '''#!/usr/bin/python3
import json,pathlib,sys
root=pathlib.Path(__file__).parent
backend=root/'backend.json'
state=json.loads(backend.read_text()) if backend.exists() else {'jobs':{},'starts':0,'stops':0}
args=sys.argv[1:]
def value(key): return args[args.index(key)+1]
command=args[:2]
if command==['projects','get']: result={'uid':'fixture-component','namespace':'fixture-namespace'}
elif command==['projects','start']:
    state['starts']+=1; uid='fixture-'+str(state['starts'])
    task={'uid':uid,'name':value('--name'),'namespace':value('--namespace'),'owner':'fixture-owner',
          'component_uid':value('--uid'),'request_marker':value('--describe'),
          'status':'Running','gpus':4,'hourly_cost':2.5,'released':False}
    state['jobs'][uid]=task; result={'uid':uid}
elif command==['tasks','list']:
    matches=[t for t in state['jobs'].values() if value('--name-like') in t['name']]
    result={'items':matches,'total':len(matches)}
elif command==['tasks','get']:
    task=state['jobs'][value('--uid')]
    if task.get('stop_requested'): task.update(status='Stopped',released=True)
    result=task
elif command==['tasks','stop']:
    state['stops']+=1; state['jobs'][value('--uid')]['stop_requested']=True; result={'ack':True}
else: raise SystemExit(2)
backend.write_text(json.dumps(state))
print(json.dumps(result))
'''

HOOK = '''import hashlib,json,pathlib,time
class Hooks:
    def __init__(self, settings): self.root=pathlib.Path(settings['root'])
    def preflight(self,r,component):
        path=self.root/('body-'+r['arm_id']+'.json')
        path.write_text(json.dumps({'fixture':True,'deadline':r['deadline'],'request_key':r['request_key']}))
        return {'ready':True,'project':r['project'],'observed_at':time.time(),'external_allocations':[],
                'normal_preflight_ref':'SYNTHETIC CPU admission',
                'lease_guard':{'deadline':r['deadline'],'scope':'allocation-release','independent':True,'evidence_ref':'SYNTHETIC TTL'},
                'body_ref':{'path':str(path),'digest':'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()},
                'body_binding':{'request_key':r['request_key'],'deadline':r['deadline'],'arm_digest':r['declared_arm_digest']}}
    def observe(self,r,task):
        now=time.time()
        return {'uid':task['uid'],'request_key':r['request_key'],'observed_at':now,'released':task['released'],
                'release_evidence_ref':'SYNTHETIC same-UID mock allocation' if task['released'] else None,
                'health':{'uid':task['uid'],'request_key':r['request_key'],'observed_at':now,'last_progress_at':now,'flags':[]}}
def create_hooks(settings): return Hooks(settings)
'''


@pytest.mark.skipif(os.name != 'posix', reason='native CLI journey executes on Linux host')
def test_two_native_mock_jobs_resume_stop_and_release_without_duplicate_submits(tmp_path):
    cli = tmp_path/'mock-lpai'; cli.write_text(CLI); cli.chmod(0o700)
    hooks = tmp_path/'project-hooks.py'; hooks.write_text(HOOK)
    c = config(tmp_path, mode='live'); s = settings()
    s.update(cli=str(cli.resolve()), cli_digest='sha256:'+hashlib.sha256(cli.read_bytes()).hexdigest(),
             project_hooks={'path':str(hooks.resolve()),'digest':'sha256:'+hashlib.sha256(hooks.read_bytes()).hexdigest()},
             hook_settings={'root':str(tmp_path.resolve())})
    adapter_path=Path(__file__).parents[1]/'lpai_paired_adapter.py'
    c.update(adapter={'path':str(adapter_path.resolve()),'digest':'sha256:'+hashlib.sha256(adapter_path.read_bytes()).hexdigest()},
             adapter_settings=s)
    approval={'schema':'sdm-paired-authorization/v1','status':'confirmed','contract_digest':contract(c),
              'plan_digest':digest(plan()),'user_message_ref':'SYNTHETIC CPU mock only',
              'operations':['submit','stop-owned-until-released'],'submit_before':time.time()+20,'resource_deadline':time.time()+60}
    c['authorization_ref']=ref(tmp_path/'approval.json',approval); out=tmp_path/'group-state'
    first=advance(c,out,allow_live=True)
    assert first['status']=='running' and first['usage']['reserved_gpus']==8, first['arms']
    uids=[v['uid'] for v in first['arms'].values()]; deadlines=[v['request']['deadline'] for v in first['arms'].values()]
    # A fresh factory/hook process has no prior in-memory cache. Durable UID state is sufficient.
    second=advance(c,out,allow_live=True,allow_submit=False,force_stop=True)
    assert second['status']=='stopping' and len(second['unreleased_arms'])==2
    third=advance(c,out,allow_live=True,allow_submit=False)
    assert third['status']=='executions-terminal-evidence-required' and not third['unreleased_arms']
    assert [v['uid'] for v in third['arms'].values()]==uids
    assert [v['request']['deadline'] for v in third['arms'].values()]==deadlines
    backend=json.loads((tmp_path/'backend.json').read_bytes())
    assert backend['starts']==2 and backend['stops']==2 and third['qualification']=='not-assessed'


@pytest.mark.skipif(os.name != 'posix', reason='project hook subprocess timeout is POSIX-only')
def test_project_hook_timeout_and_changed_bytes_are_bounded_without_importing_changed_code(tmp_path):
    hook=tmp_path/'slow.py'
    hook.write_text('import time\ndef create_hooks(settings):\n    time.sleep(20)\n')
    s=settings(); s['project_hooks']={'path':str(hook.resolve()),'digest':'sha256:'+hashlib.sha256(hook.read_bytes()).hexdigest()}
    a=LpaiAdapter(s); r=request(); r['call_timeout_seconds']=.2
    started=time.monotonic()
    with pytest.raises(RuntimeError,match='hook_timeout'): a.hook('preflight',r,{})
    assert time.monotonic()-started < 3
    hook.write_text('raise RuntimeError("changed code must not run")')
    with pytest.raises(ValueError): a.hook('preflight',r,{})
