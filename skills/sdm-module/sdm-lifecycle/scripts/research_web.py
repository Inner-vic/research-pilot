"""Local read-only research UI. No SDK, executor, queue or platform imports."""
import hashlib
import json
import secrets
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from research_observation import digest, settings

ASSET = Path(__file__).resolve().parents[1]/'assets/research-web.html'


class ResearchView:
    def __init__(self, config):
        self.cfg = settings(config)
        self.store = Path(self.cfg['store']).resolve()
        self.allowed_roots = [Path(r).resolve() for r in self.cfg['run_roots']]

    def db(self):
        # Unlike the recorder, opening a page must never create/migrate a DB.
        db = sqlite3.connect(self.store.as_uri()+'?mode=ro', uri=True, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        if db.execute('SELECT binding FROM identity WHERE id=1').fetchone()[0] != digest(self.cfg):
            db.close(); raise ValueError('research_project_binding_changed')
        return db

    def read(self, question=None):
        db = self.db()
        try:
            db.execute('BEGIN')
            where = " WHERE json_extract(payload,'$.question_id')=?" if question else ''
            args = (question,) if question else ()
            def rows(table, limit):
                return list(reversed([json.loads(r[0]) for r in db.execute(
                    'SELECT payload FROM '+table+where+' ORDER BY rowid DESC LIMIT ?',args+(limit,))]))
            nodes, runs = rows('nodes',200), rows('runs',100)
            questions = [json.loads(r[0]) for r in db.execute("SELECT payload FROM nodes WHERE json_extract(payload,'$.kind')='question'")]
            revision = db.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]
            timeline = self.history(question=question, db=db)
            totals = {t:db.execute('SELECT COUNT(*) FROM '+t+where,args).fetchone()[0] for t in ('nodes','runs')}
        finally:db.close()
        evidence = {}
        edges = []
        for n in nodes:
            n['evidence_links'] = [self.register(evidence,e) for e in n.get('evidence',[])]
            edges += [{'source':n['id'],'target':r['target'],'label':{'revises':'修正','supports':'支持','contradicts':'反证','motivates':'关联依据','related_to':'关联'}.get(r['relation'],r['relation']), 'basis':r['basis']} for r in n.get('relations',[])]
            edges += [{'source':n['id'],'target':r,'label':'引用实际运行','basis':'observed'} for r in n.get('run_ids',[])]
            if n['kind']=='hypothesis':
                edges.append({'source':n['question_id'],'target':n['id'],'label':'研究问题归属（非因果）','basis':'membership'})
        runs.sort(key=lambda r:(r.get('started_at') or 0,r['id']))
        previous = {}
        for index,r in enumerate(runs):
            r['number'] = index+1
            r['evidence_links'] = []
            for key,sha in [('source_path','source_sha256')]:
                if r.get(key):r['evidence_links'].append(self.register(evidence,{'path':r[key],'sha256':r[sha]},'执行状态（记录时摘要）'))
            if r.get('result_ref'):r['evidence_links'].append(self.register(evidence,r['result_ref'],'原始结果'))
            root = Path(r['execution_root']).resolve()
            if not self.within(root):continue
            try:
                plan_path=root/'round-plan.json';plan=json.loads(plan_path.read_bytes())
                r['parameters']=plan['parameters']
                r['parent_decision_digest']=plan.get('parent_decision_digest')
                r['evidence_links'].append(self.register_file(evidence,plan_path,'实际轮次方案'))
                for n in nodes:
                    iteration=n.get('next_action',{}).get('iteration') if isinstance(n.get('next_action'),dict) else None
                    if iteration and 'sha256:'+digest(iteration)==r['parent_decision_digest']:
                        edges.append({'source':n['id'],'target':r['id'],'label':'决策已落实到此轮','basis':'verified-contract'})
                manifest=json.loads((root/'artifact-manifest.json').read_bytes())
                for f in manifest['files']:
                    file=Path(f['path']).resolve()
                    if file.is_relative_to(root/'artifacts'):
                        r['evidence_links'].append(self.register(evidence,{'path':str(file),'sha256':f['digest']},file.name))
            except (OSError,ValueError,KeyError,TypeError) as exc:
                r['material_warning']=str(exc)
            r.setdefault('parameters',r.get('result',{}).get('parameters',{}))
            r['parameter_changes']=[{'name':k,'previous':previous.get(k),'current':v} for k,v in r['parameters'].items() if k not in previous or previous[k]!=v]
            previous=r['parameters']
        return {'project_id':self.cfg['project_id'],'revision':revision,'questions':questions,'nodes':nodes,'runs':runs,'edges':edges,'history':timeline,
                'totals':totals,'truncated':totals['nodes']>len(nodes) or totals['runs']>len(runs),
                'evidence':evidence,'read_only':True,'platform_calls':False,'served_at':time.time()}

    def within(self,path):
        return any(path.is_relative_to(root) for root in self.allowed_roots)

    def register(self, entries, ref, label=None):
        path=Path(ref['path']).resolve()
        sha=ref['sha256'].removeprefix('sha256:')
        key=hashlib.sha256((str(path)+'|'+sha).encode()).hexdigest()
        entries.setdefault(key,{'id':key,'path':str(path),'sha256':sha,'label':label or path.name})
        return key

    def register_file(self,entries,path,label):
        return self.register(entries,{'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()},label)

    def history(self,before=None,question=None,db=None):
        owned=db is None
        if owned:db=self.db()
        try:
            filters=[];args=[]
            if before is not None:filters.append('seq<?');args.append(int(before))
            if question:filters.append("json_extract(payload,'$.question_id')=?");args.append(question)
            where=' WHERE '+' AND '.join(filters) if filters else ''
            rows=list(db.execute('SELECT seq,payload FROM changes'+where+' ORDER BY seq DESC LIMIT 101',args))
            items=[{'revision':r['seq'],**json.loads(r['payload'])} for r in rows[:100]]
            return {'items':list(reversed(items)),'has_more':len(rows)>100,'next_before':items[-1]['revision'] if len(rows)>100 else None}
        finally:
            if owned:db.close()

    def evidence(self,key):
        # No caller supplied path. Only exact registered evidence can be opened.
        ref=self.read()['evidence'].get(key)
        if not ref:raise KeyError('unregistered_evidence')
        path=Path(ref['path']).resolve()
        if not self.within(path):raise PermissionError('evidence_outside_registered_run_roots')
        sha=hashlib.sha256();size=0;preview=bytearray()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):
                sha.update(chunk);size+=len(chunk)
                if len(preview)<262144:preview.extend(chunk[:262144-len(preview)])
        if sha.hexdigest()!=ref['sha256']:raise ValueError('evidence_changed_since_recording')
        text=None
        try:
            decoded=preview.decode('utf-8')
            if '\x00' not in decoded:text=decoded
        except UnicodeDecodeError:pass
        return {**ref,'verified':True,'bytes':size,'text':text,'preview_truncated':size>len(preview),
                'binary':text is None}


class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=False
    def server_bind(self):
        # Windows SO_REUSEADDR permits competing listeners on one port.
        # One project URL must resolve to exactly one token/store instance.
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()
    def __init__(self,config,port=0):
        self.view=ResearchView(config);self.view.read()
        self.token=secrets.token_urlsafe(24)
        self.done=threading.Event()
        super().__init__(('127.0.0.1',port),Handler)
        self.url=f'http://127.0.0.1:{self.server_port}/?token={self.token}'
    def server_close(self):
        self.done.set();super().server_close()


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def handle(self):
        try:super().handle()
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
            pass  # A browser leaving an SSE page is a normal disconnect.
    def reply(self,code,value,kind='application/json; charset=utf-8'):
        raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers();self.wfile.write(raw)
    def do_POST(self):self.reply(405,{'error':'read_only_view'})
    def do_GET(self):
        url=urlsplit(self.path);q=parse_qs(url.query)
        host=f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host')!=host or (self.headers.get('Origin') and self.headers['Origin']!='http://'+host):
            self.reply(403,{'error':'local_origin_required'});return
        if url.path=='/':
            self.reply(200,ASSET.read_bytes(),'text/html; charset=utf-8');return
        if not secrets.compare_digest(q.get('token',[''])[0],self.server.token):
            self.reply(403,{'error':'session_token_required'});return
        try:
            if url.path=='/api/snapshot':self.reply(200,self.server.view.read(q.get('question',[None])[0]))
            elif url.path=='/api/history':self.reply(200,self.server.view.history(q.get('before',[None])[0],q.get('question',[None])[0]))
            elif url.path=='/api/evidence':self.reply(200,self.server.view.evidence(q.get('id',[''])[0]))
            elif url.path=='/api/events':self.events()
            else:self.reply(404,{'error':'not_found'})
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
            self.reply(409 if isinstance(exc,ValueError) else 404,{'error':str(exc)})
    def events(self):
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.end_headers()
        last=None;heartbeat=0
        try:
            while not self.server.done.is_set():
                db=self.server.view.db()
                try:revision=db.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0]
                finally:db.close()
                if revision!=last:
                    self.wfile.write(('event: revision\ndata: '+json.dumps({'revision':revision})+'\n\n').encode());last=revision
                    heartbeat=time.monotonic()
                elif time.monotonic()-heartbeat>15:
                    self.wfile.write(b': alive\n\n');heartbeat=time.monotonic()
                self.wfile.flush()
                self.server.done.wait(1)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError,OSError,sqlite3.Error):return


def serve(config,port=0,ready=None):
    server=Server(config,port)
    if ready:
        from review_storage import atomic_json
        import os
        atomic_json(Path(ready),{'url':server.url,'pid':os.getpid(),'read_only':True,'store':str(server.view.store)})
    print(json.dumps({'url':server.url,'read_only':True}),flush=True)
    try:server.serve_forever()
    finally:server.server_close()
