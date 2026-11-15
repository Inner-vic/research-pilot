"""SDM-local capability declarations and immutable shared-evidence projection."""
import json
import os
import re
from pathlib import Path

from project_driver import file_digest


def pinned_json(ref):
    if not isinstance(ref,dict) or set(ref) != {'path','digest'}:
        raise ValueError('pinned_json_reference_required')
    path=Path(ref['path'])
    if not path.is_absolute() or path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('bounded_absolute_regular_json_required')
    if file_digest(path)!=ref['digest']:
        raise ValueError('pinned_json_changed')
    return json.loads(path.read_bytes())


def capabilities(config, selected, provider, cleanup_required=True):
    """A checked declaration proves compatibility, not implementation correctness."""
    required=selected|({'cleanup'} if cleanup_required else set())
    if not required:return {'status':'declared','missing_nodes':[], 'qualification':'all-selected-operations-owned-by-external-adapters'}
    ref=config.get('driver_capabilities')
    if ref is None:
        return {'status':'unverified','reason':'project_capability_declaration_missing',
                'missing_nodes':sorted(required,key=str)}
    value=pinned_json(ref)
    if not isinstance(value,dict) or value.get('schema') not in {'sdm-driver-capabilities/v1','sdm-driver-capabilities/v2'} or value.get('provider')!=config['provider']:
        raise ValueError('driver_capability_identity_mismatch')
    if value.get('driver_digest')!=file_digest(provider) or value.get('graph_revision')!=3:
        raise ValueError('driver_capability_binding_mismatch')
    nodes=value.get('nodes')
    if not isinstance(nodes,dict):raise ValueError('driver_capability_nodes_required')
    for key,row in nodes.items():
        if not isinstance(row,dict) or any(not isinstance(row.get(field),str) or not row[field].strip()
                for field in ('input_contract','output_contract')):
            raise ValueError('driver_input_output_contract_required:'+key)
    missing=sorted(required-set(nodes),key=str)
    return {'status':'incompatible' if missing else 'declared', 'missing_nodes':missing,
            'manifest_digest':ref['digest'],'driver_digest':value['driver_digest'],
            'qualification':'declaration-not-behavioral-proof'}


def store_record(task,name,record,*,equal_json=False,conflict='producer_output_conflict'):
    """Publish one immutable record, optionally as a section of a shared file."""
    from review_storage import atomic_json,locked
    import hashlib
    ref=task['orchestration'].get('records',{}).get(name,{})
    path=Path(ref.get('path',''))
    if not path.is_absolute() or path.is_symlink():raise ValueError('absolute_record_output_required')
    raw=json.dumps(record,ensure_ascii=False,sort_keys=True).encode('utf-8')
    if len(raw)>1024*1024:raise ValueError('bounded_record_required:'+name)
    path.parent.mkdir(parents=True,exist_ok=True)
    lock='.record-'+hashlib.sha256(str(path).encode()).hexdigest()[:16]+'.lock'
    with locked(path.parent,lock,'record_publication_busy'):
        if path.is_symlink():raise ValueError('absolute_record_output_required')
        section=ref.get('section')
        if section:
            if section!=name:raise ValueError('record_bundle_section_mismatch')
            if path.exists():
                if not path.is_file() or path.stat().st_size>14*1024*1024:raise ValueError('bounded_record_bundle_required')
                value=json.loads(path.read_bytes())
                if value.get('schema')!='sdm-orchestration-bundle/v1' or not isinstance(value.get('records'),dict):
                    raise ValueError('record_bundle_schema_mismatch')
            else:value={'schema':'sdm-orchestration-bundle/v1','records':{}}
            if section in value['records']:
                if value['records'][section]!=record:raise ValueError(conflict)
            else:
                value['records'][section]=record
                if len(json.dumps(value,ensure_ascii=False,indent=2).encode())>14*1024*1024:
                    raise ValueError('bounded_record_bundle_required')
                atomic_json(path,value)
        elif path.exists():
            previous=path.read_bytes()
            if (json.loads(previous)!=record if equal_json else previous!=raw):raise ValueError(conflict)
        else:
            with path.open('xb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
    from orchestration import record_receipt_digest
    return record_receipt_digest(path,section)


def produce(task, name, root=None, budget=30):
    """Project pre-existing records from one pinned bundle; never create human consent."""
    settings=task['orchestration']
    spec=settings.get('record_producers',{}).get(name)
    if spec is None:return None
    if name=='confirmation':raise ValueError('confirmation_cannot_have_automatic_producer')
    if not isinstance(spec,dict):raise ValueError('known_record_producer_required')
    production=None
    if spec.get('kind')=='work-package-material':
        from work_packages import material_record
        record=material_record(task,name,spec)
        production={'kind':'work-package-material','source_digest':spec['source']['digest'],
                    'qualification':'shared-project-material-not-new-review'}
    elif spec.get('kind')!='bundle-projection':
        from record_collectors import collect
        record,production=collect(task,name,spec,root,budget)
    else:
        if set(spec)!={'kind','source'}:raise ValueError('known_record_producer_required')
        value=pinned_json(spec['source'])
        if not isinstance(value,dict) or value.get('schema')!='sdm-evidence-bundle/v1' or not isinstance(value.get('records'),dict):
            raise ValueError('shared_evidence_bundle_required')
        record=value['records'].get(name)
    if not isinstance(record,dict) or record.get('schema')!='sdm-orchestration-'+name.replace('_','-')+'/v1':
        raise ValueError('bundle_record_required:'+name)
    record_digest=store_record(task,name,record)
    if production is not None:return {**production,'record_digest':record_digest}
    return {'kind':'bundle-projection','source_digest':spec['source']['digest'],
            'record_digest':record_digest,'qualification':'project-evidence-not-new-review'}


def validate_producers(task):
    from orchestration import RECORDS
    producers=task.get('orchestration',{}).get('record_producers',{})
    if not isinstance(producers,dict) or set(producers)-set(RECORDS.values()):
        raise ValueError('known_record_producers_required')
    if 'confirmation' in producers:
        raise ValueError('confirmation_cannot_have_automatic_producer')
    for name,spec in producers.items():
        if isinstance(spec,dict) and spec.get('kind')=='work-package-material':
            from work_packages import material_record
            material_record(task,name,spec)
            continue
        if isinstance(spec,dict) and spec.get('kind') in {'local-collector','review-import'}:
            from record_collectors import validate
            validate(name,spec);continue
        if not isinstance(spec,dict) or set(spec)!={'kind','source'} or spec['kind']!='bundle-projection':
            raise ValueError('known_record_producer_required')
        ref=spec['source']
        if not isinstance(ref,dict) or set(ref)!={'path','digest'} or not isinstance(ref['path'],str) or not Path(ref['path']).is_absolute() or not isinstance(ref['digest'],str) or not re.fullmatch(r'sha256:[0-9a-f]{64}',ref['digest']):
            raise ValueError('pinned_json_reference_required')
