# SDM执行器合同与共享证据生产

沿用SDM，不增加其他执行框架。五阶段11个工作包的最终依赖由workflow_definition维护；旧ID1—34保留为内部检查身份。合并材料会减少准备驱动调用，不省略必要验收，见[work-packages.md](work-packages.md)。

## 执行顺序

1. 任务按原objective和可选标志构图；从graph计算实际选择的project-driver节点。
2. 项目入口读取字节固定的能力声明，核对provider、graph_revision、driver_digest以及所选节点和cleanup的输入输出合同。
3. 将核验结果固定进该attempt任务，触发矩阵展示declared、incompatible或unverified。
   后两种状态不能进入计算资源动作。它们允许不计费的准备与证据收集。
4. 按当前固定依赖执行。缺少门禁记录时，若配置了bundle-projection或work-package-material，则从冻结共享材料生产记录；
   再经过原门禁的完整语义、时效和摘要检查。未配置生产者时保持显式等待。
5. 已有记录不被生产器覆盖；已接受节点通过journal恢复，不重新生产。输入或驱动变化需新attempt。
6. 正常清理仍走既有独立驱动；清理失败保留原回执，不能靠重新执行训练尝试清理。
   后续使用[cleanup-reconciliation.md](cleanup-reconciliation.md)的只读独立验收入口。

## 项目执行器能力声明

项目配置新增可选文件引用`driver_capabilities: {"path": "/absolute/capabilities.json", "digest": "sha256:..."}`。
配置可以不提供它以开展不计费准备，但新项目入口执行计算前必须完成声明核验。
直接task协调器的显式驱动合同仍保留；不能用它绕过项目入口的核验或真实资源授权。
既有不可变运行不迁移、不回填新字段；新运行使用新配置与新输出目录。

接入真实适配器前，可用`run_project_pipeline.py --project PROJECT --inspect-driver`静态检查已固定入口的显式CLI参数。该入口不创建运行目录、不导入或执行适配器；缺少握手/上下文参数列为迁移缺口，参数齐全仍须行为验收。动态构造参数的支持未由静态检查证明，不能仅凭扫描宣称业务兼容。旧RoadModel适配器等历史版本不就地修改，应在独立新版本完成迁移后再验收；不得为了探测接口执行可能有平台副作用的入口。

历史诊断回放应先核对原日志与原manifest摘要，再重算指标、核对评审事实并生成无执行授权的交付计划。原日志、历史评审及旧结论保持不变；新回放另存。历史评审重验不等于新一轮外部对话，日志中有限梯度也不能推断实际优化器更新。开放轮次预算在评审计划中保留null；停止决定、当前授权、时效与项目检查仍分别约束可执行性。

交付计划遇到压缩历史时，不将摘要行与完整原件列表直接比较。应重新核验归档原件及整体摘要，再以完整context历史的对应前缀摘要匹配该请求；后续追加允许保留，但当前轮变化仍触发原历史决定检查。改变中间轮次、归档原件或摘要必须拒绝，不能用缓存或压缩摘要代替验证。匹配成功也不解除停止决定、授权或时效检查；没有历史的停止评审不得因索引空列表而崩溃。

安装后回归必须保持正式安装树摘要。父Python进程的-B不会自动传给子进程；CLI测试子进程显式使用-B，回归环境向全部子进程传递PYTHONDONTWRITEBYTECODE=1。测试后再运行正式validate，不能只检查安装当时成功。新增字节码及空缓存目录也会改变完整树摘要；发现漂移先比较已验证副本，只对确认为新增生成物的确切文件备份并清理，不改配置摘要掩盖差异，不删除未知来源文件。

声明文件示例：

```json
{
  "schema": "sdm-driver-capabilities/v2",
  "provider": "project-owned-provider",
  "graph_revision": 3,
  "driver_digest": "sha256:ENTRY_FILE_DIGEST",
  "nodes": {
    "1": {"input_contract": "project-task/v1", "output_contract": "sdm-node-receipt/v1"},
    "cleanup": {"input_contract": "owned-allocation/v1", "output_contract": "verified-release/v1"}
  }
}
```

nodes必须实际列全本次选中节点，不能将示例中的1和cleanup当作完整训练适配器。
声明表示项目承诺支持接口，不是运行时探测或业务正确性证明；还需公开入口CPU行为测试及真实平台验证。
不自动执行未经适配的`--describe`命令，也不为核验调用SDK写操作。

## 共享证据生产合同

orchestration新增`record_producers`。每种记录可配置：

```json
{
  "record_producers": {
    "background": {
      "kind": "bundle-projection",
      "source": {"path": "/absolute/evidence-bundle.json", "digest": "sha256:..."}
    }
  }
}
```

共享文件结构为`{"schema":"sdm-evidence-bundle/v1","records":{"background":完整背景记录,"research":完整实验协议记录}}`。
其中每份记录继续采用既有`sdm-orchestration-.../v1` schema。输出位置仍是orchestration.records中的预声明绝对路径。
生产器只做可追溯投影，不推断事实、不替代评审、不创造用户授权。禁止为confirmation配置自动生产器。
评审结果若从bundle投影，必须先由所选评审者真实完成；原门禁继续验证上下文绑定和问题解决状态。

初始化只检查生产器配置形状，不读取尚未需要的证据包；每次按需生产核对源摘要。
已有记录不重新投影，不覆盖人工修订。若已有记录与共享包不同，以实际预声明记录接受检查；
不声称它来自共享包。缺失输出生成后，receipt保存源摘要和输出摘要供追溯。
投影失败不静默回退，也不自动改写bundle；修复输入后根据是否已产生状态选择相同等待任务或新attempt。

## 采用的设计边界

协调器承担项目级Runner职责；现有Loop承担试验轮次；模型训练框架管理batch/epoch。
“门禁前按需生产、回执后验收、finally独立清理”是现有SDM链路中的事件位置，暂不新增通用Hook插件系统。
身份冻结、共享记录和按需执行分别借鉴Hydra、MLflow、MMEngine的思想；未安装这些框架。
Optuna／Ray Tune／DVC仍是后续可选适配，不作为普通训练的新依赖。

## 直接采集与评审导入（P1）

record_producers现在支持三种实现：既有bundle-projection、仅用于inventory的local-collector，以及用于method_review、plan_review、review_response的review-import。触发位置仍为原门禁缺少记录时；已有记录不覆盖，已接受节点不重新生产。三类生产结果均须继续通过原有记录门禁；生产成功不是门禁通过，不替代校准、G10/S13或用户确认。

local-collector配置为：

```json
{"kind":"local-collector","python":"/absolute/python",
 "driver":{"path":"/absolute/collector.py","digest":"sha256:..."},
 "inputs":{"snapshot":{"path":"/absolute/export.json","digest":"sha256:..."}},
 "timeout_seconds":30}
```

协调器固定sdm-record-collection-request/v1请求并调用受摘要保护的采集器：`--request REQUEST --receipt RECEIPT`。inputs是具名、字节摘要固定、各不超过1MiB的JSON文件。回执为sdm-record-collection-result/v1，须含task_node=record-producer-inventory、status、record_kind、规范JSON的request_digest、input_digests、source_route和完整inventory record。source_route.kind优先official-sdk或official-cli；browser、computer-use、custom-adapter必须给出official_capability_gap。该字段记录项目提供的来源，不构成官方认证或代码沙箱。采集器仅获只读采集职责，不能提交、启停资源或创建授权。

内置collect_inventory_snapshot.py可直接接入已经导出的官方查询结果：snapshot文件采用sdm-resource-inventory-export/v1，包含inventory完整记录与source_route。它只读取本地文件，不主动查询平台，不推断平台原始字段或补造可用卡数。实时SDK/CLI查询仍由项目明确选择的只读适配器负责；未经真实平台验证不得声称已自动获得真实资源。适配器输出保留source_ref、账号、队列、配置、观察时效，原inventory门禁负责继续核验。

生产调用沿用协调器的启动标记、超时与回执机制；回执已落盘而记录尚未发布时，恢复复用原回执，保留首次调用的退出码和日志。中断后没有回执时明确阻塞，不自动重复派发。请求、驱动或输入改变后拒绝旧调用绑定，修复后使用新attempt。调用预算取配置与剩余任务预算的较小值。

review-import配置为`{"kind":"review-import","source":{"path":"/absolute/review.json","digest":"sha256:..."}}`。源文件采用sdm-reviewed-record/v1，包含record_kind、status=final、mode、reviewer、source_ref、context_digest、record。method_review绑定当前research摘要，plan_review绑定当前plan_context，review_response绑定当前review_context；record内的research_digest或context_digest必须一致。mode匹配任务选定的local-agent或pro。导入只承接已完成的评审，不发起Pro调用、不把机械采集包装成判断，也不保证文件中所称评审者身份已由外部认证。多轮问题、证据充分性和最终决策仍由原评审合同验收。

其余记录由原项目责任人提供或从固定证据包投影：背景由人机对齐，research由实验设计者完成，proposal由资源规划者完成，review_context由证据负责人准备，一致性、结论质量、下一步和转移由对应判断责任人完成。confirmation始终禁止自动生产。缺少生产器或真实证据时保留明确等待，不用通用模板补出“通过”。

## Driver behavioral handshake (P1)

Before new project dispatch, the public coordinator invokes the pinned guarded driver using `--handshake REQUEST --receipt RESPONSE`. Each selected project-owned node and cleanup must declare 2-8 behavior_cases, including valid and invalid inputs, with kind, input and expected objects. Expected results bind task_node and status; invalid cases require reason. The abbreviated example above omits cases and is not executable. Set handshake_protocol to sdm-driver-handshake/v1. A v1 declaration remains readable but cannot authorize fresh project dispatch.

The request uses sdm-driver-handshake-request/v1 and freezes provider, driver/manifest digests, contracts, cases and nonce. Expected answers are withheld. A random unsupported node must fail with reason unsupported_node. Response schema is sdm-driver-handshake-response/v1, containing request_digest (canonical JSON hash), nonce, execution_mode=offline-contract-test and results=[{case_id,result}]. Results must be unique, complete and exactly match frozen expectations. Implement cases through actual adapter validation and offline processing branches. This mode must not call platforms, allocate resources, use network or train; read_only is not an OS sandbox.

Acceptance is persisted as driver-handshake-accepted.json. Normal invocations receive --handshake-context REQUEST and must return driver_contract with schema=sdm-driver-execution-contract/v1, provider, driver_digest, manifest_digest, handshake_request_digest, node, input_contract and output_contract. Each runtime receipt is checked. Missing or changed contracts fail acceptance while retaining raw evidence. External operations retain their own authorization/readback contracts.

Resume uses the same nonce, immutable request and cached response; it does not redispatch accepted work. Changed source, contracts, cases or acceptance reject stale proof. Missing support, timeout, invalid results or crashes block before resource dispatch. Existing immutable runs are not migrated. Reused checks remain governed by existing dependency invalidation rules.

This proves offline protocol conformance only, not honesty, business correctness, GPU availability or training success. S13, resource authorization, all 34 nodes and existing dependency/cleanup policies remain unchanged.

握手的能力集合与实际调度共用当前graph的policy及builtin归属：只包含已选择的项目驱动和项目cleanup。门禁节点上被忽略的argv、不适用节点的遗留argv不进入集合；不能因为提供了argv而将原门禁变成项目执行器。未知或缺失的实际所选能力仍阻塞。畸形证据包、评审源及采集来源字段按合同失败归类，不将其当成新的业务节点或授权。

静态入口的literal_dispatch_branches和explicit_context_subscripts提供字面分支及直接上下文读取的源码行号，便于核对旧协议、执行合同与实际入口。它们不是完整覆盖证明；动态分派、get调用及其他间接行为不在此清单内。不得因参数齐全或发现节点编号，就声称离线握手安全或项目行为通过。对旧驱动发现固定小样本、硬编码时限、准备阶段资源动作和运行回执缺合同，保留migration-required；任何迁移在独立新版本进行，不修改活跃历史运行。
