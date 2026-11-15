"""Read-only RoadModel input/runtime probe before the training DAG node releases."""
import ast,hashlib,importlib.metadata,json,os,pathlib,subprocess,sys
spec=json.load(sys.stdin);root=pathlib.Path(spec['source_root'])
def git(*args):return subprocess.check_output(['git','-C',str(root),*args],text=True).strip()
if git('rev-parse','HEAD')!=spec['commit'] or git('status','--porcelain') or git('remote','get-url','origin')!=spec['source_url']:
    raise ValueError('exact_clean_source_required')
dataset=pathlib.Path(os.environ['LPAI_INPUT_DATASET_0'])/'DataReorganized-V1-3-occ-mesh'
index=dataset/'data_link_SFT_1061X11_5x15x2_withFilter_198evalscenes_v1-3_20260709.json'
if not index.is_file():raise ValueError('actual_source_index_missing')
pins={}
for node in ast.parse((root/'tools/bmad_single_run/prepare.py').read_text()).body:
    if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name) and node.targets[0].id in {'INDEX_SHA256','TRAIN_SCENES','EVAL_SCENES'}:
        pins[node.targets[0].id]=ast.literal_eval(node.value)
index_digest=hashlib.sha256(index.read_bytes()).hexdigest()
if index_digest!=pins['INDEX_SHA256'] or set(pins['TRAIN_SCENES'])&set(pins['EVAL_SCENES']):
    raise ValueError('committed_data_identity_or_split_mismatch')
baseline=list(pathlib.Path(os.environ['LPAI_INPUT_MODEL_0']).rglob('best.pth'))
if len(baseline)!=1:raise ValueError('unique_mounted_baseline_required')
rows=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,name,memory.free','--format=csv,noheader,nounits'],text=True).splitlines()
gpu=[dict(zip(('uuid','name','free_mib'),[x.strip() for x in row.split(',')])) for row in rows]
eligible=[x for x in gpu if int(x['free_mib'])>=16384 and 'A100' in x['name']]
if not eligible:raise ValueError('no_eligible_shared_A100_before_training')
versions={n:importlib.metadata.version(n) for n in ('torch','mmengine','numpy')}
print(json.dumps({'source_commit':spec['commit'],'source_clean':True,'source_index_bytes':index.stat().st_size,'source_index_sha256':index_digest,'committed_train_eval_disjoint':True,
    'baseline':str(baseline[0]),'baseline_bytes':baseline[0].stat().st_size,'gpu':gpu,'eligible_count':len(eligible),
    'versions':versions,'gpu_compute_invoked':False,'qualification':'shared visibility, not exclusive allocation'}))
