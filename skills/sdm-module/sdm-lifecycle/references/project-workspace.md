# 默认项目工作区与留存证据执行

接手项目时，先建立独立工作区并登记已有仓库、材料及目标。不要把新项目默认转换为“开发一个适配器”。已有仓库登记真实路径和commit，不复制整个仓库；只有需要隔离修改代码时再使用既有worktree能力。非Git项目可以省略repository。平台写操作或特殊模型运行接口确实有差异时，再使用现有适配合同。

默认本地链路使用统一公开入口：

```text
python run_project_pipeline.py --project PROJECT_CONFIG --output PROJECT_WORKSPACE --project-workspace
```

配置示例：

```json
{"schema":"sdm-project-workspace/v1","project_id":"project-name",
 "repository":"/absolute/existing/repository",
 "material_roots":["/absolute/narrow/evidence-directory"]}
```

project_id、仓库归属保持稳定；新增材料根保存登记修订，旧运行不覆盖。每次运行记录当前commit、tracked diff摘要和dirty状态；这是离线案例的仓库登记，不冒充训练源码完整冻结。Git登记只读，不clone、checkout、commit或修改仓库。工作区含project.json、materials索引、runs独立运行、latest指针、review-inbox及failures；配置可保存在工作区workspace-config.json。无需把日志、权重或数据重复放入Git，原件使用路径及摘要引用。

材料发现只在明确配置的根内进行，跳过.git、隐藏项与依赖缓存，限制根数、遍历量、文件数、单文件及总字节，拒绝链接。默认登记标准manifest/request/response、日志/文本、JSON/CSV、NumPy/ZIP、图片和模型原件等受支持后缀的路径、字节数及摘要；模型/pickle只作不反序列化的原件登记；不扫描任意账户目录或凭据。不支持的格式明确缺口，不能声称已经理解所有项目材料。

- 找到一个标准评审包：自动核对原始文件摘要、请求摘要和历史日志引用，调用现有loop_analysis.validate，提取已有结构化指标，归档决定、限制、来源与人类可读交付。
- 找到多个包：返回候选，由当前Agent根据已对齐任务选择；没有依据时才询问用户。不按文件时间默选最新。
- 只有原始材料且缺准备上下文：登记原件并写material-preparation-inbox.json，返回awaiting-evidence。当前Agent读取任务单及有界原始片段，填写material_preparation声明式配置，再调用同一入口；不另写项目脚本或适配器。默认生产器生成请求和原件引用manifest，评审结论仍由Agent撰写。
- 缺评审回应：在review-inbox固定请求引用、摘要和local-agent/所选评审模式；Agent按共享评审合同继续，不自动调用付费Pro，不把等待转成通过。
- 失败：保存结构化失败，不发布成功结果。恢复重核输入及归档摘要；材料或仓库版本变化形成新运行，不能复用漂移结果。

该模式落实已有项目登记、材料/身份管理、证据分析、评审、决策及归档职责，不新增第35个节点，也不修改训练模式的34节点策略。它不是“34节点训练全通过”的替代证明；不会调用SDK、训练、评估模型、资源预算或获取/停止动作。远程checkpoint及产物位置只作为历史引用登记，没有打开或认证时明确标记。

收益应通过相同真实材料的对照衡量：实际人工问答、耗时、错误拦截及可追溯交付。原SDM使用共享试验夹具时需明确哪些能力来自夹具，不能归功于旧版本；固定已有回应的回放不能证明新的评审质量或人工时间节省。


## 原始材料的通用生产配置

在原有项目配置内填写material_include_globs（可选）及material_preparation；不运行项目Python模块。材料根仍必须显式登记。glob用正斜杠，相对于各材料根；用窄根和include限制无关报告，避免把旧结论再当原件。

```json
{
  "material_include_globs": ["*.log", "arrays/*.npy", "outputs.zip"],
  "material_preparation": {
    "schema": "sdm-material-preparation/v1",
    "objective": "从保留原件诊断本次实验；不执行新训练",
    "project_metrics": ["evaluation.latency_ms"],
    "inspect_array_finite": false,
    "inspect_zip_crc": false,
    "roles": [{
      "id": "candidate",
      "root": "/registered/material/root",
      "log_globs": ["*.log"],
      "text_rules": [{"pattern": "latency_ms=(?P<latency_ms>[0-9.]+)",
                      "groups": {"latency_ms": "float"}}],
      "text_flags": [{"name": "traceback_present", "pattern": "Traceback"}]
    }]
  }
}
```

每个角色唯一选中一份日志。text_rules按命名捕获组生产int/float/str，只接受唯一匹配；重复或缺失会阻断，不擅自取最新、不补零。text_flags只表示所选文本中的匹配存在/缺失，不推断任务退出或平台状态。字段名保留单位；Agent需阅读原始日志确认提取规则含义。正则限制长度并拒绝回溯引用、lookaround及量化分组，不提供表达式求值、命令或插件加载。角色数组顺序只标识材料观察，不意味着此次实际跑过多轮训练。

可选comparisons列表以id、left/right角色和globs配对同名相对路径。默认比较文件摘要；提供json_field则比较明确JSON字段值，如scene.id。键集合必须相同且非空，缺字段/样本会阻断；相同场景/字节不证明科学实验可比。数组默认只检查有界头部和载荷大小，inspect_array_finite用于普通数值数组有限性；ZIP默认检查有界成员元数据，inspect_zip_crc才检查全部成员CRC。不会解包或反序列化模型/pickle，不从数组自动推断业务指标。

默认上限512原件、单文件256MiB、合计1GiB、遍历10000项。文本观察单文件8MiB，单行32768字符；ZIP最多2048成员/512MiB展开字节；JSON字段投影单件1MiB。达到上限应缩小材料范围；不得静默跳过并声称完整。大于上限的checkpoint需独立明确处理，本入口不会自动下载或执行。

协调器生成prepared/<内容摘要>/analysis-request.json、manifest.json(v2原件引用)、preparation.json，并写material-review-task.json。v2每个原件必须属于本轮发现集合且原位摘要匹配；不再为适配平面manifest复制日志。请求包含事实查找表、原始位置、数组/ZIP检查结果与限制。Agent直接向给定response_path提交原共享评审合同；任务单提供空模板但不会替Agent填写事实和判断，不自行调用Pro或外部服务。

材料/规则/生产器版本变化产生新准备目录、重新等待评审；旧目录保留。相同输入重复调用重核原件与生成记录，拒绝覆盖被改写的生产记录。返回timings区分登记、发现、准备和验证；登记耗时包括现有只读Git快照，不降低其检查强度。新生产材料回应标记response-for-current-produced-materials；这是材料来源分类，不认证评审者身份或生成时间。手工导入原包继续保留existing-response-revalidated分类。


## 单次调用的I/O合并边界

不保存跨调用的文件摘要或基于mtime的信任缓存。每次执行仍先发现并计算原件摘要，生产之后由统一read_case重新读取原件内容，才允许返回待评审、接受回应或恢复成功。协调器内生产器不再额外重复一次相同的全量摘要；独立调用公开prepare时仍保留自己的终态原件核验。内部_coordinator_prepare不是可单独使用的入口。

文本事实和行号取自同一次字节读取，该次摘要必须匹配初始发现；同一调用的多个JSON字段投影共用一次已绑定摘要的文档读取。下一次调用会重读；文件即使大小和mtime恢复，内容变化也会使原回应失效。CRC、数值有限性、生产记录及评审事实校验不取消。

Git登记把仓库根与HEAD合并为一次rev-parse，仍逐次核查tracked dirty和完整tracked diff；timings.registration_detail记录identity/status/diff耗时。过滤无关文件可减少路径解析，但遍历的链接仍拦截，登记范围、数量和字节上限不改变。


## Windows/WSL仓库内容快照

WSL登记Windows挂载仓库时，不能把Windows Git的空diff直接替代WSL结果。系统/用户换行配置和原索引stat缓存可能不同。支持的普通文件仓库使用fresh-index-v2：以当前真实索引的路径、对象、暂存状态重建临时私有索引，按WSL有效配置重新读取工作树内容；不改变core.autocrlf、不丢弃CRLF差异，不写真实索引。

原生Git查询先通过WSLENV显式传递私有索引、只读锁和查询配置，并在update-index之前用rev-parse --git-path index确认实际路径。未指向私有索引即阻断该加速尝试。查询禁用promisor自动拉取；临时索引清理不影响真实仓库。

第一次对每个配置/索引/内容结果，分别计算原生和WSL私有索引快照；HEAD、完整status摘要和完整diff摘要完全相同才记录等价回执。后续每次仍从私有索引重读参与比较的当前跟踪内容，并保留真实skip-worktree语义；只在候选内容结果及仓库/配置/工具/实现指纹均匹配已验证回执时省去同结果的WSL重复计算。不是mtime缓存，不直接信任上次文件摘要。内容、索引或配置变化需要重验，损坏回执视作缺失重新验证。引擎结果不等价时使用WSL私有索引基线。

存在assume-unchanged/intent-to-add等尚不支持的索引标志、符号链接/子模块/冲突、自定义attributes/过滤器/扩展或POSIX模式语义等尚不支持的情况，保守回到原Git路径并报告原因。skip-worktree标志明确复制到临时索引，被排除且缺失的稀疏路径不误报删除；其他不支持的特殊标志回退，绝不清除真实标志。HEAD、原索引字节或有效配置在查询中漂移会阻断。

fresh-index-v2纠正了旧stat缓存可能跳过内容重读的问题，因此可能比旧快照显示更多差异。它带明确snapshot_mode，形成新归档，不改写历史证据；这是内容核验修正，不是训练策略/节点变化。它不是全库原子冻结，也不替代已有训练源码与checkpoint合同。优化仅用于注册仓库的本地只读查询，不调用平台或启动训练。

## 结构化JSON原件与交付质量

仅有JSON指标时，角色可以使用json_globs和json_fields，无需伪造日志或转换格式。每个角色选择一种观察格式：原有log_globs/text_rules/text_flags，或json_globs/json_fields；不得混用。JSON必须唯一选中一件已登记的.json文件；上限1MiB，当前调用内字段和比较共享已核对摘要的读取，跨调用重新读取。重复键、缺字段、错误类型、非有限数、歧义选择和原件漂移均阻断。

```json
{"id":"candidate","root":"/registered/root","json_globs":["metrics.json"],
 "json_fields":{"accuracy":{"field":"test.accuracy","type":"float"},
                "samples":{"field":"dataset.test_rows","type":"int"}}}
```

field使用点分字典路径，不执行表达式，也不支持隐式数组遍历。int/float/str/bool严格区分，不把布尔、数字字符串或整数自动转换成浮点指标。字段名需标明必要单位。来源记录包含原件路径、摘要和JSON字段。共享历史中的log_digest保留为兼容字段，在JSON角色指向该JSON原件摘要，不代表存在控制台日志。NPZ/ZIP检查仅验证归档结构和可选CRC，不反序列化模型，也不认证模型精度。

归档包含review.json，并与diagnosis、decision和REPORT一起校验摘要。人类可读报告展示评审判断、引用事实、解释及限制；不以通过事实校验代替科学结论正确性。旧归档保持不变，实现变化形成新运行。

材料收益需区分三类：开发期间已反复使用的材料、未用于当前接入实现的格式留出材料、完全未参与开发的陌生项目。历史示例不能冒充严格盲测。记录实际声明式字段/比较数量、适配器与转换脚本数量、复制字节、人工问答、首次有效交付和恢复耗时；没有计量的人工活跃时间标记未测，不能用CPU秒数替代。

## CSV/TSV与独立CPU模拟验收

表格原件无需转换成日志。在角色中使用table_globs、table_format（csv或tsv）、row_selector和table_fields；不能与日志或JSON观察配置混用。显式选择器按文本值精确比较，必须唯一选中一条记录，不按最后一行或时间自动选择。示例：

```json
{"id":"candidate","root":"/registered/root","table_globs":["metrics.csv"],
 "table_format":"csv","row_selector":{"split":"validation"},
 "table_fields":{"accuracy":{"column":"accuracy","type":"float"},
                 "samples":{"column":"samples","type":"int"}}}
```

限制为1MiB文件、4096条数据记录、128列、单元格32768字符。表头不能为空或重复，行必须与表头同宽；支持标准引号和UTF-8 BOM。整型必须为整数字面文本，布尔只接受true/false；浮点必须有限。显式类型转换不允许表达式或命令，CSV公式只作文本，不执行。字段来源记录原件路径、摘要、数据记录序号、结束行和列名；带多行引号时数据记录序号不等于物理行号。每次调用按内容重核，调用内共享已绑定摘要的解析。

合成验收项目在工作区配置显式声明evidence_kind=cpu-simulation；省略时按已有retained-material合同处理，默认值不认证来源真实性。模拟标签进入项目登记、准备任务、请求、评审任务、诊断、最终结果和报告，公开入口等待/成功/失败也展示标签。模拟回应必须明确携带相同标签；请求即使事实正确，回应未标模拟仍不接受。登记之后不能在同一工作区去掉或改变标签，不能把模拟请求作为真实留存包导入。新建目录或复制材料不构成独立真实项目证据，来源真实性仍由Agent核查。

模拟案例可验证输入输出合同、异常拦截、失效、归档和恢复，但不验证真实训练、GPU、SDK、计费、释放或科学效果。完整CPU案例也不表示34节点训练图全执行。故障注入只作用于专用合成副本，记录预期、实际原因、恢复和来源标签；不修改生产项目原件或活跃任务。
