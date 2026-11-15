"""Frozen binary-task harness adapter; never imports or executes ML locally.

Adapts the retained baseline only at declared model/optimizer/configuration and
code-identity hooks. Data split, evaluation, selection and artifact code remain
structurally identical. Scientific implementations are supplied after review.
"""
import ast
import base64
import copy
import gzip
import hashlib
import io
import json
import math
from pathlib import Path

from component_research import reference
from review_storage import atomic_json,read_json,native_path


def sha(raw):return hashlib.sha256(raw).hexdigest()


def ref(path):return {'path':str(Path(path).resolve()),'sha256':sha(native_path(path).read_bytes())}


def launch_command(packed,packed_sha):
    """The one qualified remote wrapper, shared by production and verification."""
    return "timeout --signal=TERM --kill-after=30s 1800s python3 -u - <<'SDM_PAYLOAD'\n"+(
        "import base64,gzip,hashlib,json,pathlib,tempfile,runpy,sys\n"
        "raw=gzip.decompress(base64.b64decode('%s',validate=True))\n"
        "assert hashlib.sha256(raw).hexdigest()=='%s'\n"
        "p=pathlib.Path(tempfile.mkdtemp(prefix='sdm-component-'))\n"
        "files=json.loads(raw)\nassert set(files)=={'trainer.py','model.py','parameters.json'}\n"
        "for name,text in files.items(): (p/name).write_text(text,encoding='utf-8')\n"
        "sys.path.insert(0,str(p))\nrunpy.run_path(str(p/'trainer.py'),run_name='__main__')\n"%(packed,packed_sha))+"SDM_PAYLOAD"


def verify_request_payload(request, manifest):
    """Independently decode the real submission bytes; never execute payload code."""
    command=request['data']['lpai_task']['cmd']
    if (not command.endswith('\nSDM_PAYLOAD') or
            any(role['cmd']!=command for role in request['data']['lpai_task']['roles'])):
        raise ValueError('same_exact_role_payload_required')
    tree=ast.parse(command.split('\n',1)[1].rsplit('\nSDM_PAYLOAD',1)[0])
    calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call)
           and isinstance(node.func,ast.Attribute) and node.func.attr=='b64decode']
    if (len(calls)!=1 or len(calls[0].args)!=1 or not isinstance(calls[0].args[0],ast.Constant)
            or not isinstance(calls[0].args[0].value,str) or len(calls[0].args[0].value)>4*1024*1024):
        raise ValueError('one_bounded_literal_payload_required')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(calls[0].args[0].value,validate=True))) as stream:
        raw=stream.read(2*1024*1024+1)
    if len(raw)>2*1024*1024 or sha(raw)!=manifest['packed_sha256']:
        raise ValueError('packed_submission_digest_mismatch')
    packed=calls[0].args[0].value
    if command!=launch_command(packed,sha(raw)):
        raise ValueError('unqualified_remote_launch_wrapper')
    files=json.loads(raw)
    if set(files)!={'trainer.py','model.py','parameters.json'} or not all(isinstance(v,str) for v in files.values()):
        raise ValueError('exact_reviewed_payload_files_required')
    identities={name:sha(value.encode('utf-8')) for name,value in files.items()}
    if identities!=manifest['identities'] or sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode())!=manifest['code_sha256']:
        raise ValueError('decoded_submission_is_not_reviewed_code')
    return {'schema':'sdm-decoded-submission-proof/v1','decoded_file_sha256':identities,
            'packed_sha256':sha(raw),'code_sha256':manifest['code_sha256'],
            'same_role_commands':True,'payload_code_executed':False,'ml_executed':False,
            'launch':{'command_sha256':sha(command.encode()),
                'wrapper_template_sha256':sha(launch_command('<BASE64_PAYLOAD>','<PACKED_SHA256>').encode()),
                'wrapper_with_payload_literal_omitted':launch_command('<BASE64_PAYLOAD>',sha(raw)),
                'base64_literal_sha256':sha(packed.encode()),'base64_literal_chars':len(packed),
                'exact_qualified_wrapper':True,
                'scope':'Full wrapper shown; only compressed literal is omitted. Exact literal/raw/file hashes and every role command were checked without executing code.'}}


def executed_request_files(request,code_sha256=None):
    """Expose executed code to scientific review, not an opaque base64 excerpt."""
    command=request['data']['lpai_task']['cmd']
    if not command.endswith('\nSDM_PAYLOAD') or any(role['cmd']!=command for role in request['data']['lpai_task']['roles']):
        raise ValueError('same_exact_executed_role_payload_required')
    tree=ast.parse(command.split('\n',1)[1].rsplit('\nSDM_PAYLOAD',1)[0])
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
           and n.func.attr=='b64decode']
    if (len(calls)!=1 or len(calls[0].args)!=1 or not isinstance(calls[0].args[0],ast.Constant)
            or not isinstance(calls[0].args[0].value,str) or len(calls[0].args[0].value)>4*1024*1024):
        raise ValueError('bounded_executed_literal_payload_required')
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(calls[0].args[0].value,validate=True))) as stream:
        raw=stream.read(2*1024*1024+1)
    if len(raw)>2*1024*1024:raise ValueError('bounded_executed_code_required')
    try:files=json.loads(raw)
    except (ValueError,UnicodeError):
        # Original qualified baseline packed one Python file rather than JSON.
        if code_sha256 is not None and sha(raw)!=code_sha256:raise ValueError('executed_baseline_digest_changed')
        ast.parse(raw)
        return {'train.py':raw.decode('utf-8')},{'schema':'sdm-executed-code-proof/v1',
            'format':'single-python-baseline','code_sha256':sha(raw),'code_executed':False}
    if (not isinstance(files,dict) or set(files)!={'trainer.py','model.py','parameters.json'}
            or not all(isinstance(v,str) for v in files.values())):
        raise ValueError('qualified_executed_payload_format_required')
    identities={name:sha(value.encode()) for name,value in files.items()}
    manifest={'identities':identities,'code_sha256':sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode()),
              'packed_sha256':sha(raw)}
    if code_sha256 is not None and manifest['code_sha256']!=code_sha256:raise ValueError('executed_result_code_identity_changed')
    proof=verify_request_payload(request,manifest)
    return files,{**proof,'schema':'sdm-executed-code-proof/v1','format':'three-file-component'}


def harness(source,comparison=None,workload=None):
    """Fail closed if the original baseline does not match this explicit ABI."""
    original=source.decode('utf-8');tree=ast.parse(original)
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    def assigned(node,name):return isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)
    config=next(n for n in tree.body if assigned(n,'CONFIG'))
    code=next(n for n in main.body if assigned(n,'code_sha'))
    channels=next(n for n in main.body if assigned(n,'c'))
    model=next(n for n in main.body if assigned(n,'model'))
    optimizer=next(n for n in main.body if assigned(n,'optimizer'))
    if (not isinstance(config.value,ast.Dict) or not isinstance(model.value,ast.Call)
            or ast.unparse(model.value.func)!='nn.Sequential' or ast.unparse(optimizer.value.func)!='torch.optim.Adam'
            or channels.end_lineno+1!=model.lineno):
        raise ValueError('qualified_baseline_harness_shape_required')
    edits=[(config.lineno,config.end_lineno,"CONFIG = json.loads(Path(__file__).with_name('parameters.json').read_text(encoding='utf-8'))['training']"),
        (code.lineno,code.end_lineno,"    identities={name:sha(Path(__file__).with_name(name).read_bytes()) for name in ('trainer.py','model.py','parameters.json')}\n    code_sha=sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode())"),
        (channels.lineno,model.end_lineno,"    from model import build_model, build_optimizer\n    model = build_model(CONFIG)"),
        (optimizer.lineno,optimizer.end_lineno,"    optimizer = build_optimizer(model.parameters(), CONFIG)")]
    lines=original.splitlines(keepends=True)
    for start,end,text in sorted(edits,reverse=True):lines[start-1:end]=[text+'\n']
    rendered=''.join(lines).encode();compile(rendered,'trainer.py','exec')
    modified=ast.parse(rendered)
    # Structural equality outside the declared hooks is stronger than comparing
    # a few examples and does not evaluate/fill local ML tensors.
    def normalized(value):
        value=copy.deepcopy(value)
        value.body=[n for n in value.body if not assigned(n,'CONFIG')]
        function=next(n for n in value.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        function.body=[n for n in function.body if not any(assigned(n,k) for k in ('code_sha','identities','c','model','optimizer'))
                       and not (isinstance(n,ast.ImportFrom) and n.module=='model')]
        return ast.dump(value,include_attributes=False)
    if normalized(tree)!=normalized(modified):raise ValueError('protected_training_evaluation_logic_changed')
    if comparison is not None:
        # Host-owned failure gates; preserve successful-run arithmetic, RNG,
        # partitions and checkpoint selection. Model plugins must not inspect
        # caller frames or attach hooks to the trainer's criterion.
        expected=comparison['split_sha256']
        if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
            raise ValueError('frozen_split_digest_required')
        text=rendered.decode('utf-8').replace('\r\n','\n')
        preflight="    emit('preflight',"
        batch='            loss=criterion(model(x[batch]),y[batch]); loss.backward(); optimizer.step()'
        validation='        val,_=evaluate(vx,vy)'
        if any(text.count(token)!=1 for token in (preflight,batch,validation)):
            raise ValueError('known_training_validity_gate_sites_required')
        text=text.replace(preflight,"    if sha((out/'split.json').read_bytes()) != "+repr(expected)+":\n"
            "        raise ValueError('registered split mismatch; no fit')\n"
            "    emit('host-validity-gates', {'split_sha256':"+repr(expected)+", 'split_verified':True, "
            "'batch_loss_gate':'before backward/update', 'validation_gate':'before selection'})\n"+preflight)
        text=text.replace(batch,'            loss=criterion(model(x[batch]),y[batch])\n'
            "            if not bool(torch.isfinite(loss)): raise FloatingPointError('nonfinite batch loss; no update')\n"
            '            loss.backward(); optimizer.step()')
        text=text.replace(validation,validation+'\n'
            "        if not np.isfinite([val['loss'],val['macro_f1'],val['accuracy'],*val['class_recall']]).all():\n"
            "            raise FloatingPointError('nonfinite validation; no selection')")
        rendered=text.encode('utf-8');compile(rendered,'trainer.py','exec')
    if workload is not None:
        from research_workloads import settings
        settings(workload)
        if workload['training_loss_hook']:
            text=rendered.decode('utf-8')
            setup='    criterion = nn.CrossEntropyLoss()'
            site='            loss=criterion(model(x[batch]),y[batch])'
            if text.count(setup)!=1 or text.count(site)!=1:
                raise ValueError('qualified_training_loss_hook_sites_required')
            text=text.replace(setup,setup+'\n    from model import build_training_loss\n    training_criterion = build_training_loss(CONFIG)')
            text=text.replace(site,'            loss=training_criterion(model(x[batch]),y[batch])')
            # Validation criterion, selection and metrics remain byte-for-byte;
            # normalize only these two declared training sites for equivalence.
            restored=text.replace('\n    from model import build_training_loss\n    training_criterion = build_training_loss(CONFIG)','').replace(
                '            loss=training_criterion(model(x[batch]),y[batch])',site)
            if restored.encode()!=rendered:raise ValueError('training_hook_changed_protected_trainer')
            rendered=text.encode();compile(rendered,'trainer.py','exec')
    # Check the host's actual local helper ABI, without executing/importing ML.
    # Syntax alone cannot catch a keyword/arity mismatch in an emitted call.
    final_tree=ast.parse(rendered)
    emit=next(n for n in final_tree.body if isinstance(n,ast.FunctionDef) and n.name=='emit')
    if (len(emit.args.args)!=2 or emit.args.vararg or emit.args.kwarg
            or any(len(node.args)!=2 or node.keywords for node in ast.walk(final_tree)
                   if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='emit')):
        raise ValueError('protected_event_emission_abi_changed')
    return rendered


def contract(baseline_ref,root,comparison,manager_config,component_uid,training_limits=None,next_round=None,workload=None):
    source=reference(baseline_ref).read_bytes();root=Path(root);root.mkdir(parents=True,exist_ok=True)
    trainer=root/'trainer.py';raw=harness(source,comparison,workload)
    if trainer.exists() and trainer.read_bytes()!=raw:raise ValueError('frozen_component_harness_changed')
    if not trainer.exists():trainer.write_bytes(raw)
    value={'schema':'sdm-component-implementation-contract/v1','comparison_identity':comparison,
        'execution_scope':{'provider':'lpai','namespace':manager_config['namespace'],'owner':manager_config['owner'],
                           'component_uid':component_uid},
        'allowed_files':['model.py','parameters.json'],'parameters_file':'parameters.json','protected_inputs':[ref(trainer)],
        'model_api':'model.py exports build_model(config)->PyTorch module producing two logits and build_optimizer(parameters, config). No file/network/platform side effects or caller-frame/trainer-global access. Split and finite-loss gates belong to the protected trainer.',
        'training_parameters':'parameters.json.training contains round, model, channels, epochs(10..30), lr, batch_size, seed=42, threads=1, expected_minutes. Preserve comparison_identity, execution_scope and platform_requirements.',
        'qualification':'structural baseline equivalence outside declared hooks plus explicit fail-fast split/finite gates; no local model execution'}
    if training_limits is not None:
        value['training_limits']=training_limits
        value['model_api'] += ' Do not reconstruct a second reference model or add trainer hooks solely for review; any additional validity requirement must be satisfied by the protected host or returned as missing evidence.'
    if workload is not None:
        from research_workloads import capabilities
        value['workload']=capabilities(workload)
        value['seed_values']=workload['seed_values']
        value['training_parameters']=value['training_parameters'].replace('seed=42','seed in '+repr(workload['seed_values']))
        if workload['training_loss_hook']:
            value['model_api']+=' Also export build_training_loss(config)->callable(logits, labels) producing one finite scalar training loss. Validation remains the protected CrossEntropyLoss; no evaluation hooks, extra reference model, data/label leakage, trainer/global access or side effects.'
    if next_round is not None:
        if type(next_round) is not int or next_round<1:raise ValueError('explicit_next_round_metadata_required')
        value['next_round']=next_round
        value['training_parameters']+=' round is host attempt metadata and MUST equal '+str(next_round)+'; it is not a scientific treatment or a replay of an old task.'
    path=root/'execution-contract.json'
    if path.exists() and read_json(path)!=value:raise ValueError('component_implementation_contract_changed')
    if not path.exists():atomic_json(path,value)
    return ref(path)


def task_request(profile,execution_contract,parent_request,project_id,index):
    frozen=Path(profile['root']);spec=read_json(reference(execution_contract));parameters=read_json(frozen/'parameters.json')
    training=parameters.get('training',{})
    limits=spec.get('training_limits')
    if limits and (type(training.get('epochs')) is not int
                   or not limits['min_epochs']<=training['epochs']<=limits['max_epochs']):
        raise ValueError('training_epochs_outside_original_delegation')
    if (type(training.get('epochs')) is not int or not 10<=training['epochs']<=30
            or type(training.get('lr')) not in (int,float) or not math.isfinite(training['lr']) or not 0<training['lr']<=1
            or type(training.get('batch_size')) is not int or not 1<=training['batch_size']<=512
            or type(training.get('seed')) is not int or training['seed'] not in spec.get('seed_values',[42])
            or training.get('threads')!=1 or training.get('round')!=index
            or type(training.get('expected_minutes')) is not int or not 1<=training['expected_minutes']<=120
            or not isinstance(training.get('model'),str) or not training['model']):
        raise ValueError('reviewed_bounded_component_training_parameters_required')
    # Preserve reviewed bytes, including Windows CRLF. TextIO newline
    # translation would invalidate the profile or silently change its payload.
    files={name:(frozen/name).read_bytes().decode('utf-8') for name in ('model.py','parameters.json')}
    trainer=reference(spec['protected_inputs'][0]);files['trainer.py']=trainer.read_bytes().decode('utf-8')
    identities={name:sha(text.encode()) for name,text in files.items()}
    for name in ('model.py','parameters.json'):
        if profile['files'][name]!=identities[name]:raise ValueError('implemented_component_payload_changed')
    payload_sha=sha(json.dumps(identities,sort_keys=True,separators=(',',':')).encode())
    raw=json.dumps(files,sort_keys=True,separators=(',',':')).encode()
    packed=base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    command=launch_command(packed,sha(raw))
    request=copy.deepcopy(parent_request)
    import re
    prefix=re.sub('[^a-z0-9-]+','-',project_id.lower())[:32].strip('-') or 'sdm'
    request['data']['lpai_task']['cmd']=command
    for role in request['data']['lpai_task']['roles']:role['cmd']=command
    native_sha=sha(json.dumps({'component_uid':request['uid'],'cluster':request['cluster'],
        'data':request['data']},sort_keys=True,separators=(',',':')).encode())
    request.update(request_key=project_id+':native-data:'+native_sha,name=prefix+'-r%02d-'%index+native_sha[:10])
    expected={'protocol':spec['comparison_identity']['protocol'],'code_sha256':payload_sha,
              'source_sha256':spec['comparison_identity']['source_sha256'],'config':training,
              'split_sha256':spec['comparison_identity']['split_sha256']}
    return request,expected,{'identities':identities,'code_sha256':payload_sha,'packed_sha256':sha(raw)}
