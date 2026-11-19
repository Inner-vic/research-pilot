# 外部操作执行合同（v1）

独立的官方能力管理补充入口见[lpai-manager.md](lpai-manager.md)：统一资源
读取、配置预览、受授权管理和未知结果对账。它复用本合同的共享Guard，
不自动迁移冻结实验，也不替代协调器的审议、监控与释放核验。

用于普通任务的新平台适配配置。保留 graph revision 3、34节点及既有依赖、
门禁、S13、清理与重试策略；不迁移或改写正在执行的任务。

## 实际入口与职责

`task_pipeline.execute` 识别驱动中的 `external_operation`，调用固定的
`scripts/external_operations.py`。`run_project_pipeline.py` 将项目配置的
`external_operations`（节点ID到合同的映射，支持cleanup）传给相同入口。
历史驱动保持原绑定；新合同不自动改造已密封的活跃任务。

| P0能力 | 可执行行为 | 拦截边界 |
| --- | --- | --- |
| 官方能力选择 | 先探测候选官方SDK/CLI；缺本操作能力再转浏览器/Computer Use，最后才是有文档的自研适配器 | 审批、认证和写入错误不自动触发换路径重试 |
| 写后核验 | 回包提供候选UID，再核对provider/namespace/owner/UID/component/request_key及新鲜观察 | 退出0、停止受理、任务创建不能替代释放或训练成功 |
| 失败归因 | diagnose及失败驱动的failure_evidence进入统一分类器 | 平台标签和命令回显不能证明运行时OOM，分类器不自动改科学参数 |
| 协调器接入 | 两个公开入口接相同执行模块，项目提供具体命令和归一化事实 | 节点5/15/16仍要求accepted S13 |
| 切换事务 | 核验新配置→停止旧UID→任务/Pod释放读回→完整去重查找→提交一次→新UID读回 | 旧任务非终态、活动Pod、错误身份、不明提交结果均禁止新提交 |

`scripts/lpai_operation_adapter.py` 提供实际官方SDK映射，Pod读回使用官方
`lpai-agent`，不使用裸HTTP。probe只查本地依赖，不查凭据或调用平台。
SDK500且进程退出0不能算成功；SDK列表不能证明完整分页时明确拒绝。
它只更新已存在且未运行的新组件；创建由已授权的项目准备路径处理。
prepare读回主命令、角色、总卡数、源码和模型/数据挂载后才返回核验成功。

## 输入与返回

`external_operation`对象：

- schema=`sdm-external-operation/v1`；固定provider、namespace、owner、request_key。
- submit/switch必须提供项目共享的submission_registry绝对目录；同一逻辑提交在各attempt、SDK/CLI及UI适配器中使用同一目录与request_key，不使用attempt专属目录。改变组件或配置不能通过复用同一键绕过。
- action为read/diagnose/submit/stop/switch。stop/switch指定old_uid；
  submit/switch指定新component_uid；switch指定configuration_digest。
- 写操作的authorization_ref为绝对path及文件字节SHA256；文件绑定provider、
  namespace、owner、request_key、action、old_uid、component_uid、configuration_digest，
  含direct_user_confirmation=true和真实source_ref。它只绑定既有用户授权，
  适配器不得自行生成确认，节点28的接受合同继续适用。
- routes包含id、kind、adapter_refs及commands。kind为official-sdk/official-cli/
  browser/computer-use/custom；同级按声明顺序选择。adapter_refs固定项目适配代码
  和配置的绝对路径/字节SHA256；custom还需documentation_ref。argv不得含凭据。
- commands为结构化argv，支持{uid}与{request_key}，不经过shell。
  probe回`{status:ok,data:{available,operations}}`；其余回`{status:ok,data:...}`。
  code/http_status/error和审批字段必须保留或明确报错，不得包装成无条件ok。
- switch需要prepare/stop/read-old/submit/lookup/read-new；submit需要
  submit/lookup/read-new；stop需要stop/read-old；其他需要同名命令。
- 读回含provider/namespace/owner/uid/status/observed_at；新任务还含request_key和
  component_uid；释放包含明确released及完整pods列表。观察默认120秒内有效。
  lookup返回complete及matches；多匹配拒绝，未知写入后零匹配也不能重提。

LPAI命令：`<项目SDK解释器> -B <lpai_operation_adapter.py> --config <私有配置>
--operation <操作> --uid {uid} --request-key {request_key}`。私有配置包含
namespace/owner/cluster/component_uid/task_name/total_gpus、configuration_ref、项目共享submission_registry及
五项task_info，使用既有官方SDK配置结构。解释器或依赖缺失不自动安装。
CPU测试不证明生产账号权限、实际分页、GPU调度或训练兼容。
新项目可调用`lpai_operation_adapter.operation_binding(config_path, sdk_python,
action, request_key, authorization_ref, old_uid)`生成节点合同；它固定适配器、
私有配置及目标组件配置的文件摘要，不导入SDK、不调用平台。
将返回对象放入项目`external_operations["14"]`或明确对应驱动；
如需浏览器补缺，另外声明已固定的UI适配路径，不能把任意shell命令称为Computer Use。

## 恢复、异常与边界

所有写入前保存意图，选择路径和响应固定在`<node>-external-state.json`；
每次调用复核适配文件摘要。中断后根据原UID和request_key核对，不重写意图。
协调器原started/receipt恢复规则不变：缺回执时返回reconciliation，不自动重派
写入；独立对账不得覆盖旧失败回执或自动解锁。未扩大REC-01独立验收合同。

失败驱动可给failure_evidence：training_started、exception_type、
`lines:[{kind,text}]`，kind区分runtime-exception/command-echo/平台标签。
训练前AssertionError为preflight-assertion；实际模型CUDA异常支持runtime-cuda-oom；
其余unresolved。明确区分原始证据、分类和参数决策。

不创建自动监控，不改变远程任务时限；本地驱动超时不是训练停止期限。
所有CPU回归使用假客户端和本地进程，证据不得表述为真实平台或训练结果。

## 待确认提交与原生替代的去重（P0）

本节针对新组件/训练任务的提交事务。已确定UID的开发空间启动/停止，
若用户明确指定正常原生界面操作，按[training-operations.md](training-operations.md)
中的现有资源生命周期规则处理；不得把Agent网关卡片推断成原生页面的必经流程。
此范围区分不自动解除任何真实审批，也不允许重复创建新训练任务。

`pending_approval`不是提交成功，也不是“没有副作用”。只保存approval_id、status、mode与来源route，不复制收件人或原始敏感回包。共享提交台账按provider/namespace/owner/request_key定位，并绑定component_uid/configuration_digest。跨attempt、跨已声明路径和并发进程须共享同一台账；真实不同实验使用不同逻辑键并各自遵守原授权。

写入前持久化提交意图；待确认或未知结果占用该键。待确认后SDK/CLI、浏览器或Computer Use均不得另行提交；普通恢复不重派。任务列表为空不能证明旧审批已撤销，显示“取消”但仍可再次执行也不够。禁止仅在说明中标注“已取代”、删状态文件或换request_key掩盖同一次重提。

### 独立只读入口

```text
python task_pipeline.py --task <original-task.json> --output <original-attempt> --reconcile-submission <request.json>
```

请求字段：schema为sdm-submission-reconciliation/v1；node为原提交节点ID；task_digest为当前原任务摘要；pending_approval精确包含共享台账保存的status/approval_id/mode（仅已存在字段）；reconciliation_id为唯一目录名；driver为明确read_only:true的只读归一化驱动，receipt固定为original-attempt/submission-reconciliation/<node>/<id>/readback.json。

读回payload包含task_node:submission-readback、status:completed、scope（provider、namespace、owner、request_key、component_uid、configuration_digest，精确匹配）及：

- approval：相同approval_id，status必须cancelled/rejected/expired；irreversible:true、no_future_execution:true；observed_at为120秒内Unix秒，source_ref指向真实来源。适配器必须能证明已不可再次执行，不能由agent自行补造布尔值。
- lookup：complete:true、matches数组、observed_at、source_ref。必须在闭合证明观察时间之后完成完整去重查询，且观察不超过120秒；包含该逻辑请求在全部可能提交路径的任务，不以组件名部分匹配或某页为空替代完整性。
- matches最多一项；存在时精确核验provider/namespace/owner/UID/component/request_key及新鲜观察，后续仅认领同UID。多任务、错身份、部分分页、旧查询或仍待确认均保持阻塞。

对账仅回读，不取消审批、不提交、不停止、不改变原failed回执或journal，也不继续训练。零匹配时记为resolved-no-effect；唯一匹配时记为completed并固定UID。恢复后必须走新的合法attempt和原门禁，不在旧attempt清除intent后重跑。正常执行仍先按原SDK/CLI优先级探测，不因一次审批响应自动换路径。

请求、证明摘要和前序解决记录均保留；重复同一请求不再调用驱动。下一次新的待确认请求有独立证明，不能复用上一次闭合结论。OS锁串行化同一逻辑请求，进程中断保留意图；仅共享台账已保存待确认回包而本地响应尚未落盘时，仍可按原状态路径与驱动绑定对账。

### 能力边界

当前LPAI适配器未声明网关审批撤销或不可逆状态回读能力；不得发明官方接口、自动发飞书或要求用户发消息绕过。缺少可核验闭合接口时保持blocked，原生替代也不执行；真实支持何种取消/过期语义需另行查证。此批仅实现通用保护、已声明只读适配合同与CPU模拟，不声称真实网关已经支持撤销。

手工在协调器之外点击平台不受本地锁约束；Skill要求此类原生操作通过绑定适配路径和同一逻辑键执行。本机制不去改造已提交任务或历史CLI待确认请求。旧密封回执仍可按原恢复规则读取；缺共享目录的新提交不能自动降级为局部去重。
