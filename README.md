# ResearchPilot · 持续研究工作台

[实习项目总览](https://github.com/Inner-vic/internship-projects) · [使用与验证](docs/project-guide.md)

连接研究方案、远端实验与结果反馈，把多轮研究过程保留为可核验记录。

## 解决的问题

机器学习研究的困难不止是启动一次训练。方案调整依据什么、失败后保留什么、下一轮从哪个基线继续，以及网页显示的结果是否来自同一份实验记录，都需要明确规则。

ResearchPilot 基于 BMAD ML / SDM 演进，把研究引导、远端执行、结果核验、审议与本地工作台连接起来，保留多轮研究的证据与分支。

## 实现了什么

- 通过 Codex Skill 引导确认任务、数据、指标、基线和资源范围。
- 继承 SDM 的研究规范，重组为五阶段、13 步；主会话与独立评审会话协作，按授权范围推进方案、实施和结果分析。
- 展示当前最优、实验指标、分类明细与方案/结果图谱。
- 暂停、继续和恢复沿用原研究身份，保留活动任务、冻结运行和未知请求。

## 一轮研究如何衔接

| 阶段 | 关键交付 |
| --- | --- |
| 研究起点（1–3） | 可检验的问题、数据评价约定、执行范围与比较依据 |
| 实验准备（4–5） | 经复核的实验方案、准备检查与固定的研究依据 |
| 实施与验证（6–8） | 实现版本、独立代码审查、工程与远端功能检查 |
| 执行与评价（9–11） | 可追踪任务、完整产物及口径一致的评价结果 |
| 复核与决策（12–13） | 证据复核、保留候选，以及继续、补证、回退或结束的判断 |

后续实验可复用仍有效的约定，围绕新假设回到实验设计；最新结果不自动替换保留候选。任务执行完成、评价有效和目标达成分别判断。步骤编号表达职责，实际顺序服从环境依赖；完整说明见[研究流程](docs/WORKFLOW-GUIDE.zh-CN.md)。

## 公开案例：14 次实验的改进与回退

![指骨轮廓分类案例的全部14次有效实验、历史最优及相对所选基线变化](https://raw.githubusercontent.com/Inner-vic/internship-projects/main/assets/research-curve.png)

在已公开的指骨轮廓二分类案例中，固定内部验证集的 macro-F1 从 **0.8450** 提升至 **0.9155**。图中保留了全部有效实验，包括明显退步的分支；历史最优不会被最近一次较差结果覆盖。

此结果采用单随机种子 42，TRAIN 内部按 449/151 划分，验证集在多轮中自适应复用，独立 TEST 未使用。因此它说明这次研究过程与验证目标，不作为独立测试集泛化结论。原始[案例数据](examples/distal-outline/case.json)、[过程说明](examples/distal-outline/README.md)和[作图数据及口径](https://github.com/Inner-vic/internship-projects/blob/main/assets/research-curve.provenance.json)均保留。

## 实现架构

![ResearchPilot的主要模块、执行顺序和关键边界](https://raw.githubusercontent.com/Inner-vic/internship-projects/main/assets/architecture/research-pilot.svg)

## 关键工程设计

### 1. 研究对话、持续控制与工作台分工

前台聊天整理目标和任务范围；后台研究主会话负责持续分析与实施，独立评审会话检查方案和代码。工作台投影同一份研究记录，展示当前状态、方案和实验分支，不建立第二套执行引擎。

### 2. 把结果与下一轮依据绑定

当前流程将目标、评价约定、方案、实施、检查、受控提交、结果核验和结论组织为五阶段、13 步。单次评测与持续研究分别记录；只有结果、分析和授权范围都满足条件时，才进入相应后继流程。

### 3. 用明确的执行与结果契约接入项目

官方 LPAI 组件与项目命令驱动接口各自接入执行环境。任务、原始回执、结果文件、指标与固定比较依据共同约束接续；结果未知时先核对原请求。运行仍依赖使用者自己的账号、数据、资源和正常授权。

## 来源与实现边界

研究方法、规范对象、校验与证据规则来自 BMAD ML / SDM；ResearchPilot 在此基础上补充 Codex 双会话、后台控制、平台接入、提交防重和工作台。流程重组保留原职责及历史身份，来源与实现分别说明。详见[继承与整合说明](docs/COMPARISON-WITH-BMAD-ML.zh-CN.md)和[自主迭代](docs/AUTONOMOUS-ITERATION.zh-CN.md)。

当前通用结果契约支持规定格式的多类别分类与标量回归，提供 LPAI 组件和项目命令驱动入口。接口支持、离线测试和真实平台/模型验收分别记录，具体接入与限制见[项目接入](docs/GENERIC-DELIVERY.zh-CN.md)和[支持范围](docs/EXECUTION.md)。上述14轮曲线属于历史独立案例，不代表新接口的普遍性能收益。

## 源码导读

| 源码 | 关注的实现 |
| --- | --- |
| [skills/sdm-module/sdm-lifecycle/scripts/component_pipeline.py](skills/sdm-module/sdm-lifecycle/scripts/component_pipeline.py) | 原能力之间的持续交付 |
| [skills/sdm-module/sdm-lifecycle/scripts/event_coordinator.py](skills/sdm-module/sdm-lifecycle/scripts/event_coordinator.py) | 事件协调与推进 |
| [skills/sdm-module/sdm-lifecycle/scripts/research_dashboard_data.py](skills/sdm-module/sdm-lifecycle/scripts/research_dashboard_data.py) | 证据读取与工作台投影 |
| [examples/distal-outline/case.json](examples/distal-outline/case.json) | 14 次实验的脱敏公开案例 |

## 项目资料

使用步骤、依赖和本地验证见[使用与验证](docs/project-guide.md)；源码来源与验证范围见[发布说明](https://github.com/Inner-vic/research-pilot/blob/main/PUBLICATION.md)。本页用于介绍实习阶段的实现与设计，具体结论以所链接的代码和证据为准。

ResearchPilot 是这组项目当前采用的持续研究方案；[IterLab Codex](https://github.com/Inner-vic/iterlab-codex)保留为原生执行内核接入的探索参考，两者保留各自的实现路径与验证结论。
