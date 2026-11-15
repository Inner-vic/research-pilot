"""Real localhost/process restoration, with no registered model or platform workload."""
import hashlib,json,sqlite3,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from review_storage import atomic_json,read_json
from task_pipeline import digest
import research_restore as restore

class HostRestoreTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.project=Path(self.temp.name).resolve()
  from research_runtime import freeze
  manifest=freeze(self.project/'runtime');self.root=self.project/'service';self.cwd=self.project/'workspace';self.cwd.mkdir()
  self.cfg={'schema':'sdm-event-service/v3','project_id':'control-only-no-ml','root':str(self.root),'cwd':str(self.cwd),
   'runtime_root':manifest['runtime_root'],'runtime_manifest':manifest['runtime_manifest'],'guidance_manifest':manifest['guidance_manifest'],
   'sdk_root':str(self.project),'cli_path':sys.executable,'cli_sha256':hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
   'codex_home':str(self.project),'model':'never-called-control-fixture','timeout_seconds':30,'sandbox':'read-only',
   'task_templates':{'unused':{'kind':'report','instructions':'Never dispatched. No model, platform, ML or network work.','evidence_kind':'control-fixture'}},
   'research_context':{'version':1,'root':str(self.project/'context')},'tasks':{'held':{'kind':'coordinator','instructions':'Local control fixture only; never model/platform work.','evidence_kind':'control-fixture'}}}
  self.config=self.project/'service.json';atomic_json(self.config,self.cfg)
  from research_event_service import Inbox
  Inbox(self.cfg)
  from research_context import Context
  owner=Context(self.cfg);value=owner.load();value.update(phase='ready',thread_id='preserved-no-provider-thread');atomic_json(owner.path,value)
  atomic_json(self.root/'component-pipeline/pipeline.json',{'config_digest':digest(self.cfg),'status':'running','rounds':[],
   'attempts':[{'id':'initial','stage':'watching'}]})
  for name in ('DRAINING','STOP_AFTER_DRAIN','host-stop.json'):atomic_json(self.root/name,{'config_sha256':digest(self.cfg),'at':1})
  atomic_json(self.root/'PAUSED',{'at':1})
  atomic_json(self.root/'drain-completed.json',{'config_sha256':digest(self.cfg),'pending_jobs':0,'pending_native_context':False,'pipeline_step_active':False})
  atomic_json(self.project/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','config':str(self.config)})
  from research_dashboard import Server
  self.server=Server(self.project);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.addCleanup(self.close)
 def close(self):
  try:restore.control(self.cfg,self.config,'stop')
  except Exception:pass
  from research_runtime import owner_alive
  deadline=time.monotonic()+15
  while owner_alive(self.cfg) and time.monotonic()<deadline:time.sleep(.1)
  self.server.shutdown();self.server.server_close();self.thread.join()
 def post(self,action,request):
  with urlopen(Request(f'http://127.0.0.1:{self.server.server_port}/api/study/'+action,
   data=json.dumps(request).encode(),headers={'Authorization':'Bearer '+self.server.token,'Content-Type':'application/json'}),timeout=10) as r:
   return r.status,json.load(r)
 def request(self):
  from research_pause import generation
  return {'study_id':self.cfg['project_id'],'config_sha256':digest(self.cfg),'pause_generation':generation(self.root),'request_id':str(uuid.uuid4())}
 def wait(self,identity):
  path=self.project/'.bmad-ml/restores'/identity/'state.json';deadline=time.monotonic()+35
  while time.monotonic()<deadline:
   value=read_json(path)
   if value['phase'] in restore.TERMINAL:return value
   time.sleep(.1)
  self.fail('restore did not settle: '+str(read_json(path)))
 def test_web_restores_dead_host_and_preserves_context_without_chat_or_model(self):
  request=self.request();status,result=self.post('resume',request);self.assertEqual(status,202)
  self.server.shutdown();self.server.server_close();self.thread.join() # Web service disappears during restore.
  value=self.wait(request['request_id']);self.assertEqual(value['phase'],'resumed',value.get('reason'))
  from dashboard_control import host
  live=host(self.cfg);self.assertFalse(live['paused']);self.assertFalse(live['drain']['requested'])
  self.assertEqual(read_json(self.project/'context/context.json')['thread_id'],'preserved-no-provider-thread')
  self.assertEqual(read_json(self.root/'supervision.json')['generations'].__len__(),1)
  from research_dashboard import Server
  self.server=Server(self.project);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.assertTrue(self.post('resume',request)[1]['duplicate'])
  self.assertEqual(read_json(self.root/'supervision.json')['generations'].__len__(),1)
  with __import__('contextlib').closing(sqlite3.connect(self.root/'delivery.sqlite')) as db:self.assertEqual(db.execute('select count(*) from jobs').fetchone()[0],0)
 def test_cancellation_before_start_leaves_study_stopped(self):
  request=self.request()
  # The actual Web handler queues the worker; cancellation marker is durable even before worker startup.
  self.post('resume',request);restore.cancel(self.cfg)
  value=self.wait(request['request_id']);self.assertEqual(value['phase'],'cancelled',value.get('reason'))
  self.assertTrue((self.root/'PAUSED').exists())
  from research_runtime import owner_alive
  self.assertFalse(owner_alive(self.cfg))

 def test_cancel_during_startup_never_unpauses_the_host(self):
  request=self.request();self.post('resume',request)
  folder=self.project/'.bmad-ml/restores'/request['request_id'];deadline=time.monotonic()+20
  while not (folder/'supervisor-launch.json').exists() and time.monotonic()<deadline:time.sleep(.02)
  restore.cancel(self.cfg);value=self.wait(request['request_id'])
  self.assertEqual(value['phase'],'cancelled',value.get('reason'))
  self.assertFalse((self.root/'pipeline-resume.json').exists());self.assertTrue((self.root/'PAUSED').exists())
 def test_unknown_original_native_turn_blocks_before_any_launch(self):
  path=self.project/'context/context.json';context=read_json(path);context['pending_job']='unknown-original';context['pending_turn']='unknown-turn';atomic_json(path,context)
  request=self.request();self.post('resume',request);value=self.wait(request['request_id'])
  self.assertEqual(value['phase'],'blocked');self.assertIn('reconciliation',value['reason'])
  self.assertFalse((self.project/'.bmad-ml/restores'/request['request_id']/'supervisor-launch.json').exists())
  self.assertEqual(read_json(path)['pending_turn'],'unknown-turn');self.assertTrue((self.root/'host-stop.json').exists())

 def test_queued_work_pauses_and_resumes_with_the_same_identity(self):
  from research_event_service import Inbox,digest as event_digest
  from research_drain import pause,status
  from research_pause import generation,resume
  inbox=Inbox(self.cfg)
  for name in ('DRAINING','PAUSED','host-stop.json','STOP_AFTER_DRAIN'):(self.root/name).unlink(missing_ok=True)
  event_state={'binding':'held','phase':'exited'}
  accepted=inbox.accept({'schema':'sdm-completion-event/v1','binding':'held','phase':'exited','state':event_state,'state_sha256':event_digest(event_state)})
  atomic_json(self.root/'DRAINING',{'config_sha256':digest(self.cfg)});pause(inbox)
  self.assertTrue(status(inbox)['quiescent']);self.assertEqual(inbox.snapshot()['jobs'][0]['status'],'paused')
  calls=[]
  def execute(*args):calls.append(args[1]['id']);return {'status':'completed','detail':{'kind':'coordinator-only'}}
  worker=threading.Thread(target=inbox.work,args=(execute,),daemon=True);worker.start()
  try:
   resume(inbox,generation(self.root),str(uuid.uuid4()));deadline=time.monotonic()+5
   while inbox.snapshot()['jobs'][0]['status']!='completed' and time.monotonic()<deadline:time.sleep(.02)
   self.assertEqual(calls,[accepted['id']]);self.assertEqual(inbox.snapshot()['jobs'][0]['id'],accepted['id'])
  finally:
   inbox.done.set()
   with inbox.condition:inbox.condition.notify_all()
   worker.join(timeout=5)
 def test_global_host_pause_blocks_internal_review_calls(self):
  from research_worker import check_control
  root=self.root/'component-pipeline/attempt/replan';root.mkdir(parents=True,exist_ok=True)
  atomic_json(self.root/'component-pipeline/host-binding.json',{'configuration_digest':digest(self.cfg)})
  with self.assertRaisesRegex(ValueError,'research_host_paused'):check_control(root,0)

if __name__=='__main__':unittest.main()
