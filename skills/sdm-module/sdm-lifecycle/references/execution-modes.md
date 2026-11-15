# Conditional execution, research and review modes


Before continuing an ordinary execution task, read
[execution-recovery.md](execution-recovery.md) for the stage entry,
durable output, wait/recovery action and time-budget rules. Restore the existing
attempt and pending review before creating new work; read only the references
needed for the current stage. Keep long Pro reasoning outside the compute lease.

When the selected mode uses ChatGPT Pro review, evidence delivery through attachments or GitLab, decision
return, or planning without training, first read
[pro-review-orchestration.md](pro-review-orchestration.md).
For iterative Pro questions, evidence supplementation and prompt improvement,
read [pro-review-dialogue.md](pro-review-dialogue.md). Prefer grounded
plan quality over response speed; allow repeated questions before a decision.
Intermediate questions never advance accepted context or authorize execution.
For stalled gains, persistent error groups, ablations or a change of research
direction, read [research-strategy.md](research-strategy.md). Select
stabilization, improvement, diagnosis, study or confirmation from actual evidence;
prepare a project-owned study with explicit comparisons and outcome branches.
Research matrices do not relax the existing one-variable Loop or resource limits.
For methodology provenance and applicability, use
[methodology-reference.md](methodology-reference.md). Its source methods
are adapted into task/research procedures; publication and production checklists
do not become mandatory steps for every exploratory trial.
Use `scripts/model_review_bridge.py` for immutable packaging, existing decision
validation and offline planning. Default to locally approved sanitized attachments
and a visibly verified Pro chat; keep GitLab as the archive. Optional MCP/tunnel
setup is not a prerequisite. Reuse a fixed project/workflow conversation and
versioned local context via [pro-review-session.md](pro-review-session.md)
and `scripts/pro_review_session.py`; read that reference before every Pro delivery
or return. Restore the project's existing session binding and accepted revision
before composing a request; do not open a new chat for every review. Keep actual
chat URLs and context versions in project-owned state, not in reusable Skill
defaults. Reconcile pending delivery before retry; accept a new context version
only after the existing decision and session checks pass.
For durable pending requests, typed continuation checks and crash recovery, use
[pro-review-contracts.md](pro-review-contracts.md) and
`scripts/pro_review_workflow.py`.
Before actual transport, persist `begin-delivery`; retire obsolete pending work
through its CAS-bound `cancel` route with required same-chat reconciliation.
Retain cancelled requests and late responses; a linked replacement keeps the
fixed conversation and cannot reuse an old decision or authorize execution.
New generic Loops may use
`analysis_mode: external-review`: preserve the request, release owned resources
while waiting, and resume to a validated plan without dispatching a successor.
This asynchronous review does not start a Loop
or keep its resource-owning coordinator waiting. Historical request budgets are
not current execution authority. Continue through existing project providers
only within the user's current execution scope and after fresh preflight.

For an explicitly authorized multi-round task, read
[project-loop.md](project-loop.md) and use `scripts/run_project_loop.py`.
New projects must bind a project-owned `loop_adapter` by absolute path and SHA256.
Define variable domains, metric meanings, observation fields and comparison
conditions in project configuration. Read [loop-adapter.md](loop-adapter.md).
Do not copy RoadModel parameters, metrics, GPU or sample requirements into the
generic Loop. Required code/data identities remain; other checks follow the goal.
Use the project-authorized round scope; development defaults are not application limits. Change exactly one variable per successor, analyze
authenticated results and write the next hypothesis before execution. Check the
project's declared fixed conditions and record optional missing observations.
The outer Loop always releases
resources; model quality is separate from automation acceptance.
New Loops use `decision_mode: model-bridge`: the active model reads each immutable
`analysis-request.json` (including actual log excerpts and project metrics), reasons
about alternative hypotheses, and atomically writes `analysis-response.json`.
Read [model-loop-analysis.md](model-loop-analysis.md) for this protocol.
The validator bounds execution but does not select scientific actions. Never
silently fall back to the legacy rule policy or present agent-mediated analysis
as a standalone background model service.

### Practical dependency-based task flow

For requested low-resource parameter preparation, checkpoint recovery or bounded
model memory/throughput checks, read [bounded-runtime-preparation.md](bounded-runtime-preparation.md).
Keep model changes and profiles in the project adapter; preparation never
activates a resource. Report process deadline enforcement and platform resource
release independently, and retain the current user's execution boundary.

Read [task-pipeline.md](task-pipeline.md) and use
`scripts/task_pipeline.py --task <project-task.json> --output <fresh-attempt-root>`.
Use `--plan` to inspect the dependency graph before execution. This is the primary
coordination flow for the current execution-and-learning objective, not a second
model-quality standard. Project drivers invoke installed Skills and normal
S12/S13 operations. Bind code, dataset/subset, configuration and actual runtime;
model score thresholds do not determine whether an execution receipt is kept.

Independent read-only tasks may run in parallel. Platform mutations and ledger
CAS writers remain serialized. For independent control/treatment jobs that may
overlap on separate platform allocations, read
[parallel-comparison.md](parallel-comparison.md). Use its project-owned
group plan and planning preflight; respect the project's active-task/GPU ceiling.
For detached LPAI execution and durable paired monitoring/recovery, read
[paired-platform.md](paired-platform.md). Verify native field mappings,
project preflight hooks and independent allocation release protection before live
submission; editing a Skill or passing CPU tests grants no training authority.
For actual native response/telemetry and independent release watcher checks, read
[native-lpai-qualification.md](native-lpai-qualification.md).
Overlapping remote jobs require a detached-job provider and confirmed group scope,
not concurrent writes in one task workspace. Baseline reproduction and reviews are recorded
without holding training; code changes require engineering checks, and an actual
input incompatibility stops the affected computation. Failed training still
reaches result aggregation, next-action planning and resource cleanup. Reuse
verified receipts within the same attempt; changed inputs create a new attempt.
Missing drivers stay visible as needs-driver, never as completed steps.

For task-scoped lightweight delivery, use the minimum-delivery table in
`references/task-pipeline.md`. Use the unified work-package workflow and report actual
`coverage` instead of a fixed step-count target. Reused code and unmeasured
optional checks are distinct from completed work. Uncertain effects block
automatic retry while failure accounting and resource cleanup continue.

For an installed project provider, use the continuous entrypoint
`scripts/run_project_pipeline.py --project <project.json> --output <fresh-run-root>`.
It creates the goal-selected task drivers, invokes the installed platform Skill, verifies real
training/evaluation S13s, runs the project driver's next-action node, and performs cleanup
before any bounded resource retry. For RoadModel, provider configuration carries
the native space/source/data locators and the authorized project SSH identity.
Read `resource-start-request.json` or `resource-stop-request.json` only when the
driver reports a native capability gap: handle the already-authorized platform UI
action, then let the driver verify the same UID and continue. Do not ask the user
again for routine start/save-stop. Keep UI handoffs visible in the final evidence.

For explicitly authorized branch-local diagnosis, use
`scripts/prepare_execution_continuation.py` to prepare `ml.diagnostic-execution`.
Repository, branch, exact commit, execution provider, budget and authorization
come from project inputs; the framework must not hardcode the validation project.
Reuse authenticated data receipts for the exact UID/version/subset, bind actual
preparation, then use normal handoff create/dispatch/ingest. Preserve failed and
deviating observations. This collection action does not mark numbered gates
passed, merge code, lock a baseline, approve readiness or certify scientific
support. Original formal gates and immutable histories remain unchanged.

Keep existing intent and architecture; a short task/change list is sufficient
working context for this action. Repeat checks when relevant inputs change.
Runtime still checks actual source, data, configuration, checkpoint and budget.
Input incompatibility or actual permission failure stops the affected operation.
Merely missing independent qualification or unresolved reproduction findings
does not prevent another authorized diagnostic observation.

Providers implement their native execution and unsigned result builders. A
provider gap is an integration error; never replace a governed result with prose.
Normal setup must install a changed binding before new requests are created.
Never rewrite old requests, installations or historical evidence.

