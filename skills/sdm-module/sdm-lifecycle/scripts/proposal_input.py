"""Lossless critic history projection. No summaries, model calls or writes."""
import copy
from pathlib import Path
from review_storage import read_json
from task_pipeline import digest


def original_previous(packet,folder):
    rows=[]
    for index,item in enumerate(packet.get('prior_rounds',[]),1):
        accepted=read_json(Path(folder)/f'round-{index:02d}'/'accepted.json')
        if (digest(accepted)!=item['accepted_digest'] or accepted['proposal']['revision']!=item['proposal_revision']
                or accepted['response']!=item['response']):
            raise ValueError('critic_original_history_changed')
        rows.append(accepted)
    return rows


def projection(actual,evidence,folder):
    import hashlib
    shared=Path(folder)/'shared-evidence.json'
    index={'path':str(shared.resolve()),'sha256':'sha256:'+hashlib.sha256(shared.read_bytes()).hexdigest()}
    catalog={(str(Path(r['path']).resolve()),r['sha256'].removeprefix('sha256:'))
        for r in evidence['shared']['packet']['context']['sources']}
    rows=copy.deepcopy(actual)
    for i,row in enumerate(rows):
        row['response']={'projection':'packet.prior_rounds[%d].response'%i}
        refs=row['proposal'].get('evidence',[])
        if refs and all((str(Path(r['path']).resolve()),r['sha256'].removeprefix('sha256:')) in catalog for r in refs):
            row['proposal']['evidence']=[index]
    return rows


def legacy_project(value,folder):
    """Deduplicate only bound evidence indexes and already-present critiques."""
    evidence=value['evidence'];previous=evidence.get('previous',[])
    if value['purpose']!='method-review' or not previous:return value
    actual=original_previous(evidence['packet'],folder)
    if actual!=previous:raise ValueError('critic_packet_history_not_lossless')
    projected=copy.deepcopy(value)
    projected['evidence']['previous']=projection(actual,evidence,folder)
    projected['evidence']['previous_projection']={'version':1,'source':'accepted-receipts',
        'original_digest':digest(actual),'rounds':len(actual)}
    projected['prompt']+=(' Complete past critiques/challenges occur exactly once in packet.prior_rounds; '
        'previous retains all past proposal text, with duplicate machine evidence lists replaced by '
        'the bound shared source index. previous_projection binds the original accepted receipts. '
        'Resolve every critical prior challenge; no decision, rationale or raw evidence has been summarized. ')
    return projected


def legacy_restore(evidence,folder):
    marker=evidence.get('previous_projection')
    if marker is None:return evidence
    if marker.get('version')!=1 or marker.get('source')!='accepted-receipts':
        raise ValueError('critic_history_projection_invalid')
    actual=original_previous(evidence['packet'],folder)
    if (marker!={'version':1,'source':'accepted-receipts','original_digest':digest(actual),'rounds':len(actual)}
            or evidence['previous']!=projection(actual,evidence,folder)):
        raise ValueError('critic_history_projection_binding_changed')
    return {**{k:v for k,v in evidence.items() if k!='previous_projection'},'previous':actual}


def context_project(value,folder):
    """Lossless v2: one shared context; keep every objection and supplement."""
    projected=legacy_project(value,folder)
    evidence=projected['evidence'];packet=evidence.get('packet')
    if packet and packet.get('shared_context',{}).get('value')==evidence['shared']['packet']['context']:
        projected=copy.deepcopy(projected);evidence=projected['evidence']
        original=evidence['packet']['shared_context']['value']
        evidence['packet']['shared_context']['value']={'projection':'evidence.shared.packet.context'}
        evidence['context_projection']={'version':1,'original_digest':digest(original)}
        projected['prompt']+=(' packet.shared_context.value is exactly evidence.shared.packet.context; '
            'the identical shared context is inlined once, without removing any evidence or critique. ')
    return projected


def context_restore(evidence,folder):
    marker=evidence.get('context_projection')
    if marker is not None:
        original=evidence['shared']['packet']['context']
        if (marker!={'version':1,'original_digest':digest(original)} or
                evidence['packet']['shared_context']['value']!={'projection':'evidence.shared.packet.context'}):
            raise ValueError('critic_shared_context_projection_changed')
        evidence=copy.deepcopy(evidence);evidence.pop('context_projection')
        evidence['packet']['shared_context']['value']=original
    return legacy_restore(evidence,folder)



def source_index(evidence,folder):
    import hashlib
    shared=Path(folder)/'shared-evidence.json'
    return {'path':str(shared.resolve()),'sha256':'sha256:'+hashlib.sha256(shared.read_bytes()).hexdigest()}


def indexed(refs,evidence):
    catalog={(str(Path(r['path']).resolve()),r['sha256'].removeprefix('sha256:'))
        for r in evidence['shared']['packet']['context']['sources']}
    return refs and all((str(Path(r['path']).resolve()),r['sha256'].removeprefix('sha256:')) in catalog for r in refs)


def literal_pack(evidence):
    """Intern exact repeated prose/code text; no summaries or removed facts."""
    import hashlib
    original=digest(evidence);anchors={};count=0
    prose={'text','objective','hypothesis','evidence_summary','counterevidence','alternatives','comparison',
        'expected_observations','outcome_actions','cost_and_queue','preflight_and_stop','rationale',
        'reason','question','basis','resolution_criterion','intervention','assumption','expected_decision_value'}
    def seed(value,path):
        if isinstance(value,str) and len(value)>256:anchors.setdefault(value,path)
        elif isinstance(value,dict):
            for k,v in value.items():seed(v,[*path,k])
        elif isinstance(value,list):
            for i,v in enumerate(value):seed(v,[*path,i])
    seed(evidence['shared'],['shared'])
    # Supplements often overlap an already inlined prefix or an earlier range.
    # Use the registered source's exact character intervals, never a summary.
    from review_storage import native_path
    registry={r['id']:r for r in evidence['shared']['packet']['context']['sources']}
    texts={};intervals={}
    for index,row in enumerate(evidence['shared'].get('raw_sources',[])):
        sid=row.get('source_id');text=row.get('text')
        if sid not in registry or not isinstance(text,str) or len(text)<256:continue
        source=registry[sid];raw=native_path(Path(source['path'])).read_bytes()
        if len(raw)>8*1024*1024:continue
        if hashlib.sha256(raw).hexdigest()!=source['sha256'].removeprefix('sha256:'):
            raise ValueError('registered_literal_source_changed')
        full=raw.decode('utf-8');texts[sid]=full;offset=full.find(text)
        if offset>=0:intervals.setdefault(sid,[]).append((offset,offset+len(text),['shared','raw_sources',index,'text']))
    def registered(row,path):
        nonlocal count
        sid=row.get('source_id');text=row.get('text')
        if sid not in registry or not isinstance(text,str) or len(text)<256 or 'start_line' not in row:return None
        source=registry[sid]
        if row.get('sha256','').removeprefix('sha256:')!=source['sha256'].removeprefix('sha256:'):return None
        if sid not in texts:
            raw=native_path(Path(source['path'])).read_bytes()
            if len(raw)>8*1024*1024:return None
            if hashlib.sha256(raw).hexdigest()!=source['sha256'].removeprefix('sha256:'):raise ValueError('registered_literal_source_changed')
            texts[sid]=raw.decode('utf-8')
        full=texts[sid];offset=sum(len(s) for s in full.splitlines(keepends=True)[:row['start_line']-1]);start=full.find(text,max(0,offset-2))
        if start<0:return None
        end=start+len(text);position=start;parts=[];new_intervals=[];hits=0
        available=sorted(intervals.get(sid,[]),key=lambda x:(x[0],-x[1]))
        while position<end:
            covers=[r for r in available if r[0]<=position<r[1] and min(r[1],end)-position>=256]
            if covers:
                low,high,anchor=max(covers,key=lambda r:r[1]);finish=min(high,end);piece=full[position:finish]
                parts.append({'_sdm_literal_ref':anchor,'start':position-low,'end':finish-low,
                    'sha256':hashlib.sha256(piece.encode('utf-8')).hexdigest()});hits+=1;position=finish
            else:
                starts=[r[0] for r in available if position<r[0]<end and min(r[1],end)-r[0]>=256]
                finish=min(starts) if starts else end;piece=full[position:finish];part=len(parts);parts.append(piece)
                if len(piece)>=256:new_intervals.append((position,finish,[*path,'text','_sdm_literal_parts',part]))
                position=finish
        if hits:
            count+=hits;intervals.setdefault(sid,[]).extend(new_intervals)
            return {'_sdm_literal_parts':parts,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}
        intervals.setdefault(sid,[]).append((start,end,[*path,'text']));return None
    def visit(value,path,key=None):
        nonlocal count
        if isinstance(value,str) and len(value)>256 and key in prose:
            anchor=anchors.get(value)
            if anchor is not None:
                count+=1
                return {'_sdm_literal_ref':anchor,'sha256':hashlib.sha256(value.encode('utf-8')).hexdigest()}
            anchors[value]=path
        elif isinstance(value,dict):
            packed=registered(value,path)
            return {k:packed if k=='text' and packed is not None else visit(v,[*path,k],k) for k,v in value.items()}
        elif isinstance(value,list):return [visit(v,[*path,i],key) for i,v in enumerate(value)]
        return value
    for key in ('packet','previous'):
        if key in evidence:evidence[key]=visit(evidence[key],[key])
    if count:evidence['literal_projection']={'version':1,'original_digest':original,'references':count}
    return count


def literal_restore(evidence):
    import hashlib
    marker=evidence.get('literal_projection')
    if marker is None:return evidence
    source=copy.deepcopy(evidence);source.pop('literal_projection');count=0
    def resolve(value,stack=()):
        nonlocal count
        if isinstance(value,dict) and set(value) in ({'_sdm_literal_ref','sha256'},{'_sdm_literal_ref','start','end','sha256'}):
            pointer=value['_sdm_literal_ref']
            if not isinstance(pointer,list) or not pointer or pointer[0] not in {'shared','packet','previous'} or len(pointer)>64:
                raise ValueError('literal_projection_pointer_invalid')
            key=tuple(pointer)
            if key in stack:raise ValueError('literal_projection_cycle')
            target=source
            for part in pointer:
                if not isinstance(part,(str,int)) or isinstance(part,bool):raise ValueError('literal_projection_pointer_invalid')
                target=target[part]
            text=resolve(target,(*stack,key))
            if 'start' in value:
                if (not isinstance(text,str) or type(value['start']) is not int or type(value['end']) is not int
                        or not 0<=value['start']<value['end']<=len(text)):raise ValueError('literal_projection_range_invalid')
                text=text[value['start']:value['end']]
            if not isinstance(text,str) or hashlib.sha256(text.encode('utf-8')).hexdigest()!=value['sha256']:
                raise ValueError('literal_projection_text_changed')
            count+=1;return text
        if isinstance(value,dict) and set(value)=={'_sdm_literal_parts','sha256'}:
            parts=value['_sdm_literal_parts']
            if not isinstance(parts,list):raise ValueError('literal_projection_parts_invalid')
            decoded=[resolve(part,stack) for part in parts]
            if not all(isinstance(part,str) for part in decoded):raise ValueError('literal_projection_parts_invalid')
            text=''.join(decoded)
            if hashlib.sha256(text.encode('utf-8')).hexdigest()!=value['sha256']:raise ValueError('literal_projection_text_changed')
            return text
        if isinstance(value,dict):return {k:resolve(v,stack) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v,stack) for v in value]
        return value
    restored=resolve(source)
    if marker!={'version':1,'original_digest':digest(restored),'references':count}:
        raise ValueError('literal_projection_binding_changed')
    return restored


def json_fragment(text):
    # JSON physical newlines cannot occur inside quoted values. Preserve all
    # quoted bytes (including embedded model source); strip layout only.
    result=[];quoted=False;escaped=False
    for char in text:
        if quoted:
            result.append(char)
            if escaped:escaped=False
            elif char==chr(92):escaped=True
            elif char=='"':quoted=False
        elif char=='"':quoted=True;result.append(char)
        elif not char.isspace():result.append(char)
    if quoted:return None
    return ''.join(result)


def json_pack(evidence):
    import hashlib
    from review_storage import native_path
    original=digest(evidence);count=0;registry={r['id']:r for r in evidence['shared']['packet']['context']['sources']};cache={}
    def visit(value):
        nonlocal count
        if isinstance(value,dict):
            source=registry.get(value.get('source_id'));text=value.get('text');start=value.get('start_line')
            if (source and Path(source['path']).suffix=='.json' and isinstance(text,str) and len(text)>512
                    and type(start) is int and start>=1 and value.get('sha256','').removeprefix('sha256:')==source['sha256'].removeprefix('sha256:')):
                sid=value['source_id']
                if sid not in cache:
                    raw=native_path(Path(source['path'])).read_bytes()
                    if len(raw)>8*1024*1024:raise ValueError('registered_json_source_requires_bounded_snapshot')
                    if hashlib.sha256(raw).hexdigest()!=source['sha256'].removeprefix('sha256:'):raise ValueError('registered_json_source_changed')
                    cache[sid]=raw.decode('utf-8').splitlines()
                lines=len(text.split('\n'));original_text='\n'.join(cache[sid][start-1:start-1+lines]);compact=json_fragment(text)
                if original_text==text and compact is not None and len(text)-len(compact)>300:
                    count+=1
                    value={**value,'text':{'_sdm_registered_json_text':{'source_id':sid,'start_line':start,'line_count':lines,
                        'original_sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()},'compact':compact}}
            return {k:visit(v) for k,v in value.items()}
        if isinstance(value,list):return [visit(v) for v in value]
        return value
    for key in ('packet','previous'):
        if key in evidence:evidence[key]=visit(evidence[key])
    if count:evidence['json_text_projection']={'version':1,'original_digest':original,'ranges':count}
    return count


def json_restore(evidence):
    import hashlib
    from review_storage import native_path
    marker=evidence.get('json_text_projection')
    if marker is None:return evidence
    evidence=copy.deepcopy(evidence);evidence.pop('json_text_projection');registry={r['id']:r for r in evidence['shared']['packet']['context']['sources']};count=0;cache={}
    def visit(value):
        nonlocal count
        if isinstance(value,dict) and set(value)=={'_sdm_registered_json_text','compact'}:
            row=value['_sdm_registered_json_text'];sid=row.get('source_id');source=registry.get(sid)
            if source is None or Path(source['path']).suffix!='.json' or type(row.get('start_line')) is not int or type(row.get('line_count')) is not int or row['start_line']<1 or not 1<=row['line_count']<=200:
                raise ValueError('registered_json_range_invalid')
            if sid not in cache:
                raw=native_path(Path(source['path'])).read_bytes()
                if len(raw)>8*1024*1024 or hashlib.sha256(raw).hexdigest()!=source['sha256'].removeprefix('sha256:'):raise ValueError('registered_json_source_changed')
                cache[sid]=raw.decode('utf-8').splitlines()
            text='\n'.join(cache[sid][row['start_line']-1:row['start_line']-1+row['line_count']])
            if hashlib.sha256(text.encode('utf-8')).hexdigest()!=row['original_sha256'] or json_fragment(text)!=value['compact']:
                raise ValueError('registered_json_text_projection_changed')
            count+=1;return text
        if isinstance(value,dict):return {k:visit(v) for k,v in value.items()}
        if isinstance(value,list):return [visit(v) for v in value]
        return value
    restored=visit(evidence)
    if marker!={'version':1,'original_digest':digest(restored),'ranges':count}:raise ValueError('registered_json_projection_binding_changed')
    return restored


def project(value,folder):
    projected=context_project(value,folder);projected=copy.deepcopy(projected);evidence=projected['evidence']
    # The current critic proposal repeats the same machine evidence index as
    # prior proposals. Restore it from the immutable original round request.
    packet=evidence.get('packet')
    if packet and indexed(packet['proposal'].get('evidence',[]),evidence):
        original=read_json(Path(folder)/('round-%02d'%evidence['revision'])/'request.json')
        evidence['packet_evidence_projection']={'version':1,'request_digest':digest(original)}
        packet['proposal']['evidence']=[source_index(evidence,folder)]
    if value['purpose']=='proposal' and evidence.get('previous'):
        from proposal_evidence import history
        state=read_json(Path(folder)/'state.json');actual=(history(Path(folder),state['rounds']) if evidence.get('registered_evidence_policy') else
            [read_json(Path(folder)/row['directory']/'accepted.json') for row in state['rounds']])
        if actual!=evidence['previous']:raise ValueError('proposer_history_not_lossless')
        for row in evidence['previous']:
            if indexed(row['proposal'].get('evidence',[]),evidence):row['proposal']['evidence']=[source_index(evidence,folder)]
        evidence['proposer_previous_projection']={'version':1,'original_digest':digest(actual)}
        projected['prompt']+=(' Previous proposal evidence lists resolve to the exact bound shared source index; proposal text, past critiques and registered supplements remain intact. ')
    json_ranges=json_pack(evidence)
    if json_ranges:projected['prompt']+=(' _sdm_registered_json_text.compact preserves every JSON value and string exactly; only layout whitespace is encoded by the bound registered file/range. These may be fragments, not standalone JSON documents. No source code or prose inside strings has changed. ')
    packed=literal_pack(evidence)
    if packed:projected['prompt']+=(' _sdm_literal_ref paths refer to the exact original text elsewhere in this evidence object. '
        'Ranges start/end use character offsets; _sdm_literal_parts concatenates literal strings and referenced ranges in order. '
        'Resolve these references as written; no evidence, scientific statement or objection has been summarized. ')
    return projected


def restore(evidence,folder):
    evidence=json_restore(literal_restore(evidence))
    # Shared-context reconstruction precedes current-packet index validation.
    marker=evidence.get('packet_evidence_projection')
    if marker is not None:
        evidence=copy.deepcopy(evidence);evidence.pop('packet_evidence_projection')
        original=read_json(Path(folder)/('round-%02d'%evidence['revision'])/'request.json')
        if marker!={'version':1,'request_digest':digest(original)} or evidence['packet']['proposal']['evidence']!=[source_index(evidence,folder)]:
            raise ValueError('critic_current_evidence_projection_changed')
        evidence['packet']['proposal']['evidence']=original['proposal']['evidence']
    proposer=evidence.get('proposer_previous_projection')
    if proposer is not None:
        evidence=copy.deepcopy(evidence);evidence.pop('proposer_previous_projection')
        from proposal_evidence import history
        rows=read_json(Path(folder)/'state.json')['rounds'][:len(evidence['previous'])]
        actual=(history(Path(folder),rows) if evidence.get('registered_evidence_policy') else
            [read_json(Path(folder)/row['directory']/'accepted.json') for row in rows])
        compact=copy.deepcopy(actual)
        for row in compact:
            if indexed(row['proposal'].get('evidence',[]),evidence):row['proposal']['evidence']=[source_index(evidence,folder)]
        if proposer!={'version':1,'original_digest':digest(actual)} or compact!=evidence['previous']:
            raise ValueError('proposer_history_projection_changed')
        evidence['previous']=actual
    restored=context_restore(evidence,folder)
    if marker is not None and restored['packet']!=original:raise ValueError('critic_original_packet_projection_changed')
    return restored
