# Five phases and task-scoped delivery

Read when opening, resuming or reporting an ordinary ML task. Select purpose,
execution scope and evidence needs before invoking existing project/task drivers.
The five phases organize work and reporting. New ordinary tasks use the
[v2 execution graph](five-phase-orchestration.md), including explicit dependency
nodes and evidence gates. Existing attempts import verified observations into the
current workflow with legacy check identities; ordinary recovery does not select the old21-node route.
The formal qualification protocol remains separate from this task graph.

## Organize work through five phases

| Phase | Minimum useful output | Exit and continuation | Current task-node mapping |
| --- | --- | --- | --- |
| 问题定义与需求梳理 | Agreed question, metrics, scope and data basis. | Human–agent alignment. | 1, 2, 3, 22 |
| 方案设计与实验规划 | Model/baseline selection, hypothesis, protocol, method checks and reviewed plan. | Resolve material plan questions. | 4, 6, 24, 25, 34 |
| 实验开发与训练验证 | Implementation, engineering checks, runtime checks, baseline reproduction and training observations. | Preserve actual model/process evidence. | 9, 10, 11, 12, 7, 13, 5, 15 |
| 结果评估与评审 | Metrics, error/specialty analysis, evidence and conclusion quality. | Grounded results after appropriate review. | 16, 17, 18, 19, 32 |
| 结论输出与迭代决策 | Conclusion, grounded decision, delivery and continuation. | No implicit successor execution. | 20, 33, 21 |

R.1–R.5 (26, 27, 28, 14, 30), W.1–W.2 (23, 8), and V.1–V.2
(29, 31) are independently owned capability steps, not phase members. Use
`node_registry.py` for current display identities and report capability coverage
separately from business work packages. The34 internal IDs are not34 peer steps. Workflow revision1 uses11 work packages; driver wire revision3
reconnects actual acquisition before runtime checks and complete plan context.

Independent preparations can overlap; the real dependencies and current provider
contract determine order. Evidence requests can return to planning/collection.
Resume from the affected phase, retaining completed evidence elsewhere. Report
phase status, current blocker, owned-resource state and the next concrete action;
use existing attempt summaries/journals, cleanup and review records as sources.
Do not synthesize successful receipts from this reporting view. Phase labels do
not alter CLI status values or the numbered ledger.

The legacy34-check mapping is a compatibility detail, not an immutable product
requirement or a target completion count. First choose required/reused/optional
work. Repeated or low-value nodes may be consolidated through an explicit,
versioned framework change with affected contracts/drivers/tests and migrations
where needed. Do not silently renumber old evidence or relabel an unmeasured
check as passed. An explicitly requested governed qualification continues to use
its pinned engine and protocol until a valid migration.

## Select purpose independently of experiment size

Record a task mode and its reason in project-owned planning/context. These are
agent-facing categories, not new values for the runner's `objective`:

| Mode | Primary question and delivery | Check selection |
| --- | --- | --- |
| Workflow diagnostic | Does a specific route, timeout, collector, recovery or review interaction work? | Prefer isolated CPU checks for local defects; use a bounded actual platform probe only for external facts and within confirmed scope. Report the exact tested/untested boundary. |
| Exploration | Which explanation or change merits further investigation? | Preserve valid comparison and failure evidence. Select a scale capable of answering the question; distinguish screening from demonstrated project-wide gains. |
| Full experiment | What does the candidate achieve under the project's normal protocol? | Use the declared full data/training/evaluation settings and outcome requirements. A full training run alone does not establish deployment readiness. |
| Candidate confirmation | Is a chosen candidate reliable enough for the stated promotion/release claim? | Select repeats, held-out/full-scope evaluation, important slices, robustness and deployment checks from that claim and project policy. Budget missing checks explicitly; do not apply every production checklist to every exploratory trial. |

Purpose, scale and research mode are separate choices. Exploration can need full
training; a workflow diagnostic can be CPU-only. Available/affordable GPUs inform
the proposed profile, not the reusable mode's scientific target. Resource shortage
does not silently convert a requested full Base into a small equivalent. Retain
what the smaller experiment can and cannot establish.

## Apply lightweight delivery using existing controls

Use `execute-and-learn`, `evaluate-only` or `baseline-reproduction` only when the
operation is requested and the project driver supports it. Preparation-only work
uses analysis/preparation tools; never run an execution objective to manufacture
a planning report. Convert the chosen scope to supported, resolved project config.

| Work | Reuse or omit when | Require new work when |
| --- | --- | --- |
| Planning/background documents | Existing goal/background remains valid; retain a concise delta and evidence links. | A changed goal, hypothesis, constraint or comparison needs a revised concrete plan. |
| Data/runtime checks | Exact identities and relevant conditions are unchanged; cite trustworthy retained results. | Input/runtime changes or a material failure affect those checks. Live availability and current authority always need fresh preflight. |
| Baseline reproduction | A trustworthy applicable reference exists; retain its version and selection basis. | Reproduction itself is requested, no applicable result exists, or the comparison needs a new reference. A changed evaluator may need baseline re-evaluation rather than re-training. |
| Implementation | Existing code is reused; configure `code_change: false`. | Real code changes: configure `code_change: true`, perform the implementation and affected engineering checks. |
| Robustness | It is unrelated to the exploratory question/current claim and not required by the project. Record unmeasured. | The task, known failure, promotion claim or deployment constraint needs specified robustness evidence. Configure `robustness_required: true` and a supported driver when required. |
| Reviews and reporting | One retained summary/Pro assessment covers the needed findings; reuse background and avoid duplicate documents. | A changed source/evidence or specifically required review needs fresh assessment. Separate merge/release authority remains applicable. |

`baseline_required: false` and `robustness_required: false` make these checks
nonmandatory, not measured. In a direct task config, omit their drivers when not
running them. A configured provider selects required nodes plus explicit
`additional_task_nodes`; do not add optional nodes 5/17 merely to fill a count.
Node 19 (4.4 evidence consistency) is required by the current graph and owns its
separate acceptance record; it is not an optional check.
Mark a driver read-only only when its actual behavior is read-only. Missing
required drivers stay needs-driver; optional omissions remain unmeasured.

For a required confirmation check unsupported by the current adapter, retain the
gap and prepare adapter work; never claim coverage by changing the required flag.
Source/config/data binding, current authority/budget, owned-resource stop/recovery,
authentic results and honest analysis remain part of actual execution. Report
completion of the requested scope separately from model quality and optional work.

## Capability placement and incremental development

Keep general routing, recovery, evidence and review contracts in BMAD; model
parameters, metrics, resource defaults and domain adapters in the project.
Read only the relevant capability for the current phase:

| Capability | Phase and current route |
| --- | --- |
| Task graph and minimum delivery | Planning/execution: [task-pipeline.md](task-pipeline.md). |
| Durable recovery and bounded waits | All phases: [execution-recovery.md](execution-recovery.md). |
| Resource proposal and confirmation | Planning/execution: [training-operations.md](training-operations.md). |
| Studies, hypotheses and comparisons | Planning/review: [research-strategy.md](research-strategy.md). |
| Short-to-long training decisions | Planning/review: [staged-training.md](staged-training.md); matched progress, schedule/recovery semantics and evidence-based extension. |
| Paired result records | Evidence/review: [experiment-comparison.md](experiment-comparison.md); byte-bound exports with missing/failed/incomparable outcomes. |
| Independent source worktrees | Planning/execution: [worktree-isolation.md](worktree-isolation.md); owned create/freeze/validate/retire/restore, independent outputs. |
| Fixed Pro context and multi-turn evidence | Review/decision: [pro-review-dialogue.md](pro-review-dialogue.md). |
| Paired scheduling and budget checks | Planning/execution: [parallel-comparison.md](parallel-comparison.md); actual overlap requires a capable project provider and confirmed group scope. |
| Detached LPAI paired execution | Execution/monitoring: [paired-platform.md](paired-platform.md); durable UID reconciliation, total spend, deadlines and verified release. |
| Methodology provenance and applicability | Relevant phases: [methodology-reference.md](methodology-reference.md). |

Develop foundations first: phase/mode selection, meaningful minimum delivery,
recovery and evidence. Comparison records, independent source workspaces and the
optional LPAI paired scheduler have CPU-tested implementations. Native mappings,
project hooks and independent release protection still need project-specific
live verification before dispatch. Multi-agent
analysis is a separate choice, never implied by worktrees or a paired run.
Evaluate each addition against an actual scenario: manual interventions, elapsed
wait, repeated compute, resume correctness and maintenance effort. Keep an
explicit implementation/validation boundary instead of accumulating untested
features in the default path.
