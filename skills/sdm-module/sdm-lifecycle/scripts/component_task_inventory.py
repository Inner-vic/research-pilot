"""Resumable official inventory; complete native recipes without N redundant GETs."""
import hashlib
import time
import uuid
from pathlib import Path
from review_storage import atomic_json,read_json
from task_pipeline import digest
from research_replan import once
from component_payload import ref


class InventoryReadRace(ValueError):
    read_only=True
    retryable=True
    def __init__(self):super().__init__('task_inventory_read_raced')


def fence(value):
    return digest({'total':value['total'],'rows':[(r['uid'],r.get('project_uid'),digest(r.get('data')))
        for r in value.get('items') or []]})


def collect(manager,component_uid,parent_uid,root,on_progress=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    scope={'namespace':manager.config['namespace'],'owner':manager.config['owner'],'component_uid':component_uid}
    once(root/'scope.json',scope)
    count={'list_reads':0,'individual_reads':0,'reused_individual_reads':0}
    def progress(phase,**details):
        value={'schema':'sdm-inventory-progress/v1','scope':scope,'phase':phase,'at':time.time(),**count,**details,
            'platform_writes':0,'authority':'Read-only preflight progress, not task submission.'}
        atomic_json(root/'progress.json',value)
        if on_progress:on_progress(value)
    def page(index):
        progress('listing',page=index)
        backend=manager.backend
        if callable(getattr(backend,'owned_task_page',None)):value=backend.owned_task_page(index)
        else:value=backend.cli(['tasks','list','--namespace',scope['namespace'],'--owner',scope['owner'],
            '--page-size','100','--page-num',str(index)])
        count['list_reads']+=1
        rows=value.get('items') or []
        if type(value.get('total')) is not int or value['total']<0 or not isinstance(rows,list):
            raise ValueError('complete_official_task_inventory_required')
        if any(not isinstance(r,dict) or not r.get('uid') or r.get('namespace')!=scope['namespace']
                or r.get('owner')!=scope['owner'] for r in rows):
            raise ValueError('owned_task_inventory_scope_mismatch')
        return value
    first=page(1);signature=fence(first);checkpoint=root/'checkpoint.json'
    saved=read_json(checkpoint) if checkpoint.exists() else None
    if saved and saved['scope']!=scope:raise ValueError('inventory_checkpoint_scope_changed')
    generation=saved['generation'] if saved and saved['first_fence']==signature else uuid.uuid4().hex
    folder=root/generation;folder.mkdir(exist_ok=True)
    atomic_json(checkpoint,{'scope':scope,'generation':generation,'first_fence':signature})
    once(folder/'page-001.json',first) if not (folder/'page-001.json').exists() else None
    # Reusing a partial scan requires identical native-config/UID first page.
    # Fresh first-page status is retained separately; timestamps may advance.
    first_path=folder/('first-observation-'+uuid.uuid4().hex+'.json');once(first_path,first)
    items=list(first.get('items') or []);refs=[];index=1
    while len(items)<first['total']:
        index+=1
        if index>100:raise ValueError('task_inventory_pagination_incomplete')
        path=folder/('page-%03d.json'%index)
        if path.exists():value=read_json(path)
        else:value=page(index);once(path,value)
        if value['total']!=first['total'] or not value.get('items'):raise InventoryReadRace()
        items.extend(value['items']);refs.append(ref(path))
    if len(items)!=first['total'] or len({r['uid'] for r in items})!=len(items):raise InventoryReadRace()
    selected=[r for r in items if r.get('project_uid')==component_uid];tasks=[]
    for index,row in enumerate(selected,1):
        if row.get('namespace')!=scope['namespace'] or row.get('owner')!=scope['owner']:
            raise ValueError('owned_task_inventory_scope_mismatch')
        if isinstance(row.get('data'),dict) and isinstance(row['data'].get('lpai_task'),dict):
            tasks.append(row)
        else:
            path=folder/('task-'+hashlib.sha256(row['uid'].encode()).hexdigest()+'.json')
            if path.exists():actual=read_json(path);count['reused_individual_reads']+=1
            else:actual=manager.checked('task',row['uid']);count['individual_reads']+=1;once(path,actual)
            if any(actual.get(k)!=scope[k] for k in ('owner','namespace')) or actual.get('uid')!=row['uid'] or actual.get('project_uid')!=component_uid:
                raise ValueError('owned_task_inventory_scope_mismatch')
            tasks.append(actual);refs.append(ref(path))
        if index%10==0 or index==len(selected):progress('checking-configurations',completed=index,total=len(selected))
    if parent_uid not in {r['uid'] for r in tasks}:raise InventoryReadRace()
    last=page(1);last_path=folder/('final-fence-'+uuid.uuid4().hex+'.json');once(last_path,last)
    if fence(last)!=signature:raise InventoryReadRace()
    refs=[ref(first_path),*refs,ref(last_path)]
    progress('complete',completed=len(tasks),total=len(tasks))
    return {**scope,'complete_at_read':True,'tasks':tasks,'read_provenance':{
        'schema':'sdm-official-task-inventory-read/v1','transport':manager.config.get('component_transport','official-sdk'),
        'generation':generation,'counts':count,'pages':refs,
        'scope':'Native configurations are present in official list rows, with exact-UID GET only for omitted data; fresh final first-page fence rejects pagination races. Status may advance during the scan.'}}
