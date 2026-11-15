# 提交前审议与可靠迭代

目的：先把下一次排队/长任务的科学问题、信息收益和执行准备说清楚。RoadModel是首要验收项目；合同不依赖模型名、指标名或平台。负结果可以有价值；CPU合同通过不能证明科学质量或真实平台稳定。

## 配置与恢复

新生成的RoadModel开发空间/组件项目默认启用通用配置：

```json
{"proposal_review":{"version":3,"backend":"model","context_required":true}}
```

其他项目通过同一字段启用。新v3按结论和实质进展循环，没有min_rounds/max_rounds，也不要求为了凑次数制造质疑。训练轮数与审议往返次数分别记录。旧v1/v2冻结合同按原规则回放，新任务不套用原2—6轮或12轮上限。

| B的判断/实际进展 | 下一动作与退出条件 |
|---|---|
| approve | 同一确切方案和证据、无未关闭重大问题、理由充分，结束审议；首次已就绪也可批准。原预检和执行授权仍独立 |
| revise | A核验建议的适用性后作实质回应或修改，B重新审查；没有轮数上限 |
| needs-evidence | 暂停模型往返、等待缺项；通过原只读入口补充已登记证据后续接同一循环。新来源/变更背景须另建关联的不可变审议，不改旧记录 |
| reject | 本提案立即结束并保留拒绝理由，不提交训练；可依据新证据形成新的关联方案 |
| stalled／没有实质修订 | 分歧无法推进时暂停并说明问题；只改版本号或调用标签不会再调用B。不自动批准，也不重提训练 |

模型声称“同意”不是充分证明，Agent仍核对关键异议、证据来源、可执行性、对照与成本。对语义重复无法完全机械判断，A/B应明确报告无进展。用户主动停止或已明确授权的时间/费用边界也会暂停；不补设默认轮数预算。

v2/v3的`backend:model`通过现有文件交接接收两个实际模型的输出，模型提供方和型号不限；不因保存了`external_review`或`proposal_review.pro`就选择Pro。内部`review_mode:local-agent`表示由Agent交接文件，不证明只调用了一个模型，也不能充当双模型已运行的证据。当前协调器不自行调用模型，Agent使用实际可用的模型工具并保留原始调用记录；第二模型不可用时明确等待，不能通过两个角色标签伪装独立调用。

Pro是可选后端。只有明确选择`backend:pro`才使用`external_review`（或`proposal_review.pro`）的`project`、`workflow`、绝对路径`session_root`。这时绑定必须完整且两处一致，错误会话/缺回执不能在本次尝试内改道绕过。v2/v3选择也适用于结果评审。新RoadModel配置不能用null关闭审议。冻结v1/v2配置按原规则回放，不改历史请求/回执；新选择使用关联的新尝试，不迁移已归属资源。

使用当前安装目录的公开入口（替换为自己的实际路径）：

```sh
python "$LIFECYCLE/scripts/run_project_pipeline.py" --project /absolute/project.json --output /absolute/attempt --proposal-review
```

此模式只生成/校验文件，不调用模型、平台或分配资源。完整开发空间/组件执行入口会在新提交前调用同一检查。Study在每个具体子轮/重试自己的目录审议，依据返回状态中的子轮project、proposal_path、subject_digest操作；不要拿顶层Study配置的摘要替代子轮。

1. `awaiting-proposal`返回`subject_digest`及`proposal_path`。A在该路径写`sdm-proposal/v1`对象：精确`subject_digest`，正整数`revision`，`proposer:{role:proposer,identity,source_ref}`，以及非空文本`objective`、`hypothesis`、`evidence_summary`、`counterevidence`、`alternatives`、`comparison`、`expected_observations`、`outcome_actions`、`cost_and_queue`、`preflight_and_stop`。引用本地证据用`evidence:[{path:绝对文件路径,sha256:文件SHA256}]`；接受及恢复时重新核对。单份方案/回复上限128 KiB，证据最多32项。
2. v2/v3的`proposer`与`reviewer`还需要`model:{provider,id}`、`call_ref`，引用实际模型和调用原件。A/B模型必须不同，每轮B复审使用新的调用，A修订使用新的调用。程序能拒绝同模型换标签或重复调用引用，但这些字段仍是声明，必须与真实工具/会话记录核对；字段通过不能独立证明提供方调用已发生。
3. 再次调用得到不可变`request_path`。B读取完整请求和证据，核对假设、反证、对照、替代行动、信息收益、成本及执行准备。`backend:model`在`response_path`写`sdm-proposal-review/v1`：精确`request_digest`；`reviewer:{role:critic,identity,source_ref,model,call_ref}`；v3的`verdict:approve|revise|reject|needs-evidence|stalled`；有依据的`rationale`；`challenges:[{id,severity:blocker|major|minor,question}]`；`resolutions:[{challenge_id,status:open|resolved,reason}]`。v3没有异议时可基于充分理由批准，不虚构问题；要求修订时必须指出可行动的质疑。挑战ID不可重定义以降低严重程度，v1/v2原校验不变。
4. `revise`后A增加revision并作实质回应，`addresses:[{challenge_id,reason}]`逐条回答重大异议，保留旧快照。最终approve逐一复核并关闭所有历史blocker/major，不能同一回复新提重大异议又批准。每次重新审查使用新的真实调用；v3不设置至少/至多次数，旧v1/v2按原合同回放。机械校验不能代替评审质量判断。
5. `proposal-approved`只表示方案合同通过。全配置摘要绑定参数、输入、授权、驱动等；证据或最终方案被改动即失效。后续仍需原资源确认、实际预检、写入intent、精确UID读回、停止/释放核验。不要把审议当作用户算力授权。

## 共同证据、只读补证与建议核验

### 新的启发式研究对话

新ModelRunner提案可显式设置`proposal_review.dialogue_version:2`（仍使用policy version4），
将简短的方向审查直接放入A/B实际prompt，复用原有字段、挑战ID和按结论退出机制。
未设置时保留v1提示文本；旧冻结尝试不改版本、不热换运行源码，新选择另建关联尝试。

使用独立结果工作器时，同时显式设置`research_worker.dialogue_version:2`，让同一研究方向
原则进入结果A/B请求。该选择也进入请求、来源和重放绑定；不能只更新磁盘提示词却继续
使用旧冻结运行态。实现需要改变或决定性测量尚不存在时，B应直接`needs-evidence`，
不能要求A仅在下一段文字中承诺修复，再审议同一份未改代码。新代码经检查后建立关联
的新绑定；不把原始异常、失败尝试或尚未完成的训练改写成成功。

A先还原目标口径、剩余差距和错误结构，再在`hypothesis/alternatives`解释干预机制、
最强竞争解释及为何当前行动更值得做。B先审选题价值，再审比较与执行条件；内部比较
严谨不能替代方向有价值。A修订须核验B建议的适用性，不能靠增加实验和字段数量获得批准。
没有实质推进时保留分歧、补决定性证据或暂停；就绪可首轮批准，不规定往返次数。

这不是“效果差就必须换模型”的规则。小规模实验可以验证大幅设计变化；数值稳定不等于
学习充分，轮次有限也不证明结构达到上限。已证实的数据/度量缺陷可以直接修复。
项目提供指标和代码证据，确定性检查核对来源、参数消费和比较绑定，模型负责提出与
质疑解释；没有机械检查能证明科研判断正确。效果需用保留事实的压力案例和实际项目
讨论检验，不能只检查prompt里是否出现某些词或把增加对话数当作质量提升。

数值事实与判断分开核对。卡数、每卡batch、累计batch、epoch长度和GPU小时应从
原始配置及实测周期作确定性计算，再进入方案讨论；两模型同时接受一个数值不构成核验。
例如GPU数与per-GPU batch是不同单位。发现事实错误时保留原回复，提交有来源的纠正；
如共同背景变化，创建关联的新不可变审议，不修改已冻结的上下文或伪造原轮补证。

新生成配置的`context_required:true`在生成B请求之前要求`attempt/review-context.json`。旧策略没有该字段时保持原合同，不热改已绑定任务。共同背景不能只复制A的结论：先从项目登记、配置、运行、结果和历史记录生成事实快照，再单列A的解释。缺失事实明确写未知及其影响；程序能校验绑定和文件完整性，不能证明材料完整、作者中立或模型确实阅读。

背景格式为`schema:sdm-review-context/v1`、精确`subject_digest`，非空的`goal,constraints,data,code_revision,configuration,history,unknowns`，以及`sources:[{id,kind,path,sha256}]`。kind为code/config/log/metric/artifact/history，path为绝对本地文件路径，最多64项。应登记去敏的不可变快照；活跃大日志提取有界片段并保留来源、时间区间和缺口，checkpoint登记元数据，不把二进制直接作为文本。敏感原件不能因为B提出需求而自动外发。背景和来源会写入不可变请求，接受及恢复时重新核对，变更须开启关联的新审议而非重写旧证据。

B可以索取已登记来源的片段，不接收任意路径、命令或网络URL。创建query.json：

```json
{"request_digest":"sha256:当前request摘要","queries":[{"source_id":"train-log","start_line":1,"line_count":80,"question":"哪些更新支持学习率过大？"}]}
```

通过同一公开入口加`--proposal-review --review-evidence-query /absolute/query.json`生成不可变supplement文件，再交给实际B会话。最多16条查询，每条200行，源文本最多8MiB；超过范围先制作有界快照并进入新绑定。补证本身不调用模型、不读取平台、不执行文本、不改变审议轮次，也不自动证明已发送给B。补证不可得时保留未知，不能捏造证据或无限索取。

B回复增加`evidence_assessment:{source_ids:[实际引用ID],judgment:supported|hypothesis|insufficient,limitations:[]}`，每条challenge增加`basis`与`resolution_criterion`。信息不足不能批准，未知效果应形成可检验假设，不能一律当成阻断。一次性粘贴到Pro只能算有限上下文的第二意见；要参与持续审议，Agent必须实际交付所需补证并保留调用记录。没有工具访问不能描述为已看过全项目。

A修订时为上一轮每条质疑记录`advice_dispositions:[{challenge_id,decision:accept|modify|reject,applicability,reason}]`，核对真实代码、参数、checkpoint与比较口径。不能因为B是Pro就直接应用建议。重大问题关闭与执行授权检查仍适用；此合同不赋予B任意文件访问、平台操作或训练权限，也不保证通过后一定改善。新v3按条件循环；旧v1/v2仅在冻结任务中保留原轮次合同。

## 选择Pro时的每一轮

复用[现有固定会话合同](pro-review-contracts.md)和[对话流程](pro-review-dialogue.md)：恢复实际会话及最新context，`prepare`当前request、`begin-delivery`、实际发送并留delivery回执、取得实际Pro回复后`ingest`、`plan`收尾，最后`export-loop-result`到当前轮返回的`external_review_result`。回复保留正常BMAD事实/假设/决定结构（此处`stop-analysis`表示仅完成审议，不是终止研究），另附上述`proposal_review`对象；其内部request_digest使用`source_request_digest`，外部BMAD request_digest仍绑定完整发送包。不能伪造model=Pro/verified_visible回执，也不能直接把本地JSON包装成Pro回复。

每轮独立完成接受记录与`plan`，释放固定工作流的active_review，下一轮继续同一对话。审议恢复会验证当前完整会话链及对应历史接受事件，允许后续对话推进；一般Loop当前决策保留原严格revision检查。不同任务/轮次的回执不可复用。本次明确选择Pro的结果材料评审和Loop分析也使用同一绑定，拒绝旁放的local analysis-response。断线/未知发送先同会话对账，不新开会话蒙混重发。

## 提交与监控

保留现有提交Guard：先持久intent，未知写结果只对账原request/UID，不能重提；pending approval不能切适配器绕过。审议等待发生在新资源动作前；已启动、未知结果、观察/清理中的任务继续原生命周期，绝不等待新方案才停止资源。

采集器以last_success计算观测年龄；last_attempt另列，读失败保留最后事实，不能解释成训练失败。连续错误达到阈值后仅对该任务读取退避，默认60秒基础间隔下120、240、最多300秒；`failure_backoff_max_seconds`可调15～3600且实际不小于基础间隔。成功立即清零，恢复事件只发一次；其他任务正常读取。inbox给出`collection_available`、`stale`、`retry_after_seconds`。stop文件仍逐秒检查。独立事件服务、显式确认和原UID回收合同不变。
