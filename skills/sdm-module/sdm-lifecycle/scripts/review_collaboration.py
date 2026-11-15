"""Purpose-specific V capability contracts shared by plan and result reviews.

Transport and scientific reasoning belong to the configured reviewer. These
functions bind complete context; they never invent delivery or authorize compute.
"""

PURPOSE_RECORDS = {'plan': ('plan_context', 'plan_review'),
                   'result': ('review_context', 'review_response')}


def plan_context(task):
    from orchestration import needed, value_digest
    records={name:needed(task,name) for name in ('background','research','method_review','proposal')}
    return {'schema':'sdm-plan-review-context/v1', 'purpose':'plan',
            'background':records['background'], 'research':records['research'],
            'method_review':records['method_review'],
            'proposal':records['proposal'],
            'source_digests':{name:value_digest(value) for name,value in records.items()},
            'execution_allowed':False}


def exchange(task, purpose):
    """Resolve a real dialogue call without reusing a result review as plan review."""
    from orchestration import needed, value_digest
    if purpose not in PURPOSE_RECORDS:
        raise ValueError('explicit_review_purpose_required')
    context=plan_context(task) if purpose=='plan' else needed(task,'review_context')
    return {'purpose':purpose, 'mode':task['orchestration']['review_mode'],
            'context':context, 'context_digest':value_digest(context),
            'response_record':PURPOSE_RECORDS[purpose][1], 'execution_allowed':False}


def require_resolved_questions(value):
    if value['status']=='final' and any(not isinstance(q,dict) or q.get('status')!='resolved'
                                         for q in value['questions']):
        raise ValueError('unresolved_questions_prevent_final_review')
