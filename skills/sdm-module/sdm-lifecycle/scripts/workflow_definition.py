"""One effective dependency definition; stable legacy IDs are check identities.

Business work packages are defined separately from their deterministic checks and
cross-cutting calls. Driver wire revision 3 remains compatible; workflow revision
1 pins the new scheduling and grouping semantics in the task fingerprint.
"""
from copy import deepcopy

WORKFLOW_REVISION = 1
DEFINITION = {
    '1': ('task', [], None),
    '2': ('question', ['1'], None),
    '3': ('data', ['22'], None),
    '22': ('background-alignment', ['1', '2'], 'background'),
    '4': ('starting-model', ['22', '3'], None),
    '6': ('plan', ['2', '3', '4', '22'], None),
    '24': ('research-protocol', ['6', '22'], 'research'),
    '25': ('methodology-checks', ['24'], 'method_review'),
    '26': ('account-resource-inventory', ['22'], 'inventory'),
    '27': ('resource-budget-proposal', ['26', '25'], 'proposal'),
    '34': ('plan-review', ['27'], 'plan_review'),
    '9': ('implementation-context', ['6', '24', '34'], None),
    '23': ('source-isolation', ['9'], None),
    '10': ('implementation', ['9'], None),
    '11': ('code-review', ['10'], None),
    '12': ('engineering-check', ['10'], None),
    '8': ('config-snapshot', ['6', '24', '34'], None),
    '28': ('human-resource-confirmation', ['27', '34'], 'confirmation'),
    '14': ('execution-request', ['28', '8'], None),
    '7': ('runtime-check', ['3', '4', '6', '14', '28'], None),
    '13': ('preflight', ['7', '8'], None),
    '5': ('baseline-reproduction', ['13', '28'], None),
    '15': ('training', ['14', '13'], None),
    '16': ('evaluation', ['15'], None),
    '17': ('robustness', ['16'], None),
    '18': ('result-summary', ['15', '16'], None),
    '30': ('allocation-release', ['18'], 'release'),
    '29': ('review-context', ['18'], 'review_context'),
    '31': ('review-dialogue', ['29'], 'review_response'),
    '19': ('evidence-review', ['18', '31'], 'evidence_consistency'),
    '32': ('decision-quality', ['19', '31'], 'conclusion_quality'),
    '20': ('next-action', ['18', '32'], 'next_action'),
    '33': ('stage-transition', ['20', '32'], 'transition'),
    '21': ('finish-or-successor', ['20', '33', '30'], None),
}
RECORDS = {k: record for k, (_, _, record) in DEFINITION.items() if record and record != 'release'}
FAILURE_CONSUMERS = {'18', '19', '20', '21', '29', '30', '31', '32', '33'}


def build(task):
    for flag in ('code_change', 'baseline_required', 'robustness_required', 'code_review_required', 'merge', 'publish'):
        if flag in task and type(task[flag]) is not bool:
            raise ValueError('boolean_task_option_required:' + flag)
    objective = task.get('objective')
    if objective not in {'execute-and-learn', 'evaluate-only', 'baseline-reproduction'}:
        raise ValueError('explicit_execution_objective_required')
    if task.get('merge') or task.get('publish'):
        raise ValueError('merge_or_publish_requires_separate_workflow')
    settings = task.get('orchestration', {})
    isolate = settings.get('source_isolation', task.get('code_change', False))
    if type(isolate) is not bool:
        raise ValueError('source_isolation_boolean_required')
    if task.get('workflow_profile')=='exploration':
        if task.get('code_review_required') and not task.get('code_change'):raise ValueError('code_review_requires_code_change')
        if objective=='baseline-reproduction' and task.get('robustness_required'):raise ValueError('robustness_requires_evaluation_objective')
        from exploration_workflow import build as compact
        return compact(task)
    nodes = {k: dict(name=name, depends=list(deps), policy='required',
                     **({'builtin': record} if record else {}))
             for k, (name, deps, record) in DEFINITION.items()}
    for k in ('5', '11', '17'):
        nodes[k]['policy'] = 'record'
    if not task.get('code_change', False):
        for k in ('9', '10', '11', '12'):
            nodes[k]['policy'] = 'not-applicable'
        if task.get('code_review_required'):
            raise ValueError('code_review_requires_code_change')
    else:
        nodes['8']['depends'].append('12')
        nodes['14']['depends'] += ['10', '12']
        nodes['13']['depends'] += ['10', '12']
        if task.get('code_review_required'):
            nodes['11']['policy'] = 'required'
            nodes['8']['depends'].append('11')
            nodes['14']['depends'].append('11')
    if isolate:
        nodes['10']['depends'].append('23')
        nodes['14']['depends'].append('23')
    else:
        nodes['23']['policy'] = 'not-applicable'
    if task.get('baseline_required') and objective == 'execute-and-learn':
        nodes['5']['policy'] = 'required'
        nodes['15']['depends'].append('5')
    if task.get('robustness_required'):
        nodes['17']['policy'] = 'required'
        nodes['18']['depends'].append('17')
    if objective == 'evaluate-only':
        nodes['15']['policy'] = 'not-applicable'
        nodes['16']['depends'] = ['14', '13', '4']
    elif objective == 'baseline-reproduction':
        if task.get('robustness_required'):
            raise ValueError('robustness_requires_evaluation_objective')
        nodes['5']['policy'] = 'required'
        for k in ('15', '16', '17'):
            nodes[k]['policy'] = 'not-applicable'
        nodes['18']['depends'] = ['5']
    for k in FAILURE_CONSUMERS:
        nodes[k]['accept_failed_dependencies'] = True
    return deepcopy(nodes)
