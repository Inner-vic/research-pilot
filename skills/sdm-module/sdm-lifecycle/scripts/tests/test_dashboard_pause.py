"""Pause-only boundary, no model or platform execution."""
import hashlib,json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard_control import pause,state,run_public
from task_pipeline import digest
from review_storage import atomic_json

class PauseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=self.root/'run';self.runtime=self.root/'runtime';self.runtime.mkdir()
  self.cfg={'schema':'sdm-event-service/v3','root':str(self.store),'runtime_root':str(self.runtime),'project_id':'fixture','component_pipeline':{}}
  self.path=self.root/'service.json';atomic_json(self.path,self.cfg)
  atomic_json(self.root/'.bmad-ml/CURRENT.json',{'schema':'bmad-ml-active-study/v1','config':str(self.path)})
  atomic_json(self.store/'component-pipeline/pipeline.json',{'config_digest':digest(self.cfg),'status':'running','attempts':[{'id':'initial','stage':'watching'}]})
  self.request={'study_id':'fixture','config_sha256':digest(self.cfg),'request_id':str(uuid.uuid4())}
 def tearDown(self):self.tmp.cleanup()
 def test_original_control_delegation_and_duplicate(self):
  def apply(cfg,script,args):
   self.assertEqual(script,'research_event_service.py');action=args[-1]
   if action=='drain':atomic_json(self.store/'DRAINING',{'config_sha256':digest(cfg)})
   if action=='pause':atomic_json(self.store/'PAUSED',{'at':1})
   return {'status':'draining'}
  with patch('dashboard_control.host',return_value={'drain':{'quiescent':True}}),patch('dashboard_control.run_public',side_effect=apply) as control:
   result=pause(self.root,self.request);self.assertEqual(result['status'],'paused');self.assertEqual(control.call_count,2)
   self.assertTrue(pause(self.root,self.request)['duplicate']);self.assertEqual(control.call_count,2)
 def test_stale_or_extra_scope_rejected_before_controls(self):
  with patch('dashboard_control.run_public') as control:
   with self.assertRaisesRegex(ValueError,'version_changed'):pause(self.root,{**self.request,'config_sha256':'stale'})
   with self.assertRaisesRegex(ValueError,'exact_pause'):pause(self.root,{**self.request,'resume':True})
   control.assert_not_called()
 def test_unknown_pause_intent_is_not_replayed(self):
  atomic_json(self.root/'.bmad-ml/dashboard/controls'/(self.request['request_id']+'.json'),{'request':self.request,'phase':'intent'})
  with patch('dashboard_control.run_public') as control:
   with self.assertRaisesRegex(ValueError,'reconciliation'):pause(self.root,self.request)
   control.assert_not_called()
 def test_actual_local_health_accepts_the_original_unprefixed_config_digest(self):
  import threading
  from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
  from dashboard_control import host
  expected={'pid':123,'project_id':'fixture','config_sha256':digest(self.cfg).removeprefix('sha256:')}
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(handler):
    if handler.headers.get('Authorization')!='Bearer fixture-token':handler.send_error(403);return
    raw=json.dumps(expected).encode();handler.send_response(200);handler.send_header('Content-Length',str(len(raw)));handler.end_headers();handler.wfile.write(raw)
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   atomic_json(self.store/'ready.json',{**expected,'port':server.server_port,'token':'fixture-token'})
   self.assertEqual(host(self.cfg),expected)
  finally:server.shutdown();server.server_close();thread.join()
 def test_checked_resume_bridge_uses_same_config_and_does_not_repeat(self):
  from dashboard_control import resume
  self.cfg['runtime_manifest']={'research_pause.py':'fixture'};atomic_json(self.path,self.cfg)
  atomic_json(self.store/'component-pipeline/pipeline.json',{'config_digest':digest(self.cfg),'status':'running','attempts':[{'id':'initial','stage':'watching'}]})
  atomic_json(self.store/'DRAINING',{'config_sha256':digest(self.cfg)});atomic_json(self.store/'PAUSED',{'at':1})
  health={'paused':True,'drain':{'quiescent':True}}
  with patch('dashboard_control.host',return_value=health):
   before=state(self.cfg,self.store);self.assertTrue(before['can_resume'])
   request={**self.request,'config_sha256':digest(self.cfg),'pause_generation':before['pause_generation']}
   def apply(cfg,script,args):
    self.assertEqual(script,'research_event_service.py');self.assertIn('--expected-pause',args)
    (self.store/'DRAINING').unlink();(self.store/'PAUSED').unlink();health['paused']=False
    return {'status':'resume-applied'}
   with patch('dashboard_control.run_public',side_effect=apply) as call:
    self.assertEqual(resume(self.root,request)['status'],'resume-applied')
    self.assertTrue(resume(self.root,request)['duplicate']);self.assertEqual(call.call_count,1)

 def test_unbound_script_cannot_be_executed(self):
  file=self.runtime/'research_event_service.py';file.write_text('raise RuntimeError("never executed")')
  self.cfg['runtime_manifest']={'research_event_service.py':'wrong'}
  with self.assertRaisesRegex(ValueError,'script_changed'):run_public(self.cfg,file.name,[])

if __name__=='__main__':unittest.main()
