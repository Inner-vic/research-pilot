"""Real project workload only. Submission, observation and cleanup belong to Skill."""
import argparse, hashlib, json, math, os, pathlib, re, shutil, subprocess, sys, time, types
import prepare_roadmodel_minimal as helper
P=pathlib.Path
ROOT=P.cwd(); SPEC=json.loads((ROOT/'roadmodel-spec.json').read_text(encoding='utf-8'))
SOURCE=P(SPEC['source_root']);COMMIT=SPEC['commit'];URL=SPEC['source_url']
TRAIN_N=SPEC['train_samples'];VAL_N=SPEC['validation_samples']
EPOCHS=SPEC.get('epochs',1)
def write(n,x): (ROOT/n).write_text(json.dumps(x,indent=2,allow_nan=False),encoding='utf-8')
def sha(p): return helper.digest(p)
def completion_counts(train,val,updates,epochs,train_n,val_n):
    if type(epochs) is not int or epochs<1:raise ValueError('positive_workload_epochs_required')
    expected=set(range(epochs))
    for rows,count in ((train,train_n),(val,val_n)):
        counts={}
        for row in rows:counts[int(row[0])]=counts.get(int(row[0]),0)+1
        if set(counts)!=expected or any(v!=count for v in counts.values()):raise ValueError('incomplete_actual_epoch_frame_counts')
    if type(updates) is not int or updates!=epochs*train_n:raise ValueError('incomplete_actual_optimizer_updates')

def target_checkpoint(directory,epochs):
    checkpoints=list(directory.rglob(f'epoch_{epochs}.pth'))
    if len(checkpoints)!=1:raise ValueError('unique completed checkpoint required')
    return checkpoints[0]

def checkpoint_progress(saved,epochs,train_n):
    if type(saved.get('epoch')) is not int or type(saved.get('global_iter')) is not int or saved['epoch']!=epochs or saved['global_iter']!=epochs*train_n:
        raise ValueError('checkpoint_target_progress_mismatch')

def main():
    a=argparse.ArgumentParser();a.add_argument('--learning-rate',type=float,default=SPEC.get('learning_rate',1e-5))
    a.add_argument('--semantic-residual',choices=['true','false'],default='false');args=a.parse_args()
    runtime_v2=SPEC.get('runtime_version',1)==2
    candidate=args.semantic_residual=='true'
    if candidate and not runtime_v2:raise ValueError('semantic_candidate_requires_runtime_v2')
    parameters={'semantic_residual':candidate} if runtime_v2 else {'learning_rate':args.learning_rate}
    started=time.time()
    dataset=P(os.environ['LPAI_INPUT_DATASET_0'])/'DataReorganized-V1-3-occ-mesh'
    index=dataset/'data_link_SFT_1061X11_5x15x2_withFilter_198evalscenes_v1-3_20260709.json'
    candidates=list(P(os.environ['LPAI_INPUT_MODEL_0']).rglob('best.pth'))
    if len(candidates)!=1:raise ValueError('unique baseline required')
    baseline=candidates[0]
    selection=helper.pins(SOURCE/'tools/bmad_single_run/prepare.py')
    subset=helper.minimal_index(json.loads(index.read_text(encoding='utf-8')),selection,TRAIN_N,VAL_N)
    fixed=ROOT/'fixed-subset.json';fixed.write_text(json.dumps(subset,ensure_ascii=False),encoding='utf-8')
    prepared=helper.prepare(types.SimpleNamespace(repo_root=SOURCE,expected_commit=COMMIT,expected_source_url=URL,
        source_index=index,dataset_root=dataset,baseline=baseline,output_root=ROOT/'prepared',train_frames=TRAIN_N,val_frames=VAL_N,
        loop_parameters={'amp':False,'learning_rate':args.learning_rate},fixed_subset_path=fixed,epochs=EPOCHS,
        loader_workers=SPEC.get('loader_workers',0) if runtime_v2 else None))
    training_source=SOURCE
    if runtime_v2:
        from semantic_overlay import apply
        from mmengine import Config
        training_source=ROOT/'source'
        shutil.copytree(SOURCE,training_source,symlinks=True,ignore=shutil.ignore_patterns('.git','__pycache__','*.pth','*.pt','*.log'))
        overlay=apply(training_source)
        cfg=Config.fromfile(str(ROOT/'prepared/minimal.py'),lazy_import=False)
        cfg.model.head.feature_semantic_residual=(dict(feature_dim=int(cfg.embed_dims),
            num_classes=int(cfg.model.head.num_classes),hidden_dim=128,neighbors=8,radius=.05,
            query_chunk=512,checkpoint_chunks=True) if candidate else None)
        cfg.dump(str(ROOT/'prepared/minimal.py'))
        prepared['training_argv'][1]=str(training_source/'train.py')
        write('implementation-manifest.json',{'overlay':overlay,'candidate':candidate,
            'config_digest':sha(ROOT/'prepared/minimal.py'),'runtime_version':2,
            'comparison_scope':'engineering smoke on mounted common weights, not A800 epoch-40 continuation',
            'checkpoint_policy':'verified CPU readback before keep-two retention',
            'health_io_seconds':30,'debug_stats':False})
    rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.free,memory.total','--format=csv,noheader,nounits'],text=True).splitlines()
    facts=[]
    for row in rows:
        i,u,n,f,t=[v.strip() for v in row.split(',')];facts.append({'index':int(i),'uuid':u,'name':n,'free_mib':int(f),'total_mib':int(t)})
    permitted=os.environ.get('CUDA_VISIBLE_DEVICES')
    allowed=None if permitted is None else [v.strip() for v in permitted.split(',')]
    eligible=sorted([f for f in facts if f['free_mib']>=16384 and 'A100' in f['name'] and
        (allowed is None or str(f['index']) in allowed or any(f['uuid'].startswith(v) for v in allowed if v))],key=lambda f:(-f['free_mib'],f['index']))
    if not eligible:raise RuntimeError('no A100 with minimum free memory')
    gpu=eligible[0]
    env=os.environ.copy()
    for k in helper.config_env_names(SOURCE/'config/2-1-0.py'):env.pop(k,None)
    env.update(prepared['environment']);env.update(CUDA_VISIBLE_DEVICES=gpu['uuid'],PYTHONUNBUFFERED='1',SDM_TELEMETRY_ROOT=str(ROOT))
    if runtime_v2 and SPEC.get('loader_workers',0)>0:
        env.update(OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',OPENBLAS_NUM_THREADS='1')
    argv=[sys.executable,str(ROOT/('training_entry_v2.py' if runtime_v2 else 'training_entry.py')),*prepared['training_argv'][1:]]
    write('run-manifest.json',{'argv':argv,'gpu':gpu,'gpu_inventory':facts,'shared_allocation':True,'source_commit':COMMIT,
        'baseline_digest':sha(baseline),'data_digest':sha(fixed),'parameters':parameters,
        'amp':False,'warmup_iters':1,'train_samples':TRAIN_N,'validation_samples':VAL_N,'batch_size':1,'epochs':EPOCHS,'seed':42,
        'loader_workers':SPEC.get('loader_workers',0),
        'loader_configuration':prepared.get('loader_configuration'),
        'cpu_threads':{k:env.get(k) for k in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS')}})
    if runtime_v2:
        from resource_sampler import Sampler
        from roadmodel_runtime_safety import guarded_training
        sampler=Sampler(gpu['uuid'])
        cleanup=guarded_training(argv,training_source,env,ROOT/'train.log',ROOT/'progress.json',sampler,
            lambda value:write('resource-samples.json',value),
            maximum_seconds=SPEC.get('maximum_training_seconds',900),stall_seconds=240,
            storage_root=ROOT)
        if cleanup.get('verified') is not True or cleanup.get('scope')!='posix-process-group':
            raise ValueError('training_process_group_exit_not_verified')
    else:
        with (ROOT/'train.log').open('wb') as log:
            proc=subprocess.run(argv,cwd=training_source,env=env,stdout=log,stderr=subprocess.STDOUT)
            if proc.returncode:raise RuntimeError('RoadModel returned exit '+str(proc.returncode))
    text=(ROOT/'train.log').read_text(errors='replace')
    train=re.findall(r'\[TRAIN\] Epoch (\d+) Iter\s+(\d+)/(\d+): Loss: ([^\s]+)',text)
    val=re.findall(r'\[EVAL\] Epoch (\d+) Iter\s+(\d+): Loss: ([^\s]+)',text)
    losses=re.findall(r'Current val loss is ([^\s]+)',text)
    surfaces=re.findall(r'surface_mIoU: ([^,\s]+), nonempty_IoU: ([^\s]+)',text)
    if not losses or not surfaces:raise ValueError('evaluation metrics absent')
    metrics=[float(losses[-1]),float(surfaces[-1][0]),float(surfaces[-1][1])]
    if not all(math.isfinite(v) for v in [*metrics,*[float(r[-1]) for r in train],*[float(r[-1]) for r in val]]):raise ValueError('nonfinite metrics')
    progress=json.loads((ROOT/'progress.json').read_text())
    if runtime_v2:
        verified=json.loads((ROOT/'load-and-optimizer.json').read_bytes())
        if not verified.get('optimizer_verified'):raise ValueError('optimizer_acceptance_missing')
        if verified['actual_loads'][0]['sha256']!=sha(baseline).removeprefix('sha256:'):raise ValueError('actual_baseline_load_digest_mismatch')
        semantic=json.loads((ROOT/'semantic-runtime.json').read_bytes())
        if candidate:
            if len(semantic.get('heads',[]))!=1:raise ValueError('one_actual_semantic_head_required')
            head=semantic['heads'][0];grads=head['gradient_abs_max']
            if not all(grads.get(k,0)>0 for k in ('residual_projection','residual_value_mlp','original_probability_path','final_encoder_feature')):
                raise ValueError('actual_semantic_gradient_acceptance_failed')
            if not head['projection_weight_abs_max']>0:raise ValueError('semantic_projection_not_updated')
        elif semantic.get('heads'):raise ValueError('control_must_not_construct_semantic_head')
    completion_counts(train,val,progress['optimizer_updates'],EPOCHS,TRAIN_N,VAL_N)
    checkpoint=ROOT/'checkpoint.pth'
    if runtime_v2:
        from roadmodel_runtime_safety import export_verified_checkpoint
        export_verified_checkpoint(target_checkpoint(ROOT/'prepared/train',EPOCHS),checkpoint,ROOT)
    else:shutil.copyfile(target_checkpoint(ROOT/'prepared/train',EPOCHS),checkpoint)
    import torch
    saved=torch.load(checkpoint,map_location='cpu',weights_only=True)
    if not all(k in saved for k in ('state_dict','optimizer','scheduler','epoch','global_iter')):raise ValueError('incomplete training checkpoint')
    checkpoint_progress(saved,EPOCHS,TRAIN_N)
    if not all(torch.isfinite(v).all().item() for v in saved['state_dict'].values() if torch.is_tensor(v) and v.is_floating_point()):raise ValueError('nonfinite checkpoint')
    write('checkpoint-manifest.json',{'path':'checkpoint.pth','bytes':checkpoint.stat().st_size,'digest':sha(checkpoint),
        'keys':list(saved),'epoch':saved['epoch'],'global_iter':saved['global_iter'],'reload_verified':True})
    write('result.json',{'parameters':parameters,'score':metrics[1],'validation_loss':metrics[0],
        **({'initial_parameter_digest':verified['initial_parameter_digest'],'loader_workers':SPEC.get('loader_workers',0)} if runtime_v2 else {}),
        'surface_miou':metrics[1],'nonempty_iou':metrics[2],'optimizer_updates':progress['optimizer_updates'],
        'checkpoint_verified':True,'checkpoint_digest':sha(checkpoint),'data_digest':sha(fixed),'baseline_digest':sha(baseline),
        'source_commit':COMMIT,'train_samples':TRAIN_N,'validation_samples':VAL_N,'batch_size':1,'epochs':EPOCHS,'seed':42,
        'amp':False,'execution_device':'cuda','data_kind':'real-RoadModel','elapsed_seconds':time.time()-started})
if __name__=='__main__':
    try: main()
    except Exception as e:
        try:write('failure-detail.json',{'type':type(e).__name__,'message':str(e),'exit_code':getattr(e,'exit_code',None)})
        except Exception as storage_error:print('FAILURE_REPORT_WRITE_FAILED '+repr(storage_error)+' original='+repr(e),file=sys.stderr,flush=True)
        raise
