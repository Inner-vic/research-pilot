"""Carry actual refusals across science/implementation review without source-ID drift."""
from pathlib import Path
from review_storage import read_json
from component_research import reference
from component_payload import ref
from research_replan import once

KINDS={'sdm-implementation-capability-gap/v1','sdm-research-recovery-feedback/v1',
    'sdm-implementation-validation-feedback/v1','sdm-implementation-code-feedback/v1'}


def history(refs,root):
    rows=[];seen=set()
    for source in refs:
        path=reference(source);value=read_json(path);key=source['sha256'].removeprefix('sha256:')
        if key in seen:continue
        seen.add(key);rows.append({'source':source,'value':value})
    path=Path(root)/'research-refusal-history.json'
    once(path,{'schema':'sdm-scientific-refusal-history/v1','records':rows,
        'authority':'Original records verbatim, including objections, refusal causes and dispositions; neither approval nor new execution authority.'})
    index=Path(root)/'research-refusal-index.json'
    selected=[]
    for i,row in enumerate(rows,1):
        value=row['value'];problem=value.get('problem',{})
        entry={'record':i,'source':row['source'],'schema':value.get('schema')}
        entry.update({k:value[k] for k in ('reported_kind','implementer_claim','reason','next') if k in value})
        if problem:entry['original_problem']={k:problem[k] for k in ('reason','original_critic') if k in problem}
        selected.append(entry)
    once(index,{'schema':'sdm-scientific-refusal-index/v1','records':selected,'full_records':ref(path),
        'scope':'Verbatim blocker claims, actual critiques and criteria; other fields remain in the registered raw archive. Inspect treatment-relevant records, not every past rejected proposal indiscriminately.'})
    return ref(path)


def handoff(plan,root):
    context_path=Path(plan['review_root'])/'review-context.json';ctx=read_json(context_path)
    if ctx['subject_digest']!=plan['proposal']['subject_digest']:
        raise ValueError('scientific_handoff_context_binding_changed')
    originals=[];aliases=[]
    for source in ctx['sources']:
        path=reference(source)
        if path.suffix!='.json':continue
        value=read_json(path)
        if not isinstance(value,dict):continue
        if value.get('schema')=='sdm-scientific-refusal-history/v1':
            originals.extend(record['source'] for record in value['records'])
            aliases.append({'scientific_source_id':source['id'],'source':source,'record_count':len(value['records'])})
        elif value.get('schema') in KINDS:
            originals.append(source);aliases.append({'scientific_source_id':source['id'],'source':source})
    catalog=history(originals,root)
    index=Path(root)/'scientific-evidence-handoff.json'
    once(index,{'schema':'sdm-scientific-evidence-handoff/v1','original_review_context':ref(context_path),
        'refusal_catalog':catalog,'original_source_aliases':aliases,
        'rule':'Source IDs are local to each review. Original scientific IDs resolve through this map and verbatim refusal catalog; do not confuse them with code-source numbers. Check only treatment-relevant refusals against the accepted plan.',
        'execution_authority':False})
    return [ref(index),ref(Path(root)/'research-refusal-index.json'),catalog]
