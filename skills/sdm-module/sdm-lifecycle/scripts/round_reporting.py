"""Export public round records from real observations; metrics remain project-owned."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read(path, default=None):
    return json.loads(Path(path).read_bytes()) if Path(path).is_file() else default


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', value):
        raise ValueError('safe_report_run_id_required')
    return value


def retained_copy(value):
    """Read a verified Windows archive through the Linux coordinator's view."""
    if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', value):
        return Path('/mnt') / value[0].lower() / value[3:].replace('\\', '/')
    return Path(value)


def save(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == raw:
        return
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(raw)
    tmp.replace(path)


def save_json(path, value):
    save(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())


def value_at(observation, field):
    parts = field.split('.')
    value = observation.get(parts[0], {}).get('actual', {})
    for part in parts[1:]:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value if value is None or isinstance(value, (int, float, bool, str)) else None


def build(config, root, decision=None, error=None):
    root = Path(root)
    plan = read(root / 'round-plan.json', {})
    obs = read(root / 'round-observation.json', {})
    decision = decision if decision is not None else read(root / 'analysis-and-decision.json')
    observations = {k: obs.get(k, {}) for k in ('training', 'evaluation')}
    # If interrupted before round-observation creation, retain actual task outcomes.
    if not obs:
        summary = read(root / 'attempt-01/summary.json', {})
        for kind, node in (('training', '15'), ('evaluation', '16')):
            row = summary.get('nodes', {}).get(node, {})
            observations[kind] = row.get('observation', row)
    actual = observations['training'].get('actual', {})
    fields = config.get('project_metrics', [])
    mapping = config.get('report_metric_mapping', {})
    metrics = {field: {'value': value_at(observations, mapping.get(field, field)),
                      'source_field': mapping.get(field, field)} for field in fields}
    findings = []
    for field, item in metrics.items():
        if item['value'] is None:
            findings.append('未取得指标：' + field + '；不补造数值。')
    stages = {kind: {'status': item.get('status', 'not-observed'),
                    'completed_at': item.get('actual', {}).get('completed_at'),
                    'duration_seconds': item.get('actual', {}).get('duration_seconds'),
                    'exit_code': item.get('actual', {}).get('exit_code'),
                    'receipt': item.get('receipt') or item.get('signed_result')}
              for kind, item in observations.items()}
    retention = read(root / 'artifacts/retention.json', {'status': 'not-verified', 'files': []})
    if retention['status'] == 'verified' and retention.get('observation_digest') != digest(obs):
        retention = {'status': 'partial', 'problem': 'retention_observation_digest_mismatch', 'files': []}
    if retention['status'] not in ('verified', 'no-artifacts-reported'):
        findings.append('产物保存核验未完成：' + retention.get('problem', retention['status']))
    configurations = {}
    for item in retention.get('files', []):
        if item.get('kind') == 'configuration' and item.get('status') == 'verified-local-copy':
            path = retained_copy(item['retained_path'])
            if path.is_file() and path.stat().st_size <= 256 * 1024:
                raw = path.read_bytes()
                if 'sha256:' + hashlib.sha256(raw).hexdigest() == item['digest']:
                    configurations[item['digest']] = raw.decode('utf-8')
    artifacts = []
    for kind, item in observations.items():
        report = item.get('actual', {})
        if report.get('log_digest'):
            artifacts.append({'kind': kind + '-log', 'source': report.get('execution_output', '') + '/console.log',
                              'digest': report['log_digest']})
        if kind == 'training':
            for path, checksum in zip(report.get('checkpoint_paths', []), report.get('checkpoint_digests', [])):
                artifacts.append({'kind': 'checkpoint', 'source': path, 'digest': checksum})
    outcome = 'completed' if all(x['status'] == 'completed' for x in stages.values()) else 'incomplete'
    if any(x['status'] == 'failed' for x in stages.values()) or error:
        outcome = 'failed'
    if error:
        findings.append('执行或分析问题：' + error)
    if decision is None:
        findings.append('下一步分析尚未完成。')
    return {'schema': 'sdm-round-record/v1', 'run_id': safe_id(config['run_id']),
            'round': plan.get('round', obs.get('round', int(root.name.split('-')[-1]))),
            'status': outcome, 'objective': config.get('analysis_objective', config.get('objective', 'not-declared')),
            'hypothesis': plan.get('hypothesis'), 'parameters': plan.get('parameters', obs.get('parameters', {})),
            'changed_variable': plan.get('changed_variable', obs.get('changed_variable')),
            'provenance': {'source_commit': actual.get('source_commit'), 'source_url': actual.get('source_url'),
                           'framework': config.get('report_framework', {}),
                           'project_adapter': config.get('project_driver'),
                           'loop_adapter': config.get('loop_adapter'),
                           'data': config.get('report_data', {}), 'fixed_conditions': obs.get('fixed'),
                           'runtime': actual.get('runtime_environment_manifest'),
                           'resolved_configuration_contents': configurations,
                           'execution_commands': {k: v.get('actual', {}).get('execution_argv') for k, v in observations.items()}},
            'stages': stages, 'metrics': metrics, 'metric_definitions': config.get('metric_definitions', {}),
            'comparison': obs.get('comparison'), 'comparison_conditions': config.get('comparison_conditions', []),
            'execution_findings': obs.get('execution_findings', []),
            'artifacts': artifacts, 'retention': retention,
            'analysis': decision, 'analysis_digest': digest(decision) if decision else None,
            'observation_digest': digest(obs) if obs else None,
            'limitations': findings, 'record_origin': 'exported-from-existing-evidence',
            'execution_time_note': '完成时间来自真实报告；没有开始时间时不推算或补造。'}


def markdown(record):
    def cell(value):
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return text.replace('|', '\\|').replace('\n', ' ') if value is not None else '未取得'
    lines = [f"# 第 {record['round']} 轮结果", '',
             f"运行编号：`{record['run_id']}`；执行状态：**{record['status']}**。", '',
             '## 目的与变化', '', str(record['objective']), '',
             '本轮假设：' + str(record['hypothesis']), '',
             '变化变量：' + str(record['changed_variable'] or '首轮初始配置'), '',
             '参数：`' + cell(record['parameters']) + '`', '', '## 版本与执行', '',
             '代码提交：`' + str(record['provenance']['source_commit']) + '`', '',
             '数据：`' + cell(record['provenance']['data']) + '`', '',
             '| 阶段 | 状态 | 完成时间（UTC） | 秒 | 回执请求 |', '|---|---|---|---|---|']
    for kind, stage in record['stages'].items():
        lines.append('| ' + ' | '.join(cell(x) for x in (kind, stage['status'], stage['completed_at'],
                                                       stage['duration_seconds'], (stage['receipt'] or {}).get('request_id'))) + ' |')
    lines += ['', '## 实际指标', '', '| 项目指标 | 值 | 实际字段 |', '|---|---|---|']
    for field, item in record['metrics'].items():
        lines.append('| ' + ' | '.join(cell(x) for x in (field, item['value'], item['source_field'])) + ' |')
    decision = record['analysis'] or {}
    lines += ['', '## 分析与下一步', '', '上轮预测检查：' + str(decision.get('previous_experiment_assessment', '尚未提供')), '',
              '决定：' + str(decision.get('action', 'pending')), '', '理由：' + str(decision.get('reason', '尚未提供')), '']
    for hypothesis in decision.get('hypotheses', []):
        lines += ['- 假设：' + hypothesis['statement'] + ' 限制：' + hypothesis.get('limitations', ''), '']
    lines += ['预期观察：' + cell(decision.get('expected_observations')), '', '## 产物与证据', '',
              '保存状态：**' + record['retention']['status'] + '**；不自动删除产物。', '',
              '| 类型 | 原始位置 | SHA256 |', '|---|---|---|']
    for item in record['artifacts']:
        lines.append('| ' + ' | '.join(cell(item[k]) for k in ('kind', 'source', 'digest')) + ' |')
    lines += ['', '文件大小、保留副本位置、运行环境及完整决策见 [result.json](result.json)。', '',
              '## 限制与问题', '', '- 本记录保存实际执行及观测；业务效果是否达标依据项目目标另行判断。', '']
    lines.extend('- ' + item + '\n' for item in record['limitations'])
    lines += ['这是对实际证据的整理，未创建新的训练或改写原始回执。', '']
    return '\n'.join(lines)


def export_round(config, root, decision=None, error=None):
    repo = config.get('result_records', {}).get('repository')
    if not repo:
        return {'status': 'not-configured'}
    relative = config['result_records'].get('directory', 'docs/sdm/runs')
    target = Path(repo).resolve() / relative / safe_id(config['run_id'])
    if not target.resolve().is_relative_to(Path(repo).resolve()) or '.git' in Path(relative).parts:
        raise ValueError('report_path_must_stay_inside_repository')
    record = build(config, root, decision, error)
    destination = target / f"round-{record['round']:02d}"
    save_json(destination / 'result.json', record)
    save(destination / 'report.md', markdown(record).encode())
    records = [read(p) for p in sorted(target.glob('round-*/result.json'))]
    lines = [f"# {config['run_id']} 执行记录", '',
             '各轮有独立记录；不要求为参数实验创建独立 Git 分支。', '',
             '| 轮次 | 运行状态 | 变化变量 | 下一步 | 报告 |', '|---|---|---|---|---|']
    for item in records:
        i = item['round']
        lines.append(f"| {i} | {item['status']} | {item['changed_variable'] or '初始配置'} | {(item['analysis'] or {}).get('action', 'pending')} | [第{i}轮](round-{i:02d}/report.md) |")
    fields = config.get('project_metrics', [])
    if fields:
        lines += ['', '## 各轮实际结果', '', '| 轮次 | ' + ' | '.join(fields) + ' |',
                  '|---|' + '---|' * len(fields)]
        for item in records:
            values = [item['metrics'].get(field, {}).get('value') for field in fields]
            lines.append('| ' + str(item['round']) + ' | ' + ' | '.join('未取得' if v is None else str(v) for v in values) + ' |')
    lines += ['', '原始实验和本次整理提交的时间分别记录；Git 提交时间不是训练发生时间。', '']
    save(target / 'index.md', '\n'.join(lines).encode())
    files = [destination / 'result.json', destination / 'report.md', target / 'index.md']
    if config['result_records'].get('navigation'):
        from project_navigation import build as build_navigation
        path, content = build_navigation(repo, relative, config['result_records'])
        save(path, content)
        files.append(path)
    return {'status': 'written', 'files': [str(p.relative_to(Path(repo).resolve())).replace('\\', '/') for p in files]}


def deliver(config, root, decision=None, error=None):
    """Delivery failures are visible and retryable without rerunning training."""
    try:
        result = export_round(config, root, decision, error)
        settings = config.get('result_records', {})
        if result['status'] == 'written' and settings.get('sync'):
            script = Path(settings['sync_script'])
            state = str(Path(root) / 'git-sync.json')
            if settings.get('command_path_style') == 'windows':
                # Only the CLI argument changes; coordinator reads the same file
                # through its Linux view. Never write a second, divergent state.
                from run_project_pipeline import windows
                state = windows(state)
            argv = [settings.get('python', 'python3'), '-B', str(script),
                '--repository', settings.get('sync_repository', settings['repository']), '--remote-url', settings['remote_url'],
                '--branch', settings['branch'], '--owner', settings['owner'],
                '--record-directory', settings.get('directory', 'docs/sdm/runs'),
                '--run-id', config['run_id'], '--round', str(read(Path(root) / 'round-plan.json', {})['round']),
                '--state', state, '--apply']
            if settings.get('navigation'):
                argv.append('--include-navigation')
            invocation = subprocess.run(argv, capture_output=True, timeout=180, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result['git'] = read(Path(root) / 'git-sync.json', {'status': 'not-confirmed'})
            result['sync_exit_code'] = invocation.returncode
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        result = {'status': 'delivery-problem', 'problem': str(exc) if isinstance(exc, ValueError) else type(exc).__name__}
    save_json(Path(root) / 'record-delivery.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--round-root', type=Path, required=True)
    a = p.parse_args()
    result = deliver(read(a.project), a.round_root)
    print(json.dumps(result, ensure_ascii=False))
    return 3 if result['status'] == 'delivery-problem' or result.get('sync_exit_code', 0) else 0


if __name__ == '__main__':
    raise SystemExit(main())
