# 能力触发、执行与证据生产合同

本合同保留graph revision3的驱动/回执身份；workflow_revision1将业务合并为五阶段11个工作包，最终依赖由workflow_definition维护。材料合并、条件评审与清理恢复见[work-packages.md](work-packages.md)。
能力目录、配置绑定、实际执行、证据验收是四种不同事实，不可相互代替。

## 执行顺序

1. 明确 objective、code_change、baseline_required、robustness_required 和 source_isolation。
2. 从公开入口生成触发矩阵：
   `python scripts/task_pipeline.py --task TASK.json --output RUN --inspect-triggers`。
   此命令不创建运行、不派发驱动、不查询平台。正式调用项目入口时，协调器自动在
   attempt 目录保存 `trigger-matrix.json`，它是该次调用开始时的绑定快照。
3. 对每项 required project-driver 提供实际项目驱动；可选检查只在条件或显式绑定成立时执行。
   record-gate 只校验证据，不调用同编号项目驱动；`ignored_builtin_drivers` 列出被忽略的绑定。
4. 项目负责人或适配器在预声明路径生产记录。`producer` 是职责说明，并不表示存在自动执行器。
   如需从冻结共享证据包按需生产，使用[execution-contracts.md](execution-contracts.md)的record_producers合同。
   资源观察优先官方SDK/CLI；确认记录必须来自真实用户；评审必须来自选定评审者。
5. 首次计算资源动作前检查全部尚未执行的必需项目驱动和独立清理驱动；缺失或不同记录共用
   同一路径则失败，返回 `execution_bindings_incomplete_before_acquisition`，不获取资源。
   该检查只确认绑定存在，不证明驱动或平台可用。不将运行后才能产生的证据误判为启动前缺失。
6. 门禁等待时读取 `waiting.next_action`：生产者、记录类型、动作和恢复方法均明确列出。
   在同一预声明路径补充真实记录后，用相同任务和输出目录恢复；不得重放已完成操作。
   配置或驱动需修改时，保留旧目录，建立新attempt；未知外部副作用必须先对账。
7. 验收区分 `completed_project_drivers`、`accepted_record_gates`、`unmeasured`。
   项目驱动完成不自动成为平台训练成功；模拟结果不成为模型质量证据。

## 重复检查的处理边界

- 已接受节点由持久journal恢复，不重复执行；历史receipt摘要仍须验证，不能作为冗余删除。
- 24验证协议，25验证方法适用性；19验证事实一致，32验证结论充分；20提出建议，33选择决策。
  它们允许引用共享证据，但不能相互代替验收。
- 多种记录错误地绑定同一路径会在矩阵显示碰撞，并在资源动作前拦截。共享evidence_ref合法。
- 暂不缓存资源时效、授权校验或同一记录在不同节点语境下的验证；这些检查可能随时间失效。
- 没有执行的可选检查统一计为unmeasured，不能以recorded当作通过。

## 扩展架构的采用顺序

当前协调器承担项目级Runner职责；generic_project_loop承担实验轮次控制；训练框架仍负责
batch/epoch Loop。两种Loop不可混用。借鉴MMEngine的按需构建与Hook事件时，先定义
before_dispatch、after_receipt、on_wait、on_failure、before_cleanup等事件及失败合同；
不引入新Hook框架，也不让可选Hook绕过资源确认、提交幂等或清理责任。

Hydra式配置展开与冻结、MLflow式统一运行证据、DVC式依赖失效是后续内部合同改造方向；
Optuna可作为可选试验建议器；Ray Tune仅在需要其调度能力且与LPAI资源职责不重叠时评估。
不因借鉴概念而增加运行依赖，不修改已有模型训练后端。长期Loop和依赖失效的当前合同见
[long-loop-dependencies.md](long-loop-dependencies.md)，记录生产者见
[execution-contracts.md](execution-contracts.md)。这些能力分别依据执行文档中的专项证据验收，
不能从本触发矩阵的静态检查推出通过；尚未验证的项目/平台分支继续保留未知。
