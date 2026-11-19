"""Freeze project files and bind their exact bytes to any execution carrier."""
import ast,base64,copy,gzip,hashlib,io,json
from pathlib import Path
from component_research import reference
from component_payload import ref
from research_project import snapshot,relative
from research_replan import once
from review_storage import read_json

def sha(raw):return hashlib.sha256(raw).hexdigest()

def command(request):
    data=request['data']
    if 'lpai_task' in data:
        cmd=data['lpai_task']['cmd']
        if cmd.endswith('\nSDM_PROJECT_PAYLOAD') and any(r['cmd']!=cmd for r in data['lpai_task']['roles']):raise ValueError('same_project_role_payload_required')
        return cmd
    return data['command']

def launch(packed,checksum):
    return "timeout --signal=TERM --kill-after=30s 1800s python3 -u - <<'SDM_PROJECT_PAYLOAD'\n"+(
        "import base64,gzip,hashlib,json,pathlib,tempfile,runpy,sys\n"
        "raw=gzip.decompress(base64.b64decode('%s',validate=True))\n"
        "assert hashlib.sha256(raw).hexdigest()=='%s'\n"
        "files=json.loads(raw)\np=pathlib.Path(tempfile.mkdtemp(prefix='researchpilot-'))\n"
        "for name,text in files.items():\n"
        " q=pathlib.PurePosixPath(name)\n assert not q.is_absolute() and '..' not in q.parts and ':' not in name and '\\\\' not in name\n"
        " t=p/name\n t.parent.mkdir(parents=True,exist_ok=True)\n t.write_bytes(text.encode('utf-8'))\n"
        "sys.path.insert(0,str(p))\nrunpy.run_path(str(p/'_researchpilot_runner.py'),run_name='__main__')\n"%(packed,checksum))+"SDM_PROJECT_PAYLOAD"

def decode(request,manifest=None,code_sha256=None):
    cmd=command(request)
    if not cmd.endswith('\nSDM_PROJECT_PAYLOAD'):raise ValueError('project_wrapper_required')
    tree=ast.parse(cmd.split('\n',1)[1].rsplit('\nSDM_PROJECT_PAYLOAD',1)[0])
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='b64decode']
    if len(calls)!=1 or len(calls[0].args)!=1 or not isinstance(calls[0].args[0],ast.Constant):raise ValueError('one_project_payload_literal_required')
    packed=calls[0].args[0].value
    if not isinstance(packed,str) or len(packed)>4*1024*1024:raise ValueError('bounded_project_payload_required')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(packed,validate=True))) as z:raw=z.read(2*1024*1024+1)
    if len(raw)>2*1024*1024 or cmd!=launch(packed,sha(raw)):raise ValueError('exact_project_wrapper_required')
    files=json.loads(raw)
    if not isinstance(files,dict) or not 3<=len(files)<=35 or not all(isinstance(v,str) for v in files.values()):raise ValueError('typed_project_payload_files_required')
    for name in files:relative(name)
    if not {'_researchpilot_runner.py','_researchpilot_preflight.py','_researchpilot_project.json'}<=set(files):raise ValueError('protected_project_runtime_required')
    identities={name:sha(value.encode('utf-8')) for name,value in files.items()}
    actual={'identities':identities,'code_sha256':sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode()),'packed_sha256':sha(raw),'format':'project/v1'}
    if manifest is not None and actual!=manifest:raise ValueError('reviewed_project_payload_changed')
    if code_sha256 is not None and actual['code_sha256']!=code_sha256:raise ValueError('executed_project_payload_changed')
    return files,{'schema':'sdm-decoded-submission-proof/v1','format':'project/v1','decoded_file_sha256':identities,
        'packed_sha256':sha(raw),'code_sha256':actual['code_sha256'],'same_role_commands':True,
        'payload_code_executed':False,'ml_executed':False,
        'decoded_project_metadata':json.loads(files['_researchpilot_project.json']),'launch':{'command_sha256':sha(cmd.encode()),
        'exact_qualified_wrapper':True,'wrapper_template_sha256':sha(launch('<PAYLOAD>','<SHA256>').encode()),
        'wrapper_with_payload_literal_omitted':launch('<PAYLOAD>',sha(raw))}}

def contract(baseline_ref,root,comparison,manager_config,resource_uid,training_limits=None,next_round=None,workload=None):
    value,hashes,base=snapshot(workload['project_contract']);root=Path(root);root.mkdir(parents=True,exist_ok=True)
    protected=[]
    for name in value['source_files']:
        if name not in value['editable_files']:protected.append(ref(base/name))
    runner=Path(__file__).with_name('project_remote.py');protected.append(ref(runner));protected.append(ref(Path(__file__).with_name('project_preflight.py')))
    spec={'schema':'sdm-component-implementation-contract/v1','integration':'project/v1',
        'project_contract':workload['project_contract'],'comparison_identity':comparison,
        'execution_scope':scope(manager_config,resource_uid),'allowed_files':value['editable_files'],
        'parameters_file':value['parameters_file'],'protected_inputs':protected,
        'model_api':'Use the current project training entrypoint and its declared input/output contract. No framework or class-count ABI is imposed. Preserve protected preflight/evaluator, data and evaluation identity.',
        'training_parameters':'Preserve comparison_identity, execution_scope and platform_requirements; training epochs remain within delegation. Only declared editable files may change.',
        'training_limits':training_limits or value['training_limits'],'workload':workload,
        'project_source_root':str(base),'qualification':'Project-specific checks, original shared SDM submission and evidence gates; not qualification of every provider.'}
    if next_round is not None:spec['next_round']=next_round
    once(root/'execution-contract.json',spec);return ref(root/'execution-contract.json')

def scope(manager_config,resource_uid):
    return {'provider':manager_config.get('provider','lpai'),'namespace':manager_config['namespace'],
        'owner':manager_config['owner'],'component_uid':resource_uid}

def task_request(profile,execution_contract,parent_request,project_id,index):
    spec=read_json(reference(execution_contract));project,hashes,base=snapshot(spec['project_contract'])
    parameters=read_json(Path(profile['root'])/project['parameters_file']);training=parameters['training'];limits=spec['training_limits']
    if (type(training.get('epochs')) is not int or not limits['min_epochs']<=training['epochs']<=limits['max_epochs']
            or training.get('round')!=index or type(training.get('expected_minutes')) is not int or training['expected_minutes']<1):raise ValueError('project_training_outside_delegation')
    files={name:(base/name).read_bytes().decode('utf-8') for name in project['source_files']}
    for name in project['editable_files']:
        raw=(Path(profile['root'])/name).read_bytes()
        if sha(raw)!=profile['files'][name]:raise ValueError('implemented_project_file_changed')
        files[name]=raw.decode('utf-8')
    files['_researchpilot_runner.py']=Path(__file__).with_name('project_remote.py').read_bytes().decode('utf-8')
    files['_researchpilot_preflight.py']=Path(__file__).with_name('project_preflight.py').read_bytes().decode('utf-8')
    files['_researchpilot_project.json']=json.dumps({'project':project,'comparison_identity':spec['comparison_identity'],
        'parameters_file':project['parameters_file'],'source_sha256':{n:sha(t.encode()) for n,t in files.items()}},sort_keys=True)
    identities={name:sha(text.encode()) for name,text in files.items()}
    checksum=sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode())
    raw=json.dumps(files,sort_keys=True,separators=(',',':')).encode();cmd=launch(base64.b64encode(gzip.compress(raw,mtime=0)).decode(),sha(raw))
    request=copy.deepcopy(parent_request)
    if 'lpai_task' in request['data']:
        request['data']['lpai_task']['cmd']=cmd
        for role in request['data']['lpai_task']['roles']:role['cmd']=cmd
    else:request['data']={'purpose':request['data'].get('purpose','training'),'command':cmd}
    native=sha(json.dumps(request['data'],sort_keys=True,separators=(',',':')).encode())
    request.update(request_key=project_id+':project-payload:'+native,name=project_id[:32]+'-r'+str(index)+'-'+native[:10])
    expected={**spec['comparison_identity'],'code_sha256':checksum,'config':training,
        'project_contract':spec['project_contract'],'evaluation':project['evaluation'],'result_contract':'sdm-project-result/v1'}
    manifest={'identities':identities,'code_sha256':checksum,'packed_sha256':sha(raw),'format':'project/v1'}
    decode(request,manifest)
    return request,expected,manifest
