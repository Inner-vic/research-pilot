"""CPU health contracts and real local subprocess protection; no platform calls."""
import hashlib
import json
import os
from pathlib import Path

import pytest
from devspace_health import HealthGuard, validate_health
from test_devspace_execution import setup_case, pin, public


def policy():
    return dict(version=1, stop_authorized=True, protocol='fixed-holdout-v1', metric='iou',
        direction='maximize', baseline=.8, failure_boundary=.01, minimum_change=.5,
        minimum_samples=4, bad_evaluations=2, bad_updates=2, max_overflow_skips=4,
        interval_seconds=.02, sample_timeout_seconds=5, checkpoint_file='checkpoint.json', predictions_file='prediction.json')


def sample(root, seq, **changes):
    pred={'protocol':'fixed-holdout-v1','sample_ids':[1,2,3,4], 'targets':[0,1,2,3], 'predictions':[.1,1.1,2.1,3.1]}
    for name, value in [('checkpoint.json',{'weights':[seq]}),('prediction.json',pred)]:
        (root/name).write_text(json.dumps(value))
    row=dict(binding='bound',sequence=seq,optimizer_updates=seq,update_applied=True,loss=.1,
        protocol='fixed-holdout-v1',metric_name='iou',metric_value=.7,valid_samples=4)
    row.update(changes)
    for name in ['checkpoint_file','predictions_file']:
        row[name+'_sha256']=hashlib.sha256((root/policy()[name]).read_bytes()).hexdigest()
    tmp=root/'sample.tmp';tmp.write_text(json.dumps(row));tmp.replace(root/'health-sample.json')


def test_metric_collapse_keeps_healthy_and_first_anomaly_with_comparison(tmp_path):
    guard=HealthGuard(tmp_path,'bound',policy());sample(tmp_path,1);assert guard.poll() is None
    sample(tmp_path,2,metric_value=0);assert guard.poll() is None
    healthy=(tmp_path/'health/healthy-checkpoint.bin').read_bytes()
    anomaly=(tmp_path/'health/anomaly-checkpoint.bin').read_bytes()
    sample(tmp_path,3,metric_value=0);assert guard.poll()=='evaluation_collapse'
    assert healthy!=anomaly and (tmp_path/'health/healthy-checkpoint.bin').read_bytes()==healthy
    assert (tmp_path/'health/anomaly-checkpoint.bin').read_bytes()==anomaly
    assert guard.healthy['recovery_candidate'] and not guard.anomaly['recovery_candidate']
    assert (tmp_path/'health/comparison.svg').is_file()
    assert not json.loads((tmp_path/'health/comparison.json').read_bytes())['root_cause_confirmed']


def test_amp_skips_and_single_nonfinite_do_not_mean_sustained_divergence(tmp_path):
    g=HealthGuard(tmp_path,'bound',policy())
    sample(tmp_path,1,optimizer_updates=0,update_applied=False,amp_overflow=True,loss='NaN');assert g.poll() is None
    sample(tmp_path,2,optimizer_updates=0,update_applied=False,amp_overflow=True,loss='NaN');assert g.poll() is None
    sample(tmp_path,3,optimizer_updates=1,loss='NaN');assert g.poll() is None
    sample(tmp_path,4,optimizer_updates=2);assert g.poll() is None
    sample(tmp_path,5,optimizer_updates=3,loss='NaN');assert g.poll() is None
    sample(tmp_path,6,optimizer_updates=4,loss='NaN');assert g.poll()=='nonfinite_actual_updates'


@pytest.mark.parametrize('change',[{'protocol':'other'},{'valid_samples':0},{'metric_name':'other'},{'metric_value':None}])
def test_incompatible_or_missing_eval_breaks_bad_sequence(tmp_path,change):
    g=HealthGuard(tmp_path,'bound',policy());sample(tmp_path,1,metric_value=0);assert g.poll() is None
    sample(tmp_path,2,metric_value=0,**{k:v for k,v in change.items() if k!='metric_value'}) if 'metric_value' not in change else sample(tmp_path,2,**change)
    assert g.poll() is None
    sample(tmp_path,3,metric_value=0);assert g.poll() is None


def test_sampling_storage_failure_never_suppresses_stop(tmp_path,monkeypatch):
    g=HealthGuard(tmp_path,'bound',policy());sample(tmp_path,1,metric_value=0);g.poll()
    sample(tmp_path,2,metric_value=0)
    monkeypatch.setattr(g,'write',lambda *a: (_ for _ in ()).throw(OSError('disk full')))
    assert g.poll()=='evaluation_collapse'


def test_stale_missing_evidence_has_separate_stop_reason(tmp_path):
    g=HealthGuard(tmp_path,'bound',policy());g.last_valid_at=0
    assert g.check()=='health_observation_timeout' and g.bad_eval==0


def test_policy_requires_authorized_bounds_and_minimize_semantics(tmp_path):
    p=policy();p['stop_authorized']=False
    with pytest.raises(ValueError,match='authorization'):validate_health(p)
    p=policy();p.update(direction='minimize',baseline=.1,failure_boundary=1,minimum_change=.5)
    g=HealthGuard(tmp_path,'bound',p)
    sample(tmp_path,1,metric_value=2);assert g.poll() is None
    sample(tmp_path,2,metric_value=2);assert g.poll()=='evaluation_collapse'


def test_portable_trial_builder_zero_cost_no_platform_or_review_fabrication(tmp_path):
    import subprocess,sys
    script=Path(__file__).parents[1]/'prepare_cpu_trial.py'
    root=tmp_path/'portable'
    args=[sys.executable,'-B',str(script),'--output',str(root),'--authorization-ref','CPU-test-user-grant']
    missing=subprocess.run(args,capture_output=True,text=True)
    assert missing.returncode!=0 and not root.exists()
    ok=subprocess.run(args+['--authorize-local-cpu'],capture_output=True,text=True)
    assert ok.returncode==0,ok.stderr
    config=json.loads((root/'project.json').read_bytes())
    assert config['devspace_execution']['plan']['hourly_rate']==0
    assert 'task_event_wakeup' not in config and not (root/'attempt').exists()


WORKLOAD = '''import hashlib,json,os,time
from pathlib import Path
def save(n,v):
 p=Path(n+'.tmp');p.write_text(json.dumps(v));p.replace(n)
save('result.json',{'score':.1})
for seq in range(1,101):
 save('checkpoint.json',{'weights':[seq]})
 save('prediction.json',{'protocol':'fixed-holdout-v1','sample_ids':[1,2,3,4],'targets':[0,1,2,3],'predictions':[0,1,2,3]})
 row=dict(binding=os.environ['SDM_EXECUTION_BINDING'],sequence=seq,optimizer_updates=seq,update_applied=True,loss=.1,protocol='fixed-holdout-v1',metric_name='iou',metric_value=.8 if seq==1 else 0,valid_samples=4)
 for key,name in [('checkpoint_file','checkpoint.json'),('predictions_file','prediction.json')]:row[key+'_sha256']=hashlib.sha256(Path(name).read_bytes()).hexdigest()
 save('health-sample.json',row)
 time.sleep(.15)
'''


def test_public_execution_capsule_stops_without_observer_then_collects_review(tmp_path):
    config,root=setup_case(tmp_path,WORKLOAD,duration=20)
    config['devspace_execution']['plan']['health_policy']=policy();pin(config)
    started=public(config,root)
    import time
    # No coordinator polling while the independent worker makes the stop decision.
    for _ in range(100):
        state=json.loads((tmp_path/'remote/process.json').read_bytes())
        if state['phase']=='exited':break
        time.sleep(.05)
    assert state['phase']=='exited' and state['termination_reason']=='health:evaluation_collapse'
    assert state['exit_code']!=0 and state['health']['accepted_evaluations']>=3
    done=public(config,root,'--watch-devspace')
    assert done['status']=='awaiting-review' and done['outcome']=='failed' and done['stop_verified']
    packet=json.loads(Path(done['review']['request_path']).read_bytes())
    assert 'health:evaluation_collapse' in packet['evidence'].values()
    assert (root/'artifacts/health/comparison.svg').is_file()
    claim=(tmp_path/'remote/launch-intent.json').read_bytes();public(config,root)
    assert claim==(tmp_path/'remote/launch-intent.json').read_bytes()


def test_missing_health_evidence_cannot_be_exit_zero_success(tmp_path):
    config,root=setup_case(tmp_path)
    config['devspace_execution']['plan']['health_policy']=policy();pin(config)
    done=public(config,root,'--watch-devspace')
    assert done['outcome']=='failed' and done['process']['termination_reason']=='health:evaluation_evidence_missing'


def test_recovered_isolated_anomaly_advances_healthy_and_starts_new_episode(tmp_path):
    g=HealthGuard(tmp_path,'bound',policy())
    for seq,change in [(1,{}),(2,{'loss':'NaN'}),(3,{}),(4,{'metric_value':0}),(5,{'metric_value':0})]:
        sample(tmp_path,seq,**change);reason=g.check()
    assert reason=='evaluation_collapse'
    assert g.healthy['sequence']==3 and g.anomaly['sequence']==2 and g.episode['sequence']==4
    assert g.healthy['recovery_candidate'] and not g.episode['recovery_candidate']
    comparison=json.loads((tmp_path/'health/comparison.json').read_bytes())
    assert comparison['root_cause_confirmed'] is False


def test_second_source_failure_preserves_committed_snapshot_pair(tmp_path):
    g=HealthGuard(tmp_path,'bound',policy());sample(tmp_path,1);g.poll()
    original={name:(tmp_path/'health'/name).read_bytes() for name in ['healthy.snapshot.zip','healthy-checkpoint.bin','before.json']}
    sample(tmp_path,2)
    row=json.loads((tmp_path/'health-sample.json').read_bytes());row['predictions_file_sha256']='0'*64
    (tmp_path/'health-sample.json').write_text(json.dumps(row));g.poll()
    assert g.healthy['sequence']==1 and g.qualification()=='snapshot_evidence_incomplete'
    assert all((tmp_path/'health'/n).read_bytes()==raw for n,raw in original.items())
    sample(tmp_path,3);g.poll()
    assert g.healthy['sequence']==3 and g.qualification() is None


def test_bundle_commit_failure_keeps_previous_canonical_evidence(tmp_path,monkeypatch):
    g=HealthGuard(tmp_path,'bound',policy());sample(tmp_path,1);g.poll()
    original=(tmp_path/'health/healthy.snapshot.zip').read_bytes()
    real=g.write_bytes
    def fail(name,raw):
        if name.endswith('.snapshot.zip'):raise OSError('simulated commit failure')
        real(name,raw)
    monkeypatch.setattr(g,'write_bytes',fail)
    sample(tmp_path,2);g.poll()
    assert (tmp_path/'health/healthy.snapshot.zip').read_bytes()==original and g.healthy['sequence']==1
    assert g.qualification()=='snapshot_evidence_incomplete'


def test_progress_timeout_is_independent_of_fresh_evaluation_and_explicit(tmp_path):
    p=policy();p['progress_timeout_seconds']=10
    g=HealthGuard(tmp_path,'bound',p);g.last_update_at=0
    sample(tmp_path,1,optimizer_updates=0,update_applied=False,amp_overflow=False)
    assert g.check()=='optimizer_progress_timeout' and g.accepted==1
    sample(tmp_path,2);assert g.check() is None and g.qualification() is None
    p['progress_timeout_seconds']=0
    with pytest.raises(ValueError,match='optimizer_progress_timeout'):validate_health(p)


def test_valid_metrics_without_snapshots_cannot_pass_public_execution(tmp_path):
    code=WORKLOAD.replace(".8 if seq==1 else 0", ".8").replace('range(1,101)','range(1,4)')
    code=code.replace(" save('health-sample.json',row)", " row['checkpoint_file_sha256']='0'*64\n save('health-sample.json',row)")
    config,root=setup_case(tmp_path,code)
    config['devspace_execution']['plan']['health_policy']=policy();pin(config)
    done=public(config,root,'--watch-devspace')
    assert done['process']['exit_code']==0 and done['outcome']=='failed' and done['stop_verified']
    assert done['process']['termination_reason']=='health:snapshot_evidence_incomplete'
    assert done['process']['health']['evidence_complete'] is False


def test_old_unqualified_health_observation_is_not_success():
    from devspace_execution import controlled_result
    assert controlled_result({'health_policy':policy()},{'exit_code':0,'health':{'accepted_evaluations':3}})['outcome']=='failed'


def test_collection_detects_snapshot_tampering_but_still_stops_resource(tmp_path):
    code=WORKLOAD.replace('.8 if seq==1 else 0','.8').replace('range(1,101)','range(1,4)')
    config,root=setup_case(tmp_path,code)
    config['devspace_execution']['plan']['health_policy']=policy();pin(config)
    public(config,root)
    import time
    for _ in range(100):
        state=json.loads((tmp_path/'remote/process.json').read_bytes())
        if state['phase']=='exited':break
        time.sleep(.03)
    assert state['phase']=='exited'
    (tmp_path/'remote/health/healthy.snapshot.zip').write_bytes(b'tampered')
    done=public(config,root,'--watch-devspace')
    assert done['stop_verified'] and done['status']=='released-evidence-incomplete'
    assert not done.get('artifacts_collected') and 'health_snapshot_digest_changed' in done['artifact_error']


def test_zero_updates_cannot_qualify_even_with_fresh_metrics(tmp_path):
    g=HealthGuard(tmp_path,'bound',policy())
    sample(tmp_path,1,optimizer_updates=0,update_applied=False);g.poll()
    assert g.accepted==1 and g.qualification()=='optimizer_progress_missing'
