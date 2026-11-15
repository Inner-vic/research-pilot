"""Complete owner inventory and resource-based conflict detection, independent of names."""
TERMINAL={'failed','stopped','succeeded','completed','cancelled'}

def owner_inventory(api,owner):
    rows=[];total=None
    for page in range(1,101):
        result=api.list(owner=owner,page_size=100,page_num=page)
        if isinstance(result,dict) and 'result' in result:result=result['result']
        if not isinstance(result,dict) or type(result.get('total')) is not int:raise ValueError('inventory_total_required')
        if total is None:total=result['total']
        if result['total']!=total:raise ValueError('inventory_changed_during_pagination')
        items=result.get('items') or []
        if not items and len(rows)<total:raise ValueError('incomplete_inventory_page')
        rows.extend(items)
        if len({x['uid'] for x in rows})!=len(rows):raise ValueError('duplicate_inventory_rows')
        if len(rows)>total:raise ValueError('inventory_count_exceeds_total')
        if len(rows)==total:return {'items':rows,'total':total,'pages':page,'filter':{'owner':owner}}
    raise ValueError('bounded_inventory_incomplete')

def active_a800_conflicts(rows,checked,allowed_uid=None):
    conflicts=[]
    for row in rows:
        if str(row.get('status','')).lower() in TERMINAL:continue
        task=checked('task',row['uid']);cards=0
        for role in task['data']['lpai_task']['roles']:
            resource=role['resource'];count=role['replica']*resource['Gpu']
            if count:
                kind=resource.get('GpuType')
                if not isinstance(kind,str) or not kind:raise ValueError('active_gpu_type_unknown')
                if 'A800' in kind.upper():cards+=count
        if cards and task['uid']!=allowed_uid:
            conflicts.append({'uid':task['uid'],'status':task['status'],'cards':cards})
    return conflicts
