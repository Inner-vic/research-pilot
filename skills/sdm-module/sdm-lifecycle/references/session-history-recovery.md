# 分段清理对账与长历史上下文

保留既有恢复策略与资源授权；工作包重整不改写历史证据。以下为现有统一协调器的恢复与上下文机制，不新增节点或后台监控。

## 分段清理失败后的独立对账

清理失败时阻塞后续派发。不得覆盖原始失败文件、仅凭最后一个任务已停止解除阻塞，或重跑整个Loop来试探资源状态。

1. 读取Study的long-loop-state.json，定位sessions/<id>/cleanup-result.json及dispatch-intents.json。
2. 项目驱动根据本段全部派发轮次提供allocations归属见证；每项绑定绝对attempt路径、已接受的节点与resource_identity字段。每个派发轮次至少有一项见证，项目须完整枚举实际资源。
3. 创建schema为sdm-session-reconciliation/v1的请求，提供reconciliation_id、original_cleanup_digest、allocations和driver。driver必须声明read_only:true；外部操作只能read或diagnose。receipt必须是本session/reconciliation/<id>/readback.json。
4. 使用统一入口运行独立对账：

```text
python run_project_loop.py --project <project.json> --output <study> --reconcile-session <session> --reconciliation-request <request.json>
```

请求示意（路径须替换为实际绝对路径）：

```json
{"schema":"sdm-session-reconciliation/v1","reconciliation_id":"check-1",
 "original_cleanup_digest":"sha256:...",
 "allocations":[{"attempt":"<study>/round-01/attempt-01","node":"14","identity_field":"observation.resource_identity"}],
 "driver":{"read_only":true,"timeout_seconds":60,"argv":["<read-only-driver>"],"receipt":"<session>/reconciliation/check-1/readback.json"}}
```

回读payload须含task_node:cleanup-readback、status:completed及resource_readbacks数组。每项含精确resource_identity（provider/namespace/uid）、source_ref、带时区observed_at及resource。resource须released:true、终态status、pods_complete:true及全部终态pods。首次接受要求观察不超过5分钟，不能省略本段其他资源。

5. 验证配置、全部派发清单、原始失败、任务与归属事件摘要，以及全部资源回读。独立操作只回读，不发起停止或训练。接受结果写cleanup-reconciled.json，原始失败保留。
6. 同一请求重复调用复用已接受证明，不重复执行回读；其他请求、文件篡改或不完整证据被拒绝。正常Loop入口读取有效对账后才允许继续，并保持下一轮原授权门禁。对账本身不推进训练。

缺原始清理回执时，可在同一对账请求显式提供original_cleanup_digest:null及missing_cleanup:true。必须仍有完整dispatch-intents.json，且dispatch-intent.json与其最后一项一致，并通过原有全部资源归属、只读终态回读核验。接受证明注明原始回执缺失，不补写或伪造cleanup-result.json；后来出现原始回执会使绑定失效并要求重新核实。恢复后PAUSE与下一轮原授权门禁仍适用。缺完整派发清单的旧session继续阻塞，不猜测覆盖范围；不能将旧段成功回执当作当前段成功。项目回读不是平台认证：实际平台资源完整性和权限仍需平台验证。命令的read_only声明亦不能替代驱动代码审查。

## 长历史上下文压缩

项目可显式配置：

```json
{"history_context":{"recent_rounds":2,"max_context_bytes":16000}}
```

未配置时保留现有请求格式；配置不可在同一Study中静默改变。超过recent_rounds后，评审请求保留最近完整轮次，以及最多三个较早摘要锚点（首轮、最近失败、最近不可比）。增加失败、不确定、不可比计数和可比较有限指标的观察范围；这些范围不等同于最佳模型或目标达成。

原始round-observation.json保持不变。history-index.jsonl追加记录每轮路径及摘要；请求固定前缀摘要，后续追加不会使旧请求失效。完整历史仍参与决策验证与参数去重。原始文件或摘要不匹配时拒绝分析，不用压缩摘要覆盖原始证据。

需要较早轮次的完整内容时，通过同一公开入口按需读取：

```text
python run_project_loop.py --history-request <analysis-request.json> --history-round 50
```

可按原始r50.evaluation.scores.rmse等键引用已读取事实，验证器核验完整原始记录。压缩请求避免在扁平evidence中重复长日志；日志仍保留在最近完整记录或原始轮次中。预算按规范化UTF-8 JSON字节计量，不等同模型token；超过预算明确报错，由调用方缩小近期窗口、按需取证或显式增大预算，禁止静默截断。

按轮取证及完整读取的索引扫描采用流式处理，不先构建整份索引列表。按轮读取仍扫描并核验该请求固定的完整索引前缀，验证完成前不返回早期轮次，防止遗漏尾部篡改。完整历史读取仍核验每轮原件及整体摘要。

完整决策验证使用单次流式观察扫描：逐轮核对原件及索引绑定、累计规范JSON整体摘要、计算原统计、解析被引用原始事实、核对下一组参数是否曾出现，并保留最新一轮供原继续条件检查。发现早期重复或已找到引用仍扫描到固定前缀末尾，全部摘要通过后才接受决定。恢复绑定与归档整体摘要也逐条序列化，摘要字节与既有完整列表格式兼容，不信任未核验缓存。complete接口仅保留给显式需要整份列表的调用者。

Loop本身仍保留结果与历史对象，因此不声称整体进程内存固定，也不把局部改进视为整个Loop端到端加速。测量须同时报告完整决策核验、恢复摘要的时间及额外分配；流式恢复可能以少量CPU时间换取更低临时内存。1000/5000轮测量仅为CPU合成证据，不能外推真实训练性能。

## 验证边界

CPU覆盖只读子进程、独立公开CLI、重复调用、缺失/错误资源证明、回执篡改、分段恢复、100轮压缩、旧轮取证、跨窗口参数去重、归档追加及超预算拒绝。CPU资源身份和停止状态均为模拟；不构成真实GPU释放、训练、抢占恢复或模型质量证据。
