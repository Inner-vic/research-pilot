"""Projection integrity and live read-only refresh; no model or training calls."""
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from research_dashboard_data import DashboardData,digest
from research_dashboard import Server

class DashboardTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.data=DashboardData(self.root)
 def tearDown(self):self.tmp.cleanup()
 def write(self,path,value):
  path.parent.mkdir(parents=True,exist_ok=True);raw=json.dumps(value).encode();path.write_bytes(raw)
  return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}
 def result(self,n,metric):
  folder=self.root/str(n);artifact=self.write(folder/'result.json',{'validation':{'macro_f1':metric},'config':{'epochs':20}})
  return self.write(folder/'ready.json',{'schema':'sdm-component-result-ready/v1','valid_training_round':True,
   'uid':str(n),'primary_metric':metric,'artifact_root':str(folder),'artifact_sha256':{'result.json':artifact['sha256']}})
 def test_actual_candidate_and_corrupt_artifact(self):
  a=self.result(1,.8);b=self.result(2,.7);c=self.result(3,.82)
  plan=self.write(self.root/'plan.json',{'parent_task_uid':'2','selected_candidate':{'uid':'1'},
   'research_revision':{'candidate_decision':{'reason':'Retain superior candidate'}}})
  state={'rounds':[{'result':a},{'result':b},{'result':c,'accepted_plan':plan}]}
  rows,issues=self.data.rounds(state)
  self.assertFalse(issues);self.assertEqual(rows[2]['base_number'],1)
  self.assertAlmostEqual(rows[2]['delta_base'],.02);self.assertEqual(rows[1]['result_decision']['reason'],'Retain superior candidate')
  (self.root/'3/result.json').write_text('{}')
  rows,issues=self.data.rounds(state)
  self.assertEqual(len(rows),2);self.assertEqual(issues[0]['reason'],'research_evidence_changed')
 def test_pending_plan_and_goal_projection(self):
  cfg={'schema':'sdm-event-service/v3','project_id':'demo','root':str(self.root/'run'),'component_pipeline':{}}
  state={'config_digest':digest(cfg),'status':'running','rounds':[{'result':self.result(1,.8)}],
   'attempts':[{'stage':'needs-attention','continuation':{'category':'model-input-capacity'}}]}
  self.write(self.root/'run/component-pipeline/pipeline.json',state)
  from unittest.mock import patch
  proposal={'approved':True,'parent_uid':'1','decision':{'reason':'Try a distinct mechanism'}}
  with patch.object(self.data,'current',return_value={'needs_attention':True,'proposal':proposal,'stage':'needs-attention','label':'需要关注'}):
   study=self.data.study({},cfg,self.root/'run')
  self.assertEqual(study['status_label'],'需要关注')
  self.assertTrue(study['rounds'][0]['next_plan_pending_execution'])
  self.assertEqual(study['rounds'][0]['result_decision'],proposal['decision'])
 def test_http_access_and_sse_change(self):
  server=Server(self.root);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def request(method,path,headers=None):
   conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
   conn.request(method,path,headers=headers or {});response=conn.getresponse();status=response.status;response.read();conn.close();return status
  auth={'Authorization':'Bearer '+server.token}
  stream=None
  try:
   self.assertEqual(request('GET','/api/snapshot'),403)
   self.assertEqual(request('GET','/api/snapshot',auth),200)
   self.assertEqual(request('POST','/api/snapshot',auth),405)
   self.assertEqual(request('GET','/api/snapshot',{**auth,'Origin':'https://elsewhere.invalid'}),403)
   self.assertEqual(request('GET','/api/evidence?id=unregistered',auth),409)
   stream=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=8)
   stream.request('GET','/api/events',headers=auth);response=stream.getresponse()
   self.assertEqual(response.status,200)
   def event():
    lines=[]
    while True:
     line=response.fp.readline().decode().strip()
     if not line:return lines
     lines.append(line)
   first=event();self.assertEqual(first[0],'event: snapshot')
   # Change only this isolated fixture, never a real active research record.
   with server.lock:
    server.data.snapshot=lambda study: {'revision':'changed','read_only':True}
    server.snapshots.clear()
   second=event();self.assertIn('changed',second[1])
  finally:
   server.done.set()
   if stream:stream.close()
   server.shutdown();server.server_close();thread.join(3)

 def test_control_receipt_read_survives_unavailable_study_index(self):
  import uuid
  request_id=str(uuid.uuid4());request={'request_id':request_id,'study_id':'original-study'}
  self.write(self.root/'.bmad-ml/dashboard/controls'/(request_id+'.json'),{'request':request,'phase':'restoring'})
  self.write(self.root/'.bmad-ml/restores'/request_id/'state.json',{'request':request,'project':str(self.root.resolve()),
   'study_id':'original-study','phase':'resumed'})
  server=Server(self.root);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
   conn.request('GET','/api/study/control?request_id='+request_id,headers={'Authorization':'Bearer '+server.token})
   response=conn.getresponse();value=json.loads(response.read());conn.close()
   self.assertEqual(response.status,200);self.assertEqual(value['phase'],'resumed')
   self.assertEqual(value['study_id'],'original-study');self.assertFalse(value['pending'])
   from dashboard_control import receipt_status
   with self.assertRaisesRegex(ValueError,'identity_required'):receipt_status(self.root,'../bad')
  finally:server.shutdown();server.server_close();thread.join(3)

 def test_mainline_uses_actual_ancestors_and_graph_keeps_negative_branch(self):
  from dashboard_lineage import mainline,project
  rows=[]
  for n,base,metric in [(1,None,.6),(2,'1',.7),(3,'1',.65),(4,'2',.8)]:
   rows.append({'number':n,'uid':str(n),'base_uid':base,'metric':metric,'plan':None,'new_best':n in (1,2,4),
    'zh':{'title':'对照方案','description':'真实登记的对照改动','comparison':'固定验证结果'}})
  study={'id':'fixture','title_zh':'示例研究','rounds':rows,'completed':False,'needs_attention':False,'latest':{'uid':'4'},'incumbent':{'uid':'4'},
   'current':{'stage':'reviewing-plan','label':'方案审议','needs_attention':False,'proposal':None,'task':None}}
  self.assertEqual(mainline(study)['round_uids'],['1','2','4'])
  graph=project(self.data,{},self.root,{},study)
  self.assertIn('result:3',{n['id'] for n in graph['nodes']})
  edge=next(e for e in graph['edges'] if e['target']=='plan:3')
  self.assertEqual(edge['source'],'result:1')
  study['current']['proposal']={'base_uid':'2','parent_uid':'4','approved':False,'zh':{'title':'新的方案','description':'尚未提交'}}
  self.assertEqual(mainline(study)['round_uids'],['1','2'])
  self.assertEqual(mainline(study)['head']['trigger_uid'],'4')

 def test_graph_retains_failed_task_without_a_training_score(self):
  from dashboard_lineage import project
  ref=self.write(self.root/'diagnosis.json',{'schema':'sdm-component-diagnosis-ready/v1',
   'uid':'failed-original','valid_training_round':False,'platform_status':'failed'})
  state={'rounds':[],'attempts':[{'id':'initial','result':ref}]}
  self.write(self.root/'component-pipeline/pipeline.json',state)
  study={'id':'fixture','title_zh':'失败恢复研究','rounds':[],'completed':False,'needs_attention':True,
   'current':{'stage':'needs-attention','label':'需要处理','needs_attention':True,'proposal':None,'task':None}}
  graph=project(self.data,{},self.root,state,study)
  failed=next(n for n in graph['nodes'] if n.get('task_uid')=='failed-original')
  self.assertEqual(failed['status'],'failed');self.assertNotIn('metric',failed)
  self.assertFalse(any(n['kind']=='result' for n in graph['nodes']))

 def test_user_pause_is_visible_without_claiming_goal_met(self):
  cfg={'schema':'sdm-event-service/v3','project_id':'fixture','root':str(self.root/'run'),'component_pipeline':{}}
  state={'config_digest':digest(cfg),'status':'running','rounds':[],
   'attempts':[{'id':'initial','stage':'needs-attention','reason':'original-plan:paused'}]}
  self.write(self.root/'run/component-pipeline/pipeline.json',state)
  from task_pipeline import digest as original_digest
  self.write(self.root/'run/DRAINING',{'config_sha256':original_digest(cfg)})
  self.write(self.root/'run/drain-completed.json',{'config_sha256':original_digest(cfg),'pending_jobs':0,'pending_native_context':False,'pipeline_step_active':False})
  result=self.data.study({},cfg,self.root/'run')
  self.assertTrue(result['paused']);self.assertEqual(result['status_label'],'已暂停')
  self.assertFalse(result['completed']);self.assertFalse(result['control']['can_pause'])
  self.assertEqual(result['lineage']['execution_location']['state'],'paused')

if __name__=='__main__':unittest.main()
