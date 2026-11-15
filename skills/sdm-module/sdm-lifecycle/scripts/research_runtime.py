"""Freeze the actual resident and its review resources before any dispatch.

No model, ML or platform operations. A destination is new and immutable; this
does not repair or migrate an active runtime.
"""
import argparse
import ast
import hashlib
import json
import copy
import sqlite3
import subprocess
import sys
import os
from pathlib import Path

from review_storage import atomic_json,read_json


def freeze(destination):
    module = Path(__file__).resolve().parents[2]
    lifecycle = module / 'sdm-lifecycle'
    from research_model_runner import guidance_sources
    guidance = {}; guidance_sources(guidance)
    sources = list((lifecycle / 'scripts').glob('*.py'))
    for source in sources:
        ast.parse(source.read_bytes(), filename=str(source))
    sources += [Path(path) for path in guidance]
    # A fresh agent follows the links from SKILL.md, without access to a source
    # checkout. Ship the references (load them progressively at use time).
    sources += [lifecycle / 'SKILL.md', *sorted((lifecycle/'references').rglob('*'))]
    sources = list(dict.fromkeys(p for p in sources if p.is_file()))
    # Read every dependency before creating a destination. Never silently omit
    # a missing resource and discover it after the first real task completes.
    contents = {source.relative_to(module): source.read_bytes() for source in sources}
    if any(hashlib.sha256(contents[Path(path).relative_to(module)]).hexdigest()
           != expected.removeprefix('sha256:') for path, expected in guidance.items()):
        raise ValueError('review_guidance_changed_during_freeze')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for relative, raw in contents.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    report = {'schema': 'sdm-research-runtime/v1',
        'runtime_root': str(destination / 'sdm-lifecycle/scripts'),
        'runtime_manifest': {p.name: hashlib.sha256(contents[p.relative_to(module)]).hexdigest()
                             for p in sources if p.parent == lifecycle / 'scripts'},
        'guidance_manifest': [{'path': str(destination / Path(path).relative_to(module)),
            'sha256': expected.removeprefix('sha256:')} for path, expected in guidance.items()]}
    for relative, raw in contents.items():
        if (destination / relative).read_bytes() != raw:
            raise ValueError('frozen_runtime_readback_mismatch')
    atomic_json(destination / 'manifest.json', report)
    return report


def cold_wait(configuration, state, destination, new_root):
    """Prepare an idle known-task handoff without confusing its next proposal.

    Caller retains the original config/database. No platform write or old
    review migration; the new host observes the same Manager plan/Guard.
    """
    cfg=copy.deepcopy(configuration);old_root=Path(cfg['root']).resolve()
    from task_pipeline import digest
    if state.get('config_digest')!=digest(configuration):
        raise ValueError('cold_wait_original_state_configuration_changed')
    from research_context import Context
    context_owner=Context(cfg);context=context_owner.load()
    if not context_owner.path.exists():
        for receipt in old_root.glob('*/result.json'):
            if read_json(receipt).get('detail',{}).get('thread_id'):
                raise ValueError('cold_wait_missing_original_native_context')
    if context.get('pending_job') or context.get('pending_turn'):
        raise ValueError('cold_wait_requires_idle_native_context')
    database=old_root/'delivery.sqlite'
    with sqlite3.connect('file:'+str(database).replace('\\','/')+'?mode=ro',uri=True) as db:
        if any(status!='completed' for status,count in db.execute('select status,count(*) from jobs group by status')):
            raise ValueError('cold_wait_requires_completed_original_jobs')
    if any(not p.with_suffix('.receipt.json').exists() for p in (old_root/'deferred-events').glob('*.json') if not p.name.endswith('.receipt.json')):
        raise ValueError('cold_adoption_requires_accounted_deferred_events')
    current=state['attempts'][-1];history=copy.deepcopy(state['rounds'])
    from component_research import round_evidence,reference
    matches=[(i,row) for i,row in enumerate(history) if
        json.loads(reference(row['result']).read_bytes())['original_plan_sha256']==current['source']['plan']['sha256']]
    governing=current.get('science_plan')
    if matches:
        if len(matches)!=1 or matches[0][0]!=len(history)-1:
            raise ValueError('cold_wait_current_task_must_be_last_verified_round')
        row=history.pop();round_evidence(row['result'])
        governing=row.get('accepted_plan')
    elif current['stage']!='watching':
        raise ValueError('cold_wait_cannot_adopt_unexecuted_next_plan')
    new_root=Path(new_root).resolve()
    if new_root.exists() or new_root.is_relative_to(old_root) or new_root.is_relative_to(Path(cfg['cwd']).resolve()):
        raise ValueError('new_empty_independent_host_root_required')
    if history and not governing:raise ValueError('cold_wait_requires_governing_accepted_plan')
    manifest=freeze(destination)
    cfg.update(root=str(new_root),**{k:v for k,v in manifest.items() if k!='schema'})
    # The original task is GET/reconciliation-only after cold adoption. Its
    # request, Guard and runtime digest remain intact. New submissions still
    # require fresh plans bound to the new runtime.
    source=current['source'];original=read_json(source['plan']['path'])
    code='import sys;sys.path.insert(0,sys.argv[1]);from lpai_manager import runtime_digest;print(runtime_digest())'
    check=subprocess.run([sys.executable,'-I','-S','-B','-c',code,manifest['runtime_root']],
        capture_output=True,text=True,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    actual_runtime=check.stdout.strip();readonly_repair=None
    if original.get('runtime_digest')!=actual_runtime:
        if source.get('version')!=3:raise ValueError('cold_runtime_repair_requires_official_transport_source_v3')
        proof=Path(destination)/'original-task-readonly-runtime.json'
        atomic_json(proof,{'schema':'lpai-manager-readonly-runtime-repair/v1','read_only':True,
            'original_plan_sha256':'sha256:'+hashlib.sha256(Path(source['plan']['path']).read_bytes()).hexdigest(),
            'from_runtime':original['runtime_digest'],'to_runtime':actual_runtime,
            'source_ref':{'path':str(Path(__file__).resolve()),'sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            'scope':'Observe the same completed/known original task only. No submit, plan hash rewrite or unknown resubmission.'})
        readonly_repair={'path':str(proof.resolve()),'sha256':hashlib.sha256(proof.read_bytes()).hexdigest()}

    spec=cfg['component_pipeline']
    if spec.get('proposal_review',{}).get('evidence_policy')=={'version':1,'mode':'registered-supplements'}:
        spec['proposal_review']['evidence_policy']={'version':2,'mode':'registered-supplements'}
    spec['initial_source']=copy.deepcopy(current['source'])
    if readonly_repair:spec['initial_source']['readonly_runtime_repair']=readonly_repair
    if cfg.get('continuation_policy')=={'version':1}:
        spec['proposal_review']['model_runner']['sdk_recovery']={'version':2}
    # The adopted task is already owned by its original Guard. Its first-submit
    # authorization must not be reinterpreted as this new host's bootstrap.
    spec.pop('initial_submission',None)
    spec['verified_history']=history
    if spec.get('version')==4:
        spec['observed_task_uids']=copy.deepcopy(state.get('observed_task_uids',[]))
        refs=[*spec.get('previous_implementation_feedback',[]),*state.get('implementation_block_history',[])]
        for attempt in state['attempts']:refs.extend(attempt.get('capability_feedback',[]))
        spec['previous_implementation_feedback']=list({row['sha256']:row for row in refs}.values())
    if governing:spec['initial_science_plan']=governing
    else:spec.pop('initial_science_plan',None)
    return cfg


def readiness(cfg):
    from component_research import reference
    from lpai_manager import Manager
    spec=cfg['component_pipeline'];manager=Manager(json.loads(reference(spec['initial_source']['manager_config']).read_bytes()))
    uid=json.loads(reference(spec['initial_request']).read_bytes())['uid']
    obj=manager.checked('component',uid)
    return {'status':'ready','component_uid':obj['uid'],'namespace':obj['namespace'],
        'owner':obj['owner'],'model_calls':0,'platform_writes':0}


def owner_alive(cfg):
    ready=Path(cfg['root'])/'ready.json'
    if not ready.exists():return False
    pid=json.loads(ready.read_bytes())['pid']
    if os.name!='nt':
        try:os.kill(pid,0);return True
        except ProcessLookupError:return False
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return False
        raise ValueError('previous_owner_status_unavailable')
    try:
        class FT(ctypes.Structure):_fields_=[('low',wintypes.DWORD),('high',wintypes.DWORD)]
        times=[FT() for _ in range(4)]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*[ctypes.POINTER(FT)]*4]
        if not kernel.GetProcessTimes(handle,*[ctypes.byref(t) for t in times]):raise ValueError('previous_owner_times_unavailable')
        birth=((times[0].high<<32)+times[0].low)/10000000-11644473600
        # Reused PID is not the original owner.
        return birth<=ready.stat().st_mtime
    finally:kernel.CloseHandle(handle)


def adopt(config_path,state_path,destination,new_root,output,patch_path=None):
    from review_storage import locked
    from task_pipeline import digest
    cfg=json.loads(Path(config_path).read_bytes());state_path=Path(state_path or Path(cfg['root'])/'component-pipeline/pipeline.json')
    patch=json.loads(Path(patch_path).read_bytes()) if patch_path else {}
    if set(patch)-{'component_pipeline','runtime_readiness','completion_shutdown','recovery_exercise'}:raise ValueError('bounded_adoption_patch_required')
    if set(patch.get('component_pipeline',{}))-{'objective','decision_policy','delegation','minimum_rounds','minimum_revisions','implementation_feedback','workload','additional_evidence'}:
        raise ValueError('adoption_cannot_change_execution_identity_or_resource_scope')
    if 'additional_evidence' in patch.get('component_pipeline',{}):
        from component_research import reference
        for evidence in patch['component_pipeline']['additional_evidence']:reference(evidence)
    output=Path(output).resolve();journal=output.with_suffix('.adoption.json')
    bound=digest({'config':hashlib.sha256(Path(config_path).read_bytes()).hexdigest(),
        'state':hashlib.sha256(state_path.read_bytes()).hexdigest(),'patch':patch,
        'destination':str(Path(destination).resolve()),'new_root':str(Path(new_root).resolve()),'output':str(output)})
    with locked(output.parent,'.'+output.name+'.adoption.lock','adoption_already_owned'):
        if journal.exists():
            receipt=json.loads(journal.read_bytes())
            if receipt['binding']!=bound:raise ValueError('adoption_input_changed')
            result=receipt['configuration']
        else:
            if output.exists() or owner_alive(cfg):raise ValueError('original_owner_must_exit_before_adoption')
            result=cold_wait(cfg,json.loads(state_path.read_bytes()),destination,new_root)
            result['component_pipeline'].update(patch.get('component_pipeline',{}))
            if 'runtime_readiness' in patch:result['runtime_readiness']=patch['runtime_readiness']
            if 'completion_shutdown' in patch:result['completion_shutdown']=patch['completion_shutdown']
            if 'recovery_exercise' in patch:result['recovery_exercise']=patch['recovery_exercise']
            receipt={'schema':'sdm-runtime-adoption/v1','binding':bound,'status':'frozen','configuration':result}
            atomic_json(journal,receipt)
        code='import sys,json;sys.path.insert(0,sys.argv[1]);from research_event_service import configuration;configuration(json.load(sys.stdin))'
        proc=subprocess.run([sys.executable,'-I','-S','-B','-c',code,result['runtime_root']],
            input=json.dumps(result),capture_output=True,encoding='utf-8',timeout=60,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if proc.returncode:raise ValueError('adoption_frozen_validation_failed:'+proc.stderr.splitlines()[-1][:200])
        if output.exists() and json.loads(output.read_bytes())!=result:raise ValueError('adoption_output_changed')
        atomic_json(output,result)
        receipt.update(status='committed',output={'path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()})
        atomic_json(journal,receipt)
        return {'status':'adopted','configuration':str(output),'binding':bound,'model_calls':0,'platform_writes':0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    parser.add_argument('--config',type=Path);parser.add_argument('--state',type=Path)
    parser.add_argument('--new-root',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--patch',type=Path)
    parser.add_argument('--readiness',action='store_true')
    args=parser.parse_args()
    if args.readiness:
        if not args.config:parser.error('--readiness requires --config')
        print(json.dumps(readiness(json.loads(args.config.read_bytes()))))
    elif args.config:
        if not all((args.destination,args.new_root,args.output)):parser.error('adoption requires destination/new-root/output')
        print(json.dumps(adopt(args.config,args.state,args.destination,args.new_root,args.output,args.patch)))
    else:
        if not args.destination:parser.error('--destination required')
        print(json.dumps(freeze(args.destination)))
