# 研究记录 v2：方案、讨论、探索与焦点

通过原公开入口`run_project_pipeline.py --project PROJECT --research-event CHANGE.json`记录。
仅在已安装写入端支持v2时使用。旧v1五类型和数据库保留，不迁移历史，不为看板重写冻结任务。

v2沿用原请求幂等、项目绑定、事务、run引用、correction和证据校验。必须声明：

```json
{
  "schema":"sdm-research-event/v2",
  "project_id":"registered-project",
  "question_id":"existing-question",
  "id":"discussion-17",
  "request_id":"actual-session-turn-17-discussion",
  "kind":"discussion",
  "text":"本回合真实讨论及其依据；未验证判断须写明。",
  "recording":"live",
  "actor":{"kind":"agent","source_ref":"实际对话或保留材料引用"},
  "parent_id":"existing-plan-node"
}
```

新增kind：material、discussion、consensus、plan、branch、experiment、review、focus、status、conclusion。
旧kind也可使用v2。未知类型不能改标签塞入v1。parent_id、plan_id、parent_plan_id、target_id及relations
只能指向同问题已存在记录。branch必须有parent_id；关系支持原集合及extends/tests/cites/supersedes。
work_package可选，值为1.1、1.2、2.1、2.2、3.1、3.2、3.3、4.1、4.2、5.1、5.2；未声明不猜阶段。

方案`plan`含`id`（稳定方案族ID）、`version`（如1.0/1.1/1.0.1）、`content_ref`（绝对path及确切sha256）。
同一引用必须列入evidence，供Web按原摘要核对。该方案族后继版本必须以parent_plan_id引用前版，
relations包含revises/supersedes及依据；不得覆盖旧版本。不同方案族的改向也应显式引用来源和改向依据。
方案原件应位于本研究配置已登记run_roots之内；越界文件即使被记录，看板也拒绝展示。

重要变化当时记录；历史材料使用`recording:backfill`和`backfill_reason`，不改写at冒充实时。
Agent不冒充用户。session_id是本研究自有稳定标识；没有真实Codex thread ID时写明未知。

review必须有关系依据，review_status限advisory/changes-requested。这是语义意见；已验收评审仍由
原协调器按响应摘要自动投影，不能用新事件声称通过执行合同。提案、共识、结论均不授予执行权。

focus使用session_id、target_id及state(active/paused/closed)。暂停必须有waiting_for。
每question/session最新记录代表讨论焦点，互不覆盖；真实运行焦点另读原runs。focus关闭不停止训练，
暂停讨论不伪造平台暂停。恢复新增active记录，保留原暂停及等待条件。

在方案修订与结果分析中记录实际观察、受影响假设、替代解释、选择理由、成本和预期分支；
以明确引用关联原方案和结果。无需套用固定调参套路。缺失资料、待补评审及未知清理分别保留。
没有持久outbox时写入失败只能报告失败，不能声称已经排队。

独立看板由项目的research-dashboard/server.py提供，旧--research-web继续可用。新页面读取同一SDM
事实库，不运行第二套研究决策、训练观察或会话唤醒服务。页面不补录记录；记录仍由Skill公开入口完成。
