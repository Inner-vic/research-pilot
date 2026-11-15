"""Dependency-based SDM task coordinator; never edits numbered qualification ledgers.

Commands are explicit project-configured Skill drivers, executed without a shell.
Drivers own S12/S13 validation and platform operations; a process exit is not a
platform receipt. Results remain task observations unless those drivers verify them.
"""
from process_visibility import hidden_process_options
import argparse
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from node_registry import capability_summary


# Historical driver roster used by older callers/tests. Dependencies here are
# archival metadata only; workflow_definition.build() is the execution authority.
STEPS = {
 1: ('task', []), 2: ('question', [1]), 3: ('data', [1]),
 4: ('starting-model', [1]), 5: ('baseline-reproduction', [3,4]),
 6: ('plan', [2,3,4]), 7: ('runtime-check', [3,4,6]),
 8: ('config-snapshot', [6]), 9: ('implementation-context', [6]),
 10: ('implementation', [9]), 11: ('code-review', [10]),
 12: ('engineering-check', [10]), 13: ('preflight', [7,8]),
 14: ('execution-request', [13]), 15: ('training', [14]),
 16: ('evaluation', [15]), 17: ('robustness', [16]),
 18: ('result-summary', [15,16]), 19: ('evidence-review', [18]),
 20: ('next-action', [18]), 21: ('finish-or-successor', [20]),
}
TERMINAL = {'completed','failed','not-applicable','recorded','skipped'}


def digest(value):
    return 'sha256:'+hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def verified_s13(ref, terminal_status='successful'):
    if not isinstance(ref,dict) or not all(ref.get(k) for k in ('workspace','request_id','result_digest')):
        return False
    from workspace import validate_workspace
    from ledger import replay
    from projection import materialize
    if not validate_workspace(ref['workspace'])['valid']: return False
    row=materialize(replay(ref['workspace']))['handoffs'].get(ref['request_id'],{})
    return row.get('status')==terminal_status and row.get('result_digest')==ref['result_digest']


def core_graph(task):
    from workflow_definition import build
    return build(task)


def current_task(task,root=None):
    """Normalize the workflow while retaining legacy check/driver identities."""
    if task.get('orchestration_version',2)!=2:
        raise ValueError('historical_graph_is_read_only_use_current_graph')
    from orchestration import RECORDS, GRAPH_REVISION, GATE_CONTRACT_REVISION
    if task.get('gate_contract_revision',GATE_CONTRACT_REVISION)!=GATE_CONTRACT_REVISION:
        raise ValueError('historical_gate_contract_requires_explicit_migration')
    from workflow_definition import WORKFLOW_REVISION
    if task.get('workflow_revision', WORKFLOW_REVISION) != WORKFLOW_REVISION:
        raise ValueError('unsupported_workflow_revision')
    settings={'review_mode':'local-agent',**deepcopy(task.get('orchestration',{}))}
    if task.get('workflow_profile','legacy') not in {'legacy','exploration'}:raise ValueError('known_workflow_profile_required')
    from exploration_workflow import active,record_refs
    if root is not None and 'records' not in settings:
        records=Path(root).resolve()/'orchestration-records'
        settings['records']=(record_refs(records) if active(task) else
            {name:{'path':str(records/(name+'.json'))} for name in set(RECORDS.values())})
    result={**task,'orchestration_version':2,'graph_revision':GRAPH_REVISION,'workflow_revision':WORKFLOW_REVISION,
            'gate_contract_revision':GATE_CONTRACT_REVISION,'orchestration':settings}
    if active(task):
        from exploration_workflow import REVISION
        if task.get('exploration_revision',REVISION)!=REVISION:raise ValueError('exploration_revision_requires_frozen_coordinator')
        result['exploration_revision']=REVISION
    from work_packages import validate, bind_records
    validate(result)
    bind_records(result)
    return result


def graph(task):
    from orchestration import extend
    task=current_task(task)
    return extend(core_graph(task),task)


def available(nodes,state):
    ready=[]
    for k,n in nodes.items():
        if k in state: continue
        deps=[state.get(d,{}).get('status') for d in n['depends']]
        if all(s in TERMINAL for s in deps):
            if not n.get('accept_failed_dependencies') and any(s in {'failed','skipped'} for s in deps):
                ready.append((k,'skip'))
            else: ready.append((k,'run'))
    return ready


def coverage(nodes,state):
    """Report actual task coverage without counting optional records as passes."""
    from work_packages import PACKAGES
    result={'business_step_count':len(PACKAGES),'internal_check_count':len(nodes),'total_nodes':len(nodes),
            'phase_node_count':sum(n.get('owner_kind')=='phase' for n in nodes.values()),
            'capability_step_count':sum(n.get('owner_kind')=='capability' for n in nodes.values())}
    for label,status in (('completed','completed'),('failed','failed'),
                         ('not_applicable','not-applicable'),('recorded','recorded'),('skipped','skipped')):
        result[label]=sorted((k for k,v in state.items() if k in nodes and v['status']==status),key=int)
    required=[k for k,n in nodes.items() if n['policy']=='required']
    result.update(required_node_count=len(required),
                  required_completed_count=sum(state.get(k,{}).get('status')=='completed' for k in required),
                  remaining=sorted((k for k in nodes if k not in state),key=int),
                  reconciliation_required=sorted((k for k,v in state.items() if v.get('uncertain_effect')),key=int))
    result['unmeasured'] = sorted((k for k,v in state.items() if k in nodes and
        (v.get('qualification') == 'unmeasured' or v.get('status') == 'recorded')), key=int)
    result['accepted_record_gates'] = sorted((k for k,v in state.items() if k in nodes and
        nodes[k].get('builtin') and v.get('status') == 'completed' and k != '30'), key=int)
    result['completed_project_drivers'] = sorted((k for k,v in state.items() if k in nodes and
        not nodes[k].get('builtin') and v.get('status') == 'completed' and not v.get('reused')), key=int)
    result['reused_project_checks']=sorted((k for k,v in state.items() if v.get('reused')),key=int)
    return result


def invoke(run_driver,driver,root,key,budget):
    """Contain driver faults, preserving uncertain effects for reconciliation."""
    try:
        result=run_driver(driver,root,key,budget)
        allowed={'completed','failed'}|({'paused'} if driver.get('resumable_observation') else set())
        if not isinstance(result,dict) or not isinstance(result.get('status'),str) or result['status'] not in allowed:
            raise ValueError('driver_result_contract_invalid')
        return result
    except Exception as error:
        return {'status':'failed','reason':'driver_exception_requires_reconciliation',
                'error_type':type(error).__name__,'uncertain_effect':True}


def execute(driver,root,key,budget):
    if driver.get('resumable_observation'):
        return _resumable_execute(driver,Path(root),key,budget)
    return _execute(driver,root,key,budget)


def _resumable_execute(driver,root,key,budget):
    """Immutable observation generations; a missing receipt never redispatches.

    Only an explicit paused driver may be called again. Its provider must own
    idempotent remote commands; historical failed/uncertain calls are terminal.
    """
    if key not in {'7','13','15','16'} or 'external_operation' in driver:
        raise ValueError('resumable_observation_scope_invalid')
    from review_storage import locked
    parent=root/'continuations'/key;parent.mkdir(parents=True,exist_ok=True)
    with locked(parent,'.lock','continuation_busy'):
        generation=1
        while True:
            work=parent/str(generation)
            effective={k:v for k,v in driver.items() if k!='resumable_observation'}
            effective['allow_paused_observation']=True
            effective['receipt']=str(work/'receipt.json')
            effective['argv']=[effective['receipt'] if x==driver['receipt'] else x for x in driver['argv']]
            if (work/(key+'-started.json')).exists():
                result=_execute(effective,work,key,budget)
                if result.get('status')!='paused':return result
                generation+=1
                continue
            work.mkdir(parents=True,exist_ok=True)
            return _execute(effective,work,key,budget)


def _execute(driver,root,key,budget):
    if 'reuse_receipt' in driver:
        from dependency_invalidation import execute_reuse
        return execute_reuse(driver,key)
    if 'external_operation' in driver:
        from external_operations import sha, save
        spec=driver['external_operation']
        if driver.get('read_only') and spec.get('action') not in {'read','diagnose'}:
            raise ValueError('mutating_external_operation_cannot_be_read_only')
        specpath=root/(key+'-external-operation.json')
        if specpath.exists():
            if sha(json.loads(specpath.read_bytes()))!=sha(spec):
                raise ValueError('external_operation_spec_changed')
        else: save(specpath,spec)
        driver_argv=[sys.executable,'-B',str(Path(__file__).with_name('external_operations.py')),
                     '--spec',str(specpath),'--state',str(root/(key+'-external-state.json')),
                     '--receipt',str(Path(driver['receipt']).resolve()),'--node',key,
                     '--timeout',str(min(driver.get('timeout_seconds',1800),budget))]
    else:
        driver_argv=driver.get('argv')
    argv=driver_argv
    if not isinstance(argv,list) or not argv or not all(isinstance(a,str) for a in argv):
        raise ValueError('explicit_skill_driver_argv_required')
    receipt=Path(driver['receipt']).resolve()
    marker=root/(key+'-started.json')
    recovering=marker.exists()
    if recovering and json.loads(marker.read_bytes())['driver_digest']!=digest(driver):
        raise ValueError('uncertain_driver_changed')
    if recovering:
        saved_call=json.loads(marker.read_bytes()).get('capability_call_digest')
        call_path=root/(key+'-capability-call.json')
        if saved_call and (not call_path.is_file() or digest_bytes(call_path.read_bytes())!=saved_call):
            raise ValueError('capability_call_changed')
    if recovering and not receipt.exists():
        return {'status':'failed','reason':'uncertain_effect_requires_platform_reconciliation','uncertain_effect':True}
    if receipt.exists() and not recovering: raise ValueError('fresh_driver_receipt_required')
    # Remaining wall budget is fractional; only the configured limit is integral.
    configured=driver.get('timeout_seconds',1800)
    if type(configured) is not int or configured<1:
        raise ValueError('driver_budget_exceeded')
    timeout=min(configured,budget)
    started=time.time()
    if not recovering:
        from capability_interfaces import call
        from review_storage import atomic_json
        capability_call=call(root,key,driver,started+timeout)
        if capability_call is not None:
            atomic_json(root/(key+'-capability-call.json'),capability_call)
        with marker.open('x',encoding='utf-8') as f:
            entry={'driver_digest':digest(driver),'started_at':time.time()}
            if capability_call is not None:
                entry['capability_call_digest']=digest_bytes((root/(key+'-capability-call.json')).read_bytes())
            json.dump(entry,f); f.flush(); os.fsync(f.fileno())
    try:
        if recovering:
            stdout,stderr,code='','',None
            original=root/(key+'-invocation.json')
            if original.exists():
                saved=json.loads(original.read_bytes())
                if saved.get('argv')!=argv:raise ValueError('driver_invocation_changed')
                code=saved.get('exit_code')
                if saved.get('timed_out'):
                    return {'status':'failed','reason':'driver_timeout','uncertain_effect':True}
        else:
            options={'start_new_session':True} if os.name=='posix' else {}
            process=subprocess.Popen(argv,cwd=driver.get('cwd'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                     text=True,shell=False,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},**options, **hidden_process_options(additional_flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)))
            stdout,stderr=process.communicate(timeout=timeout)
            code=process.returncode
    except subprocess.TimeoutExpired:
        # Kill only this newly launched local group/tree. Remote platform effects
        # remain uncertain and still require the independent cleanup driver.
        termination_error=None
        try:
            if os.name=='posix': os.killpg(process.pid,signal.SIGKILL)
            else:
                killer=Path(os.environ['SystemRoot'])/'System32/taskkill.exe'
                subprocess.run([str(killer),'/PID',str(process.pid),'/T','/F'],
                               capture_output=True,timeout=3,check=True, **hidden_process_options())
        except ProcessLookupError: pass
        except (OSError,subprocess.SubprocessError,KeyError) as error:
            termination_error=type(error).__name__
            if process.poll() is None: process.kill()
        try:
            stdout,stderr=process.communicate(timeout=3)
        except subprocess.TimeoutExpired as error:
            # Escaped descendants can retain pipes. Never let output collection
            # hold cleanup indefinitely; disclose that local shutdown is partial.
            stdout,stderr=error.output or '',error.stderr or ''
            stdout=stdout.decode(errors='replace') if isinstance(stdout,bytes) else stdout
            stderr=stderr.decode(errors='replace') if isinstance(stderr,bytes) else stderr
            process.stdout.close(); process.stderr.close()
            termination_error=termination_error or 'inherited_output_still_open'
        reaped=process.poll() is not None
        invocation={'argv':argv,'exit_code':process.returncode,'stdout':stdout,'stderr':stderr,
                    'timed_out':True,'launcher_reaped':reaped,'local_termination_error':termination_error}
        (root/(key+'-invocation.json')).write_text(json.dumps(invocation),encoding='utf-8')
        return {'status':'failed','reason':'driver_timeout','uncertain_effect':True,
                'duration_seconds':round(time.time()-started,2),'launcher_reaped':reaped,
                'local_termination_error':termination_error}
    except OSError as error:
        return {'status':'failed','reason':'driver_start_failed','error_type':type(error).__name__}
    # Driver payload, not stdout, is the contract. Failed drivers may still
    # persist an authenticated failed S13: preserve it even with nonzero exit.
    invocation_path=root/(key+'-invocation.json')
    if not invocation_path.exists():
        invocation_path.write_text(json.dumps({'argv':argv,'exit_code':code,'stdout':stdout,'stderr':stderr}),encoding='utf-8')
    if not receipt.is_file():
        return {'status':'failed','reason':'driver_receipt_missing','uncertain_effect':True,'exit_code':code}
    raw=receipt.read_bytes()
    def failure(reason,uncertain=True):
        return {'status':'failed','reason':reason,'uncertain_effect':uncertain,'exit_code':code,
                'receipt':str(receipt),'receipt_digest':digest_bytes(raw)}
    try: payload=json.loads(raw)
    except ValueError: return failure('driver_receipt_invalid')
    statuses={'completed','failed'}|({'paused'} if driver.get('allow_paused_observation') else set())
    if not isinstance(payload,dict) or payload.get('task_node')!=key or not isinstance(payload.get('status'),str) or payload['status'] not in statuses:
        return failure('driver_receipt_identity_invalid')
    from driver_handshake import verify_runtime
    try:verify_runtime(driver,payload)
    except ValueError:return failure('runtime_driver_contract_mismatch')
    if payload['status']=='paused' and (code not in (0,None) or payload.get('uncertain_effect') is not False
            or payload.get('continuation')!='same-normal-task-readback'):
        return failure('invalid_resumable_observation')
    if code not in (0,None) and payload['status']=='completed':
        return failure('driver_exit_conflicts_with_completed_receipt')
    if key in {'5','15','16'} and payload['status']=='completed' and not verified_s13(payload.get('accepted_s13')):
        return failure('completed_execution_requires_accepted_s13')
    if key in {'5','15','16'} and payload['status']=='failed' and payload.get('signed_result'):
        if not verified_s13(payload['signed_result'], 'failed'):
            return failure('failed_execution_feedback_not_verified')
    if 'failure_evidence' in payload:
        from external_operations import attribution
        payload={**payload,'failure_analysis':attribution(payload['failure_evidence'])}
    return {'status':payload['status'],'driver_exit_code':code,'receipt':str(receipt),
            'receipt_digest':'sha256:'+hashlib.sha256(raw).hexdigest(),'observation':payload,
            **({'uncertain_effect':True} if payload.get('uncertain_effect') is True else {}),
            'duration_seconds':round(time.time()-started,2)}


def _coordinate(task,root,run_driver=execute):
    from review_storage import locked
    settings=_configuration(task)
    # Keep the lock inode: deleting it after unlocking races another writer.
    # OS ownership disappears on process death, unlike an O_EXCL marker.
    with locked(root, '.coordinator.lock', 'task_coordinator_busy'):
        if (Path(root)/'coordinator.lock').exists():
            raise ValueError('legacy_coordinator_lock_requires_reconciliation')
        result=_coordinate_locked(task,root,run_driver,settings)
        from work_packages import summary as package_summary
        from review_storage import atomic_json
        result['workflow_revision']=task['workflow_revision']
        result['work_packages']=package_summary(settings[0],result.get('nodes',{}))
        atomic_json(Path(root)/'work-packages.json',{
            'schema':'sdm-work-package-progress/v1','workflow_revision':task['workflow_revision'],
            'work_packages':result['work_packages']})
        atomic_json(Path(root)/'summary.json',result)
        return result


def _configuration(task):
    from execution_contracts import validate_producers
    validate_producers(task)
    if task.get('preparation_only') is True:
        raise ValueError('preparation_template_is_not_executable')
    nodes=graph(task)
    if task.get('driver_graph_revision',3)!=3:
        raise ValueError('project_adapter_graph_revision_3_required')
    budget=task.get('timeout_seconds',900)
    if type(budget) is not int or budget<1: raise ValueError('bounded_task_budget_required')
    wall=task.get('wall_time_seconds')
    if wall is not None and (type(wall) is not int or wall<1):
        raise ValueError('bounded_wall_time_required')
    cleanup_budget=task.get('cleanup_timeout_seconds',budget)
    if type(cleanup_budget) is not int or cleanup_budget<1:
        raise ValueError('bounded_cleanup_budget_required')
    return nodes,budget,wall,cleanup_budget


def _coordinate_locked(task,root,run_driver,settings):
    nodes,budget,wall,cleanup_budget=settings
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    try:
        journal=root/'events.jsonl'; fingerprint=digest(task); state={}
        versioned=task.get('orchestration_version')==2
        if versioned:
            from orchestration import COMPUTE_NODES
        acquisition_started=False
        deadline=None
        if wall is not None:
            deadline_path=root/'execution-deadline.json'
            if deadline_path.exists():
                saved_deadline=json.loads(deadline_path.read_bytes())
                if saved_deadline['task_digest']!=fingerprint: raise ValueError('task_deadline_changed')
                deadline=saved_deadline['expires_at']
            else:
                deadline=time.time()+wall
                with deadline_path.open('x',encoding='utf-8') as f:
                    json.dump({'task_digest':fingerprint,'expires_at':deadline},f)
                    f.flush(); os.fsync(f.fileno())
        def bounded_invoke(driver,root,key,_budget):
            nonlocal acquisition_started
            if versioned:
                from orchestration import COMPUTE_NODES, dispatch_guard
                if key in COMPUTE_NODES:
                    recovering=(root/(key+'-started.json')).exists()
                    if (root/'cleanup.json').exists() and not recovering:
                        return {'status':'failed','reason':'released_allocation_requires_new_attempt',
                                'uncertain_effect':False}
                    if any(row.get('uncertain_effect') for row in state.values()):
                        return {'status':'failed','reason':'uncertain_effect_requires_platform_reconciliation',
                                'uncertain_effect':False}
                    # Receipt recovery does not acquire compute or renew a lease.
                    # execute() verifies the driver binding and never re-dispatches.
                    if recovering:
                        acquisition_started=True
                        return invoke(run_driver,driver,root,key,_budget)
                    from trigger_matrix import acquisition_gaps
                    gaps = acquisition_gaps(task, nodes, state)
                    if gaps:
                        return {'status':'failed', 'reason':'execution_bindings_incomplete_before_acquisition',
                                'missing_drivers':gaps, 'uncertain_effect':False}
                    try: dispatch_guard(task,acquisition_started)
                    except (ValueError,KeyError,TypeError) as error:
                        return {'status':'failed','reason':'resource_confirmation_not_current',
                                'error_type':type(error).__name__,'uncertain_effect':False}
                    acquisition_started=True
            remaining=budget if deadline is None else min(budget,deadline-time.time())
            if remaining<=0:
                return {'status':'skipped','reason':'task_wall_time_exhausted','uncertain_effect':False}
            return invoke(run_driver,driver,root,key,remaining)
        if journal.exists():
            history=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
            if any(event['task_digest']!=fingerprint for event in history):
                raise ValueError('changed_task_requires_new_attempt_directory')
            # Establish ownership before checking earlier record bytes. A changed
            # plan/result record must fail validation without skipping cleanup
            # of an allocation already recorded by this exact frozen task.
            if versioned:
                acquisition_started=any(event['node'] in COMPUTE_NODES and
                    event['result'].get('status') in {'completed','failed','paused'} and
                    event['result'].get('reason')!='resource_confirmation_not_current' for event in history)
            for event in history:
                if event['task_digest']!=fingerprint: raise ValueError('changed_task_requires_new_attempt_directory')
                state[event['node']]=event['result']
                r=event['result']
                from orchestration import record_receipt_digest
                for source in r.get('source_receipts',[]):
                    if record_receipt_digest(source['path'],source.get('section'))!=source['digest']:
                        raise ValueError('historical_grouped_record_changed')
                if 'receipt' in r and record_receipt_digest(r['receipt'],r.get('receipt_section'))!=r['receipt_digest']:
                    raise ValueError('historical_receipt_changed')
                for artifact_key in ('recommendation','decision') if versioned and r.get('status')=='completed' else ():
                    if artifact_key not in r:continue
                    artifact_path=root/(artifact_key+'.json')
                    if not artifact_path.is_file():
                        raise ValueError('accepted_artifact_missing:'+artifact_key)
                    if json.loads(artifact_path.read_bytes())!=r[artifact_key]:
                        raise ValueError('accepted_artifact_changed:'+artifact_key)
            if versioned:
                from orchestration import COMPUTE_NODES
                acquisition_started=any(k in COMPUTE_NODES and r.get('reason')!='resource_confirmation_not_current'
                                        and r.get('status') in {'completed','failed'} for k,r in state.items())
        # Reconcile waiting nodes on explicit resume, preserving immutable
        # previous receipts/journal events and all completed observations.
        for k in list(state):
            if k=='30' and state[k].get('status') not in TERMINAL:
                del state[k]
                continue
            if state[k].get('status')=='paused' and task.get('drivers',{}).get(k,{}).get('resumable_observation'):
                del state[k]
        def record(k,result):
            with journal.open('a',encoding='utf-8') as f:
                f.write(json.dumps({'task_digest':fingerprint,'node':k,'result':result,'at':time.time()},ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
            state[k]=result
        if task.get('driver_handshake') is not None:
            from driver_handshake import ensure,bindings
            try:
                bindings(task,task['driver_handshake'])
                behavior=ensure(task['driver_handshake'],root,budget)
            except (ValueError,OSError,KeyError,TypeError) as error:
                summary={'task_digest':fingerprint,'nodes':state,'status':'blocked',
                    'reason':'driver_behavior_handshake_failed','detail':str(error),
                    'driver_handshake':{'status':'failed','qualification':'not-behaviorally-verified'},
                    'qualification':'not-assessed','numbered_gates_advanced':False}
                (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                return summary
        if acquisition_started and not task.get('cleanup_driver') and not (root/'cleanup.json').exists():
            summary={'task_digest':fingerprint,'nodes':state,'status':'cleanup-required',
                     'reason':'owned_effect_requires_release_reconciliation','coverage':coverage(nodes,state),
                     'qualification':'not-assessed','numbered_gates_advanced':False}
            if 'behavior' in locals():summary['driver_handshake']=behavior
            (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            return summary
        for k,n in nodes.items():
            if k not in state and n['policy']=='not-applicable':
                reason='existing_code_reused' if k in {'9','10','11','12'} else 'outside_requested_objective'
                record(k,{'status':'not-applicable','reason':reason})
        drivers=task.get('drivers',{})
        from trigger_matrix import inspect
        (root/'trigger-matrix.json').write_text(
            json.dumps(inspect(task,nodes,state),ensure_ascii=False,indent=2),encoding='utf-8')
        def cleanup_once():
            saved=root/'cleanup.json'
            if saved.exists():
                result=json.loads(saved.read_bytes())
                if 'receipt' in result and digest_bytes(Path(result['receipt']).read_bytes())!=result['receipt_digest']:
                    raise ValueError('cleanup_receipt_changed')
                from cleanup_reconciliation import effective
                return effective(root,result)
            cleanup=task.get('cleanup_driver')
            if not cleanup:
                return {'status':'awaiting-evidence','reason':'independent_release_driver_required'}
            result=invoke(run_driver,cleanup,root,'cleanup',cleanup_budget)
            saved.write_text(json.dumps(result),encoding='utf-8')
            return result
        # Materially mutating calls are serialized; independent read-only
        # drivers may execute concurrently. CAS ledger writers stay serialized.
        pool=ThreadPoolExecutor(max_workers=3); pending={}
        while True:
            for future in list(pending):
                if future.done(): record(pending.pop(future),future.result())
            paused=[k for k,r in state.items() if r.get('status')=='paused']
            if paused:
                if pending:
                    wait(pending,timeout=0.2,return_when=FIRST_COMPLETED);continue
                fatal=any(r.get('status')=='failed' and nodes.get(k,{}).get('policy')=='required' for k,r in state.items())
                if fatal or deadline is not None and time.time()>=deadline:
                    for k in paused:record(k,{'status':'failed','reason':'normal_wait_cancelled_by_required_failure_or_deadline','uncertain_effect':False})
                    continue
                retain_waiting_allocation=True
                summary={'task_digest':fingerprint,'nodes':state,'status':'paused','waiting_nodes':paused,
                    'coverage':coverage(nodes,state),'numbered_gates_advanced':False,
                    'qualification':'same-task observation pending; resume explicitly; no background monitor'}
                if 'behavior' in locals():summary['driver_handshake']=behavior
                (root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
                return summary
            in_flight=set(pending.values())
            ready=[(k,a) for k,a in available(nodes,state) if k not in in_flight]
            jobs=[]; before_count=len(state)
            for k,action in ready:
                if action=='skip': record(k,{'status':'skipped','reason':'required_dependency_failed'})
                elif nodes[k].get('builtin'):
                    from orchestration import gate, phase_summary
                    if deadline is not None and time.time()>=deadline and nodes[k]['builtin']!='release':
                        result={'status':'skipped','reason':'task_wall_time_exhausted'}
                    elif nodes[k]['builtin']=='release':
                        result=cleanup_once() if acquisition_started else {'status':'completed','reason':'no_allocation_acquired'}
                    elif nodes[k]['builtin']=='record-group':
                        from exploration_workflow import gate as compact_gate,RecordGroupIntegrityError
                        try:
                            result=compact_gate(task,k,root,budget,deadline=deadline)
                        except RecordGroupIntegrityError:raise
                        except (ValueError,KeyError,TypeError,OSError) as error:
                            result={'status':'failed','reason':'orchestration_record_invalid','detail':str(error),'uncertain_effect':False}
                    else:
                        try:
                            from execution_contracts import produce
                            from orchestration import RECORDS, load
                            name=RECORDS[k]
                            produced=None
                            loaded=load(task,name)
                            if loaded[0] is None:
                                produced=produce(task,name,root,max(1,int(deadline-time.time())) if deadline is not None else budget)
                                if produced is not None:loaded=load(task,name)
                            result=gate(task,k,loaded=loaded)
                            if produced is not None:result={**result,'production':produced}
                        except (ValueError,KeyError,TypeError,OSError) as error:
                            result={'status':'failed','reason':'orchestration_record_invalid',
                                    'detail':str(error),'uncertain_effect':False}
                    if result['status']=='failed' and nodes[k]['builtin']=='release':
                        result={**result,'status':'cleanup-required'}
                    if nodes[k]['builtin']=='release' and result['status'] not in {'completed','failed','skipped'}:
                        # Preserve cleanup as an unresolved obligation, but allow
                        # independent diagnostic/review work to make progress.
                        record(k,{**result,'next_action':{'operation':'reconcile-cleanup',
                            'read_only':True,'rerun_training':False}})
                        continue
                    if result['status'] not in {'completed','failed','skipped'}:
                        if nodes[k]['builtin'] == 'release':
                            result={**result,'next_action':{'operation':'reconcile-cleanup',
                                'entrypoint':'task_pipeline.py --task TASK --output ORIGINAL_ATTEMPT --reconcile-cleanup REQUEST',
                                'original_cleanup':str(root/'cleanup.json'),
                                'read_only':True,'rerun_training':False}}
                        else:
                            from trigger_matrix import waiting_action
                            result={**result,'next_action':waiting_action(task,k,result.get('record_kind'))}
                        summary={'task_digest':fingerprint,'nodes':state,'status':result['status'],
                                 'waiting_node':k,'waiting':result,'coverage':coverage(nodes,state),
                                 'phases':phase_summary(nodes,state),'capabilities':capability_summary(nodes,state),'qualification':'not-assessed',
                                 'numbered_gates_advanced':False}
                        if 'behavior' in locals():summary['driver_handshake']=behavior
                        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
                        return summary
                    for artifact_key in ('recommendation','decision') if result['status']=='completed' else ():
                        if artifact_key not in result:continue
                        decision_path=root/(artifact_key+'.json')
                        if decision_path.exists():
                            if json.loads(decision_path.read_bytes())!=result[artifact_key]:
                                raise ValueError('accepted_decision_changed')
                        else:
                            with decision_path.open('x',encoding='utf-8') as stream:
                                json.dump(result[artifact_key],stream,ensure_ascii=False)
                    record(k,result)
                else:
                    from work_packages import preparation_check
                    try:
                        prepared=preparation_check(task,k)
                    except (ValueError, OSError, KeyError, TypeError) as error:
                        prepared={'status':'failed','reason':'work_package_material_invalid',
                                  'detail':str(error),'uncertain_effect':False}
                    if prepared is not None: record(k,prepared)
                    elif k not in drivers and nodes[k]['policy']=='record': record(k,{'status':'recorded','reason':'optional_check_not_run','qualification':'unmeasured'})
                    elif k in drivers: jobs.append(k)
            if not jobs:
                if len(state)>before_count: continue
                if pending:
                    wait(pending,timeout=0.2,return_when=FIRST_COMPLETED)
                    continue
                if any(k not in state and nodes[k]['policy']=='record' for k in nodes):
                    # Optional checks blocked by missing optional prerequisites
                    # must not hold result aggregation or task completion.
                    for k,n in nodes.items():
                        if k not in state and n['policy']=='record': record(k,{'status':'recorded','reason':'optional_check_unresolved','qualification':'unmeasured'})
                    continue
                break
            reads=[k for k in jobs if drivers[k].get('read_only') is True and
                   (not versioned or k not in COMPUTE_NODES)]
            writes=[k for k in jobs if k not in reads]
            for k in reads: pending[pool.submit(bounded_invoke,drivers[k],root,k,budget)]=k
            for k in writes:
                result=bounded_invoke(drivers[k],root,k,budget)
                # An explicit recovery probe pauses AFTER the receipt is durable,
                # BEFORE journal commit. Resume must recover it without dispatch.
                probe=root/'recovery-pause.json'
                if task.get('pause_after_receipt')==k and not probe.exists() and result.get('receipt'):
                    with probe.open('x',encoding='utf-8') as f:
                        json.dump({'node':k,'receipt_digest':result['receipt_digest']},f)
                        f.flush(); os.fsync(f.fileno())
                    summary={'task_digest':fingerprint,'nodes':state,'status':'paused',
                             'pause_node':k,'qualification':'not-assessed','numbered_gates_advanced':False}
                    summary['coverage']=coverage(nodes,state)
                    if 'behavior' in locals():summary['driver_handshake']=behavior
                    (root/'summary.json').write_text(json.dumps(summary),encoding='utf-8')
                    return summary
                record(k,result)
        summary={'task_digest':fingerprint,'nodes':state,'ready':[k for k,a in available(nodes,state)],
                 'remaining':[k for k in nodes if k not in state],
                 'qualification':'not-assessed','numbered_gates_advanced':False}
        if 'behavior' in locals():summary['driver_handshake']=behavior
        summary['coverage']=coverage(nodes,state)
        from work_packages import summary as package_summary
        summary['work_packages']=package_summary(nodes,state)
        summary['workflow_revision']=task['workflow_revision']
        if versioned:
            from orchestration import phase_summary
            summary['phases']=phase_summary(nodes,state)
            summary['capabilities']=capability_summary(nodes,state)
        summary['status']='complete' if not summary['remaining'] else 'needs-driver'
        if any(row.get('reason')=='task_wall_time_exhausted' for row in state.values()):
            summary['stop_reason']='task_wall_time_exhausted'
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        return summary
    finally:
        if 'pool' in locals(): pool.shutdown(wait=True,cancel_futures=True)
        # Cleanup is independent of train/evaluate/decision success. The
        # configured platform driver must SAVE environment, then verify stop.
        cleanup=task.get('cleanup_driver')
        try:
            if cleanup and not locals().get('retain_waiting_allocation') and (not locals().get('versioned') or locals().get('acquisition_started')):
                saved=root/'cleanup.json'
                if saved.exists():
                    result=json.loads(saved.read_bytes())
                    if 'receipt' in result and digest_bytes(Path(result['receipt']).read_bytes())!=result['receipt_digest']:
                        raise ValueError('cleanup_receipt_changed')
                    from cleanup_reconciliation import effective
                    result=effective(root,result)
                else:
                    result=invoke(run_driver,cleanup,root,'cleanup',cleanup_budget)
                    saved.write_text(json.dumps(result),encoding='utf-8')
                summary_path=root/'summary.json'
                if summary_path.exists():
                    if 'summary' not in locals(): summary=json.loads(summary_path.read_bytes())
                    summary['cleanup']=result
                    if result['status']!='completed': summary['status']='cleanup-required'
                    summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            pass  # The outer OS lock covers journal replay through final cleanup.


def digest_bytes(raw): return 'sha256:'+hashlib.sha256(raw).hexdigest()


def coordinate(task,root,run_driver=execute):
    original=task
    task=current_task(task,root)
    journal=Path(root)/'events.jsonl'
    if journal.exists():
        rows=[json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
        if rows and rows[0]['task_digest']!=digest(task):
            if task.get('workflow_profile')=='exploration':
                raise ValueError('exploration_revision_changed_resume_frozen_coordinator')
            from orchestration import migrate_evidence
            from review_storage import locked
            with locked(root,'.coordinator.lock','historical_coordinator_busy'):
                root=migrate_evidence(Path(root),original,task)
    return _coordinate(task,root,run_driver)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--task',type=Path,required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--plan',action='store_true')
    p.add_argument('--inspect-triggers',action='store_true',help='Inspect all 34 bindings without executing drivers')
    p.add_argument('--reconcile-cleanup',type=Path,help='Read-only independent release readback; never run training')
    p.add_argument('--reconcile-submission',type=Path,help='Read-only pending-approval closure and submission deduplication')
    a=p.parse_args(); task=json.loads(a.task.read_bytes())
    if a.reconcile_submission:
        if a.plan or a.inspect_triggers or a.reconcile_cleanup:raise ValueError('one_coordinator_operation_required')
        from submission_guard import reconcile
        result=reconcile(task,a.output,json.loads(a.reconcile_submission.read_bytes()))
    elif a.reconcile_cleanup:
        if a.plan or a.inspect_triggers:raise ValueError('one_coordinator_operation_required')
        from cleanup_reconciliation import reconcile
        result=reconcile(task,a.output,json.loads(a.reconcile_cleanup.read_bytes()))
    elif a.inspect_triggers:
        from trigger_matrix import inspect
        result=inspect(current_task(task,a.output),graph(task))
    else:
        result=graph(task) if a.plan else coordinate(task,a.output)
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__': main()
