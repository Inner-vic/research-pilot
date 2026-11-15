"""Choose a permitted shared GPU using NVML metadata before importing CUDA code.

Reuse the proven RoadModel NVML -> UUID -> isolated child visibility pattern.
Never initialize every visible GPU merely to ask how much memory is free.
"""
import os
import subprocess


def choose(rows, inherited=None, minimum_free_mib=6144, model_name='A100'):
    facts=[]
    for line in rows:
        index,uuid,name,free=[x.strip() for x in line.split(',')]
        facts.append({'index':int(index),'uuid':uuid,'name':name,'free_mib':int(free)})
    allowed=facts
    if inherited is not None:
        tokens=[x.strip() for x in inherited.split(',') if x.strip()]
        if not tokens or '-1' in tokens:raise RuntimeError('inherited_cuda_visibility_disables_gpu')
        permitted=[]
        for token in tokens:
            matches=[f for f in facts if str(f['index'])==token or f['uuid'].startswith(token)]
            if len(matches)!=1:raise RuntimeError('unresolved_inherited_cuda_visibility')
            permitted.extend(matches)
        allowed=permitted
    eligible=[f for f in allowed if model_name in f['name'] and f['free_mib']>=minimum_free_mib]
    if not eligible:raise RuntimeError('no_permitted_gpu_with_minimum_free_memory')
    selected=sorted(eligible,key=lambda f:(-f['free_mib'],f['index']))[0]
    return selected,facts


def restrict(minimum_free_mib=6144, model_name='A100'):
    inherited=os.environ.get('CUDA_VISIBLE_DEVICES')
    rows=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,name,memory.free',
                                  '--format=csv,noheader,nounits'],text=True).splitlines()
    gpu,facts=choose(rows,inherited,minimum_free_mib,model_name)
    os.environ['CUDA_VISIBLE_DEVICES']=gpu['uuid']
    return {'selected':gpu,'inventory':facts,'inherited_cuda_visible_devices':inherited,
            'cuda_visible_devices':gpu['uuid'],'cuda_device_count_expected':1,
            'qualification':'NVML snapshot on shared hardware, not a reservation or exclusive allocation'}
