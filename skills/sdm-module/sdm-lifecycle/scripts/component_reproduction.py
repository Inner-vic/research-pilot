"""One explicit real reproduction intent, never a scientific trial quota."""
from component_research import reference,round_evidence
from review_storage import read_json


def settings(value):
    if (not isinstance(value,dict) or value.get('version') not in (1,2)
            or set(value)!=({'version','kind','baseline_result','scope_ref'} if value['version']==1 else
                {'version','kind','baseline_result','scope_ref','prior_completed_recipe_uids'})
            or value['kind']!='deterministic-reproduction'
            or not isinstance(value['scope_ref'],str) or not value['scope_ref'].strip()):
        raise ValueError('explicit_real_reproduction_scope_required')
    round_evidence(value['baseline_result'])
    if value['version']==2:
        uids=value['prior_completed_recipe_uids']
        if (not isinstance(uids,list) or not uids or len(set(uids))!=len(uids)
                or any(not isinstance(uid,str) or not uid for uid in uids)
                or round_evidence(value['baseline_result'])['uid'] not in uids):
            raise ValueError('explicit_prior_completed_recipe_uids_required')
    return value


def check(rows,value):
    settings(value);base=round_evidence(value['baseline_result'])
    index=next((i for i,row in enumerate(rows) if round_evidence(row['result'])['uid']==base['uid']),None)
    if index is None:raise ValueError('registered_reproduction_baseline_required')
    fresh=rows[index+1:]
    if not fresh:return {'status':'awaiting-real-reproduction','met':False,'baseline_uid':base['uid']}
    if len(fresh)!=1:raise ValueError('reproduction_requires_original_single_intent_reconciliation')
    current=round_evidence(fresh[0]['result'])
    def predictions(row):
        return read_json(next(path for path in row['sources'] if path.name=='predictions.json'))
    def config(row):return {k:v for k,v in row['result']['config'].items() if k not in ('round','expected_minutes')}
    checks={'different_actual_uid':current['uid']!=base['uid'],
        'same_protocol':current['fair_comparison']==base['fair_comparison'],
        'same_training_configuration':config(base)==config(current),
        'same_predictions_and_probabilities':predictions(base)==predictions(current),
        'same_complete_epoch_history':base['result']['history']==current['result']['history'],
        'same_selected_epoch':base['result']['selected_epoch']==current['result']['selected_epoch']}
    return {'schema':'sdm-actual-reproduction-check/v1','status':'matched' if all(checks.values()) else 'mismatch',
        'met':all(checks.values()),'baseline_uid':base['uid'],'actual_uid':current['uid'],
        'baseline_result':value['baseline_result'],'actual_result':fresh[0]['result'],'checks':checks,
        'checkpoint_scope':'Artifact hashes verified independently; serialized checkpoint bytes need not be identical.',
        'execution_authority':False}
