# 合并工作包与材料入口

普通任务使用workflow_revision=1：五阶段11个主步骤。旧ID1—34是内部检查和驱动回执身份，保留历史兼容，不再作为34个同级业务步骤。主步骤、条件触发和流程见[five-phase-orchestration.md](five-phase-orchestration.md)。

## 执行与依赖

`workflow_definition.py`是最终依赖唯一来源，`work_packages.py`定义职责与材料合并，`node_registry.py`赋予展示编号。项目入口自动选择实际驱动；任务和配置快照包含工作流版本。源码更新不改冻结运行时，旧记录不重新编号。

- 1.1任务约定完成后，执行1.2材料确认，再选择模型和形成方案。
- 2.2包含方案协议、方法检查和方案审查；R资源方案参与审查。方法检查不是第二次方案生成。
- 3.1仅在改代码时适用。`code_review_required:true`使旧检查11成为必需，并在冻结和资源提交前拦截缺失/失败；未声明时保留可选政策。不能为复用代码强行开启代码修改评审。
- 本地检查和身份冻结在资源获取前；远端检查在获取后、计算放行前。组件自动开训时仍须使用入口等待门，不能将新工作包当成自动开训许可。
- 4.2共用一次已完成评审的材料，但证据一致性与结论充分性仍分别检查。5.1共用一份结论/行动材料，推荐与实际决定分字段；二者均不产生计算授权。
- 清理保持及时、幂等和独立。清理对账失败不阻塞初步诊断、结果审查和无执行权限的行动选择；5.2归档完成及下一轮放行仍被阻塞。停止读回与费用结算仍分别报告。

## 一份材料替代重复准备调用

项目/TASK配置的可选`work_package_materials`映射工作包稳定名称到固定JSON引用。公开项目入口排除该材料已覆盖的准备驱动，再对各项进行确定性检查；旧配置未提供材料时仍调用原驱动。不要给同一记录同时绑定两种生产器。

```json
{
  "work_package_materials": {
    "task-contract": {"path": "/absolute/task-contract.json", "digest": "sha256:ACTUAL_FILE_HASH"}
  }
}
```

材料示意（摘要占位符和background省略体须由真实材料替换）：

```json
{
  "schema": "sdm-work-package-material/v1",
  "work_package": "task-contract",
  "evidence_refs": ["project/requirements-source"],
  "checks": {
    "1": {"finding": "项目与任务已登记", "evidence_refs": ["project/requirements-source"]},
    "2": {"finding": "指标、边界与验收标准已确认", "evidence_refs": ["project/requirements-source"]}
  },
  "records": {"background": {"schema": "sdm-orchestration-background/v1"}}
}
```

| 工作包 | 可由材料覆盖的准备检查 | 可共用的记录；均经过原语义校验 |
|---|---|---|
| task-contract | 1、2 | background |
| experiment-plan | 6 | research、method_review、plan_review |
| implementation | 9 | 无；10/11/12不能由材料假装执行 |
| evaluation | 18 | 无；实际评估16/17仍独立执行或显式复用 |
| result-review | 无准备驱动 | review_context、review_response、evidence_consistency、conclusion_quality |
| action-choice | 无准备驱动 | next_action、transition |

`checks`须列全该包可覆盖的准备检查；每项finding非空，证据引用来自材料evidence_refs。`records`仅列实际已产出的允许记录，可省略尚未完成的审查。省略时仍沿既有路径等待或使用其他已绑定的生产器，不能补造通过。

材料只声明已有项目事实/评审，不会调用模型或产生新评审；执行者仍须实际阅读原件并作出判断。它不允许代替数据、代码检查、预检、计算、用户确认、资源获取或清理；不允许自动生成confirmation。已接受源字节固定，重入不重复准备调用；内容变更需要新尝试。单份材料目前有1MiB上限，原始日志和checkpoint使用引用。

## 进度与恢复

公开协调器返回`work_packages`和`workflow_revision`；`work-packages.json`每次协调器返回时从持久记录投影，不在每个检查后重复写完整快照。`events.jsonl`保存原检查身份和接受证据。coverage分别报告business_step_count与internal_check_count；包completed表示适用的必需检查完成，可选未测列在optional_unmeasured，不代表整个项目质量达标。

旧摘要不一致时先锁定原协调器，检查原task与回执，在新目录导入可验证观察；新工作流对graph3旧任务使用`current-workflow-r1`。若旧任务已执行或可能执行资源动作，原清理尚未闭合则拒绝迁移，继续由原冻结协调器观察和清理，不能接管正在运行的任务。不修改原journal、不凭历史评审自动接受新结论、不重派未知副作用。正常恢复优先使用原输出目录和原TASK快照；禁止用改run_id规避对账。
