# 参考资料与历史归档

本目录提供案例、验收证据和设计来源。它们用于追溯具体事实及适用范围，不替代[当前使用指南](../USER-GUIDE.zh-CN.md)和[支持范围](../EXECUTION.md)。

## 案例与验收

| 资料 | 用途 |
| --- | --- |
| [指骨轮廓分类案例](../../examples/distal-outline/README.md) | 多轮真实远端实验的脱敏摘要、主线与候选分支 |
| [通用分类研究记录](2026-11/GENERIC-DELIVERY-20261117.md) | 项目接入、工程接续与独立 CPU 持续链的各自事实及限制 |
| [通用接入机器摘要](../GENERIC-DELIVERY-VALIDATION.json) | 运行、产物和核验范围的摘要 |
| [统一流程验收报告](../UNIFIED-WORKFLOW-RESULTS.zh-CN.md) | 首轮、后继、评审、恢复和证据收口的特定任务验证 |
| [统一流程机器摘要](../UNIFIED-WORKFLOW-VALIDATION.json) | 对应验收的结构化记录 |
| [自主修复与后继验证](../AUTONOMOUS-ITERATION-RESULTS.zh-CN.md) | 失败任务诊断、修复、后继尝试和预算停止 |
| [运行保障检查摘要](../CONTINUOUS-RUNTIME-VALIDATION.json) | 离线多轮接续及运行状态检查 |

各案例的数据、划分、预算和评价协议分别解释。软件检查、平台执行和模型结论不能互相替代，也不能将单个案例的指标外推到其他项目。

## 设计来源

- [SDM21 到现行 13 步的演进](../../skills/sdm-module/sdm-lifecycle/references/sdm21-to-13-evolution.zh-CN.md)：职责来源、合并理由及适用检查。
- [SDM21 来源审计](../SDM21-EVOLUTION-AUDIT.zh-CN.md)：保留模板的源码审计。
- [与历史 BMAD ML 的对比](../COMPARISON-WITH-BMAD-ML.zh-CN.md)：基于保留初始模板的项目演进说明。
- [阶段性重构记录](../SDM-REFACTOR-PROGRESS.zh-CN.md)：历史实现和验证过程。
- [双会话技术说明](../../skills/sdm-module/sdm-lifecycle/references/planner-context.zh-CN.md)：研究主会话与独立评审的设计和边界。

## 历史文档

以下文件保留重组前已发布文档的内容，单独标注历史状态并修正相对链接。旧的“当前”“本轮”和未完成项均按原文时期理解。

| 文档 | 内容性质 |
| --- | --- |
| [开发状态记录](2026-11/EXECUTION-20261118.md) | 按日期累计的交付和维护状态 |
| [原开发变更记录](2026-11/CHANGELOG-20261118.md) | 逐次实现、修复及核验记录 |
| [原交接记录](2026-11/HANDOVER-20261117.md) | 当时的安装复核及 RoadModel 工程检查演示 |
| [早期会话关联设计](2026-11/CHAT-WORKBENCH-DESIGN-20261114.md) | 含未实施方案的双向关联提案 |

新功能与现行说明直接维护所属产品文档；新增版本变化写入[版本变更](../CHANGELOG.md)，不再向各页面追加交付日志。
