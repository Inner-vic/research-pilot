# 清理失败后的独立对账

适用：协调器已产生失败cleanup.json，之后需要核查同一资源是否确已释放。
使用公开入口：

`python scripts/task_pipeline.py --task TASK.json --output ORIGINAL_ATTEMPT --reconcile-cleanup REQUEST.json`

项目入口的新运行保存attempt/coordinator-task.json，应将它作为TASK.json；project.json是项目驱动配置，不能替代协调器任务。
历史运行若没有该任务快照，需恢复与原journal摘要一致的任务，不能重新构造相似配置后强行接受。

此操作只派发已声明只读的核查驱动，不运行任务图、不停止资源、不提交任务。
优先绑定官方SDK/CLI读取；缺失能力再采用原外部能力路由。适配器必须真实只读，声明本身不是安全沙箱。

## 请求与证据

请求schema为sdm-cleanup-reconciliation/v1，字段如下：

- reconciliation_id：独立核查ID，只能使用字母、数字、下划线和连字符。
- original_cleanup_digest：原cleanup.json的字节SHA256摘要。
- ownership_node：原journal中已完成并记录资源归属的计算节点，例如14。
- identity_field：上述事件result内资源身份的点分路径，例如observation.resource_identity。
  身份必须是provider、namespace、uid三个非空字符串，不能手填另一个资源替代原归属。
- driver：沿用显式argv、receipt、timeout_seconds及可选external_operation合同；read_only必须为true，
  external_operation只能是read或diagnose。receipt必须为原attempt/cleanup-reconciliation/ID/readback.json。

驱动receipt的task_node固定为cleanup-readback，status为completed；还必须包含：

```json
{
  "resource_identity": {"provider": "实际提供方", "namespace": "实际命名空间", "uid": "原资源UID"},
  "resource": {"status": "stopped", "released": true, "pods_complete": true,
               "pods": [{"status": "Succeeded"}]},
  "observed_at": "带时区的真实观测时间",
  "source_ref": "可追溯的官方查询来源"
}
```

任务状态须归一化为stopped/failed/succeeded/completed/cancelled之一，Pod必须全部终止。
空Pod列表只有pods_complete=true且released=true才可接受；分页不完整不得标为complete。
接受时读回不超过5分钟且不在未来。来源、同UID、原事件、原失败和receipt摘要均需匹配。
旧运行若未保留可验证的资源身份，不自动认领资源或补造成功证据，应先完善项目归属适配。

## 接受与恢复

当前工作流在清理失败后允许继续初步诊断、结果审查和无执行权限的行动选择；最终归档与下一轮执行仍阻塞。独立对账完成后重入原尝试，只推进尚未完成的收尾，不重跑计算或停止。

成功后保存独立cleanup-reconciled.json；不覆盖cleanup.json、不改原失败回执或历史journal。
同一请求重复调用不重复派发。超时、缺回执等沿用未知副作用拦截，不盲目重派；先检查已有读回。
读回不合格、过期或仍运行时不接受；需要新的实际观察时使用新的reconciliation_id。

随后按原task与原attempt恢复协调器，节点30和最终收尾读取独立验收结果；此前已完成的训练与清理不重跑。
归档节点21此前被阻塞时可以首次执行。独立释放验收不清除其他节点的uncertain_effect，不授权下一轮。
summary完成表示本轮流程已收束，不等于模型训练成功。独立验收仍为项目证据，不是平台签名认证。

本批只对普通task协调器的cleanup.json接入。Loop外围另有cleanup-result.json，尚未映射到此入口，不能混用。
