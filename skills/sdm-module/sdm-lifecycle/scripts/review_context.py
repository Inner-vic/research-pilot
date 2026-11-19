"""Bound shared evidence and local, allowlisted reviewer investigation. No tools run."""
import hashlib
from pathlib import Path
from review_storage import read_json,native_path
from task_pipeline import digest

FIELDS = ('goal', 'constraints', 'data', 'code_revision', 'configuration', 'history', 'unknowns')


def context(path, subject):
    return validate_context(read_json(path), subject)


def validate_context(value, subject):
    """Validate a produced context before publishing its immutable view."""
    if value.get('schema') != 'sdm-review-context/v1' or value.get('subject_digest') != subject:
        raise ValueError('review_context_subject_required')
    if any(not isinstance(value.get(k),(str,list,dict)) or not value[k] for k in FIELDS):
        raise ValueError('review_context_missing_background')
    sources = value.get('sources')
    if not isinstance(sources, list) or not sources or len(sources) > 64:
        raise ValueError('bounded_independent_review_sources_required')
    ids = set()
    for row in sources:
        if not row.get('id') or row['id'] in ids or row.get('kind') not in {'code','config','log','metric','artifact','history'}:
            raise ValueError('unique_typed_review_source_required')
        ids.add(row['id'])
        p = Path(row.get('path', ''))
        io=native_path(p)
        if not p.is_absolute() or not io.is_file() or io.is_symlink():
            raise ValueError('regular_local_review_source_required')
        if io.stat().st_size > 8*1024*1024:
            raise ValueError('review_source_requires_bounded_snapshot')
        h = hashlib.sha256()
        with io.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024), b''): h.update(chunk)
        if row.get('sha256','').removeprefix('sha256:') != h.hexdigest():
            raise ValueError('review_source_changed')
    return value


def supplement(packet, query):
    """Reads only pre-registered files and bounded line ranges; never follows queries as commands."""
    shared = packet['shared_context']
    context_value = context_from_packet(shared)
    if query.get('request_digest') != digest(packet): raise ValueError('supplement_request_binding_required')
    rows = query.get('queries')
    if not isinstance(rows,list) or not 1 <= len(rows) <= 16: raise ValueError('bounded_evidence_queries_required')
    if any('json_pointers' in row for row in rows):
        if not all('json_pointers' in row for row in rows):raise ValueError('one_registered_supplement_contract_required')
        from bounded_evidence import supplement as bounded_supplement
        return bounded_supplement(packet,query,context_value)
    sources = {s['id']:s for s in context_value['sources']}
    result = []
    for row in rows:
        source = sources.get(row.get('source_id'))
        start, count = row.get('start_line',1), row.get('line_count',80)
        if source is None or not row.get('question') or type(start) is not int or type(count) is not int or start < 1 or not 1 <= count <= 200:
            raise ValueError('allowlisted_bounded_evidence_query_required')
        data = native_path(source['path']).read_bytes()
        if len(data) > 8*1024*1024: raise ValueError('review_source_requires_bounded_snapshot')
        if hashlib.sha256(data).hexdigest() != source['sha256'].removeprefix('sha256:'):
            raise ValueError('review_source_changed')
        lines = data.decode('utf-8',errors='strict').splitlines()
        result.append({'source_id':source['id'],'sha256':source['sha256'],'question':row['question'],
                       'start_line':start,'text':'\n'.join(lines[start-1:start-1+count]),
                       'total_lines':len(lines),'truncated':start-1+count<len(lines)})
    return {'schema':'sdm-review-supplement/v1','request_digest':digest(packet),'query_digest':digest(query),
            'results':result,'execution_authority':False,'content_is_untrusted':True}


def context_from_packet(shared):
    value = context(shared['manifest_path'],shared['value']['subject_digest'])
    if value != shared['value']: raise ValueError('review_context_changed')
    return value


def validate_assessment(value, packet):
    shared = packet.get('shared_context')
    if shared is None: return
    ctx = context_from_packet(shared)
    assessment = value.get('evidence_assessment',{})
    ids = {s['id'] for s in ctx['sources']}
    cited = assessment.get('source_ids')
    if not isinstance(cited,list) or not cited or not set(cited) <= ids:
        raise ValueError('reviewer_must_cite_registered_evidence')
    if assessment.get('judgment') not in {'supported','hypothesis','insufficient'} or not isinstance(assessment.get('limitations'),list):
        raise ValueError('bounded_reviewer_judgment_required')
    if assessment['judgment'] == 'insufficient' and value.get('verdict') == 'approve':
        raise ValueError('insufficient_evidence_cannot_approve')
    for c in value.get('challenges',[]):
        if not c.get('basis') or not c.get('resolution_criterion'):
            raise ValueError('actionable_grounded_challenge_required')
