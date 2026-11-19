"""Minimal command-provider protocol example, for local CPU engineering only.

It hashes an integer sequence; it does NOT train/evaluate a model, execute a
supplied shell command, or implement SDM decisions. Job JSON is provider-side
state. The original Manager/Guard remains the client-side submission owner.
Usage: python engineering_provider.py OPERATION PROVIDER_STATE_DIRECTORY
Input/output: one JSON object on stdin/stdout. Pin this file's bytes in the
project manager configuration. This is a demonstration, not a remote scheduler.
"""
import hashlib
import json
from pathlib import Path
import sys
import time
import uuid


def main(operation, directory, value):
    root=Path(directory).resolve()
    scope={'namespace':'engineering-demo','owner':'local-example',
           'resource_uid':'local-cpu','configuration':{'cpu':1,'gpu':0,'operation':'integer-checksum'}}
    if operation=='probe':return scope
    if operation=='lookup':
        request=value['request']
        rows=[json.loads(p.read_bytes()) for p in root.glob('*.json')]
        return {'candidates':[r['uid'] for r in rows if r['request_key']==request['request_key']]}
    if operation in ('read','collect'):
        uid=str(uuid.UUID(value['uid']))
        row=json.loads((root/(uid+'.json')).read_bytes())
        return row if operation=='read' else {'uid':uid,'logs':[json.dumps(row['result'],sort_keys=True)]}
    if operation!='submit':raise ValueError('unknown operation')
    request=value['request'];data=request['data']
    if data.get('purpose')!='engineering' or data.get('operation')!='integer-checksum':
        raise ValueError('This example accepts only non-ML integer checksums.')
    count=data['count']
    if type(count) is not int or not 1<=count<=100000:raise ValueError('bounded integer workload required')
    root.mkdir(parents=True,exist_ok=True)
    # Never replay a provider-side submission key as a new job.
    if main('lookup',root,{'request':request})['candidates']:raise ValueError('request already exists; lookup only')
    uid=str(uuid.uuid4());started=time.time()
    actual=hashlib.sha256(','.join(str(i*i) for i in range(count)).encode()).hexdigest()
    payload='sha256:'+hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    row={**scope,'uid':uid,'request_key':request['request_key'],'cluster':request['cluster'],
         'payload_digest':payload,'status':'succeeded','started_at':started,'finished_at':time.time(),
         'result':{'count':count,'sha256':actual,'ml_executed':False}}
    with (root/(uid+'.json')).open('x',encoding='utf-8') as out:json.dump(row,out,ensure_ascii=False,sort_keys=True)
    return {'uid':uid}


if __name__=='__main__':
    print(json.dumps(main(sys.argv[1],sys.argv[2],json.load(sys.stdin)),ensure_ascii=False,allow_nan=False))
