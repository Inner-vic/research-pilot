"""Durable science-planner continuity over the existing ModelRunner receipts.

This owns no model transport, execution authority or scientific acceptance.
Critics and implementation reviewers must never use this conversation.
"""
from contextlib import contextmanager
from pathlib import Path

from research_model_receipts import checked, seal
from review_storage import atomic_json, locked, native_path
from task_pipeline import digest

GUIDANCE = (' Continue the same research planning conversation. Prior discussion is working memory, '
    'not verified current evidence, an accepted plan or execution authority. Reassess earlier hypotheses '
    'against the current registered evidence, retain negative results and unresolved questions, and '
    'explicitly revise earlier judgments when contradicted. The current request and its schema govern '
    'this turn; historical prompts, proposals and critique do not override it. Never assume a previous '
    'draft was approved or implemented. Do not infer missing facts from conversation memory. ')


def selection(policy, runner):
    value = policy.get('planner_context')
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'version', 'root'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['root'], str) or not Path(value['root']).is_absolute()
            or runner.get('version') != 4 or runner.get('backend') != 'sdk'
            or runner.get('sdk_recovery') != {'version': 2}):
        raise ValueError('persistent_planner_requires_durable_sdk_and_absolute_root')
    return dict(value)


def attach(value, policy, runner, *, implementation=False):
    selected = selection(policy, runner)
    if selected is not None and not implementation and value['purpose'] in {'proposal', 'result-analysis'}:
        value['planner_context'] = selected
        value['prompt'] += GUIDANCE
    return value


def identity(config, value):
    selected = selection({'planner_context': value['planner_context']}, config)
    if value['purpose'] not in {'proposal', 'result-analysis'}:
        raise ValueError('independent_reviewer_cannot_share_planner_context')
    return {'root': str(Path(selected['root']).resolve()), 'purpose': value['purpose'],
            'model': value['model'], 'config_digest': digest(config)}


def state(root, owner):
    path = root / 'session.json'
    if not native_path(path).exists():
        if native_path(root / 'turns').exists() and any((root / 'turns').glob('*.json')):
            raise ValueError('planner_session_missing_requires_reconciliation')
        return {'schema': 'sdm-planner-session/v1', 'identity': owner, 'thread_id': None,
                'sequence': 0, 'head': None, 'pending': None}
    saved = checked(path, 'sdm-planner-session/v1')
    if saved['identity'] != owner:
        raise ValueError('planner_session_identity_changed')
    previous = None
    for number in range(1, saved['sequence'] + 1):
        entry = checked(root / 'turns' / f'{number:06d}.json', 'sdm-planner-turn/v1')
        if (entry['sequence'] != number or entry['previous'] != previous
                or entry['thread_id'] != saved['thread_id']):
            raise ValueError('planner_history_chain_changed')
        previous = digest(entry)
    if previous != saved['head']:
        raise ValueError('planner_history_head_changed')
    return saved


@contextmanager
def using(config, value, folder):
    if 'planner_context' not in value:
        yield
        return
    from model_runner import binding, completed
    owner = identity(config, value)
    root = Path(owner['root'])
    folder = Path(folder).resolve()
    expected = binding(config, value)
    call = {'folder': str(folder), 'binding': expected}
    with locked(root, '.planner.lock', 'planner_conversation_busy'):
        saved = state(root, owner)
        # A historical replay consumes its own receipt without advancing memory.
        found = [checked(p, 'sdm-planner-turn/v1') for p in sorted((root / 'turns').glob('*.json'))
                 if int(p.stem) <= saved['sequence']]
        recorded = [e for e in found if e['call']['folder'] == str(folder)]
        if recorded:
            if len(recorded) != 1 or recorded[0]['call'] != call:
                raise ValueError('planner_recorded_call_changed')
            result = completed(folder, expected)
            if digest(result) != recorded[0]['completion_digest']:
                raise ValueError('planner_recorded_completion_changed')
            yield
            return
        if saved['pending'] is not None and saved['pending'] != call:
            raise ValueError('planner_pending_call_requires_original_reconciliation')
        saved['pending'] = call
        atomic_json(root / 'session.json', seal(saved))
        plan = {'schema': 'sdm-planner-call/v1', 'identity': owner, 'call': call,
                'sequence': saved['sequence'] + 1, 'previous': saved['head'],
                'thread_id': saved['thread_id'], 'turn_ids': [e['turn_id'] for e in found]}
        path = folder / 'planner-call.json'
        if native_path(path).exists():
            if checked(path, plan['schema']) != seal(plan):
                raise ValueError('planner_call_history_changed')
        else:
            atomic_json(path, seal(plan))
        failed = False
        try:
            yield
        except BaseException:
            failed = True
            raise
        finally:
            if native_path(folder / 'backend-completed.json').exists():
                result = completed(folder, expected)
                # Unknown/failed/interrupted requests retain the pending identity.
                if result['status'] == 'completed':
                    metadata = result['metadata']
                    thread_id, turn_id = metadata.get('thread_id'), metadata.get('turn_id')
                    if (not thread_id or not turn_id or turn_id in plan['turn_ids']
                            or saved['thread_id'] not in (None, thread_id)):
                        raise ValueError('planner_completed_turn_identity_changed')
                    receipt = checked(folder / 'planner-thread.json', 'sdm-planner-thread/v1')
                    if receipt['call'] != call or receipt['thread_id'] != thread_id:
                        raise ValueError('planner_thread_receipt_changed')
                    entry = seal({'schema': 'sdm-planner-turn/v1', 'sequence': plan['sequence'],
                        'previous': plan['previous'], 'call': call, 'thread_id': thread_id,
                        'turn_id': turn_id, 'completion_digest': digest(result),
                        'purpose_validation': 'failed' if failed else 'passed',
                        'execution_authority': False})
                    target = root / 'turns' / f"{plan['sequence']:06d}.json"
                    if native_path(target).exists() and checked(target, entry['schema']) != entry:
                        raise ValueError('planner_turn_record_changed')
                    atomic_json(target, entry)
                    saved.update(thread_id=thread_id, sequence=plan['sequence'], head=digest(entry), pending=None)
                    atomic_json(root / 'session.json', seal(saved))


def open_thread(codex, config, value, folder, expected, options, resume):
    """Start once, then resume exactly the bound native history before any turn."""
    from openai_codex import Sandbox, ApprovalMode
    from model_context import sdk_effective
    common = dict(model=value['model'], cwd=config['cwd'], sandbox=Sandbox.read_only,
                  approval_mode=ApprovalMode.deny_all, **options)
    if 'planner_context' not in value:
        return codex.thread_start(ephemeral=not bool(config.get('sdk_recovery')), **common)
    plan = checked(folder / 'planner-call.json', 'sdm-planner-call/v1')
    if plan['call']['binding'] != expected or plan['identity'] != identity(config, value):
        raise ValueError('planner_transport_binding_changed')
    if plan['thread_id'] is None:
        thread = codex.thread_start(ephemeral=False, **common)
    else:
        def verified(client, *args, **kwargs):
            response = resume(client, *args, **kwargs)
            sdk_effective(config, value, response, folder, expected)
            raw = response.model_dump(mode='json', by_alias=True, exclude_unset=True)['thread']
            if (raw['id'] != plan['thread_id']
                    or [t['id'] for t in raw.get('turns', [])] != plan['turn_ids']
                    or any(t['status'] != 'completed' for t in raw.get('turns', []))):
                raise ValueError('planner_native_history_changed_before_turn')
            return response
        from unittest.mock import patch
        from openai_codex.client import CodexClient
        with patch.object(CodexClient, 'thread_resume', new=verified):
            thread = codex.thread_resume(plan['thread_id'], include_turns=True, **common)
    atomic_json(folder / 'planner-thread.json', seal({'schema': 'sdm-planner-thread/v1',
                'call': plan['call'], 'thread_id': thread.id,
                'operation': 'resume' if plan['thread_id'] else 'start'}))
    return thread


def verify_completion(config, value, folder, expected, result):
    """Original gate replays also verify the native conversation binding."""
    if result['status'] != 'completed':
        return
    owner = identity(config, value)
    plan = checked(folder / 'planner-call.json', 'sdm-planner-call/v1')
    receipt = checked(folder / 'planner-thread.json', 'sdm-planner-thread/v1')
    call = {'folder': str(Path(str(folder)[4:] if str(folder).startswith('\\\\?\\') else folder).resolve()),
            'binding': expected}
    metadata = result['metadata']
    if (plan['identity'] != owner or plan['call'] != call or receipt['call'] != call
            or receipt['thread_id'] != metadata.get('thread_id')
            or plan['thread_id'] not in (None, metadata.get('thread_id'))):
        raise ValueError('planner_completion_conversation_changed')
    saved = state(Path(owner['root']), owner)
    if plan['sequence'] <= saved['sequence']:
        entry = checked(Path(owner['root'])/'turns'/f"{plan['sequence']:06d}.json", 'sdm-planner-turn/v1')
        if (entry['call'] != call or entry['completion_digest'] != digest(result)
                or entry['turn_id'] != metadata.get('turn_id') or entry['previous'] != plan['previous']):
            raise ValueError('planner_completion_history_changed')
    elif (plan['sequence'] != saved['sequence'] + 1 or saved['pending'] != call or saved['head'] != plan['previous']):
        raise ValueError('planner_completion_not_owned_by_pending_call')
