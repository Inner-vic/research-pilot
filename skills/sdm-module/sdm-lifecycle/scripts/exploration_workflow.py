"""Exploration record groups derived from the full operational dependency graph.

All project drivers and conditional operation policies retain their original
dependencies. Only record-only gates are grouped, with durable partial acceptance.
"""
import json
from pathlib import Path
from review_storage import atomic_json

REVISION=2
GROUPS={'24':('research','method_review'),
        '31':('review_context','review_response'),
        '19':('evidence_consistency','conclusion_quality'),
        '20':('next_action','transition')}
GROUP_MEMBERS={'24':('24','25'),'31':('29','31'),'19':('19','32'),'20':('20','33')}


class RecordGroupIntegrityError(ValueError):
    """Accepted history changed; reject resume, retaining owned cleanup."""

PLAN=('background','research','method_review','inventory','proposal','plan_review','confirmation')
RESULT=('review_context','review_response','evidence_consistency','conclusion_quality','next_action','transition')
PACKAGES=(('plan','目标、材料、方案与审议',('1','2','22','3','4','6','24','26','27','34','28')),
          ('prepare','必要实现与执行准备',('9','23','10','11','12','8','14','7','13')),
          ('run','训练与评估',('5','15','16','17','18')),
          ('review','结果与后继评审',('31','19','20')),
          ('close','清理与归档',('30','21')))


def active(task):return task.get('workflow_profile')=='exploration'


def build(task):
    from workflow_definition import build as original
    base=original({**task,'workflow_profile':'legacy'})
    aliases={member:key for key,members in GROUP_MEMBERS.items() for member in members}
    rows={}
    for key,node in base.items():
        owner=aliases.get(key,key)
        if owner in rows:continue
        members=GROUP_MEMBERS.get(owner,(key,))
        dependencies=[]
        for member in members:
            for dependency in base[member]['depends']:
                mapped=aliases.get(dependency,dependency)
                if mapped!=owner and mapped not in dependencies:dependencies.append(mapped)
        rows[owner]={**base[owner],'depends':dependencies}
        if owner in GROUPS:
            rows[owner].update(builtin='record-group',record_group=list(GROUPS[owner]))
            # Preserve the failure-consumer policy of the original group members.
            if any(base[m].get('accept_failed_dependencies') for m in members):
                rows[owner]['accept_failed_dependencies']=True
    return rows


def record_refs(root):
    root=Path(root).resolve()
    return {name:{'path':str(root/('exploration-plan.json' if name in PLAN else 'exploration-result.json')),
                  'section':name} for name in PLAN+RESULT}


def put(task,paths,name,value):
    """One plan and one result document; original check schemas stay readable."""
    row={'schema':'sdm-orchestration-'+name.replace('_','-')+'/v1',**value}
    from execution_contracts import store_record
    store_record(task,name,row,equal_json=True,conflict='main_workflow_record_changed:'+name)
    return row


def gate(task,key,root,budget=30,now=None,deadline=None):
    from orchestration import gate as check,RECORDS,load,record_receipt_digest
    from execution_contracts import produce
    from review_storage import locked
    from task_pipeline import digest
    import time
    fields=GROUPS[key];folder=Path(root)/'record-groups'/key
    # Grouping does not reduce the old per-record production allowance or
    # enlarge any individual producer's allowance; the task wall limit wins.
    until=time.monotonic()+budget*len(fields)
    if deadline is not None:until=min(until,time.monotonic()+max(0,deadline-time.time()))
    with locked(folder,'.group.lock','record_group_busy'):
        state_path=folder/'accepted.json'
        try:state=json.loads(state_path.read_bytes()) if state_path.exists() else {'task_digest':digest(task),'records':{}}
        except (OSError,ValueError) as error:raise RecordGroupIntegrityError('record_group_state_invalid') from error
        if not isinstance(state,dict) or not isinstance(state.get('records'),dict):
            raise RecordGroupIntegrityError('record_group_state_invalid')
        if state.get('task_digest')!=digest(task):raise RecordGroupIntegrityError('record_group_task_changed')
        if list(state['records'])!=list(fields[:len(state['records'])]):raise RecordGroupIntegrityError('record_group_prefix_changed')
        for saved in state['records'].values():
            try:
                ref=saved['receipt']
                current=record_receipt_digest(ref['path'],ref.get('section'))
            except (OSError,ValueError,KeyError,TypeError) as error:
                raise RecordGroupIntegrityError('historical_grouped_record_changed') from error
            if current!=ref['digest']:raise RecordGroupIntegrityError('historical_grouped_record_changed')
        for name in fields:
            if name in state['records']:continue
            remaining=until-time.monotonic()
            if remaining<=0:return {'status':'awaiting-evidence','reason':'record_group_budget_exhausted','record_kind':name}
            loaded=load(task,name);production=None
            if loaded[0] is None:
                production=produce(task,name,Path(root),max(1,min(budget,int(remaining))))
                loaded=load(task,name)
            semantic_key=next(k for k,v in RECORDS.items() if v==name)
            outcome=check(task,semantic_key,now=now,loaded=loaded,record_name=name)
            if outcome['status']!='completed':return {**outcome,'record_kind':name}
            ref={'path':outcome['receipt'],'digest':outcome['receipt_digest']}
            if outcome.get('receipt_section'):ref['section']=outcome['receipt_section']
            saved={'receipt':ref,'outcome':outcome}
            if production is not None:saved['production']=production
            state['records'][name]=saved;atomic_json(state_path,state)
        result={'status':'completed','record_kind':'record-group',
            'record_group':list(fields),'record_results':{name:value['outcome'] for name,value in state['records'].items()},
            'source_receipts':[value['receipt'] for value in state['records'].values()],
            'productions':{name:value['production'] for name,value in state['records'].items() if 'production' in value},
            'qualification':'project-record-not-platform-certification'}
        primary=RECORDS[key]
        if 'production' in state['records'][primary]:result['production']=state['records'][primary]['production']
        for saved in state['records'].values():
            for output in ('recommendation','decision'):
                if output in saved['outcome']:result[output]=saved['outcome'][output]
        return result


def summary(nodes,state):
    rows=[]
    for key,label,keys in PACKAGES:
        keys=[k for k in keys if k in nodes]
        if not keys:continue
        required=[k for k in keys if nodes[k]['policy']=='required']
        active_keys=[k for k in keys if nodes[k]['policy']!='not-applicable']
        status=('not-applicable' if not active_keys else
                'failed' if any(state.get(k,{}).get('status') in {'failed','skipped'} for k in required)
                else 'completed' if required and all(state.get(k,{}).get('status')=='completed' for k in required) else 'pending')
        rows.append({'key':key,'display_id':str(len(rows)+1),'label':label,'checks':keys,
                     'required_checks':required,'optional_unmeasured':[k for k in active_keys if nodes[k]['policy']=='record' and state.get(k,{}).get('status')!='completed'],'status':status})
    return rows
