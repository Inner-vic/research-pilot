"""SDM-derived workflow presentation; never evaluates scientific acceptance."""
import argparse
import json
from pathlib import Path

# Current activity IDs, not original SDM IDs. Focus determines the phase from
# metadata rather than a fixed five-phase or 21-step assumption.
ASSOCIATIONS={
 'reviewing-initial':((1,4,5),4),'reviewing-plan':((1,3,4,13),4),
 'preparing-implementation':((6,),6),'implementing':((6,),6),
 'freezing':((6,7),7),'preflighting-code':((7,8),8),'reviewing-code':((7,),7),
 'submitting':((9,),9),'initial-submitting':((9,),9),'watching':((10,),10),
 'result-ready':((11,12),12),'research-goal-met':((13,),13),'research-stopped':((13,),13)}

def validate_catalog(value):
    """Check display identities and phase partition, not scientific duties.

    Counts may change. Original references are explicit provenance, and new
    activities may have no original step when their extension is explained.
    """
    try:
        steps=value['steps'];phases=value['phases'];source_count=value['source_step_count']
        if (value['schema']!='sdm-lifecycle-narrative/v2'
                or value['numbering']!='researchpilot-workflow'
                or type(source_count) is not int or source_count<1 or not steps or not phases
                or any(type(r['step']) is not int for r in steps)
                or [r['step'] for r in steps]!=list(range(1,len(steps)+1))
                or any(type(p['id']) is not int or not p['steps'] for p in phases)
                or [p['id'] for p in phases]!=list(range(1,len(phases)+1))
                or any(type(s) is not int for p in phases for s in p['steps'])
                or [s for p in phases for s in p['steps']]!=list(range(1,len(steps)+1))
                or any(type(r['phase']) is not int or r['phase']!=next(p['id'] for p in phases
                    if r['step'] in p['steps']) for r in steps)
                or any(not isinstance(r['title'],str) or not r['title'].strip()
                    or not isinstance(r['sdm_steps'],list)
                    or len(set(r['sdm_steps']))!=len(r['sdm_steps'])
                    or any(type(s) is not int or not 1<=s<=source_count for s in r['sdm_steps']) for r in steps)):
            raise ValueError('sdm_narrative_catalog_invalid')
    except (KeyError,TypeError,StopIteration):
        raise ValueError('sdm_narrative_catalog_invalid') from None
    return value

def catalog():
    return validate_catalog(json.loads((Path(__file__).resolve().parent.parent /
                                        'references/lifecycle-map.json').read_bytes()))

def project(stage,initial_review_phase=None):
    data=catalog();ids,focus=ASSOCIATIONS.get(stage,((),None))
    if stage=='reviewing-initial' and initial_review_phase=='code':ids,focus=(7,),7
    by_id={r['step']:r for r in data['steps']}
    if any(i not in by_id for i in ids):raise ValueError('sdm_narrative_stage_binding_invalid')
    return {'schema':'sdm-lifecycle-view/v2','numbering':data['numbering'],'basis':data['basis'],
        'runtime_stage':stage,'active_phase':by_id[focus]['phase'] if focus else None,
        'phases':data['phases'],
        'related_steps':[{k:r[k] for k in ('step','title','sdm_steps')} for r in data['steps'] if r['step'] in ids],
        'scope':'当前流程步骤关联；原 SDM 编号仅表来源，不表示步骤已获规范接受。',
        'acceptance_inferred':False}

def describe(view):
    return ('；'.join(f"{r['step']} {r['title']}" for r in view['related_steps'])
            if view['related_steps'] else '当前状态未关联具体步骤，请核对原始记录')

if __name__=='__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description='查看从 SDM 演进的当前流程步骤；数量按本版目录确定，不执行门禁或外部操作。')
    parser.add_argument('--stage',help='已有运行状态；返回当前步骤及原 SDM 来源，不推断通过')
    parser.add_argument('--initial-review-phase',choices=('science','code'))
    args=parser.parse_args()
    print(json.dumps(project(args.stage,args.initial_review_phase) if args.stage else catalog(),ensure_ascii=False,indent=2))
