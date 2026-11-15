# Study、Trial与Attempt身份

本合同是元数据和恢复边界，不增加重试次数、训练权限、调度器或外部依赖。
项目入口在每个attempt目录保存不可变experiment-identity.json。

- Study：研究目标集合，study_id默认run_id。
- Trial：同一输入和参数的试验，trial_id默认run_id。
- Attempt：一次具体执行，attempt_id为run_id-aNN。
- attempt_kind：new-trial（默认）、retry或resume。

trial_inputs由项目提供source、data、protocol身份，可添加初始化、环境等影响可比性的条件。
参数取loop_parameters或experiment_parameters，驱动入口与依赖包摘要也参与trial_fingerprint。
没有trial_inputs时只生成input-identities-unverified记录，不宣称可复现，也不允许据此建立retry/resume。
这些身份来自项目声明，本模块不查询平台或重新验证数据内容。

retry/resume需要parent_identity的绝对路径和字节摘要；父记录须有相同Study、Trial与fingerprint，
并使用新的run_id形成不同Attempt。改变batch、学习率、数据、代码或评估协议属于新Trial，不能伪装为重试。
原任务已终止、资源释放和新执行授权仍由原资源合同负责，身份验证本身不授予执行权限。

resume还需：

- resume_checkpoint：实际文件绝对路径及字节摘要。
- restore_evidence：固定摘要的sdm-checkpoint-restore/v1记录，含相同checkpoint_digest、status=verified、restored_states。
- required_resume_states：至少model、optimizer、scheduler、rng；AMP或采样器等适用状态也应列入。

模块验证文件和既有恢复证据的绑定，不执行torch.load，不将元数据测试当真实完整恢复。
真实恢复证据应由项目的恢复试验产生；只有权重时应归为初始化的新Trial。

通用Loop将各轮归入同一Study，各轮run_id形成不同Trial；单变量与授权策略保持不变，轮数与调用时限由配置选择。
轮次适配器不得覆盖Study/Trial身份、父记录、checkpoint恢复声明或执行器能力合同。
3轮／1800秒是开发默认值，不是应用硬上限；分段长期模式见long-loop-dependencies.md。没有新增自动参数搜索或监控。
