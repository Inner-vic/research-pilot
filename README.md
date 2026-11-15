# ResearchPilot · 持续研究工作台

[实习项目总览](https://github.com/Inner-vic/internship-projects) · [使用与验证](docs/project-guide.md)

连接研究方案、远端实验与结果反馈，把多轮研究过程保留为可核验记录。

## 解决的问题

机器学习研究的困难不止是启动一次训练。方案调整依据什么、失败后保留什么、下一轮从哪个基线继续，以及网页显示的结果是否来自同一份实验记录，都需要明确规则。

ResearchPilot 基于 BMAD ML / SDM 演进，把研究引导、远端执行、结果核验、审议与本地工作台连接起来，保留多轮研究的证据与分支。

## 实现了什么

- 通过 Codex Skill 引导确认任务、数据、指标、基线和资源范围。
- 在原 SDM 治理与协调器下执行远端实验，按真实结果推进分析和后继方案。
- 展示当前最优、实验指标、分类明细与方案/结果图谱。
- 暂停、继续和恢复沿用原研究身份，保留活动任务、冻结运行和未知请求。

## 公开案例：14 次实验的改进与回退

![指骨轮廓分类案例的全部14次有效实验、历史最优及相对所选基线变化](https://raw.githubusercontent.com/Inner-vic/internship-projects/main/assets/research-curve.png)

在已公开的指骨轮廓二分类案例中，固定内部验证集的 macro-F1 从 **0.8450** 提升至 **0.9155**。图中保留了全部有效实验，包括明显退步的分支；历史最优不会被最近一次较差结果覆盖。

此结果采用单随机种子 42，TRAIN 内部按 449/151 划分，验证集在多轮中自适应复用，独立 TEST 未使用。因此它说明这次研究过程与验证目标，不作为独立测试集泛化结论。原始[案例数据](examples/distal-outline/case.json)、[过程说明](examples/distal-outline/README.md)和[作图数据及口径](https://github.com/Inner-vic/internship-projects/blob/main/assets/research-curve.provenance.json)均保留。

## 实现架构

![ResearchPilot的主要模块、执行顺序和关键边界](https://raw.githubusercontent.com/Inner-vic/internship-projects/main/assets/architecture/research-pilot.svg)

## 关键工程设计

### 1. 工作台投影原始研究记录

页面的数据层读取原研究、实验和结果证据，通过 SSE 刷新视图。暂停与继续委托原协调器，工作台不维护第二套实验引擎；当前项目、研究编号和配置共同约束页面所展示的内容。

### 2. 先核验结果，再产生下一轮动作

平台终态、产物完整性、指标有效性和目标达成分别判断。结果摘要保留文件身份与固定协议，工程准备故障不算一次有效科学实验，未知写入先按原请求核对。

### 3. 把方案关系与实验顺序分开

图谱保留实际基线、方案修订和结果的关系。后继方案可以回到历史最优，失败分支和负面证据仍可展开查看，避免用实验编号连成一条虚假的单调改进路径。

## 来源与实现边界

ResearchPilot 继承 BMAD ML / SDM 的治理协议和组件归属，具体扩展对照见[有来源的比较说明](docs/COMPARISON-WITH-BMAD-ML.zh-CN.md)。持续研究由原能力协调，不把基础框架或外部平台作为本项目独立实现。

公开标准生产器/核验器覆盖固定协议二分类；多类、分割和 IoU 任务需要单独适配与验收。案例、离线测试和实际平台验证分别记录，范围见[公开验证说明](https://github.com/Inner-vic/research-pilot/blob/main/PUBLICATION.md)。

## 源码导读

| 源码 | 关注的实现 |
| --- | --- |
| [skills/sdm-module/sdm-lifecycle/scripts/component_pipeline.py](skills/sdm-module/sdm-lifecycle/scripts/component_pipeline.py) | 原能力之间的持续交付 |
| [skills/sdm-module/sdm-lifecycle/scripts/event_coordinator.py](skills/sdm-module/sdm-lifecycle/scripts/event_coordinator.py) | 事件协调与推进 |
| [skills/sdm-module/sdm-lifecycle/scripts/research_dashboard_data.py](skills/sdm-module/sdm-lifecycle/scripts/research_dashboard_data.py) | 证据读取与工作台投影 |
| [examples/distal-outline/case.json](examples/distal-outline/case.json) | 14 次实验的脱敏公开案例 |

## 项目资料

使用步骤、依赖和本地验证见[使用与验证](docs/project-guide.md)；源码来源与验证范围见[发布说明](https://github.com/Inner-vic/research-pilot/blob/main/PUBLICATION.md)。本页用于介绍实习阶段的实现与设计，具体结论以所链接的代码和证据为准。

[IterLab Codex](https://github.com/Inner-vic/iterlab-codex)是独立的原生执行内核接入原型；两个仓库保留不同实现路径与验证结论。
