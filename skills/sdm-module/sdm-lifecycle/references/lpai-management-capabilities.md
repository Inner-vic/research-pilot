# LPAI 非浏览器管理能力与身份诊断

本页说明能力发现与后续增强方向，不改变任何已冻结实验，不授予平台写权限。
不要把本机某个版本缺少命令推断为平台完全没有API。

2026-10-28 后续真实验收已补齐：Project.create成功创建独立CPU组件，
Task.submit在该新组件上实跑成功且实际日志标记通过；官方前端模块58007/10
提供Notebook v2 GET/PATCH接口，使用SDK RestClient正常认证完成同一CPU空间
stopped→ready→stopped。四次写入均无pending_approval、无CLI写入。
详见[管理工具](lpai-manager.md)及工作区
`_bmad-output/lpai-sdk-native-gap-20261028/verification.json`。
这不表示官方SDK新增了Notebook类，也不表示旧审批映射问题已修复。

## 2026-10-28 已查证范围

官方资料：[Python SDK](https://example.feishu.cn/docx/DEMO00000000000000000000002)
revision 6620；[LPAI Skills](https://example.feishu.cn/docx/DEMO00000000000000000000001)
revision 919。以实际取回正文版本为准，搜索结果的icon版本不能替代正文版本。

| 操作 | 已安装 lpai-agent v0.21.9 | 已安装官方 lpai SDK 1.0.0 |
| --- | --- | --- |
| 组件查询、新建 | projects get/create | Project.get/create |
| 组件编辑 | 未提供update命令 | Project.update(uid, config_path) |
| 组件提交新训练 | projects start | Task.submit_project_task，亦有Task.submit |
| 训练查询、停止 | tasks get/stop | Task.get；Task.update(status='stop') |
| 已有开发空间查询、启停 | devspaces get/start/stop | 未找到开发空间模块 |
| 开发空间新建、编辑 | 未提供create/update命令 | 未找到对应模块 |
| Agent网关确认查询、撤销、重发 | 未提供对应命令 | 上述SDK未提供对应管理对象 |

表中表示文档和本地实现存在，不代表所有写操作经过真实验收。
初始诊断批次仅验证组件SDK真实GET；后续写入验收结果以上方更新为准。
官方Skills概述宣称开发空间完整管理，但本机客户端未覆盖，需补充可核验的
官方接口/SDK实现。官方分发脚本通过EPT WebFetch读取返回422，未取得分发
版本，故不能声称本机版本就是最新，也没有自动安装更新。

## 可复用诊断入口

`scripts/lpai_management_probe.py`提供两个只读命令：

- `--output FILE capabilities --cli EXE --sdk-package DIR`：解析官方help与SDK
  AST，保存命令、公开方法、源码摘要与能力缺口。只执行help，不导入SDK凭据。
- `--output FILE doctor --cli EXE --lark EXE --namespace NS --devspace NAME
  --pending-response FILE`：核对飞书身份和本人资料、官方开发空间GET、原始确认
  回包收件邮箱。仅持久化白名单身份字段，原始stdout/stderr不转存。

doctor读取旧回包不等于刷新审批状态；输出保留原响应时间与字节摘要，并明确
`current_approval_status_rechecked:false`。账号不一致、查询不可用都不能推出
“未绑定”或“已发送”。本地飞书应用和LPAI消息机器人是不同应用，给前者重新
授权不证明后者的投递映射已修复。

重点检查标准化owner与真实企业邮箱的映射。若开发空间已有
`lpai.org/res-stat-task-origin-owner`，可把它作为原始邮箱证据，不能擅自修改
现有组件owner、伪造审批收件人或自行构造服务器不支持的参数。

## 增强层的实施边界

1. 复用官方传输：组件走SDK，开发空间已支持的启停/查询走CLI。保留现有
   SDM执行入口、资源授权、五项训练信息、监控与清理，不另造训练协调器。
2. 统一新建/编辑预览：完整镜像、队列、角色资源、命令、环境变量、源码和
   挂载差异；编辑前读回现状，写后精确读回。SDK进程退出0不等于写成功。
3. 复用submission_guard的共享台账处理未知/待确认写入。审批或认证故障
   不自动切换SDK/CLI重提；旧确认的查询/撤销接口没有证据时不得宣称已解除。
4. 开发空间create/update单独列为待接入能力。先取得官方版本化接口合同
   或SDK实现，再开发适配器及CPU故障注入；最后在独立本人CPU测试空间做
   创建、编辑、启动、停止、读回验收。没有接口时不能靠包装器凭空补齐。
5. 审批增强需要服务端能力：稳定用户ID/真实邮箱映射、投递结果、状态查询、
   不可再执行的撤销/过期证明。客户端可识别和呈现缺口，不能替服务器批准。

本批交付的是能力探测和身份诊断源码。管理写入整套增强层及开发空间CRUD
尚未交付；不得用诊断测试代替这些操作的真实验收。

后续同日已实现[管理补充工具首版](lpai-manager.md)，包含配置规划、共享意图
台账及官方传输。其真实写入仍未验收，不改变上面的接口缺口结论。
