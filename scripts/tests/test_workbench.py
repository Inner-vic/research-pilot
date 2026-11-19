"""Real read-only view startup/reuse and live record delivery; no research/ML."""
import hashlib,http.client,importlib.util,json,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
ROOT=Path(__file__).resolve().parents[2]
sp=importlib.util.spec_from_file_location('workbench_entry',ROOT/'skills/researchpilot/scripts/researchpilot.py')
entry=importlib.util.module_from_spec(sp);sp.loader.exec_module(entry)
sys.path.insert(0,str(ROOT/'skills/sdm-module/sdm-lifecycle/scripts'))
from review_storage import atomic_json
from research_dashboard_data import digest
import research_workbench

class WorkbenchTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(prefix='rp-view-');self.project=Path(self.temp.name);self.children=[]
 def tearDown(self):
  for child in self.children:
   if child.poll() is None:child.terminate()
   child.wait(timeout=10)
  self.temp.cleanup()
 def write(self,path,value):
  atomic_json(path,value);return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 def register_result(self):
  root=self.project/'research';cfg={'schema':'sdm-event-service/v3','project_id':'view-fixture','root':str(root),
   'component_pipeline':{'objective':'Synthetic UI fixture; never a training result','decision_policy':{'target':{'metric':'validation.mean_iou','operator':'>=','value':.9}}}}
  config=self.project/'service.json';self.write(config,cfg)
  result=self.write(root/'artifacts/result.json',{'validation':{'mean_iou':.75},'config':{'epochs':10}})
  ready=self.write(root/'receipt.json',{'schema':'sdm-project-result-ready/v1','valid_training_round':True,'uid':'synthetic-view-only',
   'metric_name':'validation.mean_iou','primary_metric':.75,'artifact_root':str(root/'artifacts'),'artifact_sha256':{'result.json':result['sha256']}})
  self.write(root/'component-pipeline/pipeline.json',{'config_digest':digest(cfg),'status':'research-stopped','rounds':[{'result':ready}],
   'attempts':[{'stage':'research-stopped','id':'first'}]})
  self.write(self.project/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','study_id':'view-fixture','root':str(self.project),'config':str(config)})
 def event(self,response):
  lines=[]
  while True:
   line=response.fp.readline().decode().strip()
   if not line:break
   lines.append(line)
  return json.loads(next(line[6:] for line in lines if line.startswith('data: ')))
 def test_public_entry_reuses_one_view_and_sse_delivers_registered_result(self):
  real=subprocess.Popen
  def launch(*args,**kwargs):
   child=real(*args,**kwargs);self.children.append(child);return child
  with patch.object(research_workbench.subprocess,'Popen',side_effect=launch):
   first=entry.workbench(self.project);second=entry.workbench(self.project)
   self.assertEqual(len(self.children),1);self.assertEqual(first['pid'],second['pid'])
   self.assertFalse(first['ui_opened']);self.assertTrue(first['ui_open_required']);self.assertFalse(first['research_started'])
   self.assertEqual(first['open_target']['type'],'browser')
   url=urlsplit(first['open_target']['url']);headers={'Authorization':'Bearer '+parse_qs(url.fragment)['token'][0]}
   connection=http.client.HTTPConnection(url.hostname,url.port,timeout=15)
   try:
    connection.request('GET','/api/events',headers=headers);response=connection.getresponse();self.assertEqual(response.status,200)
    self.assertEqual(self.event(response)['studies'],[])
    self.register_result();value=self.event(response)
    self.assertEqual(value['study']['round_count'],1);self.assertFalse(value['errors'])
    row=value['study']['rounds'][0];self.assertEqual(row['change_label'],'初始实验')
    self.assertIn('探索起点',row['zh']['comparison']);self.assertIn('均值 IoU',row['zh']['comparison']);self.assertNotIn('宏平均 F1',row['zh']['comparison'])
    results=[n for n in value['study']['lineage']['nodes'] if n['kind']=='result'];self.assertIn('均值 IoU',results[0]['title'])
    selected=entry.workbench(self.project,'view-fixture');self.assertEqual(parse_qs(urlsplit(selected['open_target']['url']).query)['study'],['view-fixture'])
    self.assertEqual(len(self.children),1)
   finally:connection.close()
 def test_registered_pending_study_can_be_opened_without_starting_research(self):
  root=self.project/'pending-store';cfg={'schema':'sdm-event-service/v3','project_id':'pending-view','root':str(root),'component_pipeline':{}}
  config=self.project/'pending.json';self.write(config,cfg)
  self.write(self.project/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','study_id':'pending-view','config':str(config)})
  with patch.object(research_workbench,'ensure',return_value={'url':'http://127.0.0.1:1234/#token=synthetic','status':'already-running'}):
   value=entry.workbench(self.project,'pending-view')
  self.assertEqual(parse_qs(urlsplit(value['open_target']['url']).query)['study'],['pending-view'])
  self.assertFalse(root.exists());self.assertFalse(value['research_started'])
 def test_unknown_study_is_rejected_before_starting_service(self):
  with patch.object(research_workbench.subprocess,'Popen') as process:
   with self.assertRaises(KeyError):entry.workbench(self.project,'another-project')
   process.assert_not_called()

if __name__=='__main__':unittest.main()
