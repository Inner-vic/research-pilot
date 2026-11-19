"""Boundary regressions for scope review. Synthetic files; no live execution."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[5]
L=SOURCE/'skills/sdm-module/sdm-lifecycle'
sys.path.insert(0,str(L/'scripts'))
import research_task_scope as scope
from research_dashboard_data import DashboardData,digest
from research_progress import runtime_view
from review_storage import atomic_json,native_path

class ScopeAuditTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
  self.result=self.root/'result.json';self.result.write_text('{"synthetic":true}',encoding='utf-8')
  self.analysis=self.root/'analysis.md';self.analysis.write_text('Synthetic analysis only.',encoding='utf-8')
 def tearDown(self):
  import shutil
  target=Path(self.temp.name).resolve()
  assert target==self.root and target.parent==Path(tempfile.gettempdir()).resolve() and target.name.startswith('tmp')
  shutil.rmtree(native_path(target));self.temp.cleanup()
 def begin(self,task='audit-task',mode='continuous-research'):
  return scope.begin(self.root,task,mode,'离线范围审查','Synthetic offline fixture')
 def config(self):
  cfg={'schema':'sdm-event-service/v3','project_id':'audit-research','root':str(self.root/'run'),
   'continuation_policy':{'version':1},'component_pipeline':{'version':4}}
  path=self.root/'config.json';atomic_json(path,cfg);return cfg,path
 def test_failed_update_preserves_original_progress(self):
  self.begin();scope.update(self.root,'audit-task',result=self.result)
  path=self.root/'.bmad-ml/task-intents/audit-task/progress.json';before=path.read_bytes()
  self.result.write_text('{"changed":true}',encoding='utf-8')
  with self.assertRaises(ValueError):scope.update(self.root,'audit-task',analysis=self.analysis)
  self.assertEqual(path.read_bytes(),before,'a rejected update must not change the record')
 def test_new_training_keeps_the_previous_result_analysis(self):
  before={'id':'initial','stage':'submitting','result':scope.reference(self.root,self.result),
   'analysis':scope.reference(self.root,self.analysis),'result_handoff':{'model_requested':True,'job_id':'synthetic-job'}}
  state={'status':'running','rounds':[{'result':before['result']}],'attempts':[before,{'id':'attempt-1','stage':'watching'}]}
  self.assertTrue(scope.analysis_available(state),'starting the successor must not erase the prior analysis status')
  h=scope.handoff({'continuation_policy':{'version':1}},state,runtime_view(state,owner_alive=True,worker_ready=True),scope.analysis_available(state))
  self.assertTrue(h['analysis_completed']);self.assertTrue(h['iteration_entered'])
  latest=self.root/'latest.json';latest.write_text('{"new_result":true}',encoding='utf-8')
  state['attempts'][-1]['result']=scope.reference(self.root,latest)
  self.assertFalse(scope.analysis_available(state),'a new unanalysed result must not inherit the previous analysis')
  h=scope.handoff({'continuation_policy':{'version':1}},state,runtime_view(state,owner_alive=True,worker_ready=True),False,{'mode':'continuous-research','result_received':True,'analysis_completed':True})
  self.assertFalse(h['analysis_completed'],'entry evidence must not complete a newer pipeline result')
 def test_corrupt_selected_task_stays_visible_as_unverified(self):
  self.begin(mode='single-evaluation');scope.update(self.root,'audit-task',result=self.result,analysis=self.analysis)
  self.analysis.write_text('changed',encoding='utf-8')
  snap=DashboardData(self.root).snapshot('entry:audit-task')
  self.assertTrue(snap['errors']);self.assertTrue(snap['study']['needs_attention'])
  self.assertFalse(snap['study']['completed']);self.assertIsNone(snap['study']['task_handoff']['analysis_completed'])
  self.assertEqual(snap['study']['title'],'离线范围审查');self.assertEqual(snap['study']['task_mode'],'single-evaluation')
 def test_second_scope_cannot_take_over_the_same_configuration(self):
  self.begin('first-task');self.begin('second-task');cfg,path=self.config()
  scope.update(self.root,'first-task',config=path)
  with self.assertRaisesRegex(ValueError,'already_bound'):scope.update(self.root,'second-task',config=path)
  self.assertIsNone(scope.status(self.root,'second-task')['config'])
 def test_legacy_duplicate_bindings_are_explicit_conflicts(self):
  self.begin('first-task');self.begin('second-task');cfg,path=self.config();scope.update(self.root,'first-task',config=path)
  first=json.loads((self.root/'.bmad-ml/task-intents/first-task/progress.json').read_bytes())
  atomic_json(self.root/'.bmad-ml/task-intents/second-task/progress.json',first)
  atomic_json(self.root/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','study_id':cfg['project_id'],'config':str(path)})
  atomic_json(self.root/'run/component-pipeline/pipeline.json',{'config_digest':digest(cfg),'status':'research-stopped','rounds':[],'attempts':[{'id':'initial','stage':'research-stopped'}]})
  snap=DashboardData(self.root).snapshot()
  self.assertTrue(snap['errors'],'conflicting task ownership must not silently select the last record')
  tasks=[s for s in snap['studies'] if s['id'].startswith('entry:')]
  self.assertEqual(len(tasks),2);self.assertTrue(all(s['needs_attention'] for s in tasks))
 def test_invalid_config_does_not_commit_progress(self):
  self.begin();path=self.root/'invalid.json';atomic_json(path,[])
  with self.assertRaises(ValueError):scope.update(self.root,'audit-task',config=path)
  self.assertFalse((self.root/'.bmad-ml/task-intents/audit-task/progress.json').exists())
 def test_control_metadata_cannot_be_bound_as_its_own_evidence(self):
  self.begin()
  with self.assertRaisesRegex(ValueError,'control_record'):scope.update(self.root,'audit-task',result=self.root/'.bmad-ml/task-intents/audit-task/intent.json')
  self.assertFalse((self.root/'.bmad-ml/task-intents/audit-task/progress.json').exists())
 @unittest.skipUnless(os.name=='nt','Windows native path boundary')
 def test_long_task_record_path_uses_native_io(self):
  project=self.root/('p'*70)/('q'*70);native_path(project).mkdir(parents=True)
  task='scope-'+('a'*70)
  self.assertGreater(len(str(project/'.bmad-ml/task-intents'/task/'intent.json')),260)
  value=scope.begin(project,task,'single-evaluation','Synthetic deep-path scope','Offline fixture')
  self.assertEqual(value['mode'],'single-evaluation')
  report=project/'report.md';native_path(report).write_text('Synthetic report.',encoding='utf-8')
  self.assertTrue(scope.update(project,task,result=report,analysis=report)['analysis_completed'])
 def test_cli_json_is_utf8_even_with_legacy_console_defaults(self):
  env=os.environ.copy();env['PYTHONIOENCODING']='cp936'
  script=SOURCE/'skills/researchpilot/scripts/researchpilot.py'
  p=subprocess.run([sys.executable,'-B',str(script),'task','--project-root',str(self.root),'--operation','declare',
   '--task-id','encoding-task','--mode','single-evaluation','--objective','中文任务','--source-ref','离线验收'],capture_output=True,env=env,timeout=30)
  self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'))
  value=json.loads(p.stdout.decode('utf-8'));self.assertEqual(value['objective'],'中文任务')

if __name__=='__main__':unittest.main()
