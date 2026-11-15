# ResearchPilot 与原 BMAD ML 的区别

更新：2026-11-13。ResearchPilot（持续研究工作台）是本仓库的新名称，继承 BMAD/SDM 的设计和源码，并在其上发展持续研究执行与用户工作台。

## 对比基线

“原 BMAD ML”指维护者本地 Git 保留的初始模板 `ec870b0e12b0932741a193520a004cba5ae9f3c3`（2026-09-18），其 README 标题是“BMAD + SDM 可分发模板”。“当前版”指本仓库 `0.3.0-rc6.34` 源码。核心闭环对比依据沿用 rc6.28 及截至 2026-11-13 的已登记验证；后续名称与工作台调整有独立验证，不代表重新完成全部训练验收。未核查原项目后续分支，因此以下不能作为其最新版本的能力排名。

原版已经有 21 步研究生命周期、14 个 SDM Skills、冻结协议、权限门禁、证据账本、科学裁决及后继研究设计。这些是继承的基础，不能算作 ResearchPilot 首创。原版也包含恢复/中断和外部平台协议；区别在于当前版补充了具体后台执行、真实平台适配与产品使用路径。

## 主要差异

| 维度 | 原 BMAD ML 基线 | 当前 ResearchPilot |
| --- | --- | --- |
| 交付定位 | BMAD 规划与工程方法 + SDM 规范驱动研究治理模板 | 面向日常使用的持续研究工具，SDM 继续作为内部核心 |
| 用户入口 | 安装 14 个 SDM Skills，依照生命周期与公共 CLI 操作 | 一个用户 Skill 引导：检查项目、补齐目标/基线/执行范围，再进入研究 |
| 持续执行 | 已定义训练、证据、科学决策、后继研究等流程；该基线没有当前后台驻留闭环的实证 | 原监督器/协调器监控同一任务，回收结果后触发分析、审议、实施、代码审议和下一次提交 |
| 平台接入 | 数据、Git、CI、ML 四类平台通过 S12/S13 交换请求/结果；基线绑定为空，演示使用测试替身 | 已接官方 LPAI SDK 并有真实连续研究记录；GitLab/CI 具备接入代码，具体平台验收分别说明 |
| 模型与工程协作 | BMAD 提供规划/架构/Story/编码/审查，SDM 管理科研状态与裁决 | 在此基础上接官方 Codex SDK，串联结果分析、独立审议、原生实施线程及持久模型回执 |
| 中断与恢复 | 有受治理的恢复、安装事务恢复与研究中断设计 | 增加在途工作排空、原请求对账、持久退避、同身份接续、监督恢复和网页暂停/继续；未知结果不盲目重发 |
| 可观察性 | 文档、CLI、账本与导出的验证材料 | 增加本地 Web：实验表、训练曲线、类别指标/混淆矩阵、真实基线主线、可局部展开的研究脉络与原始证据 |
| 安装体验 | Linux/WSL、Python 3.12+、uv；模板要求活动账本放在 Linux 文件系统，Windows 挂载盘有已知限制 | 当前 Windows 路径有真实验证；Python 3.11+ 与已登录 Codex 为前提，统一初始化独立 SDK/Skill/工作台；同事仍需自己的 LPAI 环境 |
| 验证成熟度 | 21 步测试替身演示与启动器检查通过；完整 Native10 未通过，真实训练未执行 | 有真实远端训练、结果驱动接续与恢复记录；完整长时无人接管、所有任务类型与故障范围仍未验收 |

## 继承和兼容

ResearchPilot 的主要新增价值是把既有治理基础接成可运行、可观察、可恢复的持续研究链。研究规范、可追溯证据、冻结版本、权限边界和科学验收继续保留。

仓库名称、URL、首页、工作台与 Skill 显示名统一为 ResearchPilot。兼容入口仍是 `bmad-ml`，Codex Skill 选择器显示 **ResearchPilot**；`skills/bmad-ml/`、`.bmad-ml/`、SDM 对象和既有冻结身份继续沿用。后台线程、原研究目录和原始证据不因仓库改名而迁移。使用新名称不代表另建一个研究引擎。

## 当前仍需补齐的能力

- 全程长时无人接管尚无完整验收；历史中有明确记录的工程冷接续，不能表述成全部故障均能自修复。
- 当前公开数据标准生产器/核验器以固定协议二分类、macro-F1 为主；多类/分割、IoU、场景约束与复杂候选晋升仍需真实任务适配。
- Web 读取研究记录并委托原协调器控制，不同步前台 Codex 聊天全文；关闭页面不停止研究。
- 后台仍依赖宿主和网络在线，未默认配置开机研究或定时聊天。
- 同事自己的平台接入与端到端研究需要单独验证；作者的训练记录不能替代该项验收。
- 先前远端 CI 在运行测试前因 Runner 拉取镜像失败；本地检查通过不能替代远端 CI 成功。

## 核对入口

- 原版基线：维护者原开发仓库执行 `git show ec870b0:README.md`、`git show ec870b0:docs/sdm/WORKFLOW-GUIDE.md`、`git show ec870b0:docs/sdm/README.md`。交付仓库以干净源码快照开始，不包含这段历史。
- 当前产品与边界：[首页](../README.md)、[架构与配置](ARCHITECTURE.md)、[版本验证](CHANGELOG.md)。
- 当前后台实现：`skills/sdm-module/sdm-lifecycle/scripts/` 中的 `component_research.py`、`research_worker.py`、`research_supervisor.py`、`research_model_runner.py`、`research_pause.py`、`research_restore.py`、`research_dashboard.py`。
- 使用与恢复合同：`skills/sdm-module/sdm-lifecycle/references/` 中的 `user-onboarding.md`、`required-lpai-research.md`、`research-dashboard.md`、`execution-recovery.md`。

私有研究回执留在维护者本地，不随这份对比文档上传任务日志、凭据、数据或模型。
