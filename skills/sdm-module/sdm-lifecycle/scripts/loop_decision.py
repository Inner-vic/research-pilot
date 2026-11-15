"""Explain a bounded, single-variable diagnostic successor from observed facts."""
import math


def changed(before,after):
    if set(before)!=set(after): raise ValueError('loop_parameter_keys_changed')
    return [k for k in before if before[k]!=after[k]]


def analyze(history,max_rounds):
    if not history or not 1<=len(history)<=max_rounds<=3: raise ValueError('bounded_loop_history_required')
    current=history[-1]; params=current['parameters']; train=current.get('training',{}); evaluation=current.get('evaluation',{})
    facts=train.get('actual',{}); remaining=max_rounds-len(history)
    observations={'round':len(history),'gradient_records':facts.get('gradient_record_count'),
        'nonfinite_gradients':facts.get('nonfinite_gradient_count'),
        'training_status':train.get('status'),'evaluation_status':evaluation.get('status'),
        'surface_miou':evaluation.get('actual',{}).get('surface_miou')}
    differences={}
    if len(history)>1:
        previous=history[-2]
        for label,now,old in (
            ('nonfinite_gradients',facts.get('nonfinite_gradient_count'),previous.get('training',{}).get('actual',{}).get('nonfinite_gradient_count')),
            ('surface_miou',observations['surface_miou'],previous.get('evaluation',{}).get('actual',{}).get('surface_miou'))):
            if type(now) in (int,float) and type(old) in (int,float) and math.isfinite(now) and math.isfinite(old):
                differences[label]=now-old
    report={'observations':observations,'differences_from_previous':differences,
            'comparison':'descriptive fixed-scope diagnosis; no business-effectiveness qualification',
            'remaining_rounds':remaining,'model_quality':'not-an-acceptance-condition'}
    def stop(action,reason):
        return {**report,'action':action,'reason':reason,'additional_rounds':0}
    if any(v.get('uncertain_effect') or v.get('actual',{}).get('uncertain_effect') for v in (train,evaluation)):
        return stop('stop-reconcile','Execution effects are uncertain; reconcile platform facts before another GPU call.')
    if remaining==0: return stop('stop-budget','The configured round budget is exhausted; retain all outcomes and issues.')
    if not facts or facts.get('exit_code')!=0 or facts.get('timed_out') or not facts.get('iteration_evidence_complete'):
        return stop('stop-execution-failure','No complete, known training observation supports a parameter change.')
    if not facts.get('gradient_record_count'):
        return stop('stop-insufficient-observation','Gradient observations are missing; do not guess a numerical repair.')
    if evaluation.get('status')=='failed':
        return stop('stop-evaluation-failure','A failed fixed evaluation requires diagnosis before further training.')
    nonfinite=facts.get('nonfinite_gradient_count',0)
    next_params=dict(params)
    if nonfinite and params['amp']:
        next_params['amp']=False
        hypothesis='Test whether mixed precision contributes to the observed nonfinite gradients; keep learning rate and every fixed input unchanged.'
    elif not any(r.get('changed_variable')=='learning_rate' for r in history):
        next_params['learning_rate']=params['learning_rate']/2
        hypothesis=('Test learning-rate sensitivity while precision stays fixed; this is a diagnostic probe, not a claimed fix.' if nonfinite else
                    'Gradients are observable without a current nonfinite count; test learning-rate sensitivity with the same precision and fixed evaluation.')
    else:
        return stop('stop-no-next-hypothesis','The implemented diagnostic candidates have been observed; do not repeat an unchanged experiment.')
    variables=changed(params,next_params)
    if len(variables)!=1: raise ValueError('exactly_one_loop_variable_required')
    if any(r['parameters']==next_params for r in history): return stop('stop-repeated-configuration','This parameter combination was already observed.')
    if next_params['learning_rate']<1e-7: return stop('stop-parameter-bound','The next rate would exceed the provider range.')
    return {**report,'action':'run-next-round','next_round':len(history)+1,
            'changed_variable':variables[0],'before':params[variables[0]],'after':next_params[variables[0]],
            'next_parameters':next_params,'hypothesis':hypothesis,
            'reason':'The hypothesis follows the authenticated current observations; one variable changes within the remaining budget.'}
