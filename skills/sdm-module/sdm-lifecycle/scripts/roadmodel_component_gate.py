"""New-component bootstrap: preflight, wait for node 15, execute exactly once.

The shared control directory must be a restricted, explicitly mounted volume.
An existing autostart component must never be retrofitted while a task is active.
This module does not create resources or collect platform credentials.
"""
import argparse
import hashlib
import json
import os
import math
import re
import signal
from pathlib import Path
import subprocess
import sys
import time


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for part in iter(lambda:stream.read(4*1024*1024),b''):h.update(part)
    return 'sha256:'+h.hexdigest()


def publish(path,value):
    # Exclusive publication. A restarted component cannot rewrite execution proof.
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    with temp.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    try:os.link(temp,path)
    finally:temp.unlink()


def preflight(bundle,control):
    plan=bundle['plan'];source=Path(plan['runtime_source_root'])
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip()
    if commit!=plan['source_commit']:raise ValueError('runtime_source_commit_mismatch')
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=source,text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip():
        raise ValueError('runtime_source_tracked_dirty')
    for name,wanted in bundle['source_files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or sha(source/name)!=wanted:
            raise ValueError('runtime_source_file_mismatch')
    for ref in bundle['runtime_assets']:
        if sha(ref['path'])!=ref['digest']:raise ValueError('runtime_asset_digest_mismatch')
    config=Path(plan['runtime_config_path'])
    if sha(config)!=plan['effective_config_digest']:raise ValueError('runtime_configuration_readback_mismatch')
    output=Path(plan['runtime_output_root'])
    if output.exists() and any(output.iterdir()):raise ValueError('runtime_output_not_empty')
    output.mkdir(parents=True,exist_ok=True)
    gpus=subprocess.check_output(['nvidia-smi','--query-gpu=uuid','--format=csv,noheader'],text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip().splitlines()
    visible=int(subprocess.check_output([sys.executable,'-B','-c','import torch; assert hasattr(torch.optim.Optimizer,"register_step_post_hook"); print(torch.cuda.device_count())'],text=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).strip())
    if visible!=plan['distributed']['gpus_per_node'] or len(gpus)<visible:raise ValueError('runtime_gpu_count_mismatch')
    return {'identity':plan['identity'],'source_commit':commit,'data_digest':plan['data_digest'],
        'effective_config_digest':plan['effective_config_digest'],'world_size':plan['world_size'],
        'visible_gpus_per_node':visible,'runtime_output_root':str(output),'output_empty':True,
        'observed_at':time.time(),'valid_until':time.time()+bundle['preflight_valid_seconds'],
        'plan_digest':bundle['plan_digest'],'gpu_uuids':gpus}


def validate_permit(bundle,ready,permit):
    if permit!={'schema':'roadmodel-start-permit/v1','plan_digest':bundle['plan_digest'],
                'identity':bundle['plan']['identity'],'ready_digest':ready,'node':'15'}:
        raise ValueError('normal_start_permit_mismatch')


def normal_permit(bundle,control,node,ready_digest):
    name='start-permit.json' if node=='15' else 'evaluation-permit.json'
    path=control/name
    if not path.exists():return None
    permit=json.loads(path.read_bytes())
    if (permit.get('plan_digest')!=bundle['plan_digest'] or permit.get('identity')!=bundle['plan']['identity']
        or permit.get('node')!=node or permit.get('authorization_digest')!=bundle['authorization_digest']):
        raise ValueError('normal_start_permit_mismatch')
    if node=='15' and permit.get('ready_digest')!=ready_digest:raise ValueError('normal_start_permit_stale')
    request_id=permit['request_id']
    if not re.fullmatch(r'REQ-[A-Za-z0-9_.-]+',request_id):raise ValueError('normal_request_id_invalid')
    request=control/(request_id+'.S12.json')
    if sha(request)!=permit['s12_digest']:raise ValueError('normal_s12_packet_changed')
    packet=json.loads(request.read_bytes());subjects=packet['subjects']
    if packet['operation']!='ml.normal-execution' or any(subjects.get(k)!=v for k,v in {
        'plan_digest':bundle['plan_digest'],'authorization_digest':bundle['authorization_digest'],
        'execution_scope':'normal-training','component_uid':bundle['component_uid'],
        'execution_kind':'run' if node=='15' else 'evaluate','bundle_digest':sha(control/'bundle.json')}.items()):
        raise ValueError('normal_s12_scope_mismatch')
    return permit


class TrainingHealth:
    """Observe emitted training records, not echoed commands or install output."""
    def __init__(self,limit=5):
        self.limit=limit;self.streak=0;self.last=None;self.update_records=False

    def feed(self,line):
        if line.startswith('NORMAL_UPDATE_HEALTH '):
            try:
                value=json.loads(line.split(' ',1)[1])
                if (value['schema']!='normal-update-health/v1' or type(value['attempt']) is not int
                    or value['attempt']<1 or type(value['rank']) is not int or type(value['stop']) is not bool):
                    raise ValueError('invalid update observation')
            except (ValueError,KeyError,TypeError):return {'reason':'invalid_update_health_record'}
            self.update_records=True
            if value['stop']:return {'reason':value['classification'],'update_observation':value}
            return None
        match=re.search(r'\[TRAIN\] Epoch\s+(\d+) Iter\s+(\d+)/\d+: Loss:\s*([^\s]+).*grad_norm:\s*([^,]+)',line)
        if not match:return None
        step=tuple(map(int,match.group(1,2)))
        if self.last is not None and step<=self.last:return None
        self.last=step
        loss,grad=(float(match[i]) for i in (3,4))
        self.streak=self.streak+1 if not math.isfinite(grad) else 0
        # Once the pinned observer emits real update evidence, its in-process
        # guard owns gradient decisions. Raw TRAIN grad_norm may be stale during
        # accumulation; nonfinite forward loss always remains a hard stop.
        if not math.isfinite(loss) or (not self.update_records and self.streak>=self.limit):
            return {'reason':'persistent_nonfinite_training','epoch':step[0],'iteration':step[1],
                'consecutive_nonfinite_records':self.streak,'loss_finite':math.isfinite(loss)}


def process(argv,source,log,timeout):
    child=None
    def terminate(*_):
        if child is not None and child.poll() is None:
            os.killpg(child.pid,signal.SIGTERM)
        raise SystemExit(143)
    previous=signal.signal(signal.SIGTERM,terminate)
    try:
        with log.open('xb') as stream:
            child=subprocess.Popen(argv,cwd=source,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            started=time.monotonic();health=TrainingHealth();pending=''
            with log.open(errors='replace') as reader:
                while True:
                    pending+=reader.read()
                    lines=pending.split('\n');pending=lines.pop()
                    if len(pending)>1048576:pending=pending[-1048576:]
                    for line in lines:
                        fault=health.feed(line)
                        if fault:
                            publish(log.with_suffix('.health.json'),fault)
                            if child.poll() is None:
                                os.killpg(child.pid,signal.SIGTERM)
                                try:child.wait(timeout=10)
                                except subprocess.TimeoutExpired:
                                    os.killpg(child.pid,signal.SIGKILL);child.wait()
                            return 65
                    if child.poll() is not None:return child.returncode
                    if timeout is not None and time.monotonic()-started>=timeout:
                        os.killpg(child.pid,signal.SIGKILL);child.wait();return -9
                    time.sleep(.1)
    finally:
        if child is not None and child.poll() is None:
            os.killpg(child.pid,signal.SIGKILL);child.wait()
        signal.signal(signal.SIGTERM,previous)


def training_report(bundle,control,code,permit):
    plan=bundle['plan'];rows=[];ranks=[]
    for rank in range(plan['world_size']):
        path=control/('run-rank-'+str(rank)+'.json')
        if not path.exists():path=control/('run-rank-'+str(rank)+'-progress.json')
        if path.exists():
            value=json.loads(path.read_bytes());ranks.append(value)
            if rank==0:
                for row in value.get('checkpoints',[]):
                    relative=Path(row['relative_path'])
                    if relative.is_absolute() or '..' in relative.parts:raise ValueError('normal_checkpoint_path_invalid')
                    path=Path(plan['runtime_output_root'])/relative
                    if path.is_file():rows.append({'id':'epoch-'+str(row['epoch']),'origin':plan['identity'],
                        'complete':True,'epoch':row['epoch'],'relative_path':relative.as_posix(),
                        'digest':sha(path),'components':row['components'],
                        'health_verified':row.get('health_verified',False),'finite_state':row.get('finite_state',False)})
    index={'schema':'roadmodel-checkpoint-index/v1','checkpoints':rows}
    publish(control/'checkpoint-index.json',index)
    completed_epochs=max((r['epoch'] for r in rows),default=0)
    complete=code==0 and len(ranks)==plan['world_size'] and all(r.get('completed') is True and r.get('optimizer_updates',0)>0 for r in ranks) and completed_epochs==plan['training']['epochs']
    nonfinite=0;exceptions=[]
    log=control/'training.log'
    if log.exists():
        with log.open(errors='replace') as stream:
            for line in stream:
                if 'non-finite grad_norm' in line or 'non-finite loss' in line:nonfinite+=1
                if re.match(r'^(?:torch\.(?:cuda\.)?OutOfMemoryError|RuntimeError):.*(?:CUDA out of memory|out of memory)',line.strip()):
                    if len(exceptions)<10:exceptions.append({'kind':'runtime-exception','text':line.strip()[:4096]})
    health=control/'training.health.json'
    if health.exists():exceptions.append({'kind':'training-health-stop','detail':json.loads(health.read_bytes())})
    return {'exit_code':code,'completed_epochs':completed_epochs,
        'optimizer_updates':min((r.get('optimizer_updates',0) for r in ranks),default=0),
        'world_size':plan['world_size'],'completed':complete,'checkpoint_index_digest':sha(control/'checkpoint-index.json'),
        'rank_observations':ranks,'full_resume_qualified':False,'request_id':permit['request_id'],
        'nonfinite_warning_count':nonfinite,'training_log_digest':sha(log) if log.exists() else None,
        'failure_evidence':{'training_started':True,'lines':exceptions}}


def normal_run(bundle,control,ready):
    plan=bundle['plan']
    ready.update(bundle_digest=sha(control/'bundle.json'),session_nonce=bundle['session_nonce'],
        runtime_control_root=str(control))
    publish(control/'ready.json',ready)
    while True:
        for request in control.glob('preflight-request-*.json'):
            target=control/('ready-'+request.stem.removeprefix('preflight-request-')+'.json')
            if not target.exists():
                refreshed=preflight(bundle,control);refreshed.update(bundle_digest=ready['bundle_digest'],
                    session_nonce=bundle['session_nonce'],runtime_control_root=str(control))
                publish(target,refreshed)
        # The node-15 permit binds whichever fresh observation node 13 accepted.
        if (control/'start-permit.json').exists():
            wanted=json.loads((control/'start-permit.json').read_bytes())['ready_digest']
            candidates=[control/'ready.json',*control.glob('ready-*.json')]
            if not any(sha(p)==wanted for p in candidates):raise ValueError('normal_start_permit_stale')
            permit=normal_permit(bundle,control,'15',wanted);break
        time.sleep(1)
    preflight(bundle,control)
    publish(control/'started.json',{'plan_digest':bundle['plan_digest'],'request_id':permit['request_id']})
    code=process(bundle['observed_argv'],plan['runtime_source_root'],control/'training.log',plan['training_wall_seconds'])
    actual=training_report(bundle,control,code,permit)
    publish(control/'training-result.json',actual)
    if not actual['completed']:return code or 1
    while True:
        permit=normal_permit(bundle,control,'16',None)
        if permit is not None:break
        time.sleep(1)
    checkpoint=Path(plan['runtime_output_root'])/permit['checkpoint_relative_path']
    if not checkpoint.resolve().is_relative_to(Path(plan['runtime_output_root']).resolve()) or sha(checkpoint)!=permit['checkpoint_digest']:
        raise ValueError('normal_evaluation_checkpoint_changed')
    argv=[sys.executable,'-B',plan['runtime_source_root']+'/eval.py','--py-config',plan['runtime_config_path'],
        '--work-dir',plan['runtime_output_root']+'/evaluation','--resume-from',str(checkpoint),'--seed',str(plan['training']['seed'])]
    publish(control/'evaluation-started.json',{'request_id':permit['request_id'],'checkpoint_digest':sha(checkpoint),'argv':argv})
    code=process(argv,plan['runtime_source_root'],control/'evaluation.log',bundle.get('evaluation_wall_seconds'))
    metrics={}
    # Bounded line streaming; no multi-GB raw log load or fabricated metrics.
    with (control/'evaluation.log').open(errors='replace') as stream:
        for line in stream:
            for name,spec in bundle['evaluation_metrics'].items():
                match=re.search(spec['pattern'],line)
                if match:
                    value=float(match.group(spec.get('group',1)))
                    if math.isfinite(value):metrics[name]=value
    publish(control/'evaluation-result.json',{'request_id':permit['request_id'],'exit_code':code,
        'evaluation_complete':code==0 and set(metrics)==set(bundle['evaluation_metrics']),
        'checkpoint_digest':sha(checkpoint),'metrics':metrics,'argv':argv,'evaluation_world_size':1})
    return code


def run(bundle,control):
    budget=bundle.get('allocation_wall_seconds')
    if budget is None:return _run(bundle,control)
    if type(budget) not in (int,float) or not math.isfinite(budget) or budget<=0:raise ValueError('normal_allocation_budget_invalid')
    def expired(*_):raise TimeoutError('normal_conservative_allocation_budget_exhausted')
    previous=signal.signal(signal.SIGALRM,expired)
    old=signal.setitimer(signal.ITIMER_REAL,budget)
    started=time.monotonic()
    try:return _run(bundle,control)
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)
        if old[0]:signal.setitimer(signal.ITIMER_REAL,max(.000001,old[0]-(time.monotonic()-started)),old[1])


def _run(bundle,control):
    control=Path(control);control.mkdir(parents=True,exist_ok=True)
    if bundle['plan']['distributed']['nodes']!=1:raise ValueError('component_gate_single_node_only')
    if not bundle['runtime_assets']:raise ValueError('runtime_assets_required')
    # Any prior marker is a reconciliation condition, never permission to rerun.
    if (control/'started.json').exists():raise ValueError('prior_execution_requires_reconciliation')
    ready=preflight(bundle,control)
    if bundle.get('normal_session'):return normal_run(bundle,control,ready)
    publish(control/'ready.json',ready)
    while not (control/'start-permit.json').exists():time.sleep(1)
    validate_permit(bundle,sha(control/'ready.json'),json.loads((control/'start-permit.json').read_bytes()))
    # Recheck the bytes, GPU visibility and empty output after waiting in queue.
    preflight(bundle,control)
    publish(control/'started.json',{'plan_digest':bundle['plan_digest'],'at':time.time()})
    with (control/'training.log').open('xb') as log:
        result=subprocess.run(bundle['plan']['argv'],cwd=bundle['plan']['runtime_source_root'],
            stdout=log,stderr=subprocess.STDOUT,timeout=bundle['plan']['training_wall_seconds'], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    publish(control/'process-result.json',{'exit_code':result.returncode,'plan_digest':bundle['plan_digest'],
        'training_success_verified':False,'qualification':'process exit only; metrics and S13 acceptance required'})
    return result.returncode


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--bundle',required=True);p.add_argument('--control',required=True)
    a=p.parse_args()
    try:code=run(json.loads(Path(a.bundle).read_bytes()),a.control)
    except Exception as error:
        import traceback
        control=Path(a.control)
        if control.is_dir() and not (control/'runtime-failure.json').exists():
            publish(control/'runtime-failure.json',{'exception_type':type(error).__name__,
                'traceback':traceback.format_exc()[-16384:],'execution_started':(control/'started.json').exists()})
        raise
    raise SystemExit(code)
