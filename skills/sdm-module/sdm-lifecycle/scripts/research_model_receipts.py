"""Per-call recovery for new frozen worker v3 attempts; no blind retries."""
import hashlib
from pathlib import Path
import re
import time

from review_storage import atomic_json, locked, read_json
from task_pipeline import digest


def seal(value):
    return {**value, 'integrity': digest({k: v for k, v in value.items() if k != 'integrity'})}


def checked(path, schema):
    value = read_json(path)
    if value.get('schema') != schema or value.get('integrity') != seal(value)['integrity']:
        raise ValueError('model_receipt_integrity_failed')
    return value


def identity(cfg, role, prompt, folder):
    folder = Path(folder).resolve()
    if (cfg['version'] not in {3, 4, 5} or role not in {'a', 'b'}
            or folder.parent.parent != Path(cfg['output']).resolve() / 'research-decisions'
            or not re.fullmatch(r'[0-9a-f]{64}', folder.parent.name)
            or not re.fullmatch(r'[0-9]{3,}-' + role, folder.name)):
        raise ValueError('bound_model_call_folder_required')
    return {'decision_id': folder.parent.name, 'slot': folder.name,
        'worker_digest': digest(cfg), 'role': role, 'requested_model': cfg['models'][role],
        'purpose': 'result-analysis' if role == 'a' else 'result-critique',
        'prompt_digest': 'sha256:' + hashlib.sha256(prompt.encode('utf-8')).hexdigest()}


def context(cfg, job, text):
    """Keep the exact original context when coordinator snapshots change on replay."""
    path = Path(job) / 'context.json'
    binding = {'decision_id': Path(job).name, 'worker_digest': digest(cfg)}
    if path.exists():
        value = checked(path, 'sdm-research-model-context/v1')
        if value.get('binding') != binding or not isinstance(value.get('text'), str):
            raise ValueError('model_context_binding_changed')
        return value['text']
    if any(Path(job).glob('*/intent.json')):
        raise ValueError('model_context_missing_requires_reconciliation')
    atomic_json(path, seal({'schema': 'sdm-research-model-context/v1', 'binding': binding, 'text': text}))
    return text


def intent(cfg, role, prompt, folder):
    expected = identity(cfg, role, prompt, folder)
    value = checked(Path(folder) / 'intent.json', 'sdm-research-model-intent/v1')
    if value.get('identity') != expected or value.get('call_id') != digest(expected):
        raise ValueError('model_call_identity_changed')
    if (Path(folder) / 'prompt.txt').read_text(encoding='utf-8') != prompt:
        raise ValueError('model_call_prompt_changed')
    return value


def result(cfg, role, prompt, folder):
    request = intent(cfg, role, prompt, folder)
    value = checked(Path(folder) / 'result.json', 'sdm-research-model-result/v1')
    if (value.get('call_id') != request['call_id'] or value.get('identity') != request['identity']
            or not isinstance(value.get('response'), dict)
            or value.get('response_digest') != digest(value['response'])):
        raise ValueError('completed_model_result_changed')
    if cfg['version']==5:
        from research_model_runner import validate_persisted
        validate_persisted(cfg,folder,value)
    return value


def complete(cfg, role, prompt, folder, value, provenance):
    request = intent(cfg, role, prompt, folder)
    if not isinstance(value, dict):
        raise ValueError('model_json_object_required')
    path = Path(folder) / 'result.json'
    if path.exists():
        previous = result(cfg, role, prompt, folder)
        if previous['response'] != value:
            raise ValueError('immutable_model_result_changed')
        return previous['response']
    atomic_json(path, seal({'schema': 'sdm-research-model-result/v1',
        'call_id': request['call_id'], 'identity': request['identity'],
        'response': value, 'response_digest': digest(value), 'provenance': provenance,
        'completed_at': time.time()}))
    return value


def from_cli_completion(cfg, role, prompt, folder):
    """Only the backend's successful-return receipt can reconcile its raw output."""
    folder = Path(folder)
    request = intent(cfg, role, prompt, folder)
    finished = read_json(folder / 'completed.json')
    call = read_json(folder / 'call.json')
    if (finished.get('schema') != 'sdm-cli-call-completed/v3'
            or finished.get('call_id') != request['call_id']
            or type(finished.get('returncode')) is not int or finished['returncode'] != 0
            or finished.get('requested_model') != cfg['models'][role]
            or finished.get('call_ref') != str(folder / 'stdout.jsonl')
            or call.get('role') != role or call.get('requested_model') != cfg['models'][role]
            or call.get('prompt_digest') != request['identity']['prompt_digest']):
        raise ValueError('cli_completion_binding_invalid')
    output = folder / 'response.json'
    if not output.is_file() or output.stat().st_size > 256 * 1024:
        raise ValueError('completed_cli_output_missing_or_oversized')
    value = read_json(output)
    if digest(value) != finished.get('response_digest'):
        raise ValueError('completed_cli_output_changed')
    return complete(cfg, role, prompt, folder, value, 'bound-cli-success-receipt; not provider attestation')


def invoke(cfg, role, prompt, folder, caller):
    folder = Path(folder)
    expected = identity(cfg, role, prompt, folder)
    with locked(folder, '.model-call.lock', 'model_call_already_running'):
        if (folder / 'intent.json').exists():
            intent(cfg, role, prompt, folder)
            if (folder / 'result.json').exists():
                return result(cfg, role, prompt, folder)['response']
            if cfg['version']==5:
                # The bound ModelRunner owns its transport intent/completion.
                # Its bridge refuses missing/unknown claims before any launch.
                return caller(cfg,role,prompt,folder)
            if (folder / 'completed.json').exists():
                return from_cli_completion(cfg, role, prompt, folder)
            raise ValueError('model_call_unknown_requires_reconciliation')
        if any((folder / name).exists() for name in ('result.json', 'call.json', 'completed.json', 'response.json')):
            raise ValueError('model_call_without_bound_intent')
        (folder / 'prompt.txt').write_text(prompt, encoding='utf-8')
        atomic_json(folder / 'intent.json', seal({'schema': 'sdm-research-model-intent/v1',
            'identity': expected, 'call_id': digest(expected), 'created_at': time.time()}))
        value = caller(cfg, role, prompt, folder)
        # Production CLI writes its completion before returning. Injected or
        # future typed callers may return directly under the same intent lock.
        if (folder / 'completed.json').exists():
            recovered = from_cli_completion(cfg, role, prompt, folder)
            if recovered != value:
                raise ValueError('returned_model_result_changed')
            return recovered
        return complete(cfg, role, prompt, folder, value, 'bound caller returned; not provider attestation')


def collect(cfg, job):
    receipts = {}
    for path in Path(job).glob('*/result.json'):
        folder = path.parent
        prompt = (folder / 'prompt.txt').read_text(encoding='utf-8')
        value = result(cfg, folder.name[-1], prompt, folder)
        receipts[str(path.resolve())] = digest(value)
    return receipts


def validate_committed(cfg, job, receipts):
    if not isinstance(receipts, dict) or len(receipts) < 2:
        raise ValueError('approved_model_receipts_required')
    roles = set()
    for name, expected in receipts.items():
        path = Path(name).resolve()
        if path.parent.parent != Path(job).resolve() or path.name != 'result.json':
            raise ValueError('bound_approved_model_receipt_required')
        folder = path.parent
        value = result(cfg, folder.name[-1], (folder / 'prompt.txt').read_text(encoding='utf-8'), folder)
        if digest(value) != expected:
            raise ValueError('approved_model_receipt_changed')
        roles.add(value['identity']['role'])
    if roles != {'a', 'b'}:
        raise ValueError('two_actual_model_receipts_required')
