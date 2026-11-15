"""Import an explicitly exported resource inventory; no SDK, network or writes to platforms."""
import argparse
import json
from pathlib import Path
from execution_contracts import pinned_json
from task_pipeline import digest


def main():
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    a=p.parse_args();request=json.loads(a.request.read_bytes())
    if request.get('schema')!='sdm-record-collection-request/v1' or request.get('record_kind')!='inventory' or request.get('execution_mode')!='read-only-collection' or set(request['inputs'])!={'snapshot'}:
        raise ValueError('inventory_snapshot_request_required')
    source=pinned_json(request['inputs']['snapshot'])
    if source.get('schema')!='sdm-resource-inventory-export/v1' or not isinstance(source.get('inventory'),dict) or not isinstance(source.get('source_route'),dict):
        raise ValueError('resource_inventory_export_required')
    result={'schema':'sdm-record-collection-result/v1','task_node':'record-producer-inventory','status':'completed',
        'record_kind':'inventory','request_digest':digest(request),
        'input_digests':{k:v['digest'] for k,v in request['inputs'].items()},'source_route':source['source_route'],
        'record':source['inventory']}
    with a.receipt.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False)


if __name__=='__main__':main()
