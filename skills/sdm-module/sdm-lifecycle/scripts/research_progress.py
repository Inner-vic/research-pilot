"""Derived progress from verified coordinator facts; no model/platform actions."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from review_storage import atomic_json,read_json,native_path
from task_pipeline import digest
from urllib.request import Request,urlopen


def project(cfg,state):
    if state.get('config_digest')!=digest(cfg):raise ValueError('progress_configuration_changed')
    rows=[]
    for row in state['rounds']:
        ref=row['result'];ready_path=Path(ref['path']);raw=native_path(ready_path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256'].removeprefix('sha256:'):
            raise ValueError('progress_original_result_reference_changed')
        ready=json.loads(raw);path=Path(ready['artifact_root'])/'result.json';raw=native_path(path).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ready['artifact_sha256']['result.json']:
            raise ValueError('progress_original_metric_artifact_changed')
        result=json.loads(raw);accepted=None
        if row.get('accepted_plan'):
            p=row['accepted_plan'];body=native_path(p['path']).read_bytes()
            if hashlib.sha256(body).hexdigest()!=p['sha256'].removeprefix('sha256:'):
                raise ValueError('progress_accepted_plan_changed')
            accepted=json.loads(body)
        rows.append({'uid':ready['uid'],'metric':ready['primary_metric'],
            'validation':result['validation'],'config':result['config'],
            'selected_epoch':result['selected_epoch'],'result':ref,
            'base_uid':accepted.get('selected_candidate',{}).get('uid') if accepted else None,
            'intervention':accepted['research_revision']['intervention'] if accepted else 'initial baseline'})
    current=state['attempts'][-1];stage=current['stage'];reason=current.get('reason')
    health_path=Path(cfg['root'])/'component-pipeline/health.json'
    health=read_json(health_path) if health_path.exists() else {}
    health_blocked=(health.get('status')=='requires-reconciliation' and health.get('stage')==stage
        and health.get('config_sha256',digest(cfg))==digest(cfg)
        and health.get('state_sha256',digest(state))==digest(state))
    if health_blocked:reason=health.get('error_code') or health.get('error_type') or current.get('continuation',{}).get('category')
    from research_continuation import UNKNOWN,APPROVAL
    unresolved=current.get('observation') in UNKNOWN|APPROVAL or current.get('initial_submission_status') in UNKNOWN|APPROVAL and not current.get('observation')
    if unresolved:reason=current.get('observation') or current.get('initial_submission_status')
    # Scientific replanning folders do not own a different platform task.
    folder=Path(cfg['root'])/'component-pipeline'/current['id'];uid=None
    receipt=folder/'source/verified-submission.json'
    if receipt.exists():
        observed=read_json(receipt);uid=observed.get('resource',{}).get('uid',observed.get('uid'))
    if uid is None and current.get('result'):
        ref=current['result'];raw=native_path(ref['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=ref['sha256'].removeprefix('sha256:'):
            raise ValueError('progress_current_result_reference_changed')
        uid=json.loads(raw).get('uid')
    monitor_path=folder/'source/monitor.json'
    monitor=read_json(monitor_path) if monitor_path.exists() else None
    job=current.get('job');job_status=None
    database=Path(cfg['root'])/'delivery.sqlite'
    if job and database.exists():
        import sqlite3
        with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute('select status from jobs where id=?',(job,)).fetchone()
        job_status=row[0] if row else None
    active_job=job if job_status in {'received','preparing','running'} else None
    goal=state.get('candidate_goal')
    target=cfg.get('component_pipeline',{}).get('decision_policy',{}).get('target')
    if goal is None and target is not None:
        goal={'target':target,'met':None,'quality_met':None,'verification':None,'gap':None,
            'status':'awaiting-first-verified-result'}
    from lifecycle_view import project as lifecycle_view
    from research_acceptance import read_projection as acceptance_project
    acceptance=acceptance_project(cfg,state)
    return {**({'sdm_acceptance':acceptance} if acceptance is not None else {}),'lifecycle_view':lifecycle_view(stage,current.get('initial_review_phase')),
        'schema':'sdm-research-progress/v1','project_id':cfg['project_id'],'config_sha256':digest(cfg),
        'state_sha256':digest(state),'projected_at':time.time(),'status':state['status'],'stage':stage,
        'needs_intervention':stage=='needs-attention' or health_blocked or unresolved,'intervention_reason':reason,
        'continuation':current.get('continuation'),'automatic_resubmit':False,
        'monitoring':monitor,'result_handoff':current.get('result_handoff'),
        'execution_preflight':current.get('execution_preflight'),
        'initial_review_phase':current.get('initial_review_phase'),
        'submission_acceptance':current.get('submission_acceptance'),
        'health':health,
        'current_task_uid':uid,'last_observation':current.get('observation'),
        'goal':goal,'incumbent':{k:state.get('incumbent',{}).get(k) for k in ('uid','metric')},
        'rounds':rows,'active_job':active_job,'last_job':job,'last_job_status':job_status,
        'authority':'Derived report only; original pipeline/Guard/result receipts own decisions.',
        'scope':'Persisted verified results, not new provider observations or an automatic chat notification.'}


def publish(cfg,state):
    value=project(cfg,state);root=Path(cfg['root'])/'component-pipeline'
    path=root/'progress.json'
    if path.exists() and read_json(path).get('config_sha256')!=digest(cfg):
        raise ValueError('progress_output_owned_by_other_configuration')
    atomic_json(path,value)
    from lifecycle_view import describe
    lines=['# ResearchPilot 研究进展','',f"运行状态：{value['status']} / {value['stage']}",
        '当前流程步骤：'+describe(value['lifecycle_view']),
        value['lifecycle_view']['scope'],
        f"需要处理：{value['needs_intervention']}；原因：{value['intervention_reason']}",
        f"当前任务：{value['current_task_uid']}；观察结果：{value['last_observation']}",
        '登记目标：'+json.dumps(value['goal'],ensure_ascii=False),
        '最优候选：'+json.dumps(value['incumbent'],ensure_ascii=False),'',
        '| 任务 | '+str((value.get('goal') or {}).get('target',{}).get('metric','主要指标'))+' | 实际基线 | 实验干预 |','| --- | --- | --- | --- |']
    for row in value['rounds']:
        text=row['intervention'].replace('|','/').replace('\n',' ')
        lines.append(f"| {row['uid']} | {row['metric']:.6f} | {row['base_uid'] or '初始基线'} | {text} |")
    lines+=['','本报告读取不可变结果引用，不产生模型调用、训练或提交。',
        '平台成功、有效结果、指标达标与科学裁决分别核验；目标状态不等于 D05。']
    # This is an explicitly mutable projection, never a frozen evidence artifact.
    import uuid
    target=root/'PROGRESS.md';temp=target.with_name('.progress-'+uuid.uuid4().hex+'.tmp')
    temp.write_text('\n'.join(lines)+'\n',encoding='utf-8');temp.replace(target)
    return value



# Live runtime view layered on the original verified progress producer.
def same_digest(value, expected):
    return isinstance(value, str) and value.removeprefix('sha256:') == expected.removeprefix('sha256:')


def probe(cfg, *, paused=False, terminal=False, owner_check=None, fetch=None):
    """Verify the actual original host and worker, not a saved running marker."""
    from research_runtime import owner_alive
    alive = (owner_check or owner_alive)(cfg)
    result = {'owner_alive': alive, 'worker_ready': False, 'paused': paused,
              'status': 'completed' if terminal else 'paused-or-stopped' if paused else
                        'no-live-research-owner' if not alive else 'research-owner-not-ready'}
    if not alive or paused or terminal:
        return result
    ready = read_json(Path(cfg['root']) / 'ready.json')
    expected = digest(cfg)
    if not same_digest(ready.get('config_sha256'), expected):
        raise ValueError('original_host_binding_changed')
    if type(ready.get('port')) is not int or not 0 < ready['port'] < 65536:
        raise ValueError('original_loopback_port_required')
    request = Request('http://127.0.0.1:' + str(ready['port']) + '/health',
                      headers={'Authorization': 'Bearer ' + ready['token']})
    with (fetch or urlopen)(request, timeout=2) as response:
        health = json.load(response)
    if (any(health.get(k) != ready.get(k) for k in ('pid', 'project_id')) or
            not same_digest(health.get('config_sha256'), expected)):
        raise ValueError('live_research_owner_identity_changed')
    if health.get('paused'):
        result.update(paused=True, status='paused-or-stopped')
        return result
    worker = health.get('component_owner', {})
    if worker.get('status') == 'running' and worker.get('thread_alive') is True:
        if not same_digest(worker.get('config_sha256'), expected):
            raise ValueError('live_pipeline_owner_identity_changed')
        result.update(worker_ready=True, status='managed-owner-running')
    return result


# Runtime actions describe the existing SDM controller; they are not a second
# workflow or permission to dispatch a model, resume a host, or submit a task.
ACTIONS = {
    'reviewing-initial': ('首轮评审', '研究主会话与独立评审会话', '等待首轮方案及代码评审的原始回执'),
    'watching': ('等待训练结果', '原任务监控器', '跟踪原任务终态，核验并回收结果'),
    'result-ready': ('结果待接续', '研究控制器', '登记结果分析和后继方案任务'),
    'reviewing-plan': ('分析与方案评审', '研究主会话与独立评审会话', '依据评审结论进入实施或登记停止决定'),
    'preparing-implementation': ('准备实施', '研究控制器', '派发已接受方案对应的实施任务'),
    'implementing': ('代码实施', '研究主会话', '等待实施回执，再核验实际改动'),
    'freezing': ('核验实施结果', '研究控制器', '核对实施产物、比较身份与可修改范围'),
    'preflighting-code': ('提交前检查', '研究控制器', '完成原有检查并进入独立代码评审'),
    'reviewing-code': ('代码评审', '独立评审会话', '依据原评审回执进入提交或修订'),
    'initial-submitting': ('首轮提交', '原提交门禁与任务管理器', '核对原授权和请求，登记首轮平台任务'),
    'submitting': ('后继提交', '原提交门禁与任务管理器', '核对原授权和请求，登记后继平台任务'),
}


def runtime_view(state, *, owner_alive=None, worker_ready=False, paused=False):
    state = state or {}
    attempts = state.get('attempts') or []
    current = attempts[-1] if attempts else {}
    stage = current.get('stage', 'not-started')
    continuation = current.get('continuation') or {}
    terminal = state.get('status') in ('research-goal-met', 'research-stopped')
    controller = owner_alive is True and worker_ready is True and not paused and not terminal
    label, owner, action = ACTIONS.get(stage, ('等待阶段登记', '研究控制器', '核对原控制器的阶段登记'))
    kind = 'waiting-training' if stage == 'watching' else 'advancing'
    blocking_code = None
    detail = '按原控制器阶段继续推进；是否执行仍由原评审、授权和提交门禁决定。'
    if terminal:
        kind, label, owner, action = 'completed', '研究已结束', '研究控制器', '查看原停止决定与保留的实验记录'
    elif paused:
        kind, label, owner, action = 'paused', '研究已暂停', '用户', '等待明确恢复指令，保留原现场'
    elif owner_alive is False:
        kind, label, owner, action = 'offline', '后台未运行', '原恢复入口', '核对原回执后恢复同一研究'
    elif owner_alive is not True or worker_ready is not True:
        kind, label, owner, action = 'unverified', '研究线程待核验', '原后台核验入口', '核对原后台与研究线程身份'
    elif continuation.get('phase') == 'reconciling':
        kind, label, owner, action = 'reconciling', '原请求对账中', '原控制器', '只核对原任务或模型调用，不创建新的请求身份'
    elif continuation.get('phase') == 'retry-wait':
        kind, label, owner, action = 'retry-wait', '等待重试只读查询', '原控制器', '按原退避时间重新读取原任务状态'
    elif stage == 'needs-attention' or continuation.get('phase') == 'blocked':
        kind, label, owner, action = 'blocked', '流程已阻塞', '待解决的前置条件', '处理已登记的具体阻塞，再沿原流程接续'
        blocking_code = current.get('reason') or continuation.get('error_code') or continuation.get('category') or 'unclassified-block'
        if 'awaiting-proposal-evidence' in blocking_code:
            label, detail = '等待评审证据', '评审仍有待补证据，当前不会提交下一训练。'
        else:
            detail = '后台进程仍可能存活，但当前阶段尚不能自动向下推进。'
    active = controller and kind not in ('blocked', 'completed', 'paused', 'offline', 'unverified')
    handoff = current.get('result_handoff') or {}
    return {'schema': 'sdm-research-runtime-view/v1', 'state': kind, 'label': label,
            'stage': stage, 'process_alive': owner_alive, 'controller_running': controller,
            'automatic_iteration_active': active, 'blocking_code': blocking_code,
            'next_owner': owner, 'next_action': action, 'detail': detail,
            'handoff': {'result_received': bool(current.get('result')),
                        'analysis_requested': handoff.get('model_requested') is True,
                        'analysis_job_id': handoff.get('job_id'),
                        'awaiting_analysis_dispatch': stage == 'result-ready' and handoff.get('model_requested') is not True},
            'execution_authority': False, 'model_calls': 0, 'platform_writes': 0}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    args=parser.parse_args();cfg=read_json(args.config)
    value=publish(cfg,read_json(Path(cfg['root'])/'component-pipeline/pipeline.json'))
    print(json.dumps({'status':'projected','rounds':len(value['rounds']),'stage':value['stage'],
        'needs_intervention':value['needs_intervention'],'platform_writes':0,'model_calls':0}))
