"""Versioned semantic records. Validation only; no execution or database migration."""
import re

KINDS = {'question', 'hypothesis', 'finding', 'correction', 'decision', 'material',
         'discussion', 'consensus', 'plan', 'branch', 'experiment', 'review',
         'focus', 'status', 'conclusion'}
RELATIONS = {'supports', 'contradicts', 'revises', 'motivates', 'related_to',
             'extends', 'tests', 'cites', 'supersedes'}
PACKAGES = {'1.1', '1.2', '2.1', '2.2', '3.1', '3.2', '3.3', '4.1', '4.2', '5.1', '5.2'}


def validate(body, cfg, db, question):
    import json
    from research_observation import identifier, retained_refs

    if body.get('project_id') != cfg['project_id']:
        raise ValueError('research_v2_exact_project_required')

    def target(key):
        value = body.get(key)
        if value is None:
            return None
        identifier(value)
        row = db.execute('SELECT payload FROM nodes WHERE id=?', (value,)).fetchone()
        if not row or json.loads(row[0])['question_id'] != question:
            raise ValueError('research_v2_unknown_or_other_question_' + key)
        return json.loads(row[0])

    parent = target('parent_id')
    plan_node = target('plan_id')
    prior_plan = target('parent_plan_id')
    target('target_id')
    if any(n and n['kind'] != 'plan' for n in (plan_node, prior_plan)):
        raise ValueError('research_v2_plan_reference_type_required')
    if body.get('work_package') is not None and body['work_package'] not in PACKAGES:
        raise ValueError('research_v2_work_package_required')
    if 'recording' not in body or body['recording'] not in {'live', 'backfill'}:
        raise ValueError('research_v2_recording_live_or_backfill_required')
    if body['recording'] == 'backfill' and not body.get('backfill_reason'):
        raise ValueError('research_v2_backfill_reason_required')
    if 'session_id' in body:
        identifier(body['session_id'])
    if body['kind'] == 'plan':
        plan = body.get('plan', {})
        identifier(plan.get('id'))
        if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', str(plan.get('version', ''))):
            raise ValueError('research_v2_exact_plan_version_required')
        # Exact original material is retained with the event's evidence.
        ref = retained_refs([plan.get('content_ref', {})])[0]
        if plan['content_ref'].get('sha256', '').removeprefix('sha256:') != ref['sha256']:
            raise ValueError('research_v2_exact_plan_digest_required')
        if not any(e.get('path') == ref['path'] and
                   e.get('sha256', '').removeprefix('sha256:') == ref['sha256']
                   for e in body.get('evidence', [])):
            raise ValueError('research_v2_plan_content_must_be_evidence')
        existing = [json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM nodes WHERE json_extract(payload,'$.kind')='plan' "
            "AND json_extract(payload,'$.question_id')=?", (question,))]
        same = [n for n in existing if n.get('plan', {}).get('id') == plan['id']]
        if any(n['plan']['version'] == plan['version'] for n in same):
            raise ValueError('research_v2_plan_version_immutable')
        if same:
            prior = target('parent_plan_id')
            if not prior or prior['id'] not in {n['id'] for n in same}:
                raise ValueError('research_v2_parent_plan_required')
            if not any(r['target'] == prior['id'] and r['relation'] in {'revises', 'supersedes'}
                       for r in body.get('relations', [])):
                raise ValueError('research_v2_plan_revision_basis_required')
    if body['kind'] == 'branch' and not parent:
        raise ValueError('research_v2_branch_parent_required')
    if body['kind'] == 'review':
        if not body.get('relations') or body.get('review_status') not in {'advisory', 'changes-requested'}:
            raise ValueError('research_v2_review_is_semantic_not_contract_acceptance')
    if body['kind'] == 'focus':
        if not body.get('session_id') or not target('target_id'):
            raise ValueError('research_v2_focus_session_and_target_required')
        if body.get('state') not in {'active', 'paused', 'closed'}:
            raise ValueError('research_v2_focus_state_required')
        if body['state'] == 'paused' and not body.get('waiting_for'):
            raise ValueError('research_v2_pause_wait_condition_required')
    if body['kind'] in {'status', 'conclusion'} and not (body.get('relations') or body.get('run_ids')):
        raise ValueError('research_v2_state_basis_required')
