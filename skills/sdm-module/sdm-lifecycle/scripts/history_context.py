"""Bounded reviewer context with append-only references to complete observations."""
import hashlib
import json
import math
from pathlib import Path
from review_storage import locked


def sequence_digest(values):
    """Canonical list digest without constructing another full serialized list."""
    checksum=hashlib.sha256();checksum.update(b'[')
    for index,value in enumerate(values):
        if index:checksum.update(b',')
        checksum.update(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
    checksum.update(b']')
    return 'sha256:'+checksum.hexdigest()

def policy(config):
    value=config.get('history_context')
    if value is None:return None
    if not isinstance(value,dict) or set(value)!={'recent_rounds','max_context_bytes'} or any(
            type(value[k]) is not int or value[k]<1 for k in value):raise ValueError('positive_history_context_policy_required')
    return value

def archive(history,root):
    from task_pipeline import digest
    root=Path(root).resolve();study=root.parent;path=study/'history-index.jsonl'
    entries=[]
    for row in history:
        source=study/f"round-{row['round']:02d}"/'round-observation.json'
        if source.is_symlink() or source.resolve()!=source or not source.is_file():raise ValueError('retained_round_observation_required')
        if digest(json.loads(source.read_bytes()))!=digest(row):raise ValueError('history_observation_changed')
        entries.append({'round':row['round'],'path':str(source),'digest':digest(row)})
    encoded=[(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode() for row in entries]
    with locked(study,'.history-index.lock','history_index_busy'):
        existing=path.read_bytes().splitlines(keepends=True) if path.exists() else []
        if existing[:len(encoded)]!=encoded[:len(existing)]:raise ValueError('history_index_changed')
        if len(existing)<len(encoded):
            import os
            with path.open('ab') as stream:
                for raw in encoded[len(existing):]:stream.write(raw)
                stream.flush();os.fsync(stream.fileno())
    return {'schema':'sdm-history-archive/v1','path':str(path),'count':len(history),
            'prefix_digest':'sha256:'+hashlib.sha256(b''.join(encoded)).hexdigest(),'history_digest':sequence_digest(history)}

def iter_entries(reference):
    if reference.get('schema')!='sdm-history-archive/v1' or type(reference.get('count')) is not int or reference['count']<1:
        raise ValueError('history_archive_contract_required')
    path=Path(reference['path'])
    if not path.is_absolute() or path.is_symlink():raise ValueError('absolute_history_index_required')
    checksum=hashlib.sha256()
    with path.open('rb') as stream:
        for index in range(reference['count']):
            raw=stream.readline(16385)
            if not raw.endswith(b'\n') or len(raw)>16384:raise ValueError('history_index_incomplete')
            checksum.update(raw);row=json.loads(raw)
            source=Path(row['path'])
            if row.get('round')!=index+1 or source.resolve()!=source or source.parent.parent!=path.parent or source.is_symlink():
                raise ValueError('history_reference_outside_study')
            yield row
    if 'sha256:'+checksum.hexdigest()!=reference['prefix_digest']:raise ValueError('history_index_digest_mismatch')


def entries(reference):
    return list(iter_entries(reference))

def read_round(reference,number):
    from task_pipeline import digest
    if type(number) is not int or not 1<=number<=reference['count']:raise ValueError('known_history_round_required')
    selected=None
    # Exhaust the iterator: an early selected round must not bypass prefix hashing.
    for row in iter_entries(reference):
        if row['round']==number:selected=row
    row=selected;value=json.loads(Path(row['path']).read_bytes())
    if digest(value)!=row['digest'] or value.get('round')!=number:raise ValueError('history_observation_digest_mismatch')
    return value

def iter_observations(reference):
    """Verify every source and the canonical full-history digest before exhaustion."""
    from task_pipeline import digest
    checksum=hashlib.sha256();checksum.update(b'[')
    count=0
    for row in iter_entries(reference):
        value=json.loads(Path(row['path']).read_bytes())
        if value.get('round')!=row['round'] or digest(value)!=row['digest']:raise ValueError('history_observation_digest_mismatch')
        if count:checksum.update(b',')
        checksum.update(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
        count+=1
        yield value
    checksum.update(b']')
    if count!=reference['count'] or 'sha256:'+checksum.hexdigest()!=reference['history_digest']:
        raise ValueError('complete_history_digest_mismatch')


def complete(reference):
    return list(iter_observations(reference))


def aggregate(history,metrics):
    """One pass, constant observation memory; preserve previous tie/exclusion rules."""
    from loop_contract import field
    totals={k:0 for k in ('round_count','training_failure_count','evaluation_failure_count','incomparable_count','uncertain_round_count')}
    selected={metric:metric.partition('.') for metric in metrics if metric.partition('.')[0] in {'training','evaluation'}}
    observed={metric:{'count':0} for metric in selected}
    for row in history:
        totals['round_count']+=1
        totals['training_failure_count']+=row['training']['status']!='completed'
        totals['evaluation_failure_count']+=row['evaluation']['status']!='completed'
        comparable=row.get('comparison',{}).get('comparable',False)
        uncertain=any(row.get(k,{}).get('uncertain_effect') for k in ('training','evaluation')) or any(v.get('uncertain_effect') for v in row.get('execution_findings',[]))
        totals['incomparable_count']+=not comparable;totals['uncertain_round_count']+=bool(uncertain)
        for metric,(stage,_,suffix) in selected.items():
            value=field(row[stage].get('actual',{}),suffix);stat=observed[metric]
            if comparable and row[stage]['status']=='completed' and not uncertain and type(value) in (int,float) and math.isfinite(value):
                pair=(value,row['round'])
                if not stat['count'] or pair<(stat['min'],stat['min_round']):stat.update(min=value,min_round=row['round'])
                if not stat['count'] or pair>(stat['max'],stat['max_round']):stat.update(max=value,max_round=row['round'])
                stat['count']+=1;stat['last_value']=value
    for stat in observed.values():stat['excluded_round_count']=totals['round_count']-stat['count']
    totals['metric_observations']=observed
    return totals


def scan(reference,metrics,keys=(),next_parameters=None):
    """Full verification and decision facts without loading the history list."""
    citations={};latest=None;repeated=False
    wanted={}
    for key in keys:
        if not isinstance(key,str):continue
        try:number=int(key.split('.')[0][1:])
        except (ValueError,IndexError):continue
        wanted.setdefault(number,[]).append(key)
    def rows():
        nonlocal latest,repeated
        for row in iter_observations(reference):
            latest=row
            repeated=repeated or (next_parameters is not None and row['parameters']==next_parameters)
            for key in wanted.get(row['round'],[]):
                exists,value=row_evidence(row,key)
                if exists:citations[key]=value
            yield row
    totals=aggregate(rows(),metrics)
    return {'summary':totals,'latest':latest,'repeated_parameters':repeated,'citations':citations}


def project(history,config,root):
    settings=policy(config)
    if settings is None or len(history)<=settings['recent_rounds']:return history,None,None
    if root is None:raise ValueError('history_archive_root_required')
    reference=archive(history,root);recent=history[-settings['recent_rounds']:]
    older=history[:-settings['recent_rounds']]
    anchors={older[0]['round']}
    failed=[row for row in older if row['training']['status']!='completed' or row['evaluation']['status']!='completed']
    incomparable=[row for row in older if not row.get('comparison',{}).get('comparable',False)]
    if failed:anchors.add(failed[-1]['round'])
    if incomparable:anchors.add(incomparable[-1]['round'])
    def summary(row):
        return {'round':row['round'],'parameters':row['parameters'],
            'training':{'status':row['training']['status']},'evaluation':{'status':row['evaluation']['status']},
            'comparison':{'comparable':row.get('comparison',{}).get('comparable',False)},
            'context_detail':'summary-read-archive-for-full-evidence'}
    selected=[summary(row) for row in older if row['round'] in anchors]+recent
    totals={**aggregate(history,config.get('project_metrics',[])),'omitted_round_count':len(history)-len(selected)}
    return selected,reference,totals

def enforce(packet,config):
    settings=policy(config)
    if settings is not None and len(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode())>settings['max_context_bytes']:
        raise ValueError('history_context_budget_exceeded_retrieve_or_raise_explicit_budget')

def row_evidence(row,key):
    found={}
    def flatten(value,prefix):
        if isinstance(value,dict):
            for name,item in value.items():flatten(item,prefix+'.'+name)
        else:found[prefix]=value
    number=row['round']
    for stage in ('training','evaluation'):
        found[f'r{number}.{stage}.status']=row[stage]['status']
        flatten(row[stage].get('actual',{}),f'r{number}.{stage}')
    flatten(row.get('comparison',{}),f'r{number}.comparison')
    flatten(row.get('execution_findings',[]),f'r{number}.execution_findings')
    return key in found,found.get(key)


def evidence(history,key):
    """Resolve original rN evidence, never a summary guess."""
    try:number=int(key.split('.')[0][1:])
    except (ValueError,IndexError,AttributeError):return False,None
    if not 1<=number<=len(history):return False,None
    return row_evidence(history[number-1],key)
