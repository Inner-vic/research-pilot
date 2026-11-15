"""Inspect bindings and record producers without executing drivers or platform calls."""
from pathlib import Path


# These are production responsibilities, not built-in collectors or review agents.
PRODUCERS = {
    'background': 'task-owner: aligned user requirements',
    'research': 'experiment-designer: experiment protocol',
    'method_review': 'method-reviewer: protocol applicability assessment',
    'inventory': 'resource-adapter: official SDK/CLI observations',
    'proposal': 'resource-planner: resolved configuration and cost proposal',
    'confirmation': 'human-user: direct confirmation of the exact proposal',
    'plan_review': 'selected-reviewer: contextual plan review',
    'review_context': 'evidence-owner: aligned context and evidence index',
    'review_response': 'selected-reviewer: dialogue and resolved questions',
    'evidence_consistency': 'evidence-checker: claim-to-evidence consistency',
    'conclusion_quality': 'selected-reviewer: sufficiency of the conclusion',
    'next_action': 'decision-author: recommendation and options',
    'transition': 'decision-owner: selected next action within authority',
}
TRIGGERS = {
    '5': 'baseline-reproduction objective or baseline_required; otherwise explicit optional driver',
    '11': 'code_change and explicit optional driver',
    '17': 'robustness_required or explicit optional driver',
    '23': 'source_isolation (defaults to code_change)',
    '15': 'execute-and-learn objective',
    '16': 'execute-and-learn or evaluate-only objective',
}


def inspect(task, nodes, state=None):
    """A complete catalog is not evidence of execution or record validity."""
    from orchestration import RECORDS
    state = state or {}
    drivers = task.get('drivers', {})
    records = task.get('orchestration', {}).get('records', {})
    rows = []
    for key, node in nodes.items():
        record = RECORDS.get(key)
        builtin = node.get('builtin')
        kind = 'record-gate' if record else ('cleanup-service' if builtin == 'release' else 'project-driver')
        selected = node['policy'] == 'required' or (node['policy'] == 'record' and key in drivers)
        ref = records.get(record, {}) if record else {}
        path = ref.get('path') if isinstance(ref, dict) else None
        present = bool(isinstance(path, str) and Path(path).is_file())
        from work_packages import MATERIAL_CHECKS, membership
        package=membership(key)
        shared=bool(package and key in MATERIAL_CHECKS.get(package['work_package'], ())
                    and package['work_package'] in task.get('work_package_materials', {}))
        bound = bool(task.get('cleanup_driver')) if kind == 'cleanup-service' else key in drivers or shared
        if kind == 'record-gate':
            binding = 'record-present-not-validated' if present else 'awaiting-project-record'
        else:
            binding = 'bound-not-exercised' if bound else 'missing-driver'
        if node['policy'] == 'not-applicable':
            activation = 'not-applicable'
        elif not selected:
            activation = 'optional-not-selected'
        else:
            activation = 'selected'
        rows.append(dict(node=key, display_id=node['display_id'], label=node['label'],
                         owner=node['owner'], policy=node['policy'], depends=node['depends'],
                         trigger=TRIGGERS.get(key, 'applicable objective and terminal dependencies'),
                         activation=activation, executor_kind=kind, binding=binding,
                         producer=PRODUCERS.get(record, 'project cleanup adapter' if builtin else 'project driver'),
                         record_kind=record, record_path=path,
                         record_section=ref.get('section') if isinstance(ref,dict) else None,
                         record_group=node.get('record_group'),
                         automatic_producer=task.get('orchestration',{}).get('record_producers',{}).get(record,{}).get('kind'),
                         result_status=state.get(key, {}).get('status', 'not-run'),
                         ignored_driver=bool(builtin and key in drivers)))
        rows[-1]['shared_material_bound']=shared
    paths = {}
    for row in rows:
        if isinstance(row['record_path'], str):
            normalized = str(Path(row['record_path']).resolve())
            paths.setdefault((normalized,row.get('record_section')), []).append(row['node'])
    return {'schema': 'sdm-trigger-matrix/v1', 'graph_revision': 3,
            'project_capability_check':task.get('project_capability_check'),
            'evidence_scope': 'binding-inspection-not-execution', 'nodes': rows,
            'missing_required_drivers': [r['node'] for r in rows if r['policy'] == 'required'
                and r['executor_kind'] == 'project-driver' and r['binding'] == 'missing-driver'
                and r['node'] not in state],
            'ignored_builtin_drivers': [r['node'] for r in rows if r['ignored_driver']],
            'record_path_collisions': [keys for keys in paths.values() if len(keys) > 1],
            'cleanup_driver_bound': bool(task.get('cleanup_driver'))}


def acquisition_gaps(task, nodes, state):
    report = inspect(task, nodes, state)
    collisions = ['record-path-collision:' + ','.join(keys) for keys in report['record_path_collisions']]
    check=task.get('project_capability_check')
    contract_gaps=[] if check is None or check.get('status')=='declared' else ['project-capabilities:'+check['status']]
    return report['missing_required_drivers'] + ([] if report['cleanup_driver_bound'] else ['cleanup']) + collisions + contract_gaps


def waiting_action(task, key,record_name=None):
    from orchestration import RECORDS
    name = record_name or RECORDS[key]
    return {'producer': PRODUCERS[name], 'record_kind': name,
            'action': 'obtain-direct-user-confirmation' if name == 'confirmation' else 'produce-or-revise-project-record',
            'automatic_producer_bound': name in task['orchestration'].get('record_producers',{}),
            'resume': 'write the declared record path, then invoke the same coordinator task and output directory',
            'constraint': 'do not fabricate evidence or replay completed drivers'}
