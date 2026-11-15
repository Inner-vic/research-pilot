"""Pause/reconciliation contract checks; no real model, platform or ML workload."""
import sys,threading,uuid,tempfile,unittest,sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from review_storage import atomic_json,read_json
from task_pipeline import digest
import research_pause

class CheckedResumeTests(unittest.TestCase):
 def db(self,root):
  from research_event_service import ClosingConnection
  path=root/'fixture.sqlite'
  with sqlite3.connect(path,factory=ClosingConnection) as conn:conn.execute('create table if not exists jobs(id text,status text,updated real)')
  return lambda:sqlite3.connect(path,factory=ClosingConnection)

 def case(self):
  from test_proposal_model_runner import ProposalRunnerTests
  case=ProposalRunnerTests('runTest');case.setUp();self.addCleanup(case.doCleanups);return case
 def test_pause_during_completed_call_recovers_without_new_proposer(self):
  case=self.case();case.pause_in_model=True
  self.assertEqual(case.gate()['status'],'proposal-model-blocked');self.assertEqual(len(case.calls),1)
  original=next(case.root.glob('proposal-review/*/model-calls/*/request.json')).read_bytes()
  from review_resume import resume
  identity=str(uuid.uuid4());receipt=resume(case.root,identity)
  self.assertEqual(receipt,resume(case.root,identity));case.pause_in_model=False
  self.assertEqual(case.gate()['status'],'proposal-approved')
  self.assertEqual([row[0] for row in case.calls],['a','b'])
  self.assertEqual(next(case.root.glob('proposal-review/*/model-calls/*/request.json')).read_bytes(),original)
 def test_unknown_original_call_cannot_resume_or_reissue(self):
  case=self.case();case.unknown=True;case.gate()
  from research_worker import control
  from review_resume import resume
  control(case.root,True)
  with self.assertRaises((ValueError,OSError)):resume(case.root,str(uuid.uuid4()))
  self.assertTrue(control(case.root)['paused']);self.assertEqual(len(case.calls),1)
 def test_resume_proof_rejects_changed_original_bytes(self):
  case=self.case();case.pause_in_model=True;case.gate()
  from review_resume import resume,authorized
  receipt=resume(case.root,str(uuid.uuid4()))
  request=next(case.root.glob('proposal-review/*/model-calls/*/request.json'));request.write_bytes(request.read_bytes()+b' ')
  with self.assertRaisesRegex(ValueError,'bytes_changed'):authorized(case.root,request.parent,receipt['after']['revision'])
 def test_same_host_resume_is_bound_idempotent_and_does_not_start_a_host(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);cfg={'root':str(root),'project_id':'fixture','component_pipeline':{'version':4}}
   inbox=SimpleNamespace(root=root,cfg=cfg,condition=threading.Condition(threading.RLock()),db=self.db(root))
   atomic_json(root/'DRAINING',{'config_sha256':digest(cfg)});atomic_json(root/'PAUSED',{'at':1})
   atomic_json(root/'component-pipeline/pipeline.json',{'status':'running','attempts':[{'stage':'watching'}]})
   generation=research_pause.generation(root);identity=str(uuid.uuid4())
   with patch('research_drain.status',return_value={'requested':True,'quiescent':True}),patch('research_recovery.inspect',return_value={'can_restart_host':True}):
    with self.assertRaisesRegex(ValueError,'generation_changed'):research_pause.resume(inbox,'old',identity)
    self.assertEqual(research_pause.resume(inbox,generation,identity)['status'],'resume-applied')
    self.assertTrue(research_pause.resume(inbox,generation,identity)['duplicate'])
   self.assertFalse((root/'PAUSED').exists());self.assertFalse((root/'DRAINING').exists())
   self.assertEqual(read_json(root/'pipeline-resume.json')['id'],identity)
 def test_resume_requeues_the_original_review_once_through_pipeline(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);review=root/'component-pipeline/attempt/replan';review.mkdir(parents=True)
   project=review.parent/'replan-project.json';atomic_json(project,{'fixture':'original review'})
   atomic_json(review/'research-control.json',{'revision':1,'paused':True})
   cfg={'root':str(root),'project_id':'fixture','component_pipeline':{'version':4}};inbox=SimpleNamespace(root=root,cfg=cfg,condition=threading.Condition(threading.RLock()),db=self.db(root))
   current={'stage':'needs-attention','job':'original-job','reason':'original-plan-review:proposal-model-blocked','replan_root':str(review)}
   atomic_json(root/'component-pipeline/pipeline.json',{'status':'running','attempts':[current]})
   atomic_json(root/'DRAINING',{'config_sha256':digest(cfg)});atomic_json(root/'PAUSED',{'at':1})
   with patch('research_drain.status',return_value={'requested':True,'quiescent':True}),patch('research_recovery.inspect',return_value={'can_restart_host':True}):
    research_pause.resume(inbox,research_pause.generation(root),str(uuid.uuid4()))
   from research_continuation import take_resume
   calls=[]
   def enqueue(label,template,data):calls.append(data);return 'same-context-coordinator-job'
   owner=SimpleNamespace(spec={'coordinator_template':'original'},enqueue=enqueue)
   take_resume(inbox,current,owner);take_resume(inbox,current,owner)
   self.assertEqual(len(calls),1);self.assertEqual(calls[0]['output'],str(review))
   self.assertEqual(Path(calls[0]['project']['path']).resolve(),project.resolve());self.assertEqual(current['stage'],'reviewing-plan')

 def test_unexpected_clean_host_exit_recovers_but_no_progress_does_not_spin(self):
  import research_supervisor as supervisor
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);cfg={'root':str(root),'runtime_root':str(Path(supervisor.__file__).parent)}
   path=root/'service.json';atomic_json(path,cfg)
   report={'can_restart_host':True,'progress_fingerprint':'unchanged'}
   child=SimpleNamespace(pid=123,wait=lambda:0)
   with patch('research_event_service.configuration',side_effect=lambda v:v),patch('research_runtime.owner_alive',return_value=False),patch('model_containment.WindowsJob') as job,patch.object(supervisor,'inspect',return_value=report),patch.object(supervisor.subprocess,'Popen',return_value=child):
    result=supervisor.run(path)
   self.assertEqual(result['status'],'stalled-no-progress');self.assertEqual(len(result['generations']),2)
   self.assertEqual(job.return_value.close.call_count,2)

 def test_stopped_old_freeze_has_no_web_resume(self):
  from dashboard_control import state
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);cfg={'root':str(root)}
   atomic_json(root/'host-stop.json',{'config_sha256':digest(cfg)})
   atomic_json(root/'drain-completed.json',{'config_sha256':digest(cfg),'pending_jobs':0,'pending_native_context':False,'pipeline_step_active':False})
   result=state(cfg,root);self.assertEqual(result['phase'],'paused');self.assertFalse(result['can_resume']);self.assertIn('Codex',result['resume_reason'])

if __name__=='__main__':unittest.main()
