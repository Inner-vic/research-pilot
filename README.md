# ResearchPilot

基于 Codex 与 SDM 的持续机器学习研究工作台。在用户确认研究方案和执行范围后，组织远端实验、结果核验、分析审议及下一轮实施，并通过本地页面展示实验指标和研究脉络。

本地工作台负责查看与控制原有研究记录；训练由源项目使用的 LPAI 计算平台执行。实际研究需要使用者自己的平台账号、SDK、数据和资源权限。

## 主要功能

- 引导确认任务、基线、主指标、数据与执行范围。
- 记录方案、实验、结果和分支，保留失败尝试及历史最优。
- 展示指标曲线、分类明细、混淆计数与研究图谱。
- 按原研究身份暂停、继续和核对未知请求，避免重复提交。

想先了解实际过程，可阅读[指骨轮廓分类案例](examples/distal-outline/README.md)：包含 14 次实验的主线、失败分支和脱敏结果摘要。查看案例不启动训练。

## 快速开始

准备已登录的 Codex、Python 3.11+，以及可访问的软件包源。查看案例和运行本地页面不需要本地 GPU、PyTorch 或前端构建工具。

```powershell
git clone https://github.com/Inner-vic/research-pilot.git
cd research-pilot
```

在 Codex 中打开该目录，输入 `/`，选择 **ResearchPilot**，按引导说明自己的研究任务。项目入口会准备独立运行环境和工作台；安装或加载 Skill 本身不会开始训练。

也可安装为当前用户的通用 Skill：

```powershell
python scripts/setup.py
```

安装会构建并校验 Skill、准备固定版本的官方 Codex SDK、启动或复用当前项目工作台。SDK 使用本机既有 Codex 登录。默认端口为 8792，冲突时选择空闲端口，以安装输出的地址为准。

开展实际研究前，还需安装官方 LPAI Skill/SDK，完成本人的认证，并明确 namespace、组件、数据及资源范围。详见[安装与排障](docs/delivery/INSTALLATION-MODES.md)。

## 使用流程

1. 描述任务、数据来源和目标；由引导补齐缺失字段。
2. 确认初始方案、验收指标与允许执行的资源范围。
3. 通过原协调器执行远端实验，核验返回产物后分析结果并推进下一轮。
4. 在工作台查看当前最优、实验分支和原始证据，按需暂停或继续。

工作台与当前项目及研究编号绑定，读取同一份研究记录；它不复制当前聊天全文，也不建立第二套实验执行器。

## 源码与验证

| 路径 | 内容 |
| --- | --- |
| `skills/bmad-ml/` | 用户 Skill 入口，保留兼容调用名 |
| `skills/sdm-module/` | SDM 治理、实验生命周期和工作台实现 |
| `scripts/` | 安装、构建和维护工具 |
| `examples/distal-outline/` | 可阅读、可核对的脱敏研究案例 |

无需连接模型或平台的源码与案例检查：

```powershell
python -B tools/check_public_source.py
python -B examples/distal-outline/verify_case.py
```

GitHub 的 [Source checks](.github/workflows/source-checks.yml) 检查源码语法及高置信度凭据模式；离线测试和历史平台验证范围见[发布说明](PUBLICATION.md)。

## 支持范围

已保留真实平台连续研究的案例和恢复实现。当前公开数据标准生产器/核验器按固定协议处理二分类，主指标为 macro-F1；多类、分割和 IoU 等任务需要单独适配与验收，不能仅凭页面能展示相应字段推断完整支持。

持续后台研究要求电脑和网络在线。活动研究使用冻结的运行版本，升级不应就地改写它；个人状态、凭据、数据和权重保存在 Git 之外。

## 文档

- [使用说明](docs/USER-GUIDE.zh-CN.md)与[案例操作引导](examples/distal-outline/RUN-GUIDE.zh-CN.md)
- [架构与配置](docs/ARCHITECTURE.md)
- [与原 BMAD ML 模板的对比](docs/COMPARISON-WITH-BMAD-ML.zh-CN.md)
- [版本变更](docs/CHANGELOG.md)

ResearchPilot 基于 BMAD ML / SDM 演进；内部兼容标识仍为 `bmad-ml`。
