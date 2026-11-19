"""Continuous configured provider entrypoint for dependency-based SDM execution.

This Linux entrypoint invokes a bound native or Windows project driver, then validates
its real S13 references on Linux. Platform UI handoffs are explicit bridge files;
they never masquerade as native CLI completion.
"""
import argparse
import json
import sys
from pathlib import Path
from task_pipeline import coordinate, graph, current_task
from project_driver import resolve as resolve_driver
from project_driver_launch import mode as driver_mode


def windows(path):
    path=str(path)
    if path.startswith('/mnt/') and len(path)>7 and path[6]=='/':
        return path[5].upper()+':/'+path[7:]
    if path.startswith('/home/'):
        return '\\\\wsl.localhost\\Ubuntu'+path.replace('/','\\')
    raise ValueError('configured_windows_path_required')


def run(config,output):
    from agent_roles import policy as role_policy
    role_policy(config)
    if not config.get('provider'): raise ValueError('configured_provider_required')
    if config['provider']=='roadmodel-normal-v2':
        if config.get('normal_native_preparation_only'):
            raise ValueError('normal_native_gateway_and_cleanup_not_bound_preparation_only')
        from roadmodel_normal_contract import validate_project
        normal_plan=validate_project(config)
        if config.get('normal_native_session_version')==1:
            from normal_session import validate
            validate(config,normal_plan)
    if config.get('orchestration_version',2)!=2:
        raise ValueError('historical_graph_is_read_only_use_current_graph')
    task_budget=config.get('task_timeout_seconds',1800)
    node_budget=config.get('node_timeout_seconds',1500)
    cleanup_budget=config.get('cleanup_timeout_seconds',600)
    wall_budget=config.get('task_wall_time_seconds')
    if type(task_budget) is not int or task_budget<1:
        raise ValueError('configured_task_budget_required')
    if type(node_budget) is not int or not 1<=node_budget<=task_budget:
        raise ValueError('configured_node_budget_required')
    if type(cleanup_budget) is not int or cleanup_budget<1:
        raise ValueError('configured_cleanup_budget_required')
    if wall_budget is not None and (type(wall_budget) is not int or wall_budget<1):
        raise ValueError('configured_wall_budget_required')
    if 3 not in config.get('supported_graph_revisions',[]):
        raise ValueError('project_adapter_graph_revision_3_required')
    objective=config.get('objective','execute-and-learn')
    if objective!='execute-and-learn' and objective not in config.get('supported_objectives',[]):
        raise ValueError('project_driver_must_support_requested_objective')
    limit=int(config.get('max_attempts',1))
    if not 1<=limit<=2: raise ValueError('bounded_attempts_required')
    if limit!=1:
        raise ValueError('successor_requires_fresh_resource_proposal')
    if config.get('resource_owner')=='loop' and (limit!=1 or not config.get('loop_parameters')):
        raise ValueError('loop_owns_bounded_round_and_cleanup')
    if config.get('loop_parameters') is not None and config.get('preparation_is_read_only') is not True:
        raise ValueError('project_adapter_must_defer_resource_preparation_until_confirmation')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    from orchestration import COMPUTE_NODES
    prior=output/'attempt-01'
    # Existing receipts/intents must remain resumable for readback and cleanup.
    active=any((prior/(key+'-started.json')).exists() for key in COMPUTE_NODES)
    if not active and objective=='execute-and-learn':
        from required_skills import inspect as inspect_skills
        inspect_skills(config)
        from proposal_review import gate
        readiness=gate(config,output)
        if readiness and readiness['status']!='proposal-approved':return readiness
    host=Path(__file__).resolve().parents[4]
    provider=resolve_driver(config,host)
    runtime=driver_mode(config)
    guarded=bool(config.get('driver_runtime') or config.get('project_driver',{}).get('files'))
    runtime_path=lambda path:str(path) if runtime=='native' else windows(path)
    output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=True)
    attempts=[]
    for attempt in range(1,limit+1):
        root=output/f'attempt-{attempt:02d}'; root.mkdir(exist_ok=True)
        from experiment_identity import bind as bind_identity
        bind_identity(config,root,attempt)
        cfg={**config,'linux_host':str(host),'attempt':attempt,'root':runtime_path(root),
             'workspace_id':config['run_id']+f'-a{attempt:02d}'}
        if config.get('loop_parameters') is not None:
            cfg['preparation']=runtime_path(root/'loop-preparation.json')
        cfgpath=root/'project.json'
        if cfgpath.exists():
            if json.loads(cfgpath.read_bytes())!=cfg: raise ValueError('project_attempt_changed')
        else: cfgpath.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')
        driver_prefix=([sys.executable,'-B',str(Path(__file__).with_name('project_driver_launch.py')),'--project',str(cfgpath)]
            if guarded else [config['windows_python_wsl'],'-B',windows(provider),'--project',windows(cfgpath)])
        receipt_path=lambda path:str(path) if guarded else windows(path)
        task={'objective':objective,'code_change':config.get('code_change',False),
              **({'workflow_profile':config['workflow_profile']} if 'workflow_profile' in config else {}),
              **({'exploration_revision':config['exploration_revision']} if 'exploration_revision' in config else {}),
              'code_review_required':config.get('code_review_required',False),
              'driver_graph_revision':3,'baseline_required':config.get('baseline_required',False),
              'robustness_required':config.get('robustness_required',False),'timeout_seconds':task_budget,
              'cleanup_timeout_seconds':cleanup_budget,
              'merge':config.get('merge',False),'publish':config.get('publish',False)}
        from proposal_review import mode as review_mode
        task.update(orchestration_version=2,orchestration={**config.get('orchestration',{}),'review_mode':review_mode(config)})
        if 'gate_contract_revision' in config: task['gate_contract_revision']=config['gate_contract_revision']
        if 'work_package_materials' in config: task['work_package_materials']=config['work_package_materials']
        task=current_task(task,root)
        if wall_budget is not None: task['wall_time_seconds']=wall_budget
        nodes=graph(task)
        from work_packages import MATERIAL_CHECKS
        shared={key for package in task.get('work_package_materials',{}) for key in MATERIAL_CHECKS[package]}
        selected={k for k,n in nodes.items() if n['policy']=='required' and not n.get('builtin') and k not in shared}
        optional=config.get('additional_task_nodes',[])
        if not isinstance(optional,list) or any(k not in nodes or nodes[k]['policy']!='record' for k in optional):
            raise ValueError('optional_task_nodes_must_be_declared_checks')
        selected.update(optional)
        drivers={}
        external=config.get('external_operations',{})
        if not isinstance(external,dict) or any(k not in selected and k!='cleanup' for k in external):
            raise ValueError('external_operations_must_bind_selected_drivers')
        from execution_contracts import capabilities
        task['project_capability_check']=capabilities(config,selected-set(external),provider,
                                                       cleanup_required='cleanup' not in external)
        from driver_handshake import build,invocation
        owned=(selected-set(external))|({'cleanup'} if 'cleanup' not in external else set())
        handshake=build(config,owned,provider,driver_prefix,root,receipt_path,
                        min(config.get('handshake_timeout_seconds',30),task_budget))
        if handshake is not None:task['driver_handshake']=handshake
        for node in sorted(selected,key=int):
            receipt=root/f'node-{node}.json'
            drivers[node]={'argv':driver_prefix+['--node',node,'--receipt',receipt_path(receipt)],
                           'receipt':str(receipt),'timeout_seconds':node_budget}
            if config.get('normal_native_session_version')==1 and node in {'7','13','15','16'}:
                drivers[node]['resumable_observation']=True
            read_nodes=config.get('read_only_task_nodes',
                                  ['3','4','8'] if config['provider']=='roadmodel-devspace-v1' else [])
            if node in read_nodes: drivers[node]['read_only']=True
            if node in external:
                drivers[node]['external_operation']=external[node]
                # Only read/diagnose operations may use parallel dispatch.
                drivers[node]['read_only']=external[node].get('action') in {'read','diagnose'}
            elif handshake and handshake.get('status')=='required':
                drivers[node]['argv']+=['--handshake-context',receipt_path(root/'driver-handshake-request.json')]
                drivers[node]['behavior_contract']=invocation(handshake,node)
        cleanup=root/'node-cleanup.json'
        task.update(drivers=drivers,cleanup_driver={'argv':driver_prefix+['--node','cleanup','--receipt',receipt_path(cleanup)],
                                                  'receipt':str(cleanup),'timeout_seconds':cleanup_budget})
        if 'cleanup' in external:
            task['cleanup_driver']['external_operation']=external['cleanup']
        elif handshake and handshake.get('status')=='required':
            task['cleanup_driver']['argv']+=['--handshake-context',receipt_path(root/'driver-handshake-request.json')]
            task['cleanup_driver']['behavior_contract']=invocation(handshake,'cleanup')
        if attempt==1 and config.get('recovery_probe_node'):
            if config['recovery_probe_node'] not in {'15','16'}: raise ValueError('execution_recovery_probe_required')
            task['pause_after_receipt']=config['recovery_probe_node']
        from dependency_invalidation import reuse_drivers
        reuse_drivers(config,task,nodes,root)
        task_path=root/'coordinator-task.json'
        if task_path.exists():
            if json.loads(task_path.read_bytes())!=task:raise ValueError('coordinator_task_changed')
        else:
            from review_storage import atomic_json
            atomic_json(task_path,task)
        if config.get('normal_native_session_version')==1 and (root/'cleanup.json').exists():
            previous_cleanup=json.loads((root/'cleanup.json').read_bytes())
            if previous_cleanup.get('status')!='completed' and not (root/'cleanup-reconciled.json').exists():
                from normal_session import reconcile_cleanup
                reconcile_cleanup(cfg)
        summary=coordinate(task,root); attempts.append(summary)
        decision_path=root/'decision.json'
        if summary.get('status')=='paused': break
        if not decision_path.exists(): break
        decision=json.loads(decision_path.read_bytes())
        # Unknown external effects require reconciliation; a retry proposal
        # cannot authorize another attempt merely because cleanup succeeded.
        if any(row.get('uncertain_effect') for row in summary.get('nodes',{}).values()): break
        if summary.get('cleanup',{}).get('status')!='completed' or decision['action']!='retry-resource': break
    result={'run_id':config['run_id'],'attempts':attempts,'decision':json.loads(decision_path.read_bytes()) if decision_path.exists() else None,
            'resource_state':attempts[-1].get('cleanup',{}),'new_execution_requested':True}
    (output/'pipeline-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--project',type=Path,required=True); p.add_argument('--output',type=Path)
    p.add_argument('--inspect-driver',action='store_true')
    p.add_argument('--inspect-required-skills',action='store_true',help='Inspect project-required installed Skills; no models/platform calls')
    p.add_argument('--inspect-agent-roles',action='store_true',help='Read Planner–Executor and Generator–Critic model routing; never executes')
    p.add_argument('--proposal-review',action='store_true',help='Advance bound Generator–Critic proposal dialogue; no execution or allocation')
    p.add_argument('--review-evidence-query',type=Path,help='Bound read-only evidence query for --proposal-review')
    p.add_argument('--export-roadmodel-config',action='store_true')
    p.add_argument('--prepare-roadmodel-native',action='store_true')
    p.add_argument('--bind-roadmodel-native',action='store_true')
    p.add_argument('--reconcile-roadmodel-cleanup',action='store_true')
    p.add_argument('--task-events',action='store_true',help='Read durable local collector inbox; never dispatch training')
    p.add_argument('--research-event',type=Path,help='Persist one explicit conversation change; never execute its text')
    p.add_argument('--research-context',action='store_true',help='Read live questions, judgments and linked executions')
    p.add_argument('--research-status',action='store_true',help='Read completed result/goal/consumption evidence; no execution or platform calls')
    p.add_argument('--research-replan',action='store_true',help='Planner revises strategy from real Executor results through Generator–Critic; no resource execution')
    p.add_argument('--research-implementation',action='store_true',help='Prepare a native implementation job from an originally approved replan; no platform execution')
    p.add_argument('--freeze-research-implementation',action='store_true',help='Freeze bound implementation files for original code/execution preflight; never approves or submits')
    p.add_argument('--recover-result-review',type=Path,help='Frozen review-only recovery for released results; original executor consumes')
    p.add_argument('--research-web',action='store_true',help='Serve the independent local read-only live research page')
    p.add_argument('--web-port',type=int,default=0,help='Local research page port; 0 chooses a free port')
    p.add_argument('--web-ready',type=Path,help='Write bound local web URL/PID for a hidden launcher')
    p.add_argument('--research-question',help='Scope research context to one stable question ID')
    p.add_argument('--task-notifications',action='store_true',help='Read resident diagnostic notification outbox; no platform execution')
    p.add_argument('--ack-task-notifications',type=Path,help='JSON ids and delivery_receipt after notification delivery')
    p.add_argument('--received-task-dispatch',help='Record first entry of this exact queued dispatch; does not acknowledge delivery')
    p.add_argument('--event-consumer',default='coordinator')
    p.add_argument('--event-limit',type=int,default=100)
    p.add_argument('--ack-task-events',type=Path,help='JSON event ID list; acknowledge only after durable handling')
    p.add_argument('--prepare-roadmodel-driver',action='store_true',help='Compile and pin an independent normal RoadModel contract; no platform/model execution')
    p.add_argument('--prepare-roadmodel-devspace',action='store_true',help='Compile installed RoadModel small-GPU profile and main-DAG/event contracts; no platform calls')
    p.add_argument('--project-workspace',action='store_true',help='Register/discover/review/archive existing local project evidence; no platform execution')
    p.add_argument('--devspace-execution',action='store_true',help='Advance one authorized, resumable shared devspace execution')
    p.add_argument('--inspect-devspace-plan',action='store_true',help='Validate configuration and compute authorization binding; no platform calls')
    p.add_argument('--devspace-action-receipt',type=Path,help='Bound native action receipt; still requires official readback')
    p.add_argument('--allow-devspace-live',action='store_true',help='Explicit opt-in for bound devspace resource/SSH operations')
    p.add_argument('--supervise-devspace',action='store_true',help='Supervise the same bound observer process; never resubmit computation')
    p.add_argument('--watch-devspace',action='store_true',help='Run shared observer until a wait, terminal result or deadline; no scheduling')
    p.add_argument('--background-devspace',action='store_true',help='Detach the existing supervisor and return a yield-turn handoff')
    p.add_argument('--research-worker',action='store_true',help='Independent observation and Generator-Critic result decisions; no chat wakeup')
    p.add_argument('--pause-research-worker',action='store_true',help='Pause new research decisions; does not stop an existing workload')
    p.add_argument('--resume-research-worker',action='store_true',help='Revise paused decision control; does not launch a daemon')
    p.add_argument('--study-accounting',action='store_true',help='Read retained Study timing and delivery evidence; no execution or platform calls')
    p.add_argument('--study-successor-plan',action='store_true',help='Read a consumed Study successor into a proposal-only handoff; preserve the old frozen run')
    p.add_argument('--rebind-unlaunched-study',type=Path,help='Transfer a never-acquired successor claim to this corrected fresh project; never resubmit unknown effects')
    a=p.parse_args();config=json.loads(a.project.read_bytes())
    if a.research_implementation or a.freeze_research_implementation:
        if (a.output is None or a.research_implementation and a.freeze_research_implementation
                or any(value for key,value in vars(a).items() if key not in {
                    'project','output','research_implementation','freeze_research_implementation','event_consumer','event_limit','web_port'} and value)):
            p.error('implementation preparation/freezing requires exactly one workflow and output, with no execution options')
        from research_implementation import prepare,freeze
        action=prepare if a.research_implementation else freeze
        print(json.dumps(action(config,a.output),ensure_ascii=False));return
    if a.inspect_required_skills:
        if any(value for key,value in vars(a).items() if key not in {'project','output','inspect_required_skills','event_consumer','event_limit','web_port'} and value):
            p.error('required skill inspection cannot execute another workflow')
        from required_skills import inspect as inspect_skills
        print(json.dumps(inspect_skills(config),ensure_ascii=False));return
    if a.inspect_agent_roles:
        if any(value for key,value in vars(a).items() if key not in {'project','output','inspect_agent_roles','event_consumer','event_limit','web_port'} and value):
            p.error('agent role inspection cannot be combined with another workflow')
        from agent_roles import describe
        print(json.dumps(describe(config),ensure_ascii=False));return
    if a.research_status:
        if a.output is None or any((a.devspace_execution,a.allow_devspace_live,a.research_worker,a.research_replan,a.research_event)):
            p.error('research status requires only project and output; execution modes forbidden')
        from research_terminal import inspect as terminal_status
        print(json.dumps(terminal_status(config,a.output),ensure_ascii=False));return
    if a.pause_research_worker or a.resume_research_worker:
        if a.pause_research_worker and a.resume_research_worker:p.error('choose pause or resume')
        if a.output is None or any((a.devspace_execution,a.allow_devspace_live,a.research_worker,a.task_notifications,a.task_events,a.proposal_review,a.research_event,a.research_web,a.devspace_action_receipt)):
            p.error('research control requires only project and output')
        from research_worker import settings as worker_settings, control
        worker_settings(config,a.output)
        print(json.dumps(control(a.output,a.pause_research_worker)));return
    if a.received_task_dispatch and not a.task_notifications:p.error('--received-task-dispatch requires --task-notifications')
    if sum((a.proposal_review,a.inspect_driver,a.project_workspace,a.prepare_roadmodel_driver,a.prepare_roadmodel_devspace,a.export_roadmodel_config,a.prepare_roadmodel_native,a.bind_roadmodel_native,a.reconcile_roadmodel_cleanup,a.task_events,a.task_notifications,a.devspace_execution,a.inspect_devspace_plan,a.study_accounting,a.study_successor_plan,a.rebind_unlaunched_study is not None,a.research_event is not None,a.research_context,a.research_replan,a.research_web))>1:p.error('choose exactly one inspection, preparation, event or workspace workflow')
    if a.recover_result_review:
        if a.output is None:p.error('--output required for result review recovery')
        if any((a.devspace_execution,a.allow_devspace_live,a.research_worker,a.devspace_action_receipt,a.watch_devspace,a.supervise_devspace,a.background_devspace,a.proposal_review,a.research_replan)):
            p.error('result review recovery cannot advance execution')
        from result_review_recovery import run as recover_review
        print(json.dumps(recover_review(config,a.output,a.recover_result_review),ensure_ascii=False));return
    if a.review_evidence_query and not (a.proposal_review or a.research_replan):p.error('--review-evidence-query requires a proposal or research replan review')
    if a.research_replan:
        if a.output is None:p.error('--output required for research replan')
        if any((a.devspace_action_receipt,a.allow_devspace_live,a.watch_devspace,a.supervise_devspace,a.background_devspace,a.research_worker,a.ack_task_events,a.ack_task_notifications)):
            p.error('research replan cannot execute or acknowledge')
        from research_replan import advance
        print(json.dumps(advance(config,a.output,a.review_evidence_query),ensure_ascii=False));return
    if a.proposal_review:
        if a.output is None:p.error('--output required for proposal review')
        if a.devspace_action_receipt or a.allow_devspace_live or a.watch_devspace or a.supervise_devspace or a.background_devspace or a.ack_task_events or a.ack_task_notifications:p.error('proposal review cannot execute or acknowledge')
        from proposal_review import gate
        print(json.dumps(gate(config,a.output,a.review_evidence_query) or {'status':'proposal-policy-not-configured','execution_authority':False},ensure_ascii=False));return
    if (a.web_ready or a.web_port) and not a.research_web:p.error('web options require --research-web')
    if a.research_question and not a.research_context:p.error('research question scopes context only')
    if a.research_event or a.research_context or a.research_web:
        if a.devspace_action_receipt or a.allow_devspace_live or a.watch_devspace or a.supervise_devspace or a.background_devspace or a.ack_task_events or a.ack_task_notifications:
            p.error('research observation cannot execute or acknowledge')
        if a.research_web:
            from research_web import serve
            serve(config,a.web_port,a.web_ready);return
        from research_observation import event, context
        value=event(config,json.loads(a.research_event.read_bytes())) if a.research_event else context(config,a.research_question)
        print(json.dumps(value,ensure_ascii=False));return
    if a.prepare_roadmodel_devspace:
        if a.output is None:p.error('--output required for profile preparation')
        from roadmodel_devspace_profile import prepare
        print(json.dumps(prepare(config,a.output,__file__),ensure_ascii=False));return
    if a.study_accounting:
        if a.output is None:p.error('--output required for Study accounting')
        if a.devspace_action_receipt or a.allow_devspace_live or a.watch_devspace or a.supervise_devspace or a.background_devspace or a.ack_task_events or a.ack_task_notifications:p.error('accounting cannot execute or acknowledge')
        from study_accounting import summarize
        print(json.dumps(summarize(config,a.output),ensure_ascii=False));return
    if a.study_successor_plan:
        if a.output is None:p.error('--output required for successor handoff')
        if any((a.devspace_action_receipt,a.allow_devspace_live,a.watch_devspace,a.supervise_devspace,a.background_devspace,a.research_worker,a.ack_task_events,a.ack_task_notifications)):
            p.error('successor preparation cannot execute or acknowledge')
        from study_successor import prepare
        print(json.dumps(prepare(config,a.output),ensure_ascii=False));return
    if a.rebind_unlaunched_study:
        if a.output is None:p.error('--output required for unlaunched rebind')
        if any((a.devspace_action_receipt,a.allow_devspace_live,a.watch_devspace,a.supervise_devspace,a.background_devspace,a.research_worker,a.ack_task_events,a.ack_task_notifications)):
            p.error('unlaunched rebind cannot execute or acknowledge')
        from study_successor import rebind_unlaunched
        print(json.dumps(rebind_unlaunched(config,a.output,a.rebind_unlaunched_study),ensure_ascii=False));return
    if (a.devspace_action_receipt or a.allow_devspace_live or a.watch_devspace or a.supervise_devspace or a.background_devspace or a.research_worker) and not a.devspace_execution:p.error('devspace options require --devspace-execution')
    if a.inspect_devspace_plan:
        from devspace_execution import inspect
        if config.get('devspace_study'):
            from devspace_study import validate
            validate(config)
        print(json.dumps(inspect(config,a.output),ensure_ascii=False));return
    if a.devspace_execution:
        if a.output is None:p.error('--output required for devspace execution')
        if sum((a.supervise_devspace,a.watch_devspace,a.background_devspace,a.research_worker))>1 or (a.devspace_action_receipt and (a.supervise_devspace or a.background_devspace or a.research_worker)):
            p.error('supervisor cannot combine watch or an action receipt; bind the receipt before supervision')
        from devspace_execution import project_execution
        execution=project_execution(config,a.output)
        if a.research_worker:
            from research_worker import run as decide
            result=decide(config,a.output,a.allow_devspace_live)
        elif a.background_devspace:
            from devspace_observer import background
            result=background(config,a.project,a.output,__file__,a.allow_devspace_live)
        elif a.supervise_devspace:
            from devspace_observer import supervise
            result=supervise(config,a.project,a.output,__file__,a.allow_devspace_live)
        elif a.watch_devspace:
            from devspace_observer import watch
            result=watch(config,a.output,a.devspace_action_receipt,a.allow_devspace_live)
        else:
            result=execution(config,a.output,a.devspace_action_receipt,a.allow_devspace_live)
        from research_observation import capture_execution
        observation=capture_execution(config,result,a.output)
        if observation is not None:result={**result,'research_observation':observation}
        print(json.dumps(result,ensure_ascii=False));return
    if a.ack_task_events and not a.task_events:p.error('--ack-task-events requires --task-events')
    if a.ack_task_notifications and not a.task_notifications:p.error('--ack-task-notifications requires --task-notifications')
    if a.task_notifications:
        from task_event_service import notifications
        ack=json.loads(a.ack_task_notifications.read_bytes()) if a.ack_task_notifications else None
        print(json.dumps(notifications(config,ack,a.received_task_dispatch),ensure_ascii=False));return
    if a.task_events:
        from task_event_collector import inbox
        ack=json.loads(a.ack_task_events.read_bytes()) if a.ack_task_events else None
        print(json.dumps(inbox(config,a.event_consumer,a.event_limit,ack),ensure_ascii=False));return
    if a.bind_roadmodel_native:
        if a.output is None:p.error('--output required for binding')
        from roadmodel_native_prepare import activate
        print(json.dumps(activate(config,a.output)));return
    if a.reconcile_roadmodel_cleanup:
        from normal_session import reconcile_cleanup
        print(json.dumps(reconcile_cleanup(config)));return
    if a.export_roadmodel_config or a.prepare_roadmodel_native:
        if a.output is None:p.error('--output required for preparation')
        if a.export_roadmodel_config:
            from roadmodel_config_export import export
            result=export(config['normal_config_export'],a.output)
        else:
            from roadmodel_native_prepare import build
            result=build(config,a.output)
        print(json.dumps(result,ensure_ascii=False));return
    if a.prepare_roadmodel_driver:
        if a.output is None:p.error('--output required for normal driver preparation')
        from roadmodel_normal_contract import prepare
        print(json.dumps(prepare(config,a.output),ensure_ascii=False));return
    if a.project_workspace:
        if a.output is None:p.error('--output required for project workspace')
        from project_workspace import execute as workspace_run
        result=workspace_run(config,a.output)
        visible={k:result[k] for k in ('project_id','status','workflow','evidence_kind','qualification','resumed','human_questions','execution_allowed','candidates','human_question','reason','failure_record','request_path','response_path','preparation_task','timings','next_action') if k in result}
        print(json.dumps({**visible,'workspace':str(a.output.resolve())},ensure_ascii=False));return
    if a.inspect_driver:
        from project_driver import inspect_interface
        print(json.dumps(inspect_interface(config,config.get('linux_host',str(Path(__file__).resolve().parents[4]))),ensure_ascii=False));return
    if a.output is None:p.error('--output required for execution')
    result=run(config,a.output)
    print(json.dumps({'run_id':result['run_id'],'attempts':len(result['attempts']),'decision':result['decision'],
                      'cleanup_status':result['resource_state'].get('status')},ensure_ascii=False))
if __name__=='__main__':
    from process_visibility import ensure_background_stdio
    ensure_background_stdio()
    # The public machine-readable JSON contract is UTF-8 on Windows as well.
    sys.stdout.reconfigure(encoding='utf-8')
    main()
