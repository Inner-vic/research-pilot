---
name: sdm-lifecycle
description: 'Use BMAD ML / SDM to start or continue ML research, onboard users, critique plans, submit and monitor training, analyze results and iterate toward a goal. Pro is optional; review never grants execution authority.'
---

# Specification-Driven ML Lifecycle

## Overview

Coordinate the native BMAD ML / SDM module across project training, evaluation,
reproduction and iterative experiments using configured execution backends.
Local CPU examples are verification tools, not a CPU-only product mode. LPAI
integration has partial real evidence and remains subject to route-specific
acceptance; do not infer platform qualification from the generic capability list.

Coordinate the user's project through the public entrypoint. Preserve existing
project state, accepted evidence and frozen runs. Load only the references needed
for the current route; internal checks are not a mandatory call checklist.

## On Activation

For "BMAD ML/SDM怎么用", first use [user onboarding](references/user-onboarding.md):
on activation proactively identify the project/active study, explain the next
action and ask only for missing intent. Resolve approved version/discovery
internally; user-supplied Skill paths are a temporary fallback, not the normal
experience. A usage question is teaching/read-only;
never start an example or platform task from the guide alone. For an already
concrete/authorized request, act on it and reuse known inputs instead of repeating
an onboarding interview. Users need not fill internal JSON or walk all nodes.

1. A request naming `sdm-native-proof-request/v1` exclusively uses
   [the fixed Native proof protocol](references/native-fixed-proof.md). Validate
   its fixed request, guard, nonce and exact module-owned command before execution;
   do not enter ordinary project or qualification routes.
2. Training, evaluation, baseline reproduction and result collection use
   `scripts/run_project_pipeline.py --project PROJECT --output RUN` with the
   applicable mode below. Resolve the installed Skill root and project configuration;
   start from the existing project workspace and inputs;
   do not force sequential completion of historical 21-step qualification.
3. Explicit formal qualification, lifecycle create/advance/validate/migrate and
   governed S12/S13 handoffs use [the governed protocol](references/governed-lifecycle.md).
   Its exact JSON completion contract applies only to that route. Validation is
   read-only; never repair a ledger while validating it.

## Source control and CI

For GitLab source/MR/CI or Jenkins build integration, read [SCM and CI](references/scm-ci.md).
Use the bundled adapter and existing coordinator transactions. Discover project
configuration, verify current identity, preserve original queue/build/pipeline
identity on recovery, and expose missing credentials or instance configuration.
Engineering CI success never substitutes for ML quality or governed S13.

When defining success or reviewing class/scenario tradeoffs, use the
[evaluation contract](references/evaluation-contract.md). Preserve a primary
objective while reviewing supporting metrics; identify unsupported producers
before claiming a new task type.

## Current execution contract

For the current LPAI research task, the installed OFFICIAL `lpai` Skill is
required. Resolve and read it, use its actual command scope and preserve genuine
user authorization. The custom `sdm-lpai` compatibility Skill/installer has been
retired; do not install it as a substitute. Bind `platform_requirements` to the installed
official Skill and retain that binding in successor plans; inspect it through
`--inspect-required-skills`. Missing required capability blocks new execution,
never switches platform or runs training locally. Existing runs keep their
original controller/Guard for observation and cleanup. See
[required LPAI and continuous research](references/required-lpai-research.md).
When the user explicitly selects the official Python SDK, qualify that installed
SDK with its normal credentials and exact production resource identity, then
freeze the selected SDK route in the new research configuration. Record this
explicit instruction alongside the required official Skill. Do not reinterpret
an existing unknown CLI request as permission to submit it through the SDK.
Keep that request and its Guard; use a separately authorized scope for new work.
See [SDK submission and recovery](references/lpai-manager.md).
For long component queue/training waits and result-triggered native Codex
continuation, read [component pipeline](references/component-pipeline.md).
Use its new frozen durable continuation policy for new studies. Verify terminal
discovery, artifact collection and the original analysis job separately; a poorer
next metric is evidence for iteration, not a reason to abandon an unfinished goal.
For a fresh authorized study, prepare through `scripts/research_initialize.py
--spec STUDY --destination NEW_MODULE --store NEW_STORE --workspace NEW_WORKSPACE
--output NEW_CONFIG`, then start its frozen supervisor. This planning
entry pins data/split, target, baseline and original delegation; an explicit
`dataset_input` prepares one checksum-verified official data asset, with a
separate write journal and no overwrite/share. Training remains remote. The resident
submits the first task as well as successors. Audit a real continuous chain with
`scripts/research_chain.py --config CONFIG`. Reproduction evidence, incoming
tasks and adaptive successor evidence are reported separately; none imposes a
trial-count stopping rule. IterLab is not a prerequisite for this core route.
For a new managed host, launch the frozen `scripts/research_supervisor.py
--config CONFIG`. It owns a single resident and recovers only proved durable
boundaries under the same config/Guard/Context. Inspect recovery with
`--inspect-recovery`; unknown effects never become retry permission. Public
service `--control overview` projects read-only frontend state. Workload v2
declares optional training-loss hooks and allowed seeds while retaining protected
validation. See [integrated host and workload operation](references/component-pipeline.md#integrated-host-and-workload-operation).
Read the coordinator's automatically refreshed `component-pipeline/PROGRESS.md`
or `progress.json`, including `needs_intervention`, actual best/latest results
and task identity; case-level hand-written summaries are dated snapshots.
For long histories use the versioned byte-bounded registered evidence contract
in [component pipeline](references/component-pipeline.md#byte-bounded-registered-evidence-v2):
request exact decision-relevant JSON fields, disclose omitted content and follow
actual cursors. Line count alone is not an input byte bound. Keep old frozen
receipts and the unchanged model input ceiling.
Critic history deduplication preserves original accepted receipts and restores
all prior challenges for validation; it never raises the input budget or replays
a completed model call. See [component pipeline](references/component-pipeline.md).
For goal-directed component research, use a new frozen pipeline v4. Keep the
verified incumbent separately from the latest task; require an explicit choice
of continue, rollback, redesign or stop and bind the selected actual recipe.
Use the user's actual metric target and termination scope for exits. For
continuous-until-target research, use decision policy v2 without a trial cap;
do not invent a submission budget. Only an explicitly bounded scope uses v1.
round-count qualification is not scientific success. See
[candidate decisions](references/research-strategy.md#best-candidate-is-distinct-from-the-latest-result).
For a new resident, use `scripts/research_runtime.py --destination NEW_MODULE`
to freeze its source and all review resources together. To adopt an existing
known task into a new frozen host, use its public `--config OLD_CONFIG
--destination NEW_MODULE --new-root NEW_STORE --output NEW_CONFIG` route;
do not assemble the cold handoff by hand. See the preparation, readiness and
closure options in [component pipeline](references/component-pipeline.md).
Pipeline v3 adds a
verified result analysis and structured delegated training limits to the
original proposal/implementation/submission path. A terminal task must drive
that path without a frontend message or another user confirmation within the
existing delegation. Unknown calls still reconcile their original identity.

For completion-triggered independent Codex tasks and frontend progress, use the
opt-in [event-driven execution integration](references/event-driven-execution.md).
It starts programmatic SDK tasks from durable results, without chat timers or
synthetic user messages. Preserve original coordinator/Guard ownership and its
route-specific qualification; this integration adds no mandatory workflow nodes.

RoadModel generators and the catalog default to `legacy`. Explicit `exploration`
revision2 derives from the original graph, retaining every operational driver and
conditional policy while grouping four pairs of record-only checks. Original
validators and all declared producer kinds still execute, with durable partial
acceptance and per-section receipts. This repairs the withdrawn rc3 design; local
contract evidence does not establish real-platform qualification. See the
[exploration workflow](references/exploration-workflow.md) for explicit profile use.
Reduce repeated presentation/material work without dropping actual data/model/
configuration checks, declared producers or explicit isolation.

Keep code review, reproduction and robustness conditional on real requirements.
Develop missing capabilities exposed by the actual task. Node count is not a
development goal or an audit prerequisite for every change; reconsider awkward
steps after observing real usage. Retain required operational checks and verify
the behavior affected by a change.

Within a research plan, continue its main line by default: selected plan, actual
observations, assessment and justified revision. Keep alternative explanations in
the current proposal; select the next informative action rather than pre-creating
many execution branches. Engineering checks, retries and plan revisions stay with
their plan. A separately scoped branch needs a decision-changing trigger and a
return/switch/stop condition. Read [research-strategy.md](references/research-strategy.md).
Critic dialogue, including Pro when selected, should sharpen the plan and resolve
decisive objections; more dialogue does not imply more trial chains.

Resource consent, exact submission, preflight, monitoring and cleanup remain
concrete operations. Driver wire revision3 stays compatible. Keep frozen tasks
on their original graph; do not migrate an owned allocation
midflight. Formal 21-step qualification is an explicitly requested route, not an
exploration checklist. `workflow_definition.py` and `exploration_workflow.py` own
actual scheduling; omitted nodes are not reported as completed.

- A **project driver** performs a bound operation; a **record gate** validates
  project-owned evidence; a **cleanup service** reconciles the exact allocation.
  A record gate does not produce its own judgment. Binding, execution, acceptance
  and business quality are separate facts.
- Inspect the existing task's trigger matrix, or use `scripts/task_pipeline.py
  --task TASK --output RUN --inspect-triggers` for read-only inspection. PROJECT
  configuration is not TASK input. When enabling a driver use the project entry's
  `--inspect-driver`. Missing required drivers block before acquisition; declarations
  require the existing behavior handshake. See
  [trigger-execution.md](references/trigger-execution.md) and
  [execution-contracts.md](references/execution-contracts.md).
- Resume the same unchanged attempt after a wait or read failure. Changed inputs
  require a linked fresh attempt; unknown external writes require reconciliation
  before retry. Never edit a frozen runtime to retrofit a fix.
- Windows background launches use the default windowless subprocess policy.
  Use `pythonw` for a persistent outer entry, retain stdout/stderr in local files,
  and validate configured executable paths before deployment. A missing Codex
  binary after an app update is a launcher/configuration failure, not a training
  failure; refresh a versioned deployment from the verified current installation,
  preserving task UID, event store, authorization and the old frozen version.
- `code_review_required:true` requires code changes and blocks acquisition on missing
  or failed code review. Otherwise retain the existing optional review policy.
  Cleanup failure permits diagnosis/review, but blocks completed archive and successor
  execution until same-resource reconciliation closes. Never delay a warranted stop.

## Intent routing

For initial plans and result-driven successors, use the common research contract
in [research-strategy.md](references/research-strategy.md). Select the action that
advances the user's objective or resolves a decision-relevant uncertainty, with
evidence, alternatives and outcome-dependent choices. A failed platform status
does not erase valid partial results; recovery readiness is not a prerequisite
for proposing every kind of successor. Do not automatically prescribe more
training, lower LR or diagnosis when the existing evidence already warrants a
direct fix. Project requirements, not this Skill, set meaningful gains and limits.

For continuous research until a project goal, configure `research_iteration`
with the actual objective and declared metric targets in the new frozen profile.
For a released result blocked before ModelRunner launch by input size, a new
frozen producer may use `--recover-result-review MANIFEST`. Its bound manifest
proves terminal collection/stop, unchanged project and known unlaunched calls.
This route only publishes compatible review receipts; the original frozen
coordinator still validates and consumes them. Never use it to retry an unknown
call, restart computation or silently change an active runtime.
The result review must assess intervention scale through
`next_step.research_direction`; closing or exhausting a Study is not project
success. The original worker consumes/archives the result, then automatically
uses the existing A/B gate for a required plan revision. On
`agent_continuation_required:true`, the execution Agent continues implementation
or diagnosis, freezes and reviews the new execution profile, dispatches through
the original resource Guard and repeats result assessment. A plan document is an
intermediate handoff, never a reason to end the research while its goal is unmet.
Preserve the original Base and rejected routes. Use the existing strategy guide's
[continuous research contract](references/research-strategy.md#continuous-goal-level-iteration).

For a new experimental project, register live research observation before its
first execution using [research-observation.md](references/research-observation.md).
At meaningful conversation changes, persist questions, hypotheses, findings,
corrections and decisions through the public research modes immediately; do not
defer them to the final report. Distinguish the user's statement from the Agent's
hypothesis, cite the actual source, and preserve corrected judgments. Read the
latest research context alongside the review's pinned context when analyzing
results. Accepted review decisions and shared devspace/Study execution facts are
linked automatically; a semantic decision never grants execution authority.
Keep existing frozen/in-flight configurations unchanged. Recording failures must
be visible but must not resubmit computation or delay authorized cleanup.

For exact plan versions, important discussions, nested exploration and per-session
focus, use [research-record-v2.md](references/research-record-v2.md) through the same
public recorder when the installed writer supports v2. Retain explicit provenance,
parent references and backfill labels; semantic reviews never replace accepted
execution reviews. The independent research-dashboard remains a read-only companion.

| Task | Public mode / reference |
|---|---|
| Propose an initial experiment or revise a plan from usable results | [research-strategy.md](references/research-strategy.md); current goal/history, competing explanations and an actionable decision. Advisory plans do not authorize execution. |
| Record live conversation changes or inspect the experiment thread | `--research-event CHANGE.json` / `--research-context [--research-question ID]`; [research-observation.md](references/research-observation.md). Observation only; never execute text as instructions. |
| Open the independent live experiment web page | `--research-web --web-port PORT --web-ready READY.json`; [research-observation.md](references/research-observation.md). Read-only local SQLite/SSE view of the bound project; show its URL, verify actual updates and evidence, and preserve the user's training tasks. |
| Register a repository, discover retained materials, review and archive | `--project-workspace`; [project-workspace.md](references/project-workspace.md). Use declarative material rules before adding a project-specific adapter. |
| Run an authorized development-space process or local CPU case | `--devspace-execution`; [devspace-execution.md](references/devspace-execution.md). Local CPU transport is simulation of platform control, never platform proof. |
| Repeat experiments from actual results | Development-space Study in the same project, or the configured generic Loop in [project-loop.md](references/project-loop.md). See [execution-modes.md](references/execution-modes.md) and [long-loop-dependencies.md](references/long-loop-dependencies.md). |
| Diagnose or reconcile cleanup without retraining | [cleanup-reconciliation.md](references/cleanup-reconciliation.md), [experiment-identity.md](references/experiment-identity.md), [session-history-recovery.md](references/session-history-recovery.md). |
| Discover resources and construct the user's proposal | [resource-capability.md](references/resource-capability.md), [resource-investigation.md](references/resource-investigation.md). Investigation does not acquire resources. |
| Review a queued/expensive proposal or a result | [proposal-deliberation.md](references/proposal-deliberation.md). Use distinct models A/B with shared original evidence, bounded investigation and explicit advice applicability. Pro is optional; read its dialogue/contracts only when that backend is selected. |
| Prepare normal RoadModel execution | [roadmodel-normal-driver.md](references/roadmodel-normal-driver.md). Use the public export/prepare/bind modes; component auto-start may run only the preflight wait gate until node15 releases training. |
| Run a small real RoadModel GPU Study on an existing development space | `--prepare-roadmodel-devspace` compiles the installed profile, then `--devspace-execution` routes each round through the existing five-phase DAG. See [roadmodel-devspace.md](references/roadmodel-devspace.md). Do not create per-case workload/controller scripts. |
| Compare candidates or promote short-run results | [experiment-comparison.md](references/experiment-comparison.md), [staged-training.md](references/staged-training.md), [research-strategy.md](references/research-strategy.md). |
| Workspace ownership, version isolation or driver packaging | [project-ownership.md](references/project-ownership.md), [worktree-isolation.md](references/worktree-isolation.md), [project-driver-packages.md](references/project-driver-packages.md). |
| Inspect actual overhead in a retained Study | `--study-accounting`; [devspace-execution.md](references/devspace-execution.md). Unknown human effort and missing events stay unknown; speedup requires a matched baseline. |

## Mandatory boundaries

For external capabilities, inspect the official SDK/CLI first. Use browser or
Computer Use for missing operations; custom integration needs explicit identity,
input/output and readback contracts. See
[external-capability-routing.md](references/external-capability-routing.md) and
[external-operations.md](references/external-operations.md).

Restore known resource facts, query only material gaps, and guide the user through
remaining choices. Before acquisition present the resolved queue, configuration,
cost and stop scope and obtain genuine user authorization. Reuse it within its
actual scope; do not invent time/budget limits or ask for the same approval again.
Component and development-space availability are separate. No generic Feishu
request/card is part of authorization; genuine provider restrictions need evidence.
See [training-operations.md](references/training-operations.md).

Execution termination, evidence completeness, model quality, official resource stop
and billing reconciliation must be reported separately. A container running is
not optimizer progress; exit0 is not scientific success; Pod deletion is not billing
proof. Optional workload-side protection follows
[devspace-health.md](references/devspace-health.md). Preserve healthy/exception
evidence, do not treat one NaN as sustained divergence, and never depend on an AI
notification for immediate safety termination.

## Events and continuation

Only deploy resident collection and continuation for the exact user-authorized binding.
Installing this Skill does not start daemons or restore old monitors. See
[task-event-collector.md](references/task-event-collector.md).

New projects may select `research_worker` for independent observation and A/B
result decisions through `--devspace-execution --research-worker`. It uses the
shared executor and actual read-only Codex calls; chat delivery/read receipts do
not gate continuation. Public execution validates the bound approved response,
original sources and control revision before coordinator entry; a visible response
file alone is insufficient. New frozen attempts may explicitly select worker v4:
durable approval, exact model-call receipts and shared-executor acceptance allow
completed work to be reconciled without repeating model calls or computation.
Unknown outcomes remain held. Keep older attempts frozen; select a new version
only in a new attempt. New v5 attempts may explicitly select the ModelRunner
result-review pilot with purpose schemas and the same bound raw evidence for A/B.
Do not configure chat wakeup alongside it. Read
[the continuation contract](references/task-event-consumer.md) for setup and
verified limits. Missing proposal context, a selected Pro transport, uncertain
model calls and unqualified native UI operations remain explicit waits. In
particular, background handoff on native-UI allocations is blocked until an
independent lifecycle transport is qualified; retain the active Agent for those
operations. This is not yet unattended native-space qualification.

For frozen queue-v1 runs, on an event wakeup read the bound inbox before commentary and follow
[task-event-consumer.md](references/task-event-consumer.md). `silent-duplicate`
ends quietly. Queue acceptance, consumer receipt and user delivery are distinct;
acknowledge only actually delivered IDs with the exact receipt contract.

For supported event-driven Studies, record start, use `--background-devspace`, verify
observer handoff, then end the turn promptly. At the actual terminal wakeup:
inspect result/error, decide whether to terminate, produce a grounded assessment
and continue authorized work. Briefly show result, basis, change and the next
round's actual parameters/result. A next-step suggestion is not a completed
iteration. Failures without final metrics remain diagnostic-only; do not invent
metrics or restart an unknown attempt. Keep logs and receipts in project archives.

## Completion contract

Return the public command's actual structured status and explain the result or
waiting condition briefly. For governed qualification, preserve the exact
specctl JSON completion contract. State what ran, what evidence was accepted,
which platform claims remain unverified, and the authorized next action.

First align human and Agent on the task, then align the selected reviewer with
background, accepted plan and relevant evidence. Before new expensive submissions,
use distinct proposer/critic roles, challenge/revise/reassess the exact proposal,
and close critical objections. New RoadModel profiles require this generic
condition-driven dialogue, without a fixed minimum or maximum turn count. Read
[proposal-deliberation.md](references/proposal-deliberation.md) for the public entry,
evidence/proposal bindings and restart contract. A useful negative result can
justify a task; repeated unsupported exploration cannot.

Use distinct models A/B to challenge an expensive proposal before submission,
revise decisive weaknesses and reassess the exact final version. Record actual
model IDs and call sources; role labels or declared metadata alone do not prove
that independent model calls happened. Pro is one optional backend, not a
prerequisite even when a binding is saved. New v3 policies explicitly select
`model` or `pro`; v3 exits on approval/rejection or pauses for evidence/no progress.
An explicitly selected backend cannot silently change within
an attempt. If the second model is unavailable, report that limitation rather
than calling a single-model self-review dual-model approval. Frozen v1 attempts
keep their accepted policy; changed selection needs a linked fresh attempt.
No review recommendation grants execution authority. Never delay an existing
task's monitoring, reconciliation or cleanup for a fresh proposal dialogue.
Use [node-acceptance-boundaries.md](references/node-acceptance-boundaries.md) for
protocol versus applicability, evidence consistency versus conclusion quality,
and recommendation versus chosen decision. One work package may share a material
and dialogue; its distinct acceptance checks still cannot substitute for one another.

Keep project results in the model project's workspace; reusable Skill changes
belong here. Report real execution, retained replay, CPU simulation and unverified
capabilities explicitly. Deliver accepted results, material limitations,
what remains waiting, and an actionable next step; never claim an unrun node passed.


## References

- [five-phase-orchestration.md](references/five-phase-orchestration.md) owns the
  current11-work-package responsibilities and detailed phase/dependency diagrams.
- [governed-lifecycle.md](references/governed-lifecycle.md) owns explicit21-step
  qualification; it does not replace ordinary project routing.
- [execution-modes.md](references/execution-modes.md) covers conditional research,
  review and diagnostic modes. Load it only when that mode applies.


For user pause/resume and Web progress controls, read
[research dashboard controls](references/research-dashboard.md#checked-resume).
A resume continues the same authorized scope; changes to goals, plans or resources
are new steering decisions. Reconcile original completed model receipts before any
new call. Unknown calls never become retries because the user clicked Continue.
Use Chinese human prose in the original research calls; retain schemas, enum values,
raw facts and code verbatim. No separate translation model/daemon is needed.


Web continue now uses the same checked control and core host-restore broker. For a
stopped host read the restore contract in research-dashboard.md; never tell users
they must copy a chat instruction for a supported component-v4 study. Keep recovery
and new scientific execution separate: only an explicit Continue authorizes opening
the pause gates, and it cannot expand the existing research delegation.
