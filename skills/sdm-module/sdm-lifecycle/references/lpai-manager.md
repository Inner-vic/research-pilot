# LPAI 管理补充工具 v0.2

2026-11-12 实际数据集输入接线：完整 Task.submit(data) 不会自动调用安装版
Task.validate_data，不能假定 SDK 已替调用方检查。新版 plan 显式先调用该纯校验，
在 Guard 写入意图前拒绝不完整输入（包括 name/version/fsType/env 挂载身份）。
任务幂等身份绑定完整 native data，而非仅训练代码，避免不同挂载共用旧意图。
这不是修正后重新发送未知请求的许可。

SDK worker 在打印返回值之前持久保存绑定原输入哈希的最小提交回执（UID 或错误
类型/HTTP 数字状态，不保存认证或原始错误正文）。恢复先读取同一原 plan/request
的唯一回执，再以同 UID 官方 GET 严格核验配置；丢失 stdout 不能导致第二次 POST。
无 UID/超时/无匹配对象继续 unknown；即使观察到 HTTP 错误也不自动推断零副作用。
老运行时丢失的错误正文不能通过重发补取，必须如实保留证据缺口。

2026-11-11 SDK持续研究接线：用户明确选用官方SDK时，先用同一安装和本机官方配置
只读核验prod/namespace、组件与任务身份，再为新研究固定component_transport=official-sdk、
sdk_python和原授权。官方lpai Skill仍须加载；用户对本次SDK路径的明确指示记录在项目中，
不修改官方Skill，也不把CLI确认实例切到SDK重提。旧未知请求、Guard和冻结运行时保留。
组件完成源v3显式绑定transport，并把它继承到每一后继；日志继续用官方CLI读取。
SDK/CLI提交无UID都只沿原身份只读查找唯一候选，再核验完整配置；查无任务不表示
可以重发。新版只进入新冻结运行时，不热改现有观察者。真实训练结果和两次结果驱动
方案改进才计三轮验收，连接检查与控制测试不计。

2026-10-28第二轮真实验收：官方 `Project.create` 创建独立组件，随后官方
`Task.submit` 在该新组件上实跑1CPU/4GiB/0GPU短任务，Succeeded且实际JSON
日志标记通过。开发空间通过官方前端源码确认的Notebook API及SDK RestClient，
完成同一UID的 stopped→ready→stopped。四次写入均未返回pending_approval，
没有CLI写入或浏览器管理点击。证据目录为
`_bmad-output/lpai-sdk-native-gap-20261028/`，详细结果见verification.json。
第一轮三个CLI待确认请求和原A800请求仍未闭合，不得审批或重放旧测试请求。

工具源码为`scripts/lpai_manager.py`，传输层为`lpai_manager_backend.py`，
SDK子进程为`lpai_sdk_worker.py`。调用官方SDK/CLI及有官方客户端源码依据的API，沿用现有
`submission_guard.Guard`、原子写和OS锁；不修改已安装的官方文件或冻结实验。

## 已实现范围

| 命令/操作 | 实现 | 当前验收 |
| --- | --- | --- |
| inspect component/task | 官方SDK get，校验UID、namespace、owner | 原A800组件和已失败任务真实读回通过 |
| inspect devspace | 配置official-api时用SDK RestClient GET；保留显式旧CLI兼容 | 官方API真实读回通过 |
| plan | 本地请求检查、资源/环境变量摘要、配置差异、读回基线摘要 | CPU及真实开发空间只读预览 |
| component.create | sdk_config调用公开Project.create；旧body模式保留CLI | 官方SDK创建及同UID精确读回通过；旧CLI路径仍待确认 |
| task.submit | 官方SDK公开Task.submit，传完整native data及五项说明 | 1CPU/4GiB/0GPU真实任务、配置和实际输出通过 |
| task.stop | 官方SDK Task.update(status='stop')，终态读回 | CPU检查 |
| devspace.start/stop | official-api配置走Notebook PATCH，绑定已有UID | 1CPU/4GiB/0GPU完整启停通过，无飞书确认 |
| reconcile | 对已有意图/候选UID只读对账，pending不重提 | CPU未知结果/恢复检查 |

开发空间新建/编辑、组件原地更新、审批查询/撤销/重发、日志/指标聚合尚未接入。
现阶段不要把它当成完整替代官方平台的产品。

## 为什么提交训练使用完整native data

已安装SDK `PayloadBuilder.with_envs`对环境变量名调用capitalize()，会把
`CUDA_VISIBLE_DEVICES`改为`Cuda_visible_devices`。本工具不修改该实现：
提交任务使用官方公开`Task.submit(...,data=...)`，保留原始键名；组件创建
使用通过环境变量保真校验的SDK配置。SDK离线实测已复现前者问题并确认公开方法按原样传递
data与五项说明，已通过真实任务验收。组件创建现支持公开Project.create，
使用SDK PayloadBuilder只读展开资源套餐并绑定计划，拒绝会被capitalize()
改写的环境变量名以及SDK不支持的配置字段；不会静默吞掉permission/priority。
完整native body模式仍是旧CLI路径，不自动回退。组件原地更新暂不接入。

## 配置和使用

manager JSON字段：schema=`lpai-manager/v1`；namespace、owner为固定操作范围；
official_cli、sdk_python指向现有安装；state_root保存本地诊断/传输材料；
submission_registry必须使用同一逻辑请求的共享台账目录；devspace_transport设为
`official-api`启用新接口，省略时仅为历史计划保留旧CLI行为；legacy_pending_cases
引用尚待解决的历史创建目录。配置不存API Key、JWT或飞书凭据。

```text
python lpai_manager.py --config manager.json --output component.json inspect --kind component --uid COMPONENT_UID
python lpai_manager.py --config manager.json --output devspace.json inspect --kind devspace --name NAME
python lpai_manager.py --config manager.json --output plan.json plan --request request.json
python lpai_manager.py --config manager.json --output result.json apply --plan plan.json --authorization authorization.json
python lpai_manager.py --config manager.json --output readback.json reconcile --plan plan.json
```

SDK在独立子进程中调用，单次60秒超时；超时后保持意图，不自动重提。
CLI同样60秒上限。调用失败不转存原始认证错误。
隔离SDK进程使用ASCII JSON转义输出，避免Windows旧控制台编码丢失Unicode。
旧任务null字段保留为未知，不据此报告0卡；任务名只接受原名或由该任务
确切UUID前8位构成的平台后缀，不作宽松前缀认领。

请求通用字段为action、request_key。支持：

- component.create：推荐name、transport=official-sdk及sdk_config（官方SDK JSON配置，
  含name/describe/cluster/queue_code/image/cmd/framework/gems_project/task_role_specs）。
  角色replica为正整数字符串，resource含套餐id和正整数replica；拒绝未知字段和弹性副本。
  不支持的env键在写入前报错，不能依赖Project.create设置大写环境变量。
  旧完整native body路径继续可用，但仍可能等待CLI飞书确认。
- task.submit：name、uid（已有组件）、cluster、完整native data、task_info。
  task_info必须恰好包含problem_statement、solution_approach、expected_result、
  expected_duration（正整数分钟）、expected_duration_desc。继承组件的队列、
  框架、租户项目及可用属性，写后核验。
- task.stop：uid。
- devspace.start/stop：name。

首版仅支持固定正整数角色副本。未知请求字段、重复环境变量、弹性副本或
未接入操作明确拒绝，不静默忽略。GPU摘要来自声明配置，不是空闲资源或账单证明。

apply需要已有明确用户授权的绑定记录：schema=`lpai-manager-authorization/v1`，
plan_sha256为计划文件字节摘要（带sha256:前缀），direct_user_confirmation=true，
source_ref为真实授权来源。工具开发授权不自动授权任何训练或资源启停，
Agent不得为自身生成虚假用户确认。已有授权可由SDM集成绑定，无需逐轮重问。
计划还绑定管理配置、运行源码和平台基线；变更后须重新规划。

## 去重、恢复与边界

所有写入先记录意图，再调用一次官方路径。重复apply只读回同一结果。
未知结果或pending占用逻辑键；不会改走另一个路径重提。相同历史pending
名称或完全相同native配置也被拦截，改名不能借此重发原A800请求。
历史目录不改写、不宣称审批状态已刷新。

reconcile可以认领已有候选UID；创建组件丢失回包时仅在完整查询恰好一项且
精确配置读回通过后认领。任务提交没有候选UID时保持未知，不用名称盲认领。
当前首版未接入任务完整分页找回或审批闭合接口。

状态已停止不等于Pod/计费资源已释放：结果明确返回
physical_resource_release_verified=false，现有SDM清理/释放核验仍需执行。
本工具台账只保护通过它和同一共享Guard发出的请求，无法锁住用户手工操作；
并发UI和旧独立脚本仍需统一入口。它不替代A/B审议、训练监控或现有协调器。

## 已写入请求的只读修复

真实任务已返回UID，但读回工具存在编码/兼容问题时，不改写原计划并重提。
`reconcile --plan ORIGINAL --runtime-repair PROOF`只支持读回原请求；证明schema为
lpai-manager-readonly-runtime-repair/v1，含read_only=true、original_plan_sha256、
from_runtime、to_runtime、source_ref。原授权字节摘要必须保持一致。
只读恢复证明不能用于apply，也不允许换UID或重开原写入。

native配置核验保留原始值，规范化已观察到的空列表、资源Name/PackageType/
LimitSum目录字段、resourceInfo显示摘要、由权威大写字段派生的小写资源别名及use_role_cmd默认值。
资源别名在provider读回中必须与Cpu/Mem/Gpu/GpuType一致。非空挂载、环境变量
大小写/键值、命令、资源Id和权威资源量仍不得改变。

仅SDK创建组件的读回额外投影PayloadBuilder复制而组件存储不保留的套餐目录
元数据（包括CpuLimit等目录字段），以实际Id/Cpu/Mem/Gpu/GpuType/GpuMem/Network/
replica核对套餐；完整native任务显式声明的限制字段仍严格比较。

## 开发空间官方API适配

依据2026-10-28官方页面加载的main.c8f13c8d.js模块58007和commons模块10：
GET/PATCH `/lpai/api/kubeflow/namespaces/{namespace}/notebooks/{name}`，
启动body为`{"status":"start"}`，停止为`{"status":"stop"}`。
只接受annotation `lpai.org/notebookVersion=v2`，路径限定名称/namespace。
使用已安装SDK `RestClient` 的正常LpaiJwt认证，未读取浏览器token/cookie、修改
SDK或审批策略；该路径的可用性来自本轮真实读写验证。它是本工具的API适配器，
不是官方发布的Notebook SDK类；接口兼容性以冻结源码和真实验收为依据。
通用Manager不暴露任意URL/HTTP方法，仍只接入已有Notebook启停；限定的CPU新建入口见下节。

## 本人CPU开发空间新建与Hello World实测（2026-10-29）

新增独立脚本 `scripts/lpai_notebook_create.py`，没有修改现有Manager六文件运行摘要，
不影响已冻结的A800管理计划。依据官方创建页2840.b4a35b93.chunk.js，使用SDK
RestClient认证调用POST `/lpai/api/kubeflow/namespaces/{namespace}/notebooks`。
此POST在官方UI中的含义是“保存并启动”；不应假设新建后一定处于stopped。

当前资格严格限定：本人namespace/owner、personal v2空间、套餐5×1，1CPU/4GiB/0GPU，
已有SSH公钥引用、jupyter-lab与ssh服务，无环境变量或数据/模型挂载。绑定配置、
工具源码、授权和共享Guard；未知POST只GET精确名称对账，不改名或重放。
SDK错误输出只保留结构化HTTP状态/错误类型和已知quota标志，不保存认证内容。

官方个人空间上限为同一集群最多5个，停止的空间也占名额。创建前查询本人该集群
空间数，达到5个时不POST；不得擅自删除旧空间，平台提示删除会丢失空间数据。
本次用户亲自清理后，从5个变为3个，才继续一次新的显式授权创建；原失败意图保留。

实际创建 `codex-hello-20261029`，UID `bae3ffe6-03d0-487b-aa92-b2c3c569b13b`，
owner 与该案例保留的原始授权记录一致，官方GET从pending到ready。通过API返回的SSH端口，
复用本人已有密钥，首次主机公钥记录到案例独立known_hosts，在该空间的
`/lpai/codex-hello-20261029/hello.py` 执行并得到 `Hello World`、退出码0；远端hostname
为 `codex-hello-20261029-0`。全程没有浏览器、同事登录态或飞书确认。

证据：`_bmad-output/lpai-devspace-hello-20261029/`，包括官方源码哈希、10项检查、
create-after-cleanup-result.json、hello-result.json与远端输出。创建结果不等同于
通用GPU空间、环境保存、删除接口或任意远端命令的资格；编辑/删除未暴露。

## A800实队列中发现的传输差异

2026-10-28，实际16A800任务发现Task API把环境变量值中的`$$`折叠成`$`。
因此任意base85压缩代码载荷可能损坏：组件GET正确也不能代替任务GET。
本工具现对该已知不保真的环境值写前拒绝，报
`provider_rewrites_double_dollar_environment_value`；压缩代码应使用无美元字符的
标准base64，并同时校验Task GET原文、解码SHA和文件字节。不能靠放宽比较放行。

GPU任务的gpu_type兼容别名省略`nvidia.com/`，只接受这一明确前缀变换，
权威GpuType仍严格核对。原32卡任务和16卡任务的实际Network均为3，而目录为2；
本次修正任务显式声明Network3，不将此字段加入忽略列表。重复的旧dataset.env
从新输入移除，正式env_key及挂载路径保持。枚举物理含义未据此推断。

A800修正版当前已通过真实组件/任务完整读回，总16GPU，活动匹配任务仅1个。
首次坏载荷任务已官方stopped且GPU角色Pod终态，保留原Guard与UID，并用有记录的
终态证明转接一次修正替换；累计两次Task.submit如实记录。旧32卡create审批仍
未证明关闭且身份隔离。证据：`_bmad-output/a800-recovery-16gpu-20261028-r9/`。
该结果是提交和配置验收，不能当成分布式训练完成或模型质量改善。
