"""Choose a bounded task action from real execution outcomes, not model scores."""
RETRYABLE={'selected_gpu_unavailable','deferred_no_idle_gpu'}


def decide(summary,attempt,max_attempts):
    if not 1<=attempt<=max_attempts<=2: raise ValueError('bounded_attempts_required')
    outcomes=summary.get('outcomes',{})
    train=outcomes.get('15',{}); evaluation=outcomes.get('16',{})
    issues=summary.get('issues',[])
    if train.get('status')=='completed' and evaluation.get('status')=='completed':
        return {'action':'stop-completed','reason':'Fresh training and evaluation receipts are verified.',
                'model_quality':'not-an-acceptance-condition','issues':issues,'additional_gpu_runs':0}
    failed=[v for v in outcomes.values() if v.get('status')=='failed']
    if failed and attempt<max_attempts and all(v.get('reason') in RETRYABLE and v.get('gpu_compute_invoked') is False and not v.get('uncertain_effect') for v in failed):
        return {'action':'retry-resource','reason':'Only confirmed pre-compute resource unavailability; select GPU again in a fresh attempt.',
                'next_attempt':attempt+1,'remaining_attempts':max_attempts-attempt,'issues':issues}
    uncertain=any(v.get('uncertain_effect') for v in failed)
    return {'action':'reconcile-and-stop' if uncertain else 'diagnose-and-stop',
            'reason':'Preserve actual failures and stop; no speculative repeated training.',
            'issues':issues,'failures':failed,'additional_gpu_runs':0}
