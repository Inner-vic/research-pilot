"""Protected remote runner. Executes only the frozen project's three entrypoints.

No PyTorch/data-layout/metric arithmetic is embedded here. The project owns
training and evaluation; the host independently checks identity and recounts
supported metrics from raw predictions before accepting a result.
"""
import base64,gzip,hashlib,json,os,subprocess,sys,time
from pathlib import Path

def sha(raw):return hashlib.sha256(raw).hexdigest()
def load(path):return json.loads(Path(path).read_bytes())
def emit(row):print('SDM_JSON '+json.dumps(row,sort_keys=True,allow_nan=False),flush=True)

def main():
    root=Path(__file__).resolve().parent;os.chdir(root)
    contract=load('_researchpilot_project.json');project=contract['project'];comparison=contract['comparison_identity']
    parameters=load(contract['parameters_file']);training=parameters['training']
    if parameters['comparison_identity']!=comparison:raise ValueError('project comparison identity changed')
    original={p:sha((root/p).read_bytes()) for p in contract['source_sha256']}
    if original!=contract['source_sha256']:raise ValueError('project source bytes changed')
    all_files={**original,'_researchpilot_project.json':sha((root/'_researchpilot_project.json').read_bytes())}
    code_sha=sha(json.dumps(all_files,sort_keys=True,separators=(',',':')).encode())
    started=time.monotonic()
    def run(kind):
        argv=list(project['entrypoints'][kind])
        if argv[0] in ('python','python3'):argv[0]=sys.executable
        elif argv[0] in project['source_files']:argv[0]=str(root/argv[0])
        subprocess.run(argv,check=True,timeout=max(1,1700-int(time.monotonic()-started)))
    run('preflight');preflight=load('preflight.json')
    from _researchpilot_preflight import verify_report
    try:verify_report(preflight,project['preflight_checks'])
    except ValueError as error:
        raise ValueError('project preflight did not pass; no full training; SDM_PREFLIGHT_DIAGNOSTIC '+json.dumps(preflight,sort_keys=True)) from error
    run('train');run('evaluate')
    report=load(project['evaluation']['report']);predictions=load(project['evaluation']['predictions']);split=load('split.json')
    if sha((root/'split.json').read_bytes())!=comparison['split_sha256']:raise ValueError('project split changed')
    checkpoint=report['checkpoint_file'];q=Path(checkpoint)
    if q.is_absolute() or '..' in q.parts or not (root/q).resolve().is_relative_to(root):raise ValueError('owned checkpoint path required')
    checkpoint_raw=(root/q).read_bytes()
    history=report['history']
    if [r.get('epoch') for r in history]!=list(range(1,training['epochs']+1)):raise ValueError('complete project training exposure required')
    if any(sha((root/p).read_bytes())!=h for p,h in original.items()):raise ValueError('project source mutated during execution')
    result={'schema':'sdm-project-result/v1',**comparison,'code_sha256':code_sha,'config':training,
        'validation':report['metrics'],'selected_epoch':report['selected_epoch'],'history':history,
        'evaluation':project['evaluation'],'class_names':project['data']['labels'],'elapsed_seconds':time.monotonic()-started,'test_used':False,
        'engineering_preflight':preflight,'checkpoint_sha256':sha(checkpoint_raw)}
    artifacts={'result.json':json.dumps(result,sort_keys=True,allow_nan=False).encode(),
        'predictions.json':json.dumps(predictions,sort_keys=True,allow_nan=False).encode(),
        'split.json':(root/'split.json').read_bytes(),'checkpoint.bin':checkpoint_raw,
        'preflight.json':(root/'preflight.json').read_bytes()}
    emit({'kind':'preflight','code_sha256':code_sha,'source_sha256':comparison['source_sha256'],
        'split_sha256':comparison['split_sha256'],'config':training})
    for name,raw in artifacts.items():
        encoded=base64.b64encode(gzip.compress(raw,mtime=0)).decode();parts=[encoded[i:i+3072] for i in range(0,len(encoded),3072)]
        emit({'kind':'artifact','name':name,'encoding':'base64+gzip','bytes':len(raw),'sha256':sha(raw),'chunks':len(parts)})
        for i,part in enumerate(parts):emit({'kind':'artifact-chunk','name':name,'index':i,'value':part})
    emit({'kind':'completed','result_sha256':sha(artifacts['result.json']),'validation':report['metrics']})

if __name__=='__main__':main()
