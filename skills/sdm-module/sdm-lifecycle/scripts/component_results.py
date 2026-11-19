"""Verify component result evidence before a result-driven successor is allowed.

No model loading, platform requests or training. The original manager supplies
the verified task receipt; the caller supplies raw log lines from that task's
official pod logs. Truncated/conflicting artifacts are never a valid round.
"""
import base64
import gzip
import hashlib
import io
import json
import math
from pathlib import Path

from external_operations import sha

FILES = {'split.json', 'predictions.json', 'result.json', 'checkpoint.pt'}
MAX_ARTIFACT = 32 * 1024 * 1024


def _require(value, reason):
    if not value:
        raise ValueError(reason)


def _json(raw):
    def invalid(value):
        raise ValueError('nonfinite_result_json')
    return json.loads(raw, parse_constant=invalid)


def marker_lines(observation, namespace, pod, container='main'):
    """Adapt observed official logs-pod stream shape, bound to the exact pod."""
    _require(observation.get('status') == 'ok', 'successful_official_log_read_required')
    data = observation.get('data', {})
    _require(data.get('resultType') == 'streams' and isinstance(data.get('result'), list), 'official_log_stream_shape_required')
    rows = []
    for item in data['result']:
        labels = item.get('stream', {})
        _require(labels.get('namespace') == namespace and labels.get('pod') == pod, 'official_log_pod_identity_mismatch')
        if labels.get('container') != container:
            continue
        _require(isinstance(item.get('values'), list), 'official_log_rows_required')
        for pair in item['values']:
            _require(isinstance(pair, list) and len(pair) == 2 and isinstance(pair[1], str), 'official_log_row_shape_required')
            if pair[1].startswith('SDM_JSON '):
                rows.append((int(pair[0]), pair[1]))
    return [line for _, line in sorted(rows)]


def unpack(lines,required_files=None):
    allowed_files=FILES if required_files is None else required_files
    manifests, chunks, events = {}, {}, {}
    for line in lines:
        if not isinstance(line, str) or not line.startswith('SDM_JSON '):
            continue
        _require(len(line) <= 256 * 1024, 'bounded_result_log_line_required')
        row = _json(line[len('SDM_JSON '):])
        kind = row.get('kind')
        if kind in ('preflight', 'completed'):
            _require(kind not in events or events[kind] == row, 'conflicting_result_event')
            events[kind] = row
        elif kind == 'artifact':
            name = row.get('name')
            _require(name in allowed_files and row.get('encoding') == 'base64+gzip', 'declared_result_artifact_required')
            _require(type(row.get('bytes')) is int and 0 < row['bytes'] <= MAX_ARTIFACT
                     and type(row.get('chunks')) is int and 0 < row['chunks'] <= 40000, 'bounded_result_artifact_required')
            _require(name not in manifests or manifests[name] == row, 'conflicting_artifact_manifest')
            manifests[name] = row
        elif kind == 'artifact-chunk':
            name, index, value = row.get('name'), row.get('index'), row.get('value')
            _require(name in allowed_files and type(index) is int and 0 <= index < 40000
                     and isinstance(value, str) and len(value) <= 4096, 'bounded_artifact_chunk_required')
            key = (name, index)
            _require(key not in chunks or chunks[key] == value, 'conflicting_artifact_chunk')
            chunks[key] = value
    _require(set(manifests) == allowed_files and set(events) == {'preflight', 'completed'}, 'complete_component_result_required')
    artifacts = {}
    for name, manifest in manifests.items():
        indices = {index for n, index in chunks if n == name}
        _require(indices == set(range(manifest['chunks'])), 'artifact_chunks_incomplete')
        encoded = ''.join(chunks[(name, i)] for i in range(manifest['chunks']))
        packed = base64.b64decode(encoded, validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
            raw = stream.read(manifest['bytes'] + 1)
        _require(len(raw) == manifest['bytes'] and hashlib.sha256(raw).hexdigest() == manifest.get('sha256'), 'artifact_digest_mismatch')
        artifacts[name] = raw
    return artifacts, events


def verify_submission_identity(task,receipt,identity):
    """One original submission/readback check for all result adapters."""
    identity_proof=None
    if identity is None:
        _require(sha(task.get('data')) == receipt.get('data_digest'), 'submitted_task_payload_changed')
    else:
        _require(set(identity)=={'version','manager_plan'} and identity['version']==1,
                 'explicit_original_manager_result_identity_required')
        bound=identity['manager_plan'];path=Path(bound['path'])
        _require(path.is_absolute() and hashlib.sha256(path.read_bytes()).hexdigest()==bound['sha256'],
                 'original_result_manager_plan_changed')
        plan=_json(path.read_bytes());request=plan.get('request',{})
        _require(plan.get('schema')=='lpai-manager-plan/v1' and request.get('action')=='task.submit'
                 and request.get('uid')==task.get('project_uid',task.get('resource_uid'))
                 and plan.get('namespace')==task.get('namespace') and plan.get('owner')==task.get('owner')
                 and request.get('cluster')==task.get('cluster'),'original_result_submission_scope_changed')
        if plan.get('route')=='project-command':
            _require(task.get('request_key')==request.get('request_key')
                     and task.get('payload_digest')==sha(request['data'])
                     and receipt.get('data_digest')==sha(request['data']),
                     'submitted_project_task_payload_changed')
            return {'policy':'original-manager-project-config/v1','manager_plan':bound,
                    'terminal_payload_digest':task['payload_digest'],'native_configuration_matches':True}
        # Reuse the original Manager's exact native-data rules: commands,
        # image, roles, resources and mounted assets remain bound. Provider
        # metadata/default filling need not preserve the whole JSON byte hash.
        from lpai_manager import native_config_matches,subset_matches,task_name_matches
        _require(task_name_matches(request['name'],task.get('name'),task['uid'])
                 and subset_matches(request['component_settings'],task)
                 and native_config_matches(request['data'],task.get('data')),
                 'submitted_task_native_configuration_changed')
        identity_proof={'policy':'original-manager-native-config/v1','manager_plan':bound,
            'initial_readback_data_digest':receipt.get('data_digest'),
            'terminal_data_digest':sha(task['data']),
            'raw_data_digest_equal':sha(task['data'])==receipt.get('data_digest'),
            'native_configuration_matches':True}
    return identity_proof


def verify(task, manager_receipt, lines, expected):
    """Return verified bytes plus acceptance facts; saving is caller-owned."""
    _require(manager_receipt.get('status') == 'verified', 'original_manager_verified_submission_required')
    receipt = manager_receipt.get('resource', {})
    for field in ('uid', 'namespace', 'owner', 'cluster'):
        _require(task.get(field) == expected.get(field) == receipt.get(field), 'component_result_task_identity_mismatch')
    _require(task.get('project_uid') == expected.get('component_uid'), 'component_result_parent_mismatch')
    _require(task.get('status') == 'succeeded', 'successful_component_terminal_required')
    identity_proof=verify_submission_identity(task,receipt,expected.get('submission_identity'))
    artifacts, events = unpack(lines)
    result, split, predictions = [_json(artifacts[name]) for name in ('result.json', 'split.json', 'predictions.json')]
    _require(result.get('schema') == 'sdm-component-result/v1', 'component_result_schema_required')
    for field in ('code_sha256', 'source_sha256'):
        _require(result.get(field) == expected.get(field) == events['preflight'].get(field), 'result_code_or_source_mismatch')
    _require(result.get('protocol') == split.get('protocol') == expected.get('protocol'), 'result_protocol_mismatch')
    _require(result.get('test_used') is False and split.get('test_used') is False, 'withheld_test_policy_required')
    _require(hashlib.sha256(artifacts['split.json']).hexdigest() == result.get('split_sha256')
             == events['preflight'].get('split_sha256'), 'result_split_digest_mismatch')
    if expected.get('split_sha256'):
        _require(expected['split_sha256'] == result['split_sha256'], 'successor_evaluation_split_changed')
    _require(hashlib.sha256(artifacts['result.json']).hexdigest() == events['completed'].get('result_sha256'), 'completed_result_digest_mismatch')
    _require(result.get('config') == expected.get('config') == events['preflight'].get('config'), 'result_actual_parameters_mismatch')
    history, config = result.get('history', []), result['config']
    _require([r.get('epoch') for r in history] == list(range(1, config['epochs']+1)), 'complete_training_history_required')
    selected = max(history, key=lambda r: (r['validation']['macro_f1'], -r['validation']['loss']))
    _require(result.get('selected_epoch') == selected['epoch'], 'checkpoint_selection_protocol_mismatch')
    _require(result.get('validation') == selected['validation'] == events['completed'].get('validation'), 'selected_checkpoint_metrics_mismatch')
    train_ids, val_ids = split.get('train_ids', []), split.get('validation_ids', [])
    _require(len(set(train_ids)) == len(train_ids) and len(set(val_ids)) == len(val_ids)
             and not set(train_ids) & set(val_ids) and val_ids == predictions.get('row_ids'), 'prediction_sample_identity_mismatch')
    truth, pred, prob = [predictions.get(k, []) for k in ('y_true', 'y_pred', 'probability_positive')]
    n = len(val_ids)
    _require(n > 0 and len(truth) == len(pred) == len(prob) == n, 'complete_validation_predictions_required')
    _require(set(truth) == {0, 1} and set(pred) <= {0, 1}, 'binary_result_labels_required')
    _require(all(type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 1 for p in prob), 'finite_result_probabilities_required')
    _require(all(abs(p-.5) < 1e-7 or int(p > .5) == v for p, v in zip(prob, pred)), 'prediction_probability_mismatch')
    cm = [[sum(a == i and b == j for a, b in zip(truth, pred)) for j in (0, 1)] for i in (0, 1)]
    f1 = sum(2*cm[i][i]/max(1,2*cm[i][i]+cm[i][1-i]+cm[1-i][i]) for i in (0, 1))/2
    accuracy = sum(a == b for a, b in zip(truth, pred))/n
    reported = result['validation']
    _require(cm == reported['confusion_matrix'] and abs(f1-reported['macro_f1']) < 1e-10
             and abs(accuracy-reported['accuracy']) < 1e-10, 'validation_metric_recomputation_failed')
    if 'engineering_preflight' in result or expected.get('engineering_preflight_policy'):
        verify_engineering_preflight(result,expected.get('engineering_preflight_policy'))
    return {'valid_training_round': True, 'uid': task['uid'], 'protocol': result['protocol'],
            'split_sha256': result['split_sha256'], 'primary_metric': f1, 'accuracy': accuracy,
            'epochs': config['epochs'], 'selected_epoch': result['selected_epoch'],
            'checkpoint_validation': 'byte identity only; no local model execution',
            **({'submission_identity':identity_proof} if identity_proof is not None else {}),
            'artifact_sha256': {name: hashlib.sha256(raw).hexdigest() for name, raw in artifacts.items()}}, artifacts


def verify_engineering_preflight(result, policy=None):
    value=result.get('engineering_preflight')
    _require(isinstance(value,dict) and value.get('schema')=='sdm-remote-engineering-preflight/v1',
             'actual_remote_engineering_preflight_required')
    actual=value.get('policy',{})
    _require(value.get('checks')=={'tensor':True,'finite':True,'tiny_fit':True,'resume':True}
             and value.get('initial_state_restored') is True, 'remote_engineering_preflight_did_not_pass')
    _require(policy is None or actual==policy,'remote_engineering_preflight_policy_changed')
    _require(type(value.get('tiny_steps')) is int and type(actual.get('max_steps')) is int
             and type(value.get('total_update_steps')) is int
             and value['total_update_steps']==value['tiny_steps']+2<=actual['max_steps'],
             'bounded_remote_tiny_fit_and_resume_required')
    a,b=value.get('initial_tiny_loss'),value.get('final_tiny_loss')
    _require(type(a) in (int,float) and type(b) in (int,float) and math.isfinite(a) and math.isfinite(b)
             and b<a,'actual_tiny_fit_loss_decrease_required')
    return value
