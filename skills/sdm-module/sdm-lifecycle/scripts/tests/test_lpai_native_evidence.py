"""CPU replay of actual read-only CLI shapes; no actual task dispatch or stop."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from lpai_native_evidence import job_matches, priced_allocation, release_readback, roles, observed_field_map
from lpai_paired_adapter import LpaiAdapter
from round_reporting import digest
from test_lpai_paired_adapter import settings, request, Hooks, Transport
from test_paired_execution import ref


def fixture():
    root=Path(os.environ['BMAD_PAIRED_NATIVE_DELIVERY'])
    return json.loads((root/'native-shape.fixture.json').read_bytes())


def test_observed_names_and_canonical_role_counts():
    f=fixture(); task=f['task']['data']; base=task['name'][:-9]
    assert job_matches(task['name'],base,task['uid'],'lpai-uid-suffix')
    assert not job_matches(base+'-wrong',base,task['uid'],'lpai-uid-suffix')
    assert not job_matches(task['name'],base,'foreign','lpai-uid-suffix')
    assert roles(f['task'])['gpus']==1
    t=deepcopy(f['task']); r=t['data']['data']['lpai_task']['roles'][0]
    r['resource'].update(Gpu=4,gpu=0,replica=4)
    assert roles(t)['gpus']==4  # Gpu already totals package count; lower-case gpu=0 is not authoritative.
    r.update(replica=2); assert roles(t)['gpus']==8


@pytest.mark.parametrize('field,bad',[('replica',True),('Gpu',-1),('Gpu','4'),('role','')])
def test_role_ambiguity_never_becomes_zero_cost(field,bad):
    t=fixture()['task']; role=t['data']['data']['lpai_task']['roles'][0]
    if field=='Gpu':role['resource'][field]=bad
    else:role[field]=bad
    with pytest.raises(ValueError):roles(t)


def quote(task, now):
    allocation=roles(task); native=task['data']
    return {'schema':'sdm-lpai-role-quote/v1','status':'verified','cluster':native['cluster'],
            'namespace':native['namespace'],'queue_code':native['queue_code'],'currency':'fixture-currency',
            'role_signature_digest':digest(allocation['signature']),'hourly_cost':2.5,
            'observed_at':now-1,'valid_until':now+10,'source_ref':'SYNTHETIC CPU quote only'}


@pytest.mark.parametrize('fault',['none','expired','role','namespace','price'])
def test_bound_quote_scope_freshness_and_roles(tmp_path,fault):
    t=fixture()['task']; now=time.time(); q=quote(t,now)
    if fault=='expired':q['valid_until']=now
    if fault=='role':q['role_signature_digest']='foreign'
    if fault=='namespace':q['namespace']='other-project'
    if fault=='price':q['hourly_cost']=True
    qref=ref(tmp_path/'quote.json',q)
    if fault=='none':
        result=priced_allocation(t,qref,now,'fixture-currency');assert result['hourly_cost']==2.5 and not result['billing_is_measured']
    else:
        with pytest.raises(ValueError):priced_allocation(t,qref,now,'fixture-currency')


@pytest.mark.parametrize('fault',['none','succeeded-only','empty','foreign','running'])
def test_release_requires_same_uid_and_deleted_compute_pods(fault):
    f=fixture(); t=f['task']; p=f['pods']; uid=t['data']['uid']
    if fault=='succeeded-only':
        for pod in p['data']:pod['status']='Succeeded'
    if fault=='empty':p['data']=[]
    if fault=='foreign':p['data'][0]['task_uid']='foreign'
    if fault=='running':t['data']['status']='running'
    if fault=='foreign':
        with pytest.raises(ValueError):release_readback(t,p,uid,t['data']['namespace'],t['data']['owner'])
    else:
        result=release_readback(t,p,uid,t['data']['namespace'],t['data']['owner'])
        assert result['released'] is (fault=='none')


def test_native_envelope_uid_suffix_lookup_and_quote_readback(tmp_path):
    f=fixture(); r=request(); task=f['task']['data']; uid=task['uid']
    task['name']=r['job_name']+'-'+uid[:8];task['describe']='bmad-paired-request:'+r['request_key']
    s=settings(); fields=observed_field_map();fields['start']={'uid':['uid']}
    s.update(fields=fields,job_name_policy='lpai-uid-suffix',list_item_envelope='data',allocation_mode='lpai-role-quote',
             namespace=task['namespace'],owner=task['owner'],
             phase_map={'succeeded':'succeeded'},release_mode='task-pods-deleted')
    for arm in s['arms'].values():
        arm['component_uid']=task['project_uid'];arm['quote_ref']=ref(tmp_path/'quote.json',quote(f['task'],1000))
    class Replay:
        def __call__(self,argv,timeout):
            if argv[1:3]==['tasks','get']:return f['task']
            if argv[1:3]==['tasks','pods']:return f['pods']
            if argv[1:3]==['tasks','list']:return {'status':'ok','data':{'items':[task],'total':1}}
            raise AssertionError('mutating command in readonly replay')
    h=Hooks(tmp_path);h.released=True
    h.observe=lambda request,response:{'uid':response['data']['uid'],'request_key':request['request_key'],
                                      'released':True,'observed_at':1000,'release_evidence_ref':'SYNTHETIC CPU release'}
    a=LpaiAdapter(s,transport=Replay(),hooks=h,clock=lambda:1000)
    result=a.lookup(r)
    assert result['complete'] and result['matches'][0]['uid']==uid
    assert result['matches'][0]['gpus']==1 and result['matches'][0]['allocation_provenance']['cost_basis']=='bound-role-quote'


def test_pending_approval_does_not_become_submitted_task(tmp_path):
    r=request(); t=Transport(r); h=Hooks(tmp_path); a=LpaiAdapter(settings(),transport=t,hooks=h,clock=lambda:1000)
    a.preflight(r)
    a.transport=lambda argv,timeout:{'status':'pending_approval','approval_id':'fixture-approval'}
    response=a.submit(r)
    assert response=={'outcome':'approval-pending','approval_id':'fixture-approval','task_created_verified':False}


def project_module():
    path=Path(os.environ['BMAD_PAIRED_PROJECT_HOOK'])
    spec=importlib.util.spec_from_file_location('roadmodel_paired_test_hooks',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_archival_real_trajectory_and_source_are_read_only_evidence():
    root=Path(os.environ['BMAD_PAIRED_NATIVE_DELIVERY']).parents[1]
    evidence=root/'_bmad-output/roadmodel-small-loop-20261014/runtime-evidence'; hook=project_module()
    manifest=json.loads((evidence/'inputs/source-manifest-v6.json').read_bytes())
    assert hook.verify_source(Path(os.environ['BMAD_PAIRED_NATIVE_DELIVERY'])/'archival-source',manifest)['source_files_verified']==409
    trajectory=hook.trajectory(json.loads((evidence/'output/model-probe-progress.json').read_bytes()))
    assert trajectory['healthy_optimizer_updates']==0 and 'nonfinite-gradient-streak' in trajectory['flags']


@pytest.mark.parametrize('count,expected',[(1,False),(4,False),(5,True)])
def test_gradient_streak_policy_does_not_stop_on_one_amp_overflow(count,expected):
    hook=project_module();step={'loss':1.,'loss_finite':True,'grad_norm_finite':False,'optimizer_updated':False,
                               'observations':{'unscaled_gradients_before_clip':{'parameters_with_nonfinite_elements':3}}}
    result=hook.trajectory({'steps':[step]*count})
    assert ('nonfinite-gradient-streak' in result['flags']) is expected


def test_finite_element_norm_overflow_is_not_corrupt_gradient():
    hook=project_module();step={'loss':1.,'loss_finite':True,'grad_norm_finite':False,'optimizer_updated':False,
                               'observations':{'unscaled_gradients_before_clip':{'parameters_with_nonfinite_elements':0}}}
    result=hook.trajectory({'steps':[step]*5})
    assert result['finite_element_norm_overflows']==5 and result['flags']==[]


def test_project_single_task_policy_blocks_parallel_before_s12_or_source_reads(tmp_path):
    hook=project_module();r=request();r['group_limits']={'max_concurrent_tasks':2}
    s={'project_policy_ref':ref(tmp_path/'policy.json',{'resource_limits':{'max_active_compute_tasks':1}}),'arms':{'control':{}}}
    with pytest.raises(ValueError,match='concurrency_scope'):hook.Hooks(s).preflight(r,{})


@pytest.mark.parametrize('fault',['none','source','stale','mounts','body','s12','missing-roadmodel'])
def test_project_preflight_requires_exact_frozen_inputs_and_normal_checks(tmp_path,monkeypatch,fault):
    import hashlib
    h=project_module();r=request();r.update(group_schedule='sequential',group_limits={'max_concurrent_tasks':1})
    source=tmp_path/'source';source.mkdir();file=source/'model.py';file.write_text('SYNTHETIC CPU source, never imported')
    manifest_ref=ref(tmp_path/'manifest.json',{'files':{'model.py':hashlib.sha256(file.read_bytes()).hexdigest()}})
    profile_ref=ref(tmp_path/'profile.json',{'purpose':'SYNTHETIC CPU preflight'})
    t=fixture()['task']['data'];t['data']['lpai_git']=[{'name':'roadmodel','commit_id':'f'*40}]
    t['data']['lpai_task']['roles'][0]['resource']['Gpu']=r['declared_arm']['allocation']['gpus']
    if fault=='missing-roadmodel':t['data']['lpai_git'][0]['name']='unrelated'
    body_ref=ref(tmp_path/'body.json',t)
    receipt={'schema':'roadmodel-paired-preflight/v1','status':'pass','arm_digest':r['declared_arm_digest'],
             'profile_digest':profile_ref['digest'],'normal_s12_verified':True,'normal_s12_ref':'SYNTHETIC CPU ONLY',
             'observed_at':time.time(),'checks':{k:True for k in ('source','data','image','mounts','distributed','gpu_isolation','output_isolation')},
             'body_digest':body_ref['digest'],'deadline':r['deadline'],'source_commit':'f'*40}
    if fault=='stale':receipt['observed_at']=0
    if fault=='mounts':receipt['checks']['mounts']=False
    if fault=='body':receipt['body_digest']='foreign'
    if fault=='s12':receipt['normal_s12_verified']=False
    if fault=='source':file.write_text('changed source must not execute')
    normal_ref=ref(tmp_path/'normal.json',receipt)
    qualification=ref(tmp_path/'qualification.json',{'status':'verified','owner':'fixture-owner','namespace':'fixture-namespace','scope':'SYNTHETIC ONLY'})
    monkeypatch.setattr(h,'proof',lambda path,req:{'deadline':req['deadline'],'scope':'allocation-release','independent':True,
                                                'evidence_ref':'SYNTHETIC CPU guard','platform_release_qualification_ref':qualification})
    settings={'namespace':'fixture-namespace','owner':'fixture-owner','release_watch_heartbeat':'SYNTHETIC',
              'project_policy_ref':ref(tmp_path/'policy.json',{'resource_limits':{'max_active_compute_tasks':1}}),
              'external_allocations_ref':ref(tmp_path/'external.json',{'project':r['project'],'observed_at':time.time(),'allocations':[]}),
              'arms':{'control':{'source_root':str(source),'source_manifest_ref':manifest_ref,'profile_ref':profile_ref,
                                 'normal_preflight_ref':normal_ref,'body_ref':body_ref}}}
    if fault=='none':assert h.Hooks(settings).preflight(r,{})['ready'] is True
    else:
        with pytest.raises(ValueError):h.Hooks(settings).preflight(r,{})
