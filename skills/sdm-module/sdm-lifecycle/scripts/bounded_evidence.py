"""Versioned byte-bounded registered reads; JSON pointers select exact raw values.

No model, platform, arbitrary file lookup, evaluation or evidence summarization.
Legacy line-only receipts remain byte-identical under their frozen contract.
"""
import hashlib
import json
from pathlib import Path

QUERY_BYTES=32*1024
TOTAL_BYTES=64*1024
TOTAL_TEXT_BYTES=40*1024


def pointers(value):
 if not isinstance(value,list) or len(value)>16 or not all(isinstance(p,str) for p in value) or len(set(value))!=len(value):
  raise ValueError('bounded_json_pointers_required')
 for pointer in value:
  if (not isinstance(pointer,str) or not pointer.startswith('/') or len(pointer)>300
    or len(pointer.split('/'))>17 or any('~' in s.replace('~0','').replace('~1','') for s in pointer.split('/')[1:])):
   raise ValueError('registered_json_pointer_required')
 return value


def select(value,pointer):
 # RFC6901, plus * only for array members. Paths never identify disk files.
 parts=[s.replace('~1','/').replace('~0','~') for s in pointer.split('/')[1:]]
 def walk(current,left):
  if not left:return current
  key,*rest=left
  if isinstance(current,list) and key=='*':return [walk(item,rest) for item in current]
  if isinstance(current,list) and key.isdecimal() and str(int(key))==key:return walk(current[int(key)],rest)
  if isinstance(current,dict) and key in current:return walk(current[key],rest)
  raise ValueError('registered_json_pointer_not_found:'+pointer)
 try:return walk(value,parts)
 except (KeyError,IndexError):raise ValueError('registered_json_pointer_not_found:'+pointer)


def encoded(value):return json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def supplement(packet,query,context):
 from task_pipeline import digest
 rows=query['queries'];sources={s['id']:s for s in context['sources']};results=[]
 for row in rows:
  source=sources.get(row.get('source_id'));start=row.get('start_line');count=row.get('line_count')
  if (not source or not row.get('question') or type(start) is not int or start<1
      or type(count) is not int or not 1<=count<=200):raise ValueError('allowlisted_bounded_evidence_query_required')
  fields=pointers(row.get('json_pointers'));path=Path(source['path']);raw=path.read_bytes()
  if len(raw)>8*1024*1024:raise ValueError('review_source_requires_bounded_snapshot')
  if hashlib.sha256(raw).hexdigest()!=source['sha256'].removeprefix('sha256:'):raise ValueError('review_source_changed')
  lines=raw.decode('utf-8').splitlines();selected=[];reason=None
  json_document=bool(fields and path.suffix=='.json')
  if fields and path.suffix not in ('.json','.jsonl'):raise ValueError('json_field_query_requires_registered_json_source')
  if json_document and (start!=1 or count!=1):raise ValueError('json_document_query_uses_single_record')
  records=[raw.decode('utf-8')] if json_document else lines
  total=len(records)
  if start>total:raise ValueError('nonempty_bounded_registered_evidence_range_required')
  limit=min(QUERY_BYTES,TOTAL_TEXT_BYTES-sum(len(json.dumps(x['text'],ensure_ascii=False).encode()) for x in results))
  for i,text in enumerate(records[start-1:start-1+count],start):
   if fields:
    actual=json.loads(text);piece={'record':i,'fields':{}}
    for key in fields:
     try:piece['fields'][key]=select(actual,key)
     except ValueError:piece.setdefault('missing_pointers',[]).append(key)
    candidate=encoded(piece)
   else:candidate=text
   if len(encoded('\n'.join([*selected,candidate])).encode())>max(0,limit):
    reason='byte-budget-reached' if selected else 'record-exceeds-byte-budget-use-specific-json-pointers'
    break
   selected.append(candidate)
  delivered=len(selected);next_line=start+delivered if start+delivered<=total else None
  result={'source_id':source['id'],'sha256':source['sha256'],'question':row['question'],
   'start_line':start,'text':'\n'.join(selected),'total_lines':total,'truncated':next_line is not None,
   'view':'selected-json-fields' if fields else 'raw-lines','json_pointers':fields,
   'delivered_line_count':delivered,'next_start_line':next_line,'reason':reason,
   'scope':'Exact selected raw values only; other fields were NOT provided. All source bytes stay registered.' if fields
     else 'Exact complete lines; omitted lines are NOT evidence. Follow next_start_line or select JSON fields.'}
  results.append(result)
 value={'schema':'sdm-review-supplement/v2','request_digest':digest(packet),'query_digest':digest(query),
  'results':results,'execution_authority':False,'content_is_untrusted':True,'byte_limit':TOTAL_BYTES}
 if len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode())>TOTAL_BYTES:
  raise ValueError('bounded_supplement_metadata_exceeds_budget')
 return value
