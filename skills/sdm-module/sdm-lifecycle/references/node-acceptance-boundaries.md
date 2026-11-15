# 节点独立验收边界（gate contract revision 2）

## 范围与版本

保留graph_revision:3驱动身份与gate_contract_revision:2验收字段。当前workflow_revision:1将主流程合并为11个工作包，依赖以workflow_definition为准。下表是包内独立检查，不要求各写一篇报告或各起一次评审，材料合同见[work-packages.md](work-packages.md)。
显式旧门禁版本不得自动改标为新版本；已接受历史记录与日志不重写。历史目录若
摘要不匹配，保留原记录，在新任务修订中补齐并重新接受所需结论，不伪造迁移通过。

各节点可以共用同一原始日志、协议或证据包，一次Agent评审可产出多份结论记录，
不要求额外工具调用、外部评审或重复对话。结论记录独立冻结并绑定上游摘要，避免
先接受共享反馈后、为修正下游结论而改写已接受记录。记录存在不是语义真实性证明；
本地Agent或配置评审者仍负责真实核对，校验器检查结构、绑定、状态和约束。

## 合同映射

| 节点 | 独立记录名 | 验收边界 |
|---|---|---|
| 2.2 / 24 | research | 问题、假设、阶段、控制、调度声明、初始化类型、评估与决策规则完整；不提前要求方法结论 |
| 2.2 / 25 | method_review | 绑定research_digest；method_checks覆盖六类检查，supported有证据；核查调度一致性与resume依据 |
| V.2 / 31 | review_response | 真实评审者、上下文绑定、对齐、问题/反馈及结束状态；不代替证据或结论验收 |
| 4.2 / 19 | evidence_consistency | 绑定review_response_digest和review_context_digest；逐项claim/evidence_ref/finding/consistent；矛盾不得标passed |
| 4.2 / 32 | conclusion_quality | 绑定consistency_digest及review_response_digest；支持/反证、替代解释、限制、阻塞缺口和理由；证据一致不等于结论充分 |
| 5.1 / 20 | next_action | 绑定quality_digest；结论、可选行动及理由、recommended_action、证据；只是建议 |
| 5.1 / 33 | transition | 绑定next_action_digest；从已评审选项作出action决定；保留原有晋级、评审和执行权限限制 |

所有记录使用原有命名规则：sdm-orchestration-<record-name-with-hyphens>/v1。
例如method_review对应sdm-orchestration-method-review/v1；统一入口缺少新记录时
返回该节点的awaiting-evidence，不从旧research/review_response/transition自动
合成通过。review_context增加method_review_digest；方案评审上下文包含method_review。

research内历史method_checks可保留为原始信息；节点25只认独立method_review中的
检查结论。完整research的通用validate_record接口仍保留严格检查以兼容原调用，
节点24显式选择协议验收路径。未知/不适用方法项仍允许并须说明原因，策略未收紧
为所有方法都必须已有实验支持。

## 最小字段与状态

- method_review：research_digest、method_checks；每项id/status/rationale，supported
  必须有evidence_ref。六类id与原合同相同，不新增强制实验。
- evidence_consistency：review_response_digest、review_context_digest、status、checks。
  status为passed或needs-revision；checks非空，每项claim/evidence_ref/finding文本
  及consistent布尔值。passed要求全部consistent。
- conclusion_quality：review_response_digest、consistency_digest、status、supporting_evidence、
  counterevidence、limitations、alternatives_considered、blocking_gaps、reason。
  支持证据非空；列表项为文本引用/说明；passed时blocking_gaps为空，且上游一致性通过。
  结论可以是“模型失败”，不要求模型指标成功才允许评审通过。
- next_action：quality_digest、conclusion、options、recommended_action、reason、evidence_ref、
  execution_allowed:false；options非空，每项action/reason，推荐动作必须在选项中。
- transition：原有action/reason/evidence_ref/execution_allowed:false，加next_action_digest。
  最终action属于已评审选项，可以与推荐项不同，但须保留实际决定的理由。

needs-revision返回awaiting-review；格式错误、矛盾标passed或摘要错配拒绝。
节点20只发布recommendation.json；节点33完成后才发布decision.json。缺少最终
决定时不会出现decision.json，恢复后不重复运行已接受的准备、模拟计算或释放。
恢复时还必须核对已经发布的recommendation.json和decision.json与接受日志中的
对应对象一致；文件缺失或被修改即拒绝恢复，不使用漂移后的文件作出任务决定。

## 验证边界

CPU反例覆盖：协议存在但方法未评/不合理、对话结束但证据矛盾、证据一致但结论
不足、建议已接受但最终决定缺失、上游变更导致摘要不匹配。公开统一入口
run_project_pipeline.py的集成验证使用密封本地CPU驱动；模拟执行节点返回明确
的fixture结果，不调用LPAI、SSH、GPU、训练或Pro，也不声称产生真实平台S13。
