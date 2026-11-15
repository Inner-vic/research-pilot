"""Idempotent local workbench startup; never starts a study or model call."""
import argparse,importlib.util,json,os,socket,subprocess,sys,time
from pathlib import Path

sys.dont_write_bytecode=True
from urllib.request import urlopen


def ensure(project,skill,python=None,port=8792):
 project=Path(project).resolve();skill=Path(skill).resolve();scripts=skill/'references/sdm-module/sdm-lifecycle/scripts'
 if not scripts.is_dir():scripts=skill.parent/'sdm-module/sdm-lifecycle/scripts'
 sys.path.insert(0,str(scripts))
 from review_storage import locked
 folder=project/'.bmad-ml/dashboard';folder.mkdir(parents=True,exist_ok=True);ready=folder/'ready.json'
 def live():
  try:
   r=json.loads(ready.read_bytes())
   if Path(r['project_root']).resolve()!=project or not r.get('data_read_only'):return None
   base=r['url'].split('/#')[0]
   with urlopen(base+'/health',timeout=1) as response:h=json.load(response)
   if h.get('service')=='sdm-research-dashboard' and h.get('instance')==r['instance'] and h.get('pid')==r['pid']:return r
  except (OSError,ValueError,KeyError):pass
  return None
 with locked(folder,'.startup.lock','dashboard_startup_in_progress'):
  existing=live()
  if existing:return {'status':'already-running',**existing,'model_calls':0,'platform_writes':0}
  try:
   with socket.socket() as probe:probe.bind(('127.0.0.1',port))
  except OSError:port=0 # Preserve unrelated applications; never kill by port number.
  launched=time.time()
  with (folder/'stdout.log').open('ab') as out,(folder/'stderr.log').open('ab') as err:
   child=subprocess.Popen([str(python or sys.executable),'-B',str(scripts/'research_dashboard.py'),'--project-root',str(project),'--port',str(port)],
    stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=os.name!='nt',
    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
  deadline=time.monotonic()+20
  while time.monotonic()<deadline:
   found=live()
   if found and ready.stat().st_mtime>=launched-1:return {'status':'started',**found,'model_calls':0,'platform_writes':0}
   if child.poll() is not None:raise ValueError('dashboard_start_failed: inspect .bmad-ml/dashboard/stderr.log')
   time.sleep(.1)
  raise ValueError('dashboard_readiness_unknown: inspect existing process; do not blindly relaunch')

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project-root',required=True);p.add_argument('--skill',required=True);p.add_argument('--python');p.add_argument('--port',type=int,default=8792)
 args=vars(p.parse_args());args["project"]=args.pop("project_root")
 print(json.dumps(ensure(**args),ensure_ascii=False))
