# 安装、升级与排障

## 推荐方式

同事 git clone 后，用 Codex 打开仓库，`/` 选择 ResearchPilot 并发送。仓库 `.agents/skills/bmad-ml/` 提供轻量入口，首次自动调用统一初始化，再按用户语言引导任务。前提是 Python 3.11+ 和已登录的 Codex；LPAI 是唯一需要单独完成的业务平台接入。

如果当前 Codex 版本尚未重新发现项目 Skill，重新打开此项目；也可显式运行一次：

```sh
python scripts/setup.py
```

默认安装到 CODEX_HOME/skills/bmad-ml（CODEX_HOME 已设置时），否则 ~/.agents/skills/bmad-ml；保留自定义认证和项目配置。研究模式始终在 Codex 当前会话中，安装脚本不是另开聊天的 CMD 启动器。

## 自动处理

创建 .runtime/bmad-ml-python 虚拟环境，安装 requirements-runtime.txt 固定的官方 Codex SDK/匹配 CLI；不装本地 ML 训练框架。构建自包含 Skill，逐文件摘要核验；同版本相同内容重复安装复用，更新先核验所有权并备份。

生成 .bmad-ml/ENVIRONMENT.json，包含真实机器路径和 SDK 位置，不含密钥。自动启动/复用工作台，输出带会话令牌的 loopback 链接。无研究时显示引导页，研究登记后自动发现。正常启动的后台不依赖浏览器或初始化脚本窗口。

仅希望准备当前项目而不安装全局入口：

```sh
python scripts/setup.py --no-install
```

使用已批准环境中的官方 SDK：`--sdk-root /absolute/path/to/site-packages`。离线维护校验可用 `--skip-model-sdk --no-dashboard --no-install`，此模式明确不代表模型环境已就绪。

单独恢复工作台：

```sh
python scripts/ensure-dashboard.py --project-root . --skill /absolute/path/to/bmad-ml
```

该命令只启动 Web，不启动研究；也可直接让 Codex“打开研究工作台”，无需记命令。默认8792端口冲突会改用空闲端口，查看 .bmad-ml/dashboard/ready.json 的实际链接。

## LPAI 接入

由同事安装官方 LPAI Skill/SDK，配置正常平台认证，确认 namespace、组件、数据挂载和执行资源范围。不要复制作者的 .bmad-ml、.runtime、.research 或任何 Token。Codex 应从实际安装和平台只读查询准备配置，不让用户手填内部运行JSON；首次研究确认后才可提交。

## 常见问题

- Python 不存在或低于3.11：安装受信任 Python 后重试；不从不明下载站自动拉取解释器。
- 软件包源/网络失败：使用组织批准的 pip 源或代理后重试；不删除代理和证书保护，不打印认证配置。
- Codex 未登录：使用客户端正常登录；脚本不会代建账号或复制同事凭据。
- 同名 Skill 被修改/无有效 BUNDLE：保留现场，交由 Codex 比较处理，不混合覆盖。
- 点击继续后结果未知：保留原请求ID，查询原回执，不换名字重复提交。
- 主机重启：默认不自动恢复研究，先在 Codex/工作台核对原身份并继续；没有安装周期消息或开机训练任务。

运行中的研究使用原冻结版本；安装升级不会偷偷迁移活动任务。完整旧模块 Linux 治理安装器仅供明确旧宿主维护，日常使用不需要它。
