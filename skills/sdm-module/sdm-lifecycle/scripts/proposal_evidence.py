"""Opt-in registered, bounded evidence requests inside the original review loop.

No invented evidence, external retrieval, arbitrary path or execution capability.
The model identifies the decision gap; existing review_context reads and binds
only declared files. Rewording an identical request is not new evidence.
"""
from pathlib import Path

from review_storage import read_json
from task_pipeline import digest


def enabled(policy):
    value=policy.get('evidence_policy')
    if value is None:return False
    if (not isinstance(value,dict) or type(value.get('version')) is not int
            or value not in ({'version':1,'mode':'registered-supplements'},{'version':2,'mode':'registered-supplements'}) or policy.get('version')!=4 or policy.get('backend')!='model'):
        raise ValueError('explicit_registered_proposal_evidence_policy_required')
    return True


def query_schema(source_ids,version=1):
    from research_model_runner import object_schema
    fields=({'json_pointers':{'type':'array','maxItems':16,'items':{'type':'string'}}} if version==2 else {})
    return {'type':'array','maxItems':16,'items':object_schema({
        'source_id':{'type':'string','enum':source_ids},
        'start_line':{'type':'integer','minimum':1},
        'line_count':{'type':'integer','minimum':1,'maximum':200},
        'question':{'type':'string'},**fields})}


def validate(value,payload):
    queries=value.get('evidence_queries')
    sources={s['id']:s for s in payload['shared']['packet']['context']['sources']};ids=set(sources)
    if not isinstance(queries,list) or len(queries)>16:
        raise ValueError('bounded_registered_evidence_queries_required')
    if value.get('verdict')!='needs-evidence' and queries:
        raise ValueError('only_needs_evidence_may_request_supplements')
    if payload.get('registered_evidence_policy')==2:
        import json
        if len(json.dumps(queries,ensure_ascii=False).encode())>8192:raise ValueError('bounded_evidence_query_metadata_required')
    seen=set()
    for row in queries:
        if (not isinstance(row,dict) or set(row)!=({'source_id','start_line','line_count','question','json_pointers'} if payload.get('registered_evidence_policy')==2 else {'source_id','start_line','line_count','question'})
                or row['source_id'] not in ids or type(row['start_line']) is not int or row['start_line']<1
                or type(row['line_count']) is not int or not 1<=row['line_count']<=200
                or not isinstance(row['question'],str) or not row['question'].strip()):
            raise ValueError('typed_registered_evidence_query_required')
        if payload.get('registered_evidence_policy')==2:
            from bounded_evidence import pointers
            pointers(row['json_pointers'])
            if len(row['question'])>1000:raise ValueError('bounded_evidence_question_required')
        key=(row['source_id'],row['start_line'],row['line_count'],tuple(row.get('json_pointers',[])))
        if key in seen:raise ValueError('duplicate_registered_evidence_query')
        source=Path(sources[row['source_id']]['path'])
        if source.stat().st_size>8*1024*1024 or row['start_line']>len(source.read_bytes().splitlines()):
            raise ValueError('nonempty_bounded_registered_evidence_range_required')
        seen.add(key)


def history(folder,rows):
    from proposal_review import supplied
    result=[]
    for row in rows:
        saved=folder/row['directory'];accepted=read_json(saved/'accepted.json')
        supplements=supplied(read_json(saved/'request.json'),saved)
        result.append({**accepted,'registered_supplements':supplements})
    return result


def auto_supply(folder,rows):
    """Return a bound read or a real no-progress condition, never rerun the LLM."""
    from proposal_review import supplied,supply
    if not rows:return None
    current=folder/rows[-1]['directory'];accepted=read_json(current/'accepted.json')
    if accepted['response']['verdict']!='needs-evidence':return None
    queries=accepted['response'].get('evidence_queries',[])
    if not queries:return None  # External/new measurements require the Agent.
    seen={};line_counts={};queried=set()
    def identity(source,query):
        return (source['path'],source['sha256'],query['start_line'],query['line_count'],tuple(sorted(query.get('json_pointers',[]))))
    def segment(source,query):
        key=(source['path'],source['sha256'])
        if key not in line_counts:line_counts[key]=len(Path(source['path']).read_bytes().splitlines())
        key=(*key,tuple(sorted(query.get('json_pointers',[]))))
        return key,query['start_line'],min(line_counts[(source['path'],source['sha256'])],query['start_line']+query['line_count']-1)
    for row in rows:
        saved=folder/row['directory'];packet=read_json(saved/'request.json')
        supplied(packet,saved)  # Existing bytes and receipts must still match.
        sources={s['id']:s for s in packet['shared_context']['value']['sources']}
        for path in saved.glob('query-*.json'):
            for query in read_json(path)['queries']:
                source=sources[query['source_id']]
                queried.add(identity(source,query))
                key,start,end=segment(source,query)
                if 'json_pointers' in query:
                    responses=supplied(packet,saved)
                    delivered=[r for item in responses for r in item['value']['results']
                        if r['source_id']==query['source_id'] and r['start_line']==start and r.get('json_pointers')==query['json_pointers']]
                    end=start+max((r['delivered_line_count'] for r in delivered),default=0)-1
                    if end<start:end=start  # Repeating an unreadable huge record is no new evidence.
                seen.setdefault(key,[]).append((start,end))
    packet=read_json(current/'request.json')
    sources={s['id']:s for s in packet['shared_context']['value']['sources']}
    fresh=[]
    for query in queries:
        source=sources[query['source_id']]
        if identity(source,query) in queried:continue
        key,start,end=segment(source,query);remaining=start
        # The actual delivered ranges are authoritative; requested-but-omitted bytes are not read.
        for low,high in sorted(seen.get(key,[])):
            if high<remaining:continue
            if low>end:break
            if low>remaining:fresh.append({**query,'start_line':remaining,'line_count':min(low-1,end)-remaining+1})
            remaining=max(remaining,high+1)
        if remaining<=end:fresh.append({**query,'start_line':remaining,'line_count':end-remaining+1})
    fresh=fresh[:16] # Bounded registered query contract; unserved gaps remain unresolved.
    if not fresh:
        return {'status':'proposal-review-stalled','reason':'same_registered_evidence_already_supplied',
                'execution_authority':False,'automatic_retry':False}
    return supply(packet,current,{'request_digest':digest(packet),'queries':fresh})
