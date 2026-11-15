"""Live research changes and execution projections. Never dispatches computation.

The Agent supplies meaning at conversation boundaries. Only the coordinator
projects execution facts; recording failure cannot authorize/retry/stop work.
"""
import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

KINDS = {'question', 'hypothesis', 'finding', 'correction', 'decision'}
RELATIONS = {'supports', 'contradicts', 'revises', 'motivates', 'related_to'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}', value):
        raise ValueError('research_stable_id_required')
    return value


def settings(config):
    cfg = config.get('research_observation')
    if not isinstance(cfg, dict) or cfg.get('version') != 1:
        raise ValueError('research_observation_v1_required')
    identifier(cfg['project_id'])
    identifier(cfg['question_id'])
    if not Path(cfg['store']).is_absolute() or not cfg.get('run_roots'):
        raise ValueError('research_absolute_store_and_run_roots_required')
    for path in cfg['run_roots']:
        if not Path(path).is_absolute():
            raise ValueError('research_absolute_run_root_required')
    return cfg


def connect(config):
    cfg = settings(config)
    path = Path(cfg['store']); path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY, binding TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,
          request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS nodes(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);''')
    binding = digest(cfg)
    with db:
        db.execute('INSERT OR IGNORE INTO identity VALUES(1,?)', (binding,))
    if db.execute('SELECT binding FROM identity WHERE id=1').fetchone()[0] != binding:
        db.close(); raise ValueError('research_project_binding_changed')
    return cfg, db


def revision(db):
    return db.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]


def retained_refs(refs):
    if not isinstance(refs, list) or len(refs) > 32:
        raise ValueError('bounded_research_evidence_required')
    result = []
    for item in refs:
        path = Path(item['path'])
        if not path.is_absolute() or not path.is_file():
            raise ValueError('existing_research_evidence_required')
        # Reference and verify original bytes, never copy large checkpoints.
        sha = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                sha.update(chunk)
        value = sha.hexdigest()
        if item.get('sha256', value).removeprefix('sha256:') != value:
            raise ValueError('research_evidence_changed')
        result.append({'path': str(path), 'sha256': value, 'bytes': path.stat().st_size})
    return result


def event(config, body, *, projection=None):
    """One explicit conversation change, atomic and immutable; not an authority."""
    from research_record_contract import KINDS as V2_KINDS, RELATIONS as V2_RELATIONS, validate
    v2 = body.get('schema') == 'sdm-research-event/v2'
    if (not v2 and body.get('schema') != 'sdm-research-event/v1') or body.get('kind') not in (V2_KINDS if v2 else KINDS):
        raise ValueError('research_semantic_event_required')
    if 'projection' in body:
        raise ValueError('research_projection_coordinator_owned')
    if len(json.dumps(body, ensure_ascii=False).encode()) > 65536:
        raise ValueError('research_event_too_large')
    request = identifier(body['request_id']); node = identifier(body['id'])
    question = identifier(body.get('question_id', node))
    if not isinstance(body.get('text'), str) or not body['text'].strip():
        raise ValueError('research_change_text_required')
    actor = body.get('actor', {})
    if actor.get('kind') not in {'user', 'agent'} or not actor.get('source_ref'):
        raise ValueError('research_actor_provenance_required')
    cfg, db = connect(config)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM changes WHERE request_id=?', (request,)).fetchone()
            # Retries reuse accepted intent, even after context revision advances.
            intent = {k:v for k,v in body.items() if k != 'expected_revision'}
            if projection is not None:intent['projection'] = projection
            fingerprint = digest(intent)
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise ValueError('research_request_conflict')
                return {'status':'recorded', 'revision':previous['seq'], 'id':node, 'reused':True,
                        'execution_authority':False}
            if body.get('expected_revision', revision(db)) != revision(db):
                raise ValueError('research_revision_conflict_reread_context')
            if db.execute('SELECT 1 FROM nodes WHERE id=?', (node,)).fetchone():
                raise ValueError('research_node_immutable_use_correction')
            if body['kind'] == 'question':
                if question != node:
                    raise ValueError('question_identity_mismatch')
            else:
                parent = db.execute('SELECT payload FROM nodes WHERE id=?', (question,)).fetchone()
                if not parent or json.loads(parent[0])['kind'] != 'question':
                    raise ValueError('existing_research_question_required')
            runs = body.get('run_ids', [])
            if not isinstance(runs, list) or len(runs) > 32:
                raise ValueError('bounded_research_run_links_required')
            for run in runs:
                row = db.execute('SELECT payload FROM runs WHERE id=?', (identifier(run),)).fetchone()
                if not row or json.loads(row[0])['question_id'] != question:
                    raise ValueError('unknown_or_other_question_run')
            relations = body.get('relations', [])
            if not isinstance(relations, list) or len(relations) > 32:
                raise ValueError('bounded_research_relations_required')
            for rel in relations:
                if rel.get('relation') not in (V2_RELATIONS if v2 else RELATIONS) or rel.get('basis') not in {'observed','hypothesis','unknown'}:
                    raise ValueError('explicit_research_relation_required')
                target = db.execute('SELECT payload FROM nodes WHERE id=?', (identifier(rel['target']),)).fetchone()
                if not target or json.loads(target[0])['question_id'] != question:
                    raise ValueError('unknown_or_other_question_relation')
            if body['kind'] == 'correction' and not any(r['relation']=='revises' for r in relations):
                raise ValueError('correction_must_preserve_prior_judgment')
            if v2:
                validate(body, cfg, db, question)
            evidence = retained_refs(body.get('evidence', []))
            if body['kind'] in {'finding','correction','decision'} and not (evidence or runs or relations):
                raise ValueError('research_judgment_basis_required')
            if body['kind'] == 'decision' and not body.get('next_action'):
                raise ValueError('research_decision_next_action_required')
            payload = {**intent, 'question_id':question, 'evidence':evidence,
                       'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                       'execution_authority':False, 'provenance':'explicit-conversation-record'}
            db.execute('INSERT INTO nodes VALUES(?,?)', (node,json.dumps(payload, ensure_ascii=False)))
            db.execute('INSERT INTO changes(request_id,fingerprint,payload) VALUES(?,?,?)',
                       (request,fingerprint,json.dumps(payload, ensure_ascii=False)))
            return {'status':'recorded', 'revision':revision(db), 'id':node, 'reused':False,
                    'execution_authority':False}
    finally:
        db.close()


def capture_execution(config, result, output):
    """Project actual coordinator state at milestones; do not create a run."""
    if not config.get('research_observation'):
        return None
    db = None
    try:
        cfg, db = connect(config)
        root = Path(result.get('execution_root', output)).resolve()
        if not any(root.is_relative_to(Path(r).resolve()) for r in cfg['run_roots']):
            raise ValueError('research_execution_outside_registered_roots')
        source = root/'execution.json'
        source_bytes = source.read_bytes()
        state = json.loads(source_bytes)
        if not state.get('launch_intent'):
            return {'status':'not-launched', 'run_created':False}
        if not db.execute('SELECT 1 FROM nodes WHERE id=?', (cfg['question_id'],)).fetchone():
            raise ValueError('research_question_not_yet_recorded')
        run_id = 'run-' + digest({'root':str(root),'binding':state['binding']})[:32]
        process = state.get('process', {})
        terminal = state.get('process_terminal', False)
        payload = {'id':run_id, 'question_id':cfg['question_id'], 'kind':'run',
            'execution_binding':state['binding'], 'execution_root':str(root),
            'evidence_kind':state['evidence_kind'], 'status':state['status'],
            'phase':process.get('phase'), 'exit_code':process.get('exit_code'),
            'started_at':process.get('started_at'), 'finished_at':process.get('finished_at'),
            'updates_observed':bool(process.get('progress',{}).get('optimizer_updates')) if process.get('progress') else False,
            'optimizer_updates':process.get('progress',{}).get('optimizer_updates') if terminal and process.get('progress') else None,
            'stop_verified':state.get('stop_verified',False), 'source_path':str(source),
            'source_sha256':hashlib.sha256(source_bytes).hexdigest()}
        progress = process.get('progress') or {}
        payload['progress'] = {k:progress[k] for k in ('optimizer_updates','total_optimizer_updates','epoch','total_epochs') if k in progress}
        material = root/'artifacts/result.json'
        if state.get('artifacts_collected') and material.is_file() and material.stat().st_size <= 1048576:
            payload['result'] = json.loads(material.read_bytes())
            payload['result_ref'] = retained_refs([{'path':str(material)}])[0]
        # Mutable state readbacks can carry fresh timestamps without a research change.
        fingerprint = digest({k:v for k,v in payload.items() if k != 'source_sha256'})
        request = 'execution:' + run_id + ':' + fingerprint
        with db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM changes WHERE request_id=?', (request,)).fetchone():
                payload['at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                db.execute('INSERT OR REPLACE INTO runs VALUES(?,?)', (run_id,json.dumps(payload,ensure_ascii=False)))
                db.execute('INSERT INTO changes(request_id,fingerprint,payload) VALUES(?,?,?)',
                           (request,fingerprint,json.dumps(payload,ensure_ascii=False)))
        review = state.get('review', {})
        if review.get('status') == 'completed':
            # Completed workspace results expose case_directory; the waiting
            # result alone exposes response_path. Support the actual contract.
            response = Path(review['response_path']) if review.get('response_path') else Path(review['case_directory'])/'analysis-response.json'
            if 'sha256:' + digest(json.loads(response.read_bytes())) != review['binding']['response_digest']:
                raise ValueError('research_accepted_response_changed')
            accepted = review['decision']; key = digest(accepted)[:32]
            common = {'schema':'sdm-research-event/v1','question_id':cfg['question_id'],
                      'actor':{'kind':'agent','source_ref':str(response)},
                      'run_ids':[run_id], 'evidence':[{'path':str(response)}]}
            assessment_id = 'assessment-' + key
            event(config, {**common, 'request_id':assessment_id, 'id':assessment_id,
                'kind':'finding', 'text':accepted['previous_experiment_assessment'],
                'limitations':[h.get('limitations') for h in accepted.get('hypotheses',[])]},
                projection='accepted-agent-assessment')
            event(config, {**common, 'request_id':'decision-'+key, 'id':'decision-'+key,
                'kind':'decision', 'text':accepted['reason'],
                'relations':[{'relation':'motivates','target':assessment_id,'basis':'observed'}],
                'next_action':review['next_step']}, projection='accepted-agent-decision')
        return {'status':'recorded','run_id':run_id,'revision':revision(db),
                'execution_authority':False,'execution_unchanged':True}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return {'status':'unavailable','error':str(exc),'execution_unchanged':True,
                'queued':False}
    finally:
        if db is not None:db.close()


def context(config, question=None):
    cfg, db = connect(config)
    try:
        if question is not None:identifier(question)
        where = " WHERE json_extract(payload,'$.question_id')=?" if question else ''
        args = (question,) if question else ()
        def rows(table, limit):
            return list(reversed([json.loads(r[0]) for r in db.execute(
                'SELECT payload FROM '+table+where+' ORDER BY rowid DESC LIMIT ?',args+(limit,))]))
        nodes = rows('nodes',100); runs = rows('runs',30)
        counts = {t:db.execute('SELECT COUNT(*) FROM '+t+where,args).fetchone()[0]
                  for t in ('nodes','runs','changes')}
        edges = [{'source':n['id'],'target':r['target'],'relation':r['relation'],'basis':r['basis']}
                 for n in nodes for r in n.get('relations',[])]
        edges += [{'source':n['id'],'target':r,'relation':'uses_evidence','basis':'observed'}
                  for n in nodes for r in n.get('run_ids',[])]
        timeline = [{'revision':r['seq'], **{k:v for k,v in json.loads(r['payload']).items()
                     if k in {'id','kind','text','status','question_id','next_action','at',
                              'evidence_kind','exit_code','optimizer_updates'}}}
                    for r in db.execute('SELECT seq,payload FROM changes'+where+' ORDER BY seq DESC LIMIT 100',args)]
        return {'schema':'sdm-research-context/v1','project_id':cfg['project_id'],
                'revision':revision(db),'nodes':nodes,'runs':runs,
                'edges':edges,'timeline':list(reversed(timeline)),
                'has_more':counts['nodes']>100 or counts['runs']>30 or counts['changes']>100,
                'total_nodes':counts['nodes'],'total_runs':counts['runs'],
                'context_window':'latest-100-nodes-30-runs-100-changes; original history retained in store',
                'execution_authority':False,'scope':'research-records-not-current-resource-authorization'}
    finally:db.close()


def review_context(config, root):
    """Pin conversational context beside an existing review, never change facts.

    New conversation changes remain available from --research-context. This
    snapshot shows what was available when this review was first prepared.
    """
    if not config.get('research_observation'):return None
    try:
        path = Path(root)/'research-context.json'
        if not path.exists():
            value = context(config, settings(config)['question_id'])
            from review_storage import atomic_json
            atomic_json(path,value)
        value = json.loads(path.read_bytes())
        return {'status':'available','path':str(path), 'revision':value['revision'],
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'execution_authority':False}
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return {'status':'unavailable','error':str(exc),'execution_unchanged':True,'queued':False}
