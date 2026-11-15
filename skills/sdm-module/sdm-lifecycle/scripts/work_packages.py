"""Business responsibilities and consolidated material acceptance.

Stable numeric IDs address old checks/driver receipts, not business steps. A
package material references original evidence and can supply several preparation
checks/records once. It cannot replace compute, code checks, reviews or consent.
"""
from pathlib import Path

PACKAGES = {
    'task-contract': ('1.1', '任务约定', 'background', ('1', '2', '22')),
    'data-readiness': ('1.2', '数据与材料确认', 'background', ('3',)),
    'model-reference': ('2.1', '模型与对照选择', 'research', ('4',)),
    'experiment-plan': ('2.2', '实验方案与审查', 'research', ('6', '24', '25', '34')),
    'implementation': ('3.1', '实现与工程验证', 'execution', ('9', '10', '11', '12')),
    'execution-readiness': ('3.2', '执行准备与预检', 'execution', ('7', '13')),
    'experiment-run': ('3.3', '运行实验', 'execution', ('5', '15')),
    'evaluation': ('4.1', '评估与比较', 'review', ('16', '17', '18')),
    'result-review': ('4.2', '结果审查', 'review', ('19', '32')),
    'action-choice': ('5.1', '结论与行动选择', 'decision', ('20', '33')),
    'archive-continuation': ('5.2', '归档与迭代接续', 'decision', ('21',)),
}
# Only preparatory material slots, never operational or scientific passes.
MATERIAL_CHECKS = {'task-contract': ('1', '2'), 'experiment-plan': ('6',),
                   'implementation': ('9',), 'evaluation': ('18',),
                   'result-review': (), 'action-choice': ()}
MATERIAL_RECORDS = {'task-contract': ('background',),
                    'experiment-plan': ('research', 'method_review', 'plan_review'),
                    'result-review': ('review_context', 'review_response', 'evidence_consistency', 'conclusion_quality'),
                    'action-choice': ('next_action', 'transition')}


def membership(key):
    for name, (display, label, phase, keys) in PACKAGES.items():
        if key in keys:
            return dict(work_package=name, work_package_id=display,
                        work_package_label=label, phase=phase)
    return None


def summary(nodes, state):
    if any(node.get('builtin')=='record-group' for node in nodes.values()):
        from exploration_workflow import summary as compact
        return compact(nodes,state)
    rows = []
    for name, (display, label, phase, keys) in PACKAGES.items():
        required = [k for k in keys if k in nodes and nodes[k]['policy'] == 'required']
        active = [k for k in keys if k in nodes and nodes[k]['policy'] != 'not-applicable']
        status = ('not-applicable' if not active else
                  'failed' if any(state.get(k, {}).get('status') in {'failed', 'skipped'} for k in required) else
                  'completed' if all(state.get(k, {}).get('status') == 'completed' for k in required)
                  and required else
                  'completed' if not required and any(state.get(k, {}).get('status') == 'completed' for k in active) else
                  'unmeasured' if not required else 'pending')
        rows.append(dict(key=name, display_id=display, label=label, phase=phase,
                         checks=list(keys), required_checks=required, status=status,
                         optional_unmeasured=[k for k in active if nodes[k]['policy'] == 'record'
                                              and state.get(k, {}).get('status') != 'completed']))
    return rows


def validate(task):
    sources = task.get('work_package_materials', {})
    if not isinstance(sources, dict) or set(sources) - set(MATERIAL_CHECKS):
        raise ValueError('known_material_work_package_required')
    from record_collectors import reference
    for ref in sources.values():
        reference(ref)


def material(task, package):
    from execution_contracts import pinned_json
    ref = task['work_package_materials'][package]
    value = pinned_json(ref)
    allowed_records = MATERIAL_RECORDS.get(package, ())
    if (not isinstance(value, dict) or value.get('schema') != 'sdm-work-package-material/v1'
            or value.get('work_package') != package
            or not isinstance(value.get('evidence_refs'), list) or not value['evidence_refs']
            or any(not isinstance(x, str) or not x.strip() for x in value['evidence_refs'])
            or not isinstance(value.get('checks'), dict)
            or set(value['checks']) != set(MATERIAL_CHECKS[package])
            or not isinstance(value.get('records', {}), dict)
            or set(value.get('records', {})) - set(allowed_records)):
        raise ValueError('work_package_material_contract_invalid')
    for key, row in value['checks'].items():
        if (not isinstance(row, dict) or set(row) != {'finding', 'evidence_refs'}
                or not isinstance(row['finding'], str) or not row['finding'].strip()
                or not isinstance(row['evidence_refs'], list) or not row['evidence_refs']
                or any(x not in value['evidence_refs'] for x in row['evidence_refs'])):
            raise ValueError('work_package_check_evidence_required:' + key)
    return value, ref


def preparation_check(task, key):
    owner = membership(key)
    if not owner or owner['work_package'] not in task.get('work_package_materials', {}):
        return None
    package = owner['work_package']
    if key not in MATERIAL_CHECKS.get(package, ()):
        return None
    value, ref = material(task, package)
    return dict(status='completed', receipt=ref['path'], receipt_digest=ref['digest'],
                work_package=package, finding=value['checks'][key]['finding'],
                qualification='project-material-not-execution-or-new-review')


def bind_records(task):
    """Use the same material source for its records; never overwrite a binding."""
    settings = task['orchestration']
    for package, source in task.get('work_package_materials', {}).items():
        value, _ = material(task, package)
        for name in value.get('records', {}):
            spec = {'kind': 'work-package-material', 'source': source, 'work_package': package}
            producers = settings.setdefault('record_producers', {})
            if name in producers and producers[name] != spec:
                raise ValueError('duplicate_work_package_record_producer:' + name)
            producers[name] = spec


def material_record(task, name, spec):
    if (set(spec) != {'kind', 'source', 'work_package'}
            or name not in MATERIAL_RECORDS.get(spec.get('work_package'), ())
            or task.get('work_package_materials', {}).get(spec['work_package']) != spec['source']):
        raise ValueError('work_package_record_binding_mismatch')
    value, _ = material(task, spec['work_package'])
    return value.get('records', {}).get(name)
