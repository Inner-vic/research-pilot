"""Display/decision diagnostics from recorded confusion counts, never model execution.

The caller verifies the result artifact. Matrix rows are true labels, columns
predictions; no class meaning, IoU producer support or target is inferred.
"""
import math


def finite(value):
 return type(value) in (int,float) and math.isfinite(value)


def classification_metrics(result,baseline=None):
 validation=result.get('validation',{});matrix=validation.get('confusion_matrix')
 if not isinstance(matrix,list) or not 1<=len(matrix)<=512:return None
 n=len(matrix)
 if any(not isinstance(row,list) or len(row)!=n or any(type(v) is not int or v<0 for v in row) for row in matrix):return None
 names=result.get('class_names')
 if not isinstance(names,list) or len(names)!=n or any(not isinstance(v,str) or not v for v in names):names=['类别 '+str(i) for i in range(n)]
 total=sum(map(sum,matrix));rows=[]
 if total==0:return None
 for i,name in enumerate(names):
  tp=matrix[i][i];support=sum(matrix[i]);predicted=sum(row[i] for row in matrix);fp=predicted-tp;fn=support-tp;tn=total-tp-fp-fn
  ratio=lambda numerator,denominator:numerator/denominator if denominator else None
  rows.append({'id':str(i),'label':name,'support':support,'precision':ratio(tp,predicted),'recall':ratio(tp,support),
   'f1':ratio(2*tp,2*tp+fp+fn),'accuracy_ovr':ratio(tp+tn,total)})
 report={'schema':'sdm-classification-details/v1','source':'recorded-confusion-matrix','axis':'rows=true;columns=predicted',
  'sample_count':total,'classes':rows,'confusion_matrix':matrix,'undefined':'zero denominator => null, never zero by assumption',
  'accuracy_definition':'one-vs-rest: (TP+TN)/all samples; class hit rate is recall','baseline_comparable':False}
 if baseline:
  base=classification_metrics(baseline)
  identity=('protocol','source_sha256','split_sha256','selection')
  same=base and all(isinstance(result.get(k),str) and result[k] and result[k]==baseline.get(k) for k in identity)
  same=bool(same and [r['label'] for r in base['classes']]==names and [r['support'] for r in base['classes']]==[r['support'] for r in rows])
  report['baseline_comparable']=same
  if same:
   for row,old in zip(rows,base['classes']):
    row['delta']={k:row[k]-old[k] if row[k] is not None and old[k] is not None else None for k in ('precision','recall','f1','accuracy_ovr')}
 return report


def reported_scalar_metrics(result):
 """Retain optional evaluator-reported metrics; do not manufacture IoU from class CM."""
 names={'accuracy':'总体准确率','balanced_accuracy':'平衡准确率','precision':'精确率','recall':'召回率','macro_f1':'宏平均 F1',
  'micro_f1':'微平均 F1','weighted_f1':'加权 F1','iou':'IoU','mean_iou':'平均 IoU','miou':'平均 IoU','dice':'Dice','loss':'验证损失','mae':'平均绝对误差','mse':'均方误差'}
 return [{'key':k,'label':label,'value':result.get('validation',{}).get(k),'source':'evaluator-reported'}
         for k,label in names.items() if finite(result.get('validation',{}).get(k))]
