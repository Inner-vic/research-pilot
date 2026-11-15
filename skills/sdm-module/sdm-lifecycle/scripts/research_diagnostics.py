"""Evidence-led hypotheses and uncertainty; never chooses a model or trains."""
import math


def summarize(actual):
    if not actual:raise ValueError('verified_results_required_for_diagnostics')
    best=max(actual,key=lambda r:r['primary_metric']);latest=actual[-1]
    results=[r['result'] for r in actual];last=results[-1]
    history=last.get('history',[]);curve=[]
    for epoch in history:
        val=epoch.get('validation',{})
        curve.append({k:v for k,v in {'epoch':epoch.get('epoch'),'train_loss':epoch.get('train_loss'),
            'validation_loss':val.get('loss'),'macro_f1':val.get('macro_f1')}.items() if v is not None})
    recalls=last.get('validation',{}).get('class_recall',[])
    signals=[]
    if latest['primary_metric']<best['primary_metric']:
        signals.append({'kind':'regression','evidence':{'latest_uid':latest['uid'],'incumbent_uid':best['uid'],
            'delta':latest['primary_metric']-best['primary_metric']},'implication':'retain incumbent; explain against the selected experimental base'})
    if recalls and min(recalls)<max(recalls):
        signals.append({'kind':'class-asymmetry','evidence':{'class_recall':recalls},
            'implication':'inspect class support, confusion and measurement before choosing representation or loss'})
    if curve and last.get('selected_epoch',0)<curve[-1].get('epoch',0):
        signals.append({'kind':'best-before-final','evidence':{'selected_epoch':last.get('selected_epoch'),
            'final_epoch':curve[-1]['epoch']},'implication':'inspect complete curves; this alone does not establish overfitting or the cause'})
    seeds=sorted({r.get('config',{}).get('seed') for r in results if type(r.get('config',{}).get('seed')) is int})
    n=sum(sum(row) for row in last.get('validation',{}).get('confusion_matrix',[]))
    acc=last.get('validation',{}).get('accuracy');interval=None
    if n and type(acc) in (int,float) and math.isfinite(acc):
        z=1.959963984540054;den=1+z*z/n
        center=(acc+z*z/(2*n))/den;radius=z*math.sqrt(acc*(1-acc)/n+z*z/(4*n*n))/den
        interval=[max(0,center-radius),min(1,center+radius)]
    return {'schema':'sdm-research-diagnostics/v1','signals':signals,'latest_curve':curve,
        'diagnostic_order':'Choose the cheapest discriminating evidence for the observed symptom. Check data/measurement, optimization and representation as competing explanations; no mandatory model-first order.',
        'decision_requirements':['cite actual baseline and negative results','state competing mechanisms and a falsifier',
            'reject already completed recipes and unchanged blocked plans','choose continue, rollback or redesign from evidence; do not equate latest with best'],
        'uncertainty':{'observed_seeds':seeds,'replicated_recipe_across_seeds_verified':False,
            'untouched_test_verified':False,'validation_accuracy_wilson95_descriptive':interval,
            'scope':'Wilson interval is descriptive conditional on this validation sample. It does not correct adaptive selection, prove independent samples or establish cross-seed/model superiority.'},
        'execution_authority':False}
