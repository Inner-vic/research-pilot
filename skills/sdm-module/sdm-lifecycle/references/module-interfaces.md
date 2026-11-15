# R/W/V 模块接口与状态所有权（revision 1）

适用当前 graph revision 3。可执行目录为 `scripts/capability_interfaces.py`，
由 `node_registry.py` 接入九个横向步骤。没有增加独立 Skill、节点、资格门禁或
第二套任务状态机；已有项目驱动 argv/receipt 协议保持兼容。
新的外部操作驱动可采用[external-operations.md](external-operations.md)，
由相同协调器调用；操作意图与平台读回是驱动证据，不是第二份节点接受状态。

## 调用与返回

| 模块/入口 | 必需输入 | 返回及边界 |
| --- | --- | --- |
| R 调查：companion；`resource_planning.discovery_route(value)` | 目的、账号/项目范围、已知对象与已有观察；调查不要求任务 UID | 观察来源与时间、目录覆盖、发现/假设/未知、下一采集路径；不产生资源使用权 |
| R 规划/确认：`orchestration.gate(task, '26'/'27'/'28')` | 任务 records；完整 inventory/proposal；真实用户确认及绑定摘要/期限 | completed 或等待/缺项；协调器接受并固定记录，不替用户确认 |
| R 获取/释放：项目驱动，经 `task_pipeline.execute(driver, root, key, budget)` | 固定 argv、receipt、attempt、请求身份、授权范围与原始截止；cleanup 独立入口 | task_node/status；平台适配器附真实请求/资源身份与读回。停止回包不等于释放 |
| W 隔离/冻结：`worktree_isolation` 或项目驱动 | 项目计划、确切源提交、路径/归属、解析后配置与输入身份 | 所有权及快照证据；失败保留现场；工作区删除不代表算力释放 |
| V：`review_collaboration.exchange(task, purpose)` | purpose=plan/result、模式、对应 records 及上下文版本 | context/context_digest/response_record；沿用既有评审响应合同；不能授予计算权限 |

协调器选节点与 attempt，传入证据引用、已有授权和截止时间。缺依赖返回
needs-driver/needs-skill；缺记录返回 awaiting-evidence；评审问题返回 awaiting-review；
缺确认返回 awaiting-user-confirmation。驱动异常/不确定副作用进入 reconciliation，
释放失败进入 cleanup-required。等待不是完成，失败证据不丢弃。

R/W 实际驱动及 cleanup 在执行前保存 `<node>-capability-call.json`，包含接口版本、
操作、attempt、驱动摘要及本次驱动原始截止。started 标记固定调用文件摘要；
恢复拒绝调用文件漂移，不更新旧截止。V 的真实上下文继续使用既有上下文摘要，
不另建一套会话记录。旧 started 标记仍按原绑定恢复，不补造历史调用文件。

## 唯一状态所有者

| 状态 | 唯一写入/接受者 | 其他模块的权限 |
| --- | --- | --- |
| 节点接受、执行顺序、已接受记录摘要 | task_pipeline 的 events.jsonl | 生产候选证据或返回结果，禁止直接改任务日志 |
| 任务原始截止、驱动 started/调用绑定 | task_pipeline | 恢复读取，不续期；平台租约另行约束 |
| 用户确认 | 用户真实对话是来源；协调器验证并接受 | R 提案、V 建议均不能生成确认 |
| 平台请求/资源身份、实际状态 | provider 为事实来源，项目平台适配器保存与读回 | 协调器固定回执；本地进程退出不推断平台停止 |
| cleanup 接受 | task_pipeline 的 cleanup.json | 平台适配器执行并提供同身份释放证据 |
| Worktree 归属与冻结快照 | worktree_isolation 受锁私有状态/Git marker；或已绑定项目后端 | 协调器只接受引用；不接管整个实验状态 |
| Pro 会话/交付/返回 | pro_review_session、pro_review_workflow 各自已有 CAS 记录 | V 组装上下文；协调器不直接改写会话状态 |
| 科学接受与最终决定 | 对应业务节点 25/34/19/32/20/33 | V 交付意见，不能代替业务节点作统一通过 |

## 中断与恢复

协调器使用 `.coordinator.lock` 的 OS 进程锁；文件保留，锁随进程退出释放。
不能删除锁文件来夺取另一个活跃进程的锁。旧 `coordinator.lock` 仍需人工/运维
核验没有活跃写入者后处理，不能自动删除。锁覆盖日志重放至最终清理。

1. 请求前已保存 started、尚无 receipt：不得重提，明确不确定副作用，先平台核对。
2. receipt 已落盘、日志尚未提交：只恢复原驱动绑定的 receipt，不重新调用平台，
   也不因恢复读取而要求续期；后续新计算仍须核验当前授权。
3. 已保存 cleanup：可以恢复历史回执及分析，不得在原 attempt 派发新的计算。
   需要新计算时建立新 attempt 并按当前范围检查资源与授权。
4. cleanup 自身中断：同样依据 started/receipt 恢复；不确定停止不得盲重试。
5. 硬杀进程无法依靠 finally 释放远程资源。平台实际运行必须有独立租约/释放监护；
   本地恢复测试不构成该平台能力的验收。

验收分别保留：真实本地进程中断、真实 Git 操作、平台同 UID 读回、平台请求/
执行/释放、签名 S13 接受和真实 Pro 交付。任一层通过不能替代其他层。
