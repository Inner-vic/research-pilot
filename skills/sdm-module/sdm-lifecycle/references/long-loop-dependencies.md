# 长期分段Loop与跨试验依赖失效

五阶段工作包保留单变量研究与资源授权边界；旧ID仅用于内部检查兼容。3轮和1800秒仅为开发默认值，不是应用上限。
不增加框架、后台调度、监控或付费评审。

## 预算与执行范围

| 配置 | 含义 | 可选值 |
|---|---|---|
| max_rounds | 整个Study的试验轮数 | 任意正整数；分段模式可为null，但须明确study_stop_criteria |
| long_loop.rounds_per_invocation | 本次调用最多推进的新轮数 | 任意正整数；历史轮次重放不计新轮数 |
| loop_timeout_seconds | 本次调用不再开始新轮次的时间预算 | 正整数或null；默认1800秒，不是Study总时长 |
| task_timeout_seconds / node_timeout_seconds | 协调器与驱动的调用时限 | 正整数，可超过1800秒；节点时限不超过任务时限 |
| task_wall_time_seconds | 一个attempt不可续期的总调用期限 | 正整数或省略；恢复保留原截止时间 |
| cleanup_timeout_seconds | 独立清理调用预算 | 正整数，可超过1800秒 |

这些是本地调用时限，不替代资源proposal中的排队、训练、费用和停止规则。放大调用时限不增加资源授权。
历史无loop_adapter的RoadModel兼容路径保持原协议；新项目使用通用适配器，不选择该兼容路径。

## 长期模式配置

通过原公开入口run_project_loop.py执行。在既有项目配置中显式加入：

```json
{
  "max_rounds": null,
  "study_stop_criteria": "达到已约定指标、证据不足需决策或用户暂停时停止",
  "long_loop": {"rounds_per_invocation": 1},
  "loop_timeout_seconds": 1800,
  "preparation_is_read_only": true,
  "segmented_cleanup_supported": true
}
```

1. 同一输出目录恢复同一Study；配置和既有计划、观察、决策保持不可变。
2. 一次仅推进配置数量的新轮次。历史观察重新核验，历史决策按history和decision摘要校验，不再发起重复评审。
   历史结果重放不重新写待分析交付或同步旧结论，避免重复发布；新观察或新决策仍正常交付。
   缺少新的分析响应时立即返回awaiting-analysis，释放本段资源，不在前台固定等待180秒。
3. 新轮次先由只读prepare_round生成资料；proposal.execution_scope必须精确为当前study_id与trial_id，确认摘要必须匹配。
   不同轮次不能复用上一轮授权。缺少记录返回awaiting-user-confirmation；完整人类确认、资源时效及队列合同仍由原门禁验证。
4. 分段适配器的cleanup_driver使用独立session目录，config.loop_output_root指向Study根目录；需正确保存自己的归属资料并只释放本段资源。
5. 每段保存独立cleanup-result.json并固定其摘要；失败或后续文件变化则下一调用不派发新轮次。不是重新启动已经释放的空间或allocation。
6. Study根目录存在PAUSE文件时，在轮次边界暂停；清除后可手工续接。此标志不强杀正在运行的训练。
7. OS锁覆盖整个分段调用；进程退出自动释锁。派发意图存在但没有清理证据的异常退出，恢复进入cleanup-required，不猜测释放或重提。
8. 达到调用预算暂停当前推进，不把它当Study达标。没有自动唤醒；外部评审建议仍须下一轮资源确认。

该模式支持长时间研究跨多次调用持续推进，不等于已经实现可无限等待的平台异步任务驱动。
外部训练任务的长运行、抢占与checkpoint恢复仍由对应项目驱动及真实平台验证负责。
session清理失败通过公开入口的独立只读对账模式处理；见[分段对账与历史上下文](session-history-recovery.md)。普通attempt/cleanup.json仍使用原入口，不混用回执。

## 跨试验失效与复用

项目配置dependency_inputs，明确goal、source、data、parameters、protocol、environment六种身份。
源码身份额外绑定驱动入口／依赖包；参数额外绑定实际loop_parameters或experiment_parameters；目标绑定objective，环境绑定driver_runtime。
生成dependency-invalidation.json：标出变化维度、依赖闭包、实际复用和未复用原因。

复用需显式reuse_from，指向上一次已保存coordinator-task.json及journal的绝对attempt目录。
长期模式在上一轮有完整attempt记录时自动选择上一轮作为候选，但仍执行全部复用检查。

- 默认每项检查依赖全部维度，参数变化也会重做。项目确认独立性后，可用check_dependencies为节点3、4、12声明更窄的维度；不能删除规定的最小依赖。
- 原驱动和新驱动必须均为只读；原节点必须已完成，回执摘要必须匹配。
- 回执须显式携带`reusable_check: {"scope":"immutable-input-check","self_contained":true}`，声明结果不依赖外部可变产物或实时状态。
- 任意外部操作，包括只读的SDK查询，也不从此缓存替换；缺标记、缺声明、依赖改变时正常重做。
- 训练、评估、预检、输入冻结、资源目录、预算、用户确认、清理、评审和结论门禁均不从该缓存跳过。
- 原任务还有未知副作用时先对账，不能通过缓存接续新执行。回执损坏或父任务摘要不匹配时失败，不当作普通缓存未命中。
- 复用在新attempt保存独立副本及来源摘要。覆盖统计列为reused_project_checks，不计新执行驱动。

例如只变学习率，项目可声明数据内容检查不依赖parameters；数据版本、源码或协议变化则数据检查及后续依赖重新处理。
对于augmentation、裁剪或输入形状等参数影响数据检查的项目，应保留parameters依赖，不能照搬例子。
声明身份仍是项目证据，不自动证明数据内容真实；真实资产内容与可用性需项目验证。
