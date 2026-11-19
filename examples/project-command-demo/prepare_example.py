"""Prepare deterministic synthetic inputs and project facts, without ML or platform calls."""
import hashlib,json,math,random
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def write(name,value):
    raw=(json.dumps(value,sort_keys=True,ensure_ascii=False)+'\n').encode();(ROOT/name).write_bytes(raw);return hashlib.sha256(raw).hexdigest()
def prepare():
    rng=random.Random(42);x=[];y=[];train=[];validation=[]
    labels=['inner','middle','outer']
    for label,(lo,hi) in enumerate(((.1,.4),(.6,.9),(1.1,1.4))):
        ids=[]
        for i in range(240):
            radius=rng.uniform(lo,hi);angle=rng.uniform(-math.pi,math.pi)
            ids.append(len(x));x.append([round(radius*math.cos(angle),8),round(radius*math.sin(angle),8)]);y.append(label)
        rng.shuffle(ids);train.extend(ids[:180]);validation.extend(ids[180:])
    train.sort();validation.sort()
    data_hash=write('data.json',{'generator':'prepare_example.py; random.Random(42); three separated radial intervals',
        'x':x,'y':y,'labels':labels})
    split_hash=write('split-input.json',{'train_ids':train,'validation_ids':validation,'test_used':False})
    check_hash=hashlib.sha256((ROOT/'check_inputs.py').read_bytes()).hexdigest();protocol='project-rings-fixed-validation-v1'
    tensor={'inputs':['coordinates'],'outputs':['class_logits'],'shape':{'coordinates':[2],'class_logits':[3]},
        'dtype':'float64','constraints':{'finite':True,'layout':'N by input/output dimension'}}
    contracts={
      'G03':{'dataset_identity':'sha256:'+data_hash,'version':data_hash,
        'schema':{'coordinates':'two finite dimensionless floating values','label':'inner/middle/outer'},
        'quality_policy':{'partition':'Fixed disjoint 540 training / 180 validation IDs; synthetic observations; no TEST.'},
        'lineage':{'generator':'prepare_example.py','source':'Synthetic three concentric rings, not real-world quality evidence.'}},
      'G07':{'ontology_identity':'three-rings-labels','classes':labels,
        'semantics':{'inner':'Synthetic radius in [0.1,0.4]','middle':'Synthetic radius in [0.6,0.9]','outer':'Synthetic radius in [1.1,1.4]'},'version':data_hash},
      'G08':{'operations':[{'operation_id':'identity','type':'identity'}],'probabilities':{'identity':1.0},
        'ordering':['identity'],'geometry_effects':{'identity':{'coordinates':'unchanged','labels':'unchanged'}}},
      'G09':{**{k:tensor[k] for k in ('inputs','outputs','shape','dtype')},
        'units':{'coordinates':'dimensionless synthetic features','class_logits':'dimensionless'},'runtime_validator_digest':'sha256:'+check_hash},
      'G10':{'evaluator_identity':protocol,'metric_definitions':{
        'mean_iou':'Equal mean of three class TP/(TP+FP+FN), zero denominator contributes zero.',
        'macro_f1':'Equal mean of three class 2TP/(2TP+FP+FN).','accuracy':'Correct fixed-validation predictions divided by count.'},
        'slices':['fixed-validation'],'tolerances':{'metric_recomputation':1e-10},
        'inconclusive_rule':'Synthetic adaptive validation does not establish confirmatory or real-project performance.',
        'owner_principal_ids':['researchpilot-cpu-acceptance-owner']}}
    omitted={
      'G04':'Synthetic dimensionless features make no spatial reference-frame or vehicle geometry claim.',
      'G05':'No physical sensor calibration, projection or calibration-dependent target is used in this synthetic task.',
      'G06':'Independent synthetic observations contain no cross-sensor timestamps or synchronization requirement.',
      'G11':'This bounded engineering acceptance does not claim robustness; such claims need their own protocol.',
      'G12':'No model deployment, device timing, production health or service qualification is claimed.'}
    definition={'schema':'researchpilot-project/v1','project_id':'rings-example',
      'source_files':['model_impl.py','project_data.py','check_inputs.py','train_model.py','evaluate_model.py',
                      'experiment.json','data.json','split-input.json'],
      'editable_files':['model_impl.py','experiment.json'],'parameters_file':'experiment.json',
      'entrypoints':{'preflight':['python3','check_inputs.py'],'train':['python3','train_model.py'],'evaluate':['python3','evaluate_model.py']},
      'data':{'identity':data_hash,'partition_identity':split_hash,'description':'720 independent synthetic two-coordinate observations across three concentric rings.',
              'labels':labels,'quality_policy':{'partition':'fixed per-class disjoint rows; no TEST'}},
      'tensor':tensor,'evaluation':{'kind':'classification','primary_metric':'mean_iou','operator':'>=','target':.95,
          'selection':'last','report':'evaluation.json','predictions':'predictions.json'},
      'preflight_checks':[{'name':name,'applicable':True,'rationale':reason} for name,reason in [
          ('partition','Check actual disjoint project row membership.'),('tensor','Check actual project input-to-logit shapes.'),
          ('finite','Check actual finite outputs, losses and gradients.'),('tiny_fit','Check bounded training-only loss decrease, not validation quality.'),
          ('resume','Check a serialized parameter roundtrip and equal next update.'),('initialization','Check a fresh full-training process uses the declared deterministic initial state.')]],
      'training_limits':{'min_epochs':10,'default_epochs':10,'max_epochs':10},
      'contract_contents':contracts,'not_applicable_contracts':omitted}
    write('researchpilot.project.json',definition)
    print(json.dumps({'project':'rings-example','samples':720,'training':540,'validation':180,
        'primary_metric':'validation.mean_iou','ml_executed':False,'platform_writes':0}))
if __name__=='__main__':prepare()
