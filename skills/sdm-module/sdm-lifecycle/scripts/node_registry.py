"""Eleven business work packages; legacy check IDs and R/W/V call identities."""

GRAPH_REVISION = 3
PHASES = {
    'background': ('问题定义与需求梳理', (1, 2, 3, 22)),
    'research': ('方案设计与实验规划', (4, 6, 24, 25, 34)),
    'execution': ('实验开发与训练验证', (9, 10, 11, 12, 7, 13, 5, 15)),
    'review': ('结果评估与评审', (16, 17, 18, 19, 32)),
    'decision': ('结论输出与迭代决策', (20, 33, 21)),
}
CAPABILITIES = {
    'resource': ('R', '资源与预算', (26, 27, 28, 14, 30)),
    'workspace': ('W', '工作区与版本', (23, 8)),
    'review-collaboration': ('V', '评审协作', (29, 31)),
}
LABELS = {
    1:'任务与需求登记', 2:'问题、指标与验收边界', 3:'数据集与数据依据',
    22:'需求共识与背景对齐', 4:'模型与 baseline 选择', 6:'技术方案与研究假设',
    24:'实验与评估协议', 25:'方法适用性检查', 34:'方案评审与修订',
    9:'实现范围与接口准备', 10:'代码与配置实现', 11:'代码质量评审',
    12:'工程与输入验证', 7:'运行环境准备', 13:'执行预检',
    5:'baseline 复现验证', 15:'训练执行与过程监控', 16:'指标评估与对照分析',
    17:'专项质量验证', 18:'结果与证据汇总', 19:'证据一致性复核',
    32:'结论质量审查', 20:'结论与下一步建议', 33:'迭代或停止决策',
    21:'交付归档与任务接续', 26:'资源发现与调度调查', 27:'资源选择与预算方案',
    28:'用户确认与授权核验', 14:'平台执行请求与资源获取',
    30:'资源停止、释放与成本核对', 23:'工作区隔离与归属',
    8:'源码、配置与输入身份冻结', 29:'评审上下文准备与对齐', 31:'多轮评审对话与问题管理',
}


def registry():
    from work_packages import membership
    result = {}
    for index, (phase, (label, keys)) in enumerate(PHASES.items(), 1):
        for offset, key in enumerate(keys, 1):
            package=membership(str(key))
            result[str(key)] = dict(display_id=package['work_package_id']+'/'+str(key),
                                    legacy_display_id=f'{index}.{offset}', label=LABELS[key],
                                    **{k:v for k,v in package.items() if k!='phase'},
                                    owner_kind='phase', owner=phase, phase=phase, phase_label=label)
    for owner, (prefix, label, keys) in CAPABILITIES.items():
        for offset, key in enumerate(keys, 1):
            if str(key) in result:
                raise ValueError('duplicate_node_owner')
            result[str(key)] = dict(display_id=f'{prefix}.{offset}', label=LABELS[key],
                                    owner_kind='capability', owner=owner, capability=owner,
                                    capability_label=label)
            from capability_interfaces import for_node
            result[str(key)]['module_contract'] = for_node(key)
    if set(result) != {str(i) for i in range(1, 35)}:
        raise ValueError('complete_34_node_registry_required')
    return result


def summaries(nodes, state, kind):
    groups = PHASES if kind == 'phase' else CAPABILITIES
    result = []
    for owner, definition in groups.items():
        label = definition[0] if kind == 'phase' else definition[1]
        keys = [k for k in registry() if k in nodes and nodes[k].get('owner') == owner]
        required = [k for k in keys if nodes[k]['policy'] == 'required']
        result.append({kind:owner, 'label':label, 'nodes':keys,
                       'display_ids':[nodes[k]['display_id'] for k in keys],
                       'remaining':[k for k in keys if k not in state],
                       'failed':[k for k in keys if state.get(k,{}).get('status') in {'failed','skipped'}],
                       'required_node_count':len(required),
                       'required_completed_count':sum(state.get(k,{}).get('status')=='completed' for k in required)})
    return result


def capability_summary(nodes, state):
    return summaries(nodes, state, 'capability')


def mermaid(nodes):
    """Exact internal check dependencies (use business_mermaid for the main view)."""
    lines=['flowchart TB']
    for owner, (label, _) in PHASES.items():
        lines.append(f'  subgraph {owner}["{label}"]')
        for key, node in nodes.items():
            if node.get('owner') == owner:
                lines.append(f'    N{key}["{node["display_id"]} {node["label"]}"]')
        lines.append('  end')
    for owner, (_, label, _) in CAPABILITIES.items():
        lines.append(f'  subgraph cap_{owner.replace("-","_")}["独立能力：{label}"]')
        for key,node in nodes.items():
            if node.get('owner') == owner:
                lines.append(f'    N{key}["{node["display_id"]} {node["label"]}"]')
        lines.append('  end')
    for key,node in nodes.items():
        lines.extend(f'  N{dep} --> N{key}' for dep in node['depends'])
    return '\n'.join(lines)


def business_mermaid():
    """Five ordered phases, conditional branches and cross-cutting obligations."""
    return '''flowchart TB
  subgraph P1["1 问题定义与需求梳理"]
    A["1.1 任务约定"] --> B["1.2 数据与材料确认"]
  end
  subgraph P2["2 方案设计与实验规划"]
    C["2.1 模型与对照选择"] --> D["2.2 实验方案与审查"]
  end
  subgraph P3["3 实验开发与训练验证"]
    E{"是否需要实现"} -->|需要| F["3.1 实现与工程验证"]
    E -->|复用| G["3.2 执行准备：本地预检与身份冻结"]
    F --> G
    G --> H["资源授权与获取"]
    H --> I["3.2 远端环境预检；计算前放行"]
    I --> J{"实验目标"}
    J -->|仅评估| K["4.1 评估执行"]
    J -->|baseline/训练| L["3.3 运行实验；baseline按需先验证"]
  end
  subgraph P4["4 结果评估与评审"]
    M["4.1 评估与比较；专项按需"] --> N["4.2 结果审查：证据一致性与结论质量"]
  end
  subgraph P5["5 结论输出与迭代决策"]
    O["5.1 结论与行动选择"] --> Q["5.2 归档与迭代接续"]
  end
  B --> C
  D --> E
  D -.横向资源：实际队列、配置与预算.-> R["R 资源生命周期"]
  R -.确认范围.-> H
  K --> M
  L --> M
  L -.失败或保护停止.-> S["现场保留与诊断"]
  I -.失败.-> S
  S --> N
  M -.及时清理.-> T["R 停止读回、释放与成本对账"]
  S --> T
  T -.未闭合不宣称完整交付.-> Q
  N --> O
  Q --> U{"下一步与原授权范围"}
  U -->|修订并实际执行下一轮| D
  U -->|补证/复评| M
  U -->|结束且清理闭合| V["结束"]
  W["W 工作区与版本"] -.按需隔离、执行前冻结.-> G
  X["V 评审服务：上下文、追问与问题闭合"] -.方案/代码/结果按需调用.-> D
  X -.评审结果.-> N
'''
