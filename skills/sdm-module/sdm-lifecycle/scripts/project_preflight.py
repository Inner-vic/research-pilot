"""Shared verification of project-specific remote functional-check evidence."""
def verify_report(value,declared):
    if (not isinstance(value,dict) or value.get('passed') is not True
            or value.get('training_only') is not True or not isinstance(value.get('checks'),dict)):
        raise ValueError('actual_training_only_project_preflight_required')
    expected={row['name']:row for row in declared}
    if not expected or set(value['checks'])!=set(expected):raise ValueError('all_declared_project_checks_required')
    for name,result in value['checks'].items():
        if (not isinstance(result,dict) or result.get('status') not in ('passed','not-applicable')
                or not isinstance(result.get('evidence'),str) or len(result['evidence'].strip())<10):
            raise ValueError('project_check_needs_actual_evidence:'+name)
        if expected[name]['applicable'] and result['status']!='passed':raise ValueError('applicable_project_check_did_not_pass:'+name)
        if not expected[name]['applicable'] and result['status']=='not-applicable':
            if not isinstance(result.get('reason'),str) or len(result['reason'].strip())<20:
                raise ValueError('project_check_inapplicability_needs_reason:'+name)
    return value
