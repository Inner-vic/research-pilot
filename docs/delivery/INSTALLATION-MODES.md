# 安装与更新

ResearchPilot 安装为 Codex Skill。本机运行研究后台和工作台，模型训练与评价在已接入的远端环境执行。完整 Skill 可用于多个模型项目，各项目分别保存环境配置和研究记录。

## 环境要求

- Python 3.11 或更高版本。
- 已安装并登录 Codex，账号可使用研究所需模型。
- 可访问组织批准的软件包源和代码仓库。
- 所选训练平台的本人账号、资源权限及数据访问能力。

本机无需 GPU 或额外前端构建环境。LPAI 接入通过组织官方渠道配置，安装器不代办平台登录和权限申请。

## 安装到模型项目

在 ResearchPilot 仓库目录运行安装命令：

```powershell
git clone https://github.com/Inner-vic/research-pilot.git
cd research-pilot
python scripts/setup.py --project-root "D:\Projects\MyModel"
```

将示例路径替换为已存在的实际模型项目绝对路径。`--project-root` 指向模型项目，安装命令仍在 ResearchPilot 仓库中执行。

安装器构建并核验完整 Skill，准备项目的官方 Codex SDK 环境，生成本机配置，并启动或复用本地工作台。设置 `CODEX_HOME` 时，默认安装到其 `skills/researchpilot`；否则安装到 `~/.agents/skills/researchpilot`。

安装完成后，核对输出中的 Skill 路径、项目目录和工作台链接。新项目尚无研究记录时，工作台显示引导页。安装完成只表示本机环境已准备，项目执行接入和账号权限仍需核对。

换一个模型项目时，为新路径再次运行上述命令。相同的用户 Skill 可以复用，项目环境和研究记录分别建立。

## 在 Codex 中使用

用 Codex 打开刚才指定的模型项目，选择 ResearchPilot Skill 并发送需求，也可在消息中使用 `$researchpilot`：

> 使用 $researchpilot，先检查当前项目的训练、数据、评价和已有结果，列出需要补充的信息，暂不提交任务。

若安装后未发现 Skill，重新打开项目；仍未出现时重启 Codex 并检查实际安装位置。安装包需包含完整 `researchpilot` 目录及 `references/`，单独复制 `SKILL.md` 不能完成安装。

后续操作见[使用指南](../USER-GUIDE.zh-CN.md)。任务提交需具备已确认方案、有效执行范围和相关检查结果。

## 配置执行环境

LPAI 方式需要本人可用的官方 Skill/SDK、平台认证、namespace、资源队列、组件、数据挂载和输出位置。项目命令驱动方式需要提供固定驱动及实际任务系统接口，详见[项目接入](../GENERIC-DELIVERY.zh-CN.md)。

Codex 优先从当前项目和可用环境读取配置，只补问无法查到的信息。账号、密钥与本机路径保存在项目本地，不随 Skill 分享；接手项目时重新核对本人权限。

## 更新

在工作区无未处理修改的情况下，获取仓库更新并重新运行安装：

```powershell
git pull --ff-only
python scripts/setup.py --project-root "D:\Projects\MyModel"
```

安装器核验已有包，保留升级备份后替换用户 Skill；发现摘要不符或额外文件时保留现场并拒绝覆盖。研究继续使用其已固定的运行版本，升级不会自动迁移活动研究、恢复暂停状态或增加预算。

## 安装选项

以下选项用于已有环境接入或维护。首次使用通常采用默认安装即可。

| 选项 | 用途 |
| --- | --- |
| `--skill-destination` | 指定完整 Skill 的安装目录 |
| `--sdk-root` | 使用已有、版本匹配的官方 SDK 目录 |
| `--no-dashboard` | 安装时不启动工作台 |
| `--no-install` | 构建隔离包并准备项目环境，不安装全局用户入口 |
| `--skip-model-sdk` | 跳过 SDK 安装；已有有效项目配置会保留，首次无 SDK 时不具备模型调用环境 |

只在 ResearchPilot 仓库内学习时，可省略 `--project-root`。此时准备的是仓库自身的环境。

需要重新打开工作台时，可在 Codex 中说明“打开当前项目的研究工作台”；维护人员也可运行：

```sh
python scripts/ensure-dashboard.py --project-root /absolute/model/project --skill /absolute/path/to/researchpilot
```

访问链接与端口以实际输出为准。工作台链接包含本机访问令牌，应由当前环境生成。

## 常见安装问题

| 问题 | 处理方式 |
| --- | --- |
| Python 不存在或版本不足 | 使用 Python 3.11+ 后重新安装 |
| 依赖下载失败 | 核对组织批准的软件包源、代理和网络，保留安装错误信息 |
| 当前项目缺少环境配置 | 回到 ResearchPilot 仓库，为正确的模型项目路径运行安装 |
| 安装包校验失败 | 核对分发来源和文件完整性，避免混合覆盖不同版本 |
| 平台无法访问 | 核对本人账号、所选项目和资源权限；安装 Skill 不会授予平台权限 |
| 页面仍显示旧内容 | 重新打开本次安装输出的工作台链接并刷新页面 |

研究启动后的问题见[运行状态与排障](../CONTINUOUS-RUNTIME.zh-CN.md)。
