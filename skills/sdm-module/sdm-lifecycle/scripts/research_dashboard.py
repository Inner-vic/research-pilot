"""Serve research records and delegate bound user controls to the original host."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
from research_dashboard_data import DashboardData
from review_storage import atomic_json,native_path

ASSETS=Path(__file__).resolve().parents[1]/'references/dashboard'
FILES={'/':('index.html','text/html; charset=utf-8'),
 '/assets/dashboard.css':('dashboard.css','text/css; charset=utf-8'),
 '/assets/theme-bootstrap.js':('theme-bootstrap.js','text/javascript; charset=utf-8'),
 '/assets/lineage-view.js':('lineage-view.js','text/javascript; charset=utf-8'),
 '/assets/dashboard.js':('dashboard.js','text/javascript; charset=utf-8'),
 '/assets/NOTICE.txt':('NOTICE.txt','text/plain; charset=utf-8'),
 '/assets/favicon.svg':('favicon.svg','image/svg+xml'),
 '/assets/exploration-map.js':('exploration-map.js','text/javascript; charset=utf-8'),
 '/assets/exploration-map.css':('exploration-map.css','text/css; charset=utf-8'),
 '/assets/exploration-map.js.LEGAL.txt':('exploration-map.js.LEGAL.txt','text/plain; charset=utf-8'),
 '/assets/exploration-map.NOTICE.txt':('exploration-map.NOTICE.txt','text/plain; charset=utf-8')}


def web_runtime_digest():
 import hashlib
 root=native_path(Path(__file__).resolve().parent)
 files=[*root.glob('*.py'),*(p for p in native_path(ASSETS).rglob('*') if p.is_file())]
 return hashlib.sha256(json.dumps({str(p.relative_to(root.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
     for p in sorted(files)},sort_keys=True).encode()).hexdigest()


class Server(ThreadingHTTPServer):
 daemon_threads=True
 allow_reuse_address=False
 def server_bind(self):
  if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
  super().server_bind()
 def __init__(self,project,port=0):
  self.data=DashboardData(project);self.data.snapshot();self.token=secrets.token_urlsafe(32)
  self.runtime_digest=web_runtime_digest()
  self.instance=secrets.token_hex(12);self.done=threading.Event();self.lock=threading.RLock();self.snapshots={}
  super().__init__(('127.0.0.1',port),Handler)
  self.url=f'http://127.0.0.1:{self.server_port}/#token={self.token}'
 def snapshot(self,study):
  with self.lock:
   cached=self.snapshots.get(study)
   if cached and time.monotonic()-cached[0]<1.5:return cached[1]
   value=self.data.snapshot(study);self.snapshots[study]=(time.monotonic(),value);return value
 def server_close(self):self.done.set();super().server_close()


class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def handle(self):
  try:super().handle()
  except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
 def local(self):
  expected=f'127.0.0.1:{self.server.server_port}'
  return self.headers.get('Host')==expected and self.headers.get('Origin') in (None,'http://'+expected)
 def authorized(self):
  return self.local() and secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token)
 def send_headers(self,kind,length=None,status=200):
  self.send_response(status);self.send_header('Content-Type',kind)
  if length is not None:self.send_header('Content-Length',str(length))
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Referrer-Policy','no-referrer')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
  self.end_headers()
 def reply(self,status,value,kind='application/json; charset=utf-8'):
  raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
  self.send_headers(kind,len(raw),status);self.wfile.write(raw)
 def do_POST(self):
  if self.path not in ('/api/study/pause','/api/study/resume'):self.reply(405,{'error':'read_only_records'});return
  if not self.authorized():self.reply(403,{'error':'dashboard_session_required'});return
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=4096 or self.headers.get('Content-Type')!='application/json':raise ValueError('bounded_pause_request_required')
   request=json.loads(self.rfile.read(size))
   if not isinstance(request,dict):raise ValueError('exact_pause_request_required')
   from dashboard_control import pause,resume
   result=(pause if self.path.endswith('/pause') else resume)(self.server.data.project,request)
   with self.server.lock:self.server.snapshots.clear()
   self.reply(202 if result.get("status")=="restoring" else 200,result)
  except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
   self.reply(409,{'error':str(error) if isinstance(error,ValueError) else 'pause_outcome_requires_reconciliation'})
 def do_PUT(self):self.reply(405,{'error':'read_only_records'})
 do_PATCH=do_PUT
 do_DELETE=do_PUT
 def do_GET(self):
  if not self.local():self.reply(403,{'error':'local_origin_required'});return
  path=urlsplit(self.path);query=parse_qs(path.query)
  if path.path=='/health':
   self.reply(200,{'service':'sdm-research-dashboard','runtime_digest':self.server.runtime_digest,'instance':self.server.instance,'pid':os.getpid(),'read_only':False,'data_read_only':True,'control_actions':['pause-study','resume-study']});return
  if path.path in FILES:
   name,kind=FILES[path.path];self.reply(200,native_path(ASSETS/name).read_bytes(),kind);return
  if not self.authorized():self.reply(403,{'error':'dashboard_session_required'});return
  try:
   study=query.get('study',[None])[0]
   if study and len(study)>200:raise KeyError('unknown_study')
   if path.path=='/api/study/control':
    from dashboard_control import receipt_status
    self.reply(200,receipt_status(self.server.data.project,query.get('request_id',[''])[0]))
   elif path.path=='/api/snapshot':self.reply(200,self.server.snapshot(study))
   elif path.path=='/api/evidence':
    with self.server.lock:value=self.server.data.preview(query.get('id',[''])[0])
    self.reply(200,value)
   elif path.path=='/api/events':self.events(study)
   else:self.reply(404,{'error':'not_found'})
  except (OSError,ValueError,KeyError,TypeError) as error:
   self.reply(409,{'error':str(error) if isinstance(error,(ValueError,KeyError)) else 'research_record_temporarily_unavailable'})
 def events(self,study):
  # File observation refreshes the UI only. It cannot enqueue research work.
  self.send_headers('text/event-stream');last=None;heartbeat=0
  try:
   while not self.server.done.is_set():
    value=self.server.snapshot(study)
    if value['revision']!=last:
     self.wfile.write(('event: snapshot\ndata: '+json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n\n').encode('utf-8'))
     last=value['revision'];heartbeat=time.monotonic()
    elif time.monotonic()-heartbeat>15:self.wfile.write(b': connected\n\n');heartbeat=time.monotonic()
    self.wfile.flush();self.server.done.wait(2)
  except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,OSError):return
  except (ValueError,KeyError,TypeError):
   self.wfile.write(b'event: record-error\ndata: {"message":"research_snapshot_unavailable"}\n\n');self.wfile.flush()


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--project-root',required=True)
 parser.add_argument('--port',type=int,default=8792);parser.add_argument('--ready',type=Path)
 args=parser.parse_args();server=Server(args.project_root,args.port)
 ready=args.ready or Path(args.project_root)/'.bmad-ml/dashboard/ready.json'
 atomic_json(ready,{'schema':'sdm-dashboard-ready/v1','runtime_digest':server.runtime_digest,'url':server.url,'pid':os.getpid(),'instance':server.instance,
  'project_root':str(Path(args.project_root).resolve()),'read_only':False,'data_read_only':True,'control_actions':['pause-study','resume-study'],'started_at':time.time()})
 print(json.dumps({'url':server.url,'data_read_only':True,'control_actions':['pause-study','resume-study']}),flush=True)
 try:server.serve_forever()
 finally:server.server_close()


if __name__=='__main__':main()
