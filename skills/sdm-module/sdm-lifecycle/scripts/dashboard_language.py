"""Chinese evidence summaries; original English receipts remain untouched."""
import hashlib,json,re
from functools import lru_cache
from pathlib import Path

def key(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()

@lru_cache(maxsize=8)
def saved_briefs(path,modified,size):
 if size>8*1024*1024:return {}
 return json.loads(Path(path).read_bytes())['entries']

def brief(project,intervention,level='方案调整'):
 try:
  path=Path(project)/'.bmad-ml/dashboard/zh-briefs.json';stat=path.stat()
  rows=saved_briefs(str(path),stat.st_mtime_ns,stat.st_size)
  saved=rows.get(key(intervention))
  if saved and saved.get('source_sha256')==key(intervention):return dict(saved)
 except (OSError,ValueError,KeyError):pass
 lower=intervention.lower()
 if 'absolute-difference' in lower:
  return {'title':'成对乘积改为绝对差','description':'保留相同的时间分段配对，将成对乘积的对数特征替换为绝对差的对数特征；保留直接分类器、零初始化交互分支和其余登记配方。这是表征方式的对照。','kind':'本地类型概要'}
 width=re.search(r'channels (?:from )?(\d+) to (\d+)',lower)
 if width:return {'title':'卷积通道'+width[1]+'→'+width[2],'description':'将骨干卷积通道从'+width[1]+'增加到'+width[2]+'，并调整依赖维度；保留已登记配方的其余设置。这是容量假设的对照试验，不表示根因已确定。','kind':'本地类型概要'}
 if re.search('[\u4e00-\u9fff]',intervention) or not re.search('[A-Za-z]{3,}',intervention):return {'title':intervention,'description':intervention,'kind':'原始中文记录'}
 rules=[('cross-channel','跨通道时序交互'),('third-order','三阶时序交互'),('pairwise','成对交互特征'),
  ('channels','卷积通道宽度调整'),('amsgrad','优化器二阶矩策略'),('label_smoothing','标签平滑调整'),
  ('label smoothing','标签平滑调整'),('learning rate','学习率方案调整'),('lr=','学习率方案调整'),
  ('batch_size','批量大小调整'),('first-difference','一阶差分分支'),('groupnorm','分组归一化'),
  ('weight_decay','权重衰减'),('dropout','随机失活'),('pool','时序池化方式调整'),('epoch','训练轮数调整')]
 title=next((zh for en,zh in rules if en in lower),level)
 if any(t in lower for t in ('end target-directed','retain round')):title='达到目标，保留最优候选'
 return {'title':title,'description':'已登记'+title+'方案。这里是基于记录类型的中文索引；完整假设和反证请核对原始方案。',
  'kind':'类型索引','source_sha256':key(intervention)}

def verdict(value):return {'approve':'通过','revise':'要求修改','needs-evidence':'要求补证','reject':'否决','stalled':'审议停滞'}.get(value,'已记录')

def annotate(project,study):
 for row in study['rounds']:
  row['zh']=brief(project,row['intervention'],row['change_label'])
  base=('实验'+str(row['base_number'])) if row['base_number'] else '初始基线'
  row['zh']['question']='检验“'+row['zh']['title']+'”在当前登记验证协议下的表现；这是一项待测假设，不是机制已被证实。'
  row['zh']['comparison']=('实际比较基线为'+base+'，宏平均 F1 为 '+format(row['metric'],'.4f')+
   ('，相对该基线变化 '+format(row['delta_base'],'+.4f') if row['delta_base'] is not None else '')+'。结果不自动证明因果或跨种子泛化。')
 current=study['current']
 if current.get('governing_plan'):
  p=current['governing_plan'];p['zh']=brief(project,p.get('intervention',''),p.get('change_label','研究方案'))
 if current.get('proposal'):
  p=current['proposal'];p['zh']=brief(project,p.get('intervention',''),p.get('change_label','研究方案'))
 for row in study['rounds']:
  next_row=next((r for r in study['rounds'] if r.get('plan',{} ) and r['plan'].get('parent_uid')==row['uid']),None)
  plan=current.get('proposal') if row.get('next_plan_pending_execution') else next_row.get('plan') if next_row else None
  if plan:
   decision=plan.get('decision',{});base=next((r for r in study['rounds'] if r['uid']==plan.get('base_uid')),None)
   row['next_decision_zh']=('后续选择：'+{'continue':'沿用当前候选','rollback':'回到更优候选','redesign':'调整研究方案','stop':'结束当前研究'}.get(decision.get('choice'),'继续审议')+
    ('；实际起点为实验'+str(base['number']) if base else '')+'。'+brief(project,plan.get('intervention',''))['description'])
 return study
