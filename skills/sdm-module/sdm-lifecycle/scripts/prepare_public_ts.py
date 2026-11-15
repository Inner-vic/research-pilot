"""Prepare pinned public binary, fixed-length univariate TS research inputs.

Metadata/download/code rendering only. No Torch/NumPy import, fit, evaluation,
platform asset/task writes or invented results. Unsupported input stays explicit.
"""
import argparse
import ast
import hashlib
import json
import math
from pathlib import Path
import re
from public_dataset_source import fetch
from review_storage import atomic_json,read_json,locked
from component_payload import ref


def sha(raw):return hashlib.sha256(raw).hexdigest()


def metadata(raw,protocol):
    header={};records=[];data=False
    for line in raw.decode('utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        if not data:
            if line.lower()=='@data':data=True;continue
            key,_,value=line.partition(' ');header[key.lower()]=value.strip()
        else:
            if line.count(':')!=1:raise ValueError('only_univariate_without_timestamps_supported')
            values,label=line.rsplit(':',1)
            row=[float(v) for v in values.split(',')];label=float(label)
            if not row or not all(math.isfinite(v) for v in [*row,label]):
                raise ValueError('finite_numeric_input_required')
            records.append((row,label))
    if header.get('@timestamps','false').lower()!='false' or header.get('@univariate','true').lower()!='true':
        raise ValueError('only_univariate_without_timestamps_supported')
    labels=sorted({label for _,label in records});lengths={len(row) for row,_ in records}
    if len(labels)!=2 or len(lengths)!=1:raise ValueError('binary_fixed_length_input_required')
    train=[];validation=[]
    for label in labels:
        ids=[i for i,(_,value) in enumerate(records) if value==label]
        if len(ids)<4:raise ValueError('insufficient_per_class_train_validation_samples')
        ids.sort(key=lambda i:sha((protocol+':'+str(i)).encode()))
        count=math.ceil(len(ids)*.25);validation+=ids[:count];train+=ids[count:]
    train.sort();validation.sort()
    # Duplicate leakage is checked again against the exact float32 tensors remotely.
    hashes=[sha(json.dumps(row,separators=(',',':')).encode()) for row,_ in records]
    if {hashes[i] for i in train}&{hashes[i] for i in validation}:
        raise ValueError('duplicate_signal_crosses_split_choose_grouped_protocol')
    split={'protocol':protocol,'source_sha256':sha(raw),'train_ids':train,'validation_ids':validation,'test_used':False}
    return {'samples':len(records),'length':next(iter(lengths)),'class_labels':labels,
        'class_counts':[sum(label==v for _,label in records) for v in labels],
        'train_samples':len(train),'validation_samples':len(validation),'split':split}


def render(constants):
    path=Path(__file__).with_name('public_ts_baseline.py');text=path.read_text(encoding='utf-8')
    tree=ast.parse(text);lines=text.splitlines(keepends=True);replacements=[]
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            key=node.targets[0].id
            if key in constants:replacements.append((node.lineno,node.end_lineno,key+' = '+repr(constants[key])+'\n'))
    if len(replacements)!=len(constants):raise ValueError('baseline_constant_contract_changed')
    for first,last,value in sorted(replacements,reverse=True):lines[first-1:last]=[value]
    result=''.join(lines);ast.parse(result);return result.encode()


def prepare(args):
    root=Path(args.output).resolve()
    if not re.fullmatch(r'[a-z][a-z0-9-]{3,60}',args.name):raise ValueError('valid_study_name_required')
    if not args.protocol or not args.remote_root.startswith('/mnt/') or '..' in Path(args.remote_root).parts:
        raise ValueError('explicit_split_protocol_and_remote_output_root_required')
    binding={key:getattr(args,key) for key in ('source_url','checksum','checksum_algorithm','name','protocol','namespace','remote_root')}
    with locked(root,'.public-input.lock','public_input_preparation_owned'):
        manifest=root/'preparation.json'
        if manifest.exists():
            saved=read_json(manifest)
            if saved['binding']!=binding:raise ValueError('public_input_binding_changed')
            if saved['status']=='prepared':
                for name,expected in saved['files'].items():
                    if sha((root/name).read_bytes())!=expected:raise ValueError('prepared_input_changed:'+name)
                return saved
        else:
            if any(root.iterdir()):
                # locked() owns its lock file; no other previous payload is accepted.
                if any(p.name!='.public-input.lock' for p in root.iterdir()):raise ValueError('fresh_public_input_root_required')
            atomic_json(manifest,{'binding':binding,'status':'downloading'})
        filename=args.name.replace('-','_')+'_TRAIN.ts'
        raw=fetch(args.source_url,root/filename,args.checksum,args.checksum_algorithm)
        info=metadata(raw,args.protocol);split=info.pop('split');atomic_json(root/'metadata.json',info)
        # Match the protected remote split serialization exactly, including spaces.
        split_raw=json.dumps(split,sort_keys=True).encode();(root/'expected-split.json').write_bytes(split_raw)
        constants={'SOURCE':args.source_url,'DATA_SHA':sha(raw),'PROTOCOL':args.protocol,'REMOTE_ROOT':args.remote_root,
            'INPUT_FILE':filename,'SAMPLES':info['samples'],'LENGTH':info['length'],'CLASS_LABELS':info['class_labels'],
            'TRAIN_SAMPLES':info['train_samples'],'VAL_SAMPLES':info['validation_samples']}
        (root/'baseline.py').write_bytes(render(constants))
        # Keep the initial model equal to the protected trainer's model, without importing it.
        source=(root/'baseline.py').read_text(encoding='utf-8');tree=ast.parse(source)
        model=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='model' for t in n.targets))
        expression=ast.get_source_segment(source,model.value)
        code=('import torch\nfrom torch import nn\n\ndef build_model(config):\n    c=config["channels"]\n    return '+expression+
            '\n\ndef build_optimizer(parameters,config):\n    return torch.optim.Adam(parameters,lr=config["lr"])\n'+
            '\n\ndef build_training_loss(config):\n    return nn.CrossEntropyLoss()\n')
        ast.parse(code);(root/'model.py').write_text(code,encoding='utf-8')
        atomic_json(root/'asset-spec.json',{'namespace':args.namespace,'name':args.name,'version':'1.0.0',
            'file':str(root/filename),'sha256':sha(raw),'source_ref':args.source_url})
        inputs={'baseline_source':ref(root/'baseline.py'),'baseline_model':ref(root/'model.py'),
            'comparison_identity':{'protocol':args.protocol,'source_sha256':sha(raw),'split_sha256':sha(split_raw),
                'selection':'max validation macro_f1 then min validation loss; earliest tie'},
            'workload':{'version':1,'adapter':'binary-component/v2','training_loss_hook':True,
                'seed_values':[42],'source_ref':args.source_url},
            'dataset_input':{'spec':ref(root/'asset-spec.json'),'state':str(root/'asset-state.json')}}
        atomic_json(root/'study-inputs.json',inputs)
        saved={'schema':'sdm-public-ts-input/v1','binding':binding,'status':'prepared',
            'files':{p.name:sha(p.read_bytes()) for p in root.iterdir() if p.is_file() and p.name not in ('preparation.json','.public-input.lock')},
            'study_inputs':ref(root/'study-inputs.json'),'metadata':info,'local_ml_executed':False,'platform_writes':0}
        atomic_json(manifest,saved);return saved


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source-url','checksum','name','protocol','namespace','remote-root','output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--checksum-algorithm',choices=('md5','sha256'),default='sha256')
    args=parser.parse_args();print(json.dumps(prepare(args),ensure_ascii=False))
