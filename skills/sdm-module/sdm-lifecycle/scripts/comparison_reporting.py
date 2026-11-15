"""Export immutable paired records from existing round evidence; never runs models."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from comparison_preflight import CONDITIONS
from review_storage import atomic_json, locked, read_json
from round_reporting import digest, safe_id

DEFAULT_PATHS = {k: 'provenance.fixed_conditions.' + k for k in CONDITIONS}
DEFAULT_PATHS.update(dataset_version='provenance.data.version', split_digest='provenance.data.split_digest',
                     metric_definition='metric_definitions', config_digest='provenance.fixed_conditions.config_digest')


def at(value, selector):
    if not isinstance(selector, str) or not selector:
        raise ValueError('declared_record_field_path_required')
    for part in selector.split('.'):
        if not isinstance(value, dict): return None
        value = value.get(part)
    return value


def load_ref(ref):
    if not isinstance(ref, dict) or not isinstance(ref.get('path'), str):
        raise ValueError('bound_file_reference_required')
    path = Path(ref['path'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('absolute_regular_evidence_file_required')
    raw = path.read_bytes()
    actual = 'sha256:' + hashlib.sha256(raw).hexdigest()
    if actual != ref.get('digest'): raise ValueError('evidence_bytes_changed:' + path.name)
    def invalid(value): raise ValueError('nonfinite_evidence_json:' + value)
    value = json.loads(raw, parse_constant=invalid)
    if not isinstance(value, dict): raise ValueError('evidence_object_required')
    return value, {'path': str(path.resolve()), 'digest': actual}


def finite(value):
    if type(value) not in (int, float): return False
    try: return math.isfinite(value)
    except OverflowError: return False


def build(spec, verify_receipt=None):
    if not isinstance(spec, dict) or spec.get('schema') != 'sdm-comparison-export/v1':
        raise ValueError('comparison_export_schema_required')
    plan, plan_ref = load_ref(spec.get('plan_ref'))
    if plan.get('schema') != 'sdm-comparison-group-planning/v1': raise ValueError('comparison_group_plan_required')
    group_id = safe_id(plan.get('group_id'))
    declared = plan.get('arms')
    if not isinstance(declared, list) or len(declared) != 2 or any(not isinstance(a, dict) for a in declared):
        raise ValueError('exactly_two_planned_arms_required')
    roles = {a.get('role', a.get('id')): a for a in declared}
    if set(roles) != {'control', 'treatment'}: raise ValueError('explicit_control_and_treatment_roles_required')
    declared = [roles['control'], roles['treatment']]
    ids = [safe_id(a.get('id')) for a in declared]
    if len(set(ids)) != 2: raise ValueError('distinct_arm_ids_required')
    references = spec.get('record_refs')
    if not isinstance(references, dict) or set(references) != set(ids):
        raise ValueError('record_refs_must_match_planned_arms')
    metrics = spec.get('metrics')
    if not isinstance(metrics, list) or not metrics: raise ValueError('project_metric_definitions_required')
    fields = []
    for metric in metrics:
        if (not isinstance(metric, dict) or not isinstance(metric.get('field'), str)
            or metric.get('direction') not in ('maximize', 'minimize') or not isinstance(metric.get('unit'), str)):
            raise ValueError('metric_field_direction_and_unit_required')
        fields.append(metric['field'])
    if len(set(fields)) != len(fields): raise ValueError('distinct_metric_fields_required')
    paths = {**DEFAULT_PATHS, **spec.get('actual_condition_paths', {})}
    if verify_receipt is None:
        from task_pipeline import verified_s13
        verify_receipt = verified_s13
    findings, missing, arms = [], [], []
    seen_records = set()
    for index, expected in enumerate(declared):
        arm_id = expected['id']; ref = references[arm_id]
        arm = {'id': arm_id, 'role': 'control' if index == 0 else 'treatment', 'record_ref': None,
               'status': 'not-observed', 'metrics': {}, 'receipts': {}}
        arms.append(arm)
        if ref is None:
            missing.append(arm_id + ':record_missing'); continue
        record, reference = load_ref(ref)
        if record.get('schema') != 'sdm-round-record/v1': raise ValueError('round_record_schema_required')
        key = (record.get('run_id'), record.get('round'))
        if key in seen_records: findings.append(arm_id + ':duplicate_run_round_identity')
        seen_records.add(key)
        arm.update(record_ref=reference, status=record.get('status', 'not-observed'),
                   run_id=record.get('run_id'), round=record.get('round'),
                   source_commit=at(record, 'provenance.source_commit'),
                   config_digest=at(record, paths['config_digest']), parameters=record.get('parameters'),
                   conditions={k: at(record, paths[k]) for k in CONDITIONS},
                   stages=record.get('stages', {}), retention=record.get('retention'),
                   resource_usage=record.get('resource_usage'), limitations=record.get('limitations', []))
        identity = expected.get('record_identity', {})
        for field in ('run_id', 'round'):
            wanted = identity.get(field)
            if wanted is None: missing.append(arm_id + ':planned_' + field + '_missing')
            elif record.get(field) != wanted: findings.append(arm_id + ':' + field + '_mismatch')
        for field in ('source_commit', 'config_digest'):
            wanted = expected.get(field)
            if wanted is None or arm[field] is None: missing.append(arm_id + ':' + field + '_binding_missing')
            elif arm[field] != wanted: findings.append(arm_id + ':' + field + '_mismatch')
        arm['source_snapshot_ref'] = expected.get('source_snapshot_ref')
        if arm['source_snapshot_ref'] is not None:
            frozen, snapshot_ref = load_ref(arm['source_snapshot_ref'])
            arm['source_snapshot_ref'] = snapshot_ref
            if frozen.get('schema') != 'sdm-source-snapshot/v1': raise ValueError('source_snapshot_schema_required')
            if frozen.get('source_commit') != arm['source_commit'] or frozen.get('configuration', {}).get('digest') != arm['config_digest']:
                findings.append(arm_id + ':source_snapshot_mismatch')
        if not isinstance(expected.get('parameters'), dict) or not expected['parameters']:
            missing.append(arm_id + ':planned_parameters_missing')
        elif arm['parameters'] != expected['parameters']:
            findings.append(arm_id + ':actual_parameters_mismatch')
        for condition in CONDITIONS:
            wanted = expected.get('conditions', {}).get(condition)
            actual = arm['conditions'][condition]
            if wanted is None or actual is None: missing.append(arm_id + ':' + condition + '_missing')
            elif actual != wanted: findings.append(arm_id + ':' + condition + '_mismatch')
        for metric in metrics:
            field = metric['field']; value = record.get('metrics', {}).get(field, {}).get('value')
            arm['metrics'][field] = value if finite(value) else None
            if value is None: missing.append(arm_id + ':metric_missing:' + field)
            elif not finite(value): findings.append(arm_id + ':invalid_metric:' + field)
            definition = record.get('metric_definitions', {}).get(field)
            if not isinstance(definition, dict): missing.append(arm_id + ':metric_definition_missing:' + field)
            elif any(definition.get(k) != metric[k] for k in ('direction', 'unit')):
                findings.append(arm_id + ':metric_definition_mismatch:' + field)
        for stage in ('training', 'evaluation'):
            row = arm['stages'].get(stage, {})
            ref = row.get('receipt')
            try:
                verified = row.get('status') == 'completed' and verify_receipt(ref)
            except (ValueError, OSError, KeyError, TypeError): verified = False
            arm['receipts'][stage] = 'verified-successful-S13' if verified else 'not-verified'
        if arm['status'] == 'completed' and any(arm['stages'].get(s, {}).get('status') != 'completed' for s in ('training', 'evaluation')):
            findings.append(arm_id + ':completed_record_conflicts_with_stage_status')
    if all(a['record_ref'] for a in arms):
        if any(arms[0]['conditions'][k] is not None and arms[1]['conditions'][k] is not None
               and arms[0]['conditions'][k] != arms[1]['conditions'][k] for k in CONDITIONS):
            findings.append('actual_comparison_conditions_differ')
        if set(declared[0].get('parameters', {})) != set(declared[1].get('parameters', {})) or {
            k for k, v in declared[0].get('parameters', {}).items() if v != declared[1].get('parameters', {}).get(k)
        } != {plan.get('changed_factor')}:
            findings.append('declared_contrast_is_not_exactly_one_factor')
    terminal = {'completed', 'failed', 'incomplete'}
    both_completed = all(a['status'] == 'completed' for a in arms)
    trusted = all(all(v == 'verified-successful-S13' for v in a['receipts'].values()) and len(a['receipts']) == 2 for a in arms)
    if findings: status = 'invalid-comparison'
    elif any(a['status'] not in terminal for a in arms): status = 'waiting'
    elif not both_completed or missing: status = 'incomplete'
    elif not trusted: status = 'paired-observations'
    else: status = 'paired-results-ready'
    contrasts = []
    for metric in metrics:
        left, right = [a['metrics'].get(metric['field']) for a in arms]
        can_compare = both_completed and not findings and not missing and finite(left) and finite(right)
        delta = right - left if can_compare else None
        if delta is not None and not finite(delta):
            delta = None; status = 'invalid-comparison'; findings.append('metric_delta_out_of_range:' + metric['field'])
        contrasts.append({**metric, 'control': left, 'treatment': right, 'treatment_minus_control': delta,
                          'direction_adjusted_gain': delta * (1 if metric['direction'] == 'maximize' else -1) if delta is not None else None})
    limitations = list(missing)
    if not trusted: limitations.append('Some successful S13 references are missing or not revalidated; descriptive observations are not authenticated completion.')
    limitations += ['Round exports are derived records; verifying S13 references does not authenticate every exported field.',
                    'A single pair does not establish a causal or statistically reliable improvement.',
                    'Resource cost/overlap stays unmeasured unless present in original evidence; no missing values are inferred.']
    return {'schema': 'sdm-comparison-record/v1', 'group_id': group_id, 'project': plan.get('project'),
            'question': plan.get('question'), 'changed_factor': plan.get('changed_factor'),
            'schedule_proposed': plan.get('schedule'), 'plan_ref': plan_ref, 'input_digest': digest(spec),
            'status': status, 'arms': arms, 'contrasts': contrasts, 'invalid_findings': findings,
            'limitations': limitations, 'record_origin': 'exported-from-existing-bound-evidence',
            'qualification': 'not-assessed', 'execution_authorized': False}


def markdown(record):
    def cell(value):
        if value is None: return '未取得'
        return str(value).replace('|', '\\|').replace('\n', ' ')
    lines = ['# 实验对比：' + record['group_id'], '', '状态：**' + record['status'] + '**。', '',
             str(record['question']), '', '| 组别 | 运行/轮次 | 状态 | 源码提交 | 配置摘要 |', '|---|---|---|---|---|']
    for arm in record['arms']:
        identity = str(arm.get('run_id')) + '/' + str(arm.get('round'))
        lines.append('| ' + ' | '.join(cell(v) for v in (arm['id'], identity, arm['status'], arm.get('source_commit'), arm.get('config_digest'))) + ' |')
    lines += ['', '| 指标 | 对照 | 实验 | 实验−对照 | 按优化方向计算的增益 | 单位 |', '|---|---|---|---|---|---|']
    for metric in record['contrasts']:
        lines.append('| ' + ' | '.join(cell(metric.get(k)) for k in ('field', 'control', 'treatment', 'treatment_minus_control', 'direction_adjusted_gain', 'unit')) + ' |')
    lines += ['', '## 不可比项和证据缺口', '']
    lines += ['- ' + item for item in record['invalid_findings'] + record['limitations']]
    lines += ['', '单轮证据、原始回执和版本绑定见 [comparison.json](comparison.json)。', '',
              '本报告整理既有证据，不启动训练，不将单次差值解释为稳定提升。', '']
    return '\n'.join(lines)


def export(spec, output):
    record = build(spec); root = Path(output)
    if '.git' in root.resolve().parts or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('plain_project_record_output_required')
    with locked(root):
        binding = root / 'comparison-input.json'
        if binding.exists() and read_json(binding) != spec:
            raise ValueError('changed_comparison_requires_fresh_revision_directory')
        target = root / 'comparison.json'
        report = root / 'report.md'
        receipt = root / 'export-binding.json'
        if receipt.exists() and (not target.is_file() or not report.is_file()):
            raise ValueError('historical_comparison_output_missing')
        if target.exists():
            saved = read_json(target)
            # Live S13 availability can change on re-entry: preserve the old report.
            if saved.get('input_digest') != record['input_digest']:
                raise ValueError('comparison_record_binding_changed')
            if receipt.exists():
                bound = read_json(receipt)
                for path in (target, report):
                    if not path.is_file() or 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest() != bound.get(path.name):
                        raise ValueError('historical_comparison_output_changed')
                return saved
            if saved != record: raise ValueError('partial_export_requires_evidence_reconciliation')
        else:
            if not binding.exists(): atomic_json(binding, spec)
            atomic_json(target, record)
        if report.exists():
            if report.read_text(encoding='utf-8') != markdown(record): raise ValueError('historical_comparison_report_changed')
        else:
            with report.open('x', encoding='utf-8', newline='\n') as stream: stream.write(markdown(record))
        atomic_json(receipt, {p.name: 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest() for p in (target, report)})
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        record = export(read_json(args.spec), args.output)
        print(json.dumps({'status': record['status'], 'group_id': record['group_id'], 'execution_authorized': False}))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as error:
        print(json.dumps({'status': 'blocked', 'problem': str(error) if isinstance(error, ValueError) else type(error).__name__}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
