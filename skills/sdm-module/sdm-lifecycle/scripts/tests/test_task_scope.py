"""Task intent, evidence and routing distinction. No execution or ML."""
import json
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import research_task_scope as scope
from research_dashboard_data import DashboardData,digest
from review_storage import atomic_json

class TaskScopeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
 def tearDown(self):self.temp.cleanup()
 def begin(self,mode='single-evaluation'):
  return scope.begin(self.root,'scope-test',mode,'离线任务范围展示检查','Synthetic fixture; no evaluation executed')
 def evidence(self):
  r=self.root/'result.json';r.write_text('{"synthetic": true}',encoding='utf-8')
  a=self.root/'analysis.md';a.write_text('Synthetic analysis, not an actual evaluation',encoding='utf-8')
  return r,a
 def test_single_evaluation_finishes_without_creating_a_controller(self):
  self.begin();result,analysis=self.evidence()
  pending=scope.update(self.root,'scope-test',result=result)
  self.assertFalse(pending['analysis_completed'])
  final=scope.update(self.root,'scope-test',analysis=analysis)
  self.assertEqual(final['label'],'单次评测已完成');self.assertFalse(final['iteration_entered'])
  self.assertIn('正常结束',final['reason']);self.assertFalse(final['execution_authority'])
  before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
  snap=DashboardData(self.root).snapshot(final['id']);self.assertEqual(snap['errors'],[])
  self.assertTrue(snap['study']['completed']);self.assertFalse(snap['study']['needs_attention'])
  self.assertFalse(snap['study']['control']['can_resume']);self.assertEqual(DashboardData(self.root).discover(),[])
  self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
 def test_analysis_needs_result_and_scope_cannot_silently_expand(self):
  original=self.begin();r,a=self.evidence()
  with self.assertRaisesRegex(ValueError,'requires_registered_result'):scope.update(self.root,'scope-test',analysis=a)
  with self.assertRaisesRegex(ValueError,'immutable'):self.begin('continuous-research')
  c=self.root/'config.json';atomic_json(c,{'schema':'sdm-event-service/v3','root':str(self.root),'project_id':'demo','component_pipeline':{'version':4}})
  with self.assertRaisesRegex(ValueError,'implicitly'):scope.update(self.root,'scope-test',config=c)
  self.assertEqual(scope.status(self.root,'scope-test')['mode'],original['mode'])
 def test_changed_or_outside_evidence_does_not_claim_completion(self):
  self.begin();r,a=self.evidence();scope.update(self.root,'scope-test',result=r,analysis=a)
  a.write_text('changed',encoding='utf-8')
  with self.assertRaisesRegex(ValueError,'artifact_changed'):scope.status(self.root,'scope-test')
  snap=DashboardData(self.root).snapshot('entry:scope-test');self.assertTrue(snap['study']['needs_attention']);self.assertFalse(snap['study']['completed']);self.assertEqual(len(snap['errors']),1)
  with self.assertRaisesRegex(ValueError,'project_owned'):scope.reference(self.root,Path(__file__))
 def test_continuous_intent_and_analysis_are_not_a_started_loop(self):
  self.begin('continuous-research');r,a=self.evidence()
  v=scope.update(self.root,'scope-test',result=r,analysis=a,pending_reason='scope-unconfirmed')
  self.assertTrue(v['analysis_completed']);self.assertFalse(v['research_started']);self.assertFalse(v['iteration_entered'])
  self.assertIn('执行范围',v['reason']);self.assertFalse(v['automatic_iteration_active'])
  value=DashboardData(self.root).snapshot(v['id'])['study'];self.assertFalse(value['completed']);self.assertFalse(value['needs_attention'])
 def test_bound_research_shares_scope_without_duplicate_task_or_config_mutation(self):
  self.begin('continuous-research');r,a=self.evidence();scope.update(self.root,'scope-test',result=r,analysis=a)
  cfg={'schema':'sdm-event-service/v3','project_id':'scope-test','root':str(self.root/'run'),'continuation_policy':{'version':1},'component_pipeline':{'version':4}}
  config=self.root/'config.json';atomic_json(config,cfg);before=config.read_bytes()
  scope.update(self.root,'scope-test',config=config)
  self.assertIsNone(scope.status(self.root,'scope-test')['research_started'])
  atomic_json(self.root/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','study_id':'scope-test','config':str(config)})
  state={'status':'running','config_digest':digest(cfg),'rounds':[],'attempts':[{'id':'initial','stage':'needs-attention','reason':'original-plan-review:awaiting-proposal-evidence','result_handoff':{'model_requested':True,'job_id':'synthetic-analysis'}}]}
  atomic_json(self.root/'run/component-pipeline/pipeline.json',state)
  with patch('research_progress.probe',return_value={'owner_alive':True,'worker_ready':True,'paused':False}):snap=DashboardData(self.root).snapshot('scope-test')
  self.assertEqual(len(snap['studies']),1);self.assertEqual(snap['errors'],[])
  h=snap['study']['task_handoff'];self.assertTrue(h['analysis_completed']);self.assertTrue(h['iteration_entered'])
  self.assertFalse(h['automatic_iteration_active']);self.assertEqual(h['label'],'持续迭代受阻');self.assertIn('评审',h['reason']);self.assertIn('awaiting-proposal-evidence',h['reason_code'])
  self.assertEqual(config.read_bytes(),before)
  with patch('research_progress.probe',return_value={'owner_alive':True,'worker_ready':True,'paused':False}):redirected=DashboardData(self.root).snapshot('entry:scope-test')
  self.assertEqual(redirected['study']['id'],'scope-test');self.assertEqual(redirected['selection_alias'],{'requested':'entry:scope-test','canonical':'scope-test'})
  self.assertEqual(len(redirected['studies']),1)

 def test_historical_handoff_is_not_current_automatic_progress(self):
  from research_progress import runtime_view
  cfg={'continuation_policy':{'version':1}};state={'status':'running','attempts':[{'stage':'reviewing-plan','result_handoff':{'model_requested':True,'job_id':'known-job'}}]}
  offline=scope.handoff(cfg,state,runtime_view(state,owner_alive=False),True)
  self.assertTrue(offline['iteration_entered']);self.assertFalse(offline['automatic_iteration_active']);self.assertIn('未运行',offline['reason'])
  active=scope.handoff(cfg,state,runtime_view(state,owner_alive=True,worker_ready=True))
  self.assertTrue(active['iteration_entered']);self.assertTrue(active['automatic_iteration_active']);self.assertFalse(active['analysis_completed'])
  unknown=scope.handoff({},state,runtime_view(state,owner_alive=True,worker_ready=True))
  self.assertEqual(unknown['mode'],'unconfirmed');self.assertIsNone(unknown['iteration_entered']);self.assertFalse(unknown['automatic_iteration_active'])

class ScopeBoundaryTests(unittest.TestCase):
 setUp=TaskScopeTests.setUp
 tearDown=TaskScopeTests.tearDown
 begin=TaskScopeTests.begin
 evidence=TaskScopeTests.evidence
 def test_raw_progress_cannot_relabel_an_evaluation_as_research(self):
  self.begin();atomic_json(self.root/'.bmad-ml/task-intents/scope-test/progress.json',{'config':{'path':'elsewhere','sha256':'bad'},'study_id':'study'})
  with self.assertRaisesRegex(ValueError,'implicitly'):scope.status(self.root,'scope-test')
  self.assertEqual(len(DashboardData(self.root).snapshot()['errors']),1)
 def test_existing_analysis_report_is_not_a_successor_dispatch(self):
  from research_progress import runtime_view
  r,a=self.evidence();state={'status':'running','attempts':[{'stage':'result-ready','analysis':scope.reference(self.root,a)}]}
  completed=scope.analysis_available(state);self.assertTrue(completed)
  h=scope.handoff({'continuation_policy':{'version':1}},state,runtime_view(state,owner_alive=True,worker_ready=True),completed)
  self.assertTrue(h['analysis_completed']);self.assertFalse(h['iteration_entered']);self.assertIn('等待分析接续',h['label'])
  a.write_text('changed',encoding='utf-8');self.assertIsNone(scope.analysis_available(state))
 def test_real_entry_cli_records_scope_without_research_initialization(self):
  import subprocess
  script=Path(__file__).resolve().parents[5]/'skills/researchpilot/scripts/researchpilot.py'
  call=subprocess.run([sys.executable,'-X','utf8','-B',str(script),'task','--project-root',str(self.root),'--operation','declare','--task-id','entry-test','--mode','single-evaluation','--objective','Synthetic scope CLI test','--source-ref','Offline test only'],capture_output=True,text=True,encoding='utf-8',timeout=30)
  self.assertEqual(call.returncode,0,call.stderr);value=json.loads(call.stdout);self.assertEqual(value['mode'],'single-evaluation')
  self.assertFalse(value['research_started']);self.assertFalse((self.root/'.research').exists())

if __name__=='__main__':unittest.main()
