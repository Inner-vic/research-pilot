# Training operations and resource confirmation

Read this for actual training, evaluation using paid compute, or a bounded model
probe. Apply it through the existing task/Loop and project providers. It preserves
the unified five-phase work-package workflow and task-scoped lightweight delivery; it does not
add a second qualification workflow or make every experiment a full-data run.

For complete CPU/RAM/GPU requests, queue/workspace headroom, per-node placement,
configuration suitability and bounded queue diagnosis, read
[resource-feasibility.md](resource-feasibility.md). Record unavailable evidence
explicitly; an aggregate free-card count is insufficient to claim immediate fit.

## Project inputs and confirmation

1. Restore the authorized project, owned branch, source/configuration identities,
   current execution scope and previous decisions. Collect training requirements
   from the user and the project's versioned defaults. Resolve conflicting prompt,
   README and component settings explicitly; a live component is not automatically
   authoritative. Record the source of each selected default.
2. Ask only for missing choices that affect execution. If the user has no
   preference, propose the compatible project defaults. If no project default
   exists, prepare a conservative candidate from verified runtime requirements,
   available allocation granularity and actual quotes, and explain the basis.
   Do not import another project's GPU type, card count or budget.
3. Make one concrete reviewable proposal before confirmation: purpose, exact
   source/image/data/config, allocation across all roles, training parameters,
   concurrency, time and cost bounds, monitoring/stop/recovery and output paths.
   Use [resource-confirmation.template.toml](resource-confirmation.template.toml)
   as a collection aid. Empty/zero fields mean unresolved; this is not a provider
   configuration and must never be dispatched. Convert a completed proposal to
   the existing provider's supported configuration and retain its digest.
4. Obtain the user's confirmation before starting new paid compute or training.
   Preserve the actual user message/reference, scope and resolved configuration
   digest in project-owned state. A proposal, a default choice, elapsed time or
   Pro's plan is not approval. Existing authorization remains reusable for the
   same resolved configuration within its approved duration, cost, purpose and
   retry scope; do not repeatedly ask for routine steps within that scope.
   Changed GPU allocation, budget, data, model scope or a successor outside those
   bounds requires a new proposal/confirmation. Preparation remains available.
5. Recheck platform availability, running/pending owned tasks, data/image access
   and input compatibility immediately before dispatch. A quote is not a card
   reservation. Missing availability or an expired budget stops dispatch; never
   silently substitute a more expensive tier. Keep platform approvals distinct
   from user confirmation. A returned request/pending approval is not execution.

Attribute confirmation responses to the entrypoint that returned them. An Agent
CLI `pending_approval` response does not establish that the platform's ordinary
web workflow requires approval or that a notification was sent or delivered.
Preserve the response and check actual owned task/space state. Do not claim a
notification exists from a `recipients` field alone, repeatedly ask the user to
find it, or globally block unrelated authorized work from an unverified tool
request. Inspect the documented native workflow under the same explicit scope;
retain its own confirmation, identity and cleanup evidence. Never switch routes
to evade an actual required approval, weaken access controls or duplicate a
submission whose execution outcome is unknown.

For an already identified development space, an explicit user instruction to use
the normal browser start/stop workflow is not a request to keep retrying the Agent
gateway or to search repeatedly for an undelivered card. Inspect and operate the
normal authenticated UI within the confirmed resource/budget scope. If that UI
itself requires approval, honor it. Record the user's route instruction, exact
space UID, pre-state, UI outcome and official readback; never claim an earlier
gateway request was cancelled without evidence. Keep that unresolved response in
the audit record. This existing-resource lifecycle route must not be generalized
to duplicate component creation or new training submissions. Confirm the native
stop route before leaving paid compute unattended.

This confirmation is an outer-agent responsibility. In the
[v2 execution graph](five-phase-orchestration.md), nodes 26–28 and 34 validate the
project-owned inventory, proposal, review and human-message confirmation before
resource-capable drivers. This is not cryptographic human authentication and does
not replace S12/S13, execution lease or cleanup checks. Historical observations
continue through the current workflow with stable internal check IDs; they do not authorize new acquisition.

The generic confirmation route is the current human conversation, not a request
sent to Feishu. Do not ask the user to message Feishu or send an approval request
on their behalf. A gateway pending-approval response is entrypoint-specific;
preserve it, reconcile actual task identity and report a genuine provider
restriction without manufacturing or bypassing an organizational approval.

Bind the proposal to the actual provider kind (development space, component task
or another supported runner), queue and package. A GPU package offered by the
development-space catalog does not establish that the component catalog can
submit it, even within the same queue. Verify that catalog and the selected
provider's start, monitoring and release path; do not adapt a task payload merely
by replacing its queue name. Separate shared package capacity from the program's
device limit and from observed GPU UUID/free memory. Values such as `unlimited`
or `gpu: -1` describe a shared dialect, not an exclusive card allocation.
Default idle culling is not a short experiment deadline. Record its actual
window and retain an independent bounded stop path for the paid allocation.

For parallel control/treatment training, read
[parallel-comparison.md](parallel-comparison.md). Confirm the aggregate group
allocation and budget as well as each arm; a previously approved single-task
lease cannot authorize simultaneous jobs. Release each terminal arm independently
and reconcile all owned resources before a replacement or Pro wait.

For the detached LPAI adapter, use [paired-platform.md](paired-platform.md).
Its group budget includes pending/startup/idle/cleanup, and runtime protection
must release the allocation independently of the coordinator. The informational
expected-duration field or a model process timer does not prove platform release.

## Reusable training requirements

Select training length through [staged-training.md](staged-training.md) when
screening candidates or proposing a longer run. Resolve actual-update budget,
scheduler horizon versus early stop, checkpoint/restore semantics and fair controls
in the concrete resource proposal. Reaching a short-run limit does not authorize
extension, and pipeline success does not establish a scientific improvement.

| Requirement | Minimum operational behavior | Values owned by the project |
| --- | --- | --- |
| Every experiment is traceable | Before a successor, retain hypothesis/change, exact source commit or sealed dirty-source digest, resolved config and dataset/split version, actual component/task UID and allocation, result/failure, analysis and artifact links. Record missing evidence honestly. | Metric names, thresholds, visual scenes and record layout. |
| Manage alternative approaches | Prefer configuration switches for compatible alternatives. Use an owned separate branch for a materially different implementation; preserve the baseline and record parent identities. Proposals outside allowed model paths require the user's approval. | Allowed model paths, branch naming and implementation constraints. |
| Prioritize within resource bounds | Queue candidates by expected information/value and cost. Check active/pending tasks; count master/worker instances and every GPU package. Release the current allocation before waiting for Pro or acquiring a successor. No automatic expansion to the ceiling. | Maximum GPUs, active tasks, accepted GPU types and candidate budgets. |
| Monitor and stop deliberately | Define metric validity, divergence checks, polling interval, hard deadline and cost bound before starting. Nonfinite loss/gradient, fatal/OOM errors, no optimizer updates, stalled logs and a sudden metric collapse require the project's declared handling. Stop conditions must reach the actual owned platform task and be verified. | Exact thresholds, patience, exemptions, cadence and stop mechanism. |
| Preserve failure evidence | Preserve logs/config, the last valid checkpoint and failure-period artifacts where available. Capture checkpoint identities, optimizer/scaler status and supported visualization evidence. State unavailable checkpoints/visuals and why; never rerun solely to fabricate missing failure evidence. | Checkpoint schedule, retention destination, recovery semantics and visualization protocol. |
| Keep comparisons meaningful | Pin the intended dataset version and split/subset. A data or protocol change is an explicit new attempt/deviation. Before resizing GPUs resolve per-device batch, accumulation, effective batch and optimizer/scheduler clocks; compare actual healthy updates, throughput and cost. | Dataset identities, full/small profiles, evaluation protocol and duration. |

Numerical and metric stop rules need different treatment. A nonfinite numerical
signal invalidates that computation; a one-off zero IoU can instead reflect an
empty-support class, malformed evaluation or wrong checkpoint. Preserve class
support and evaluation context, apply the declared pause/stop rule and investigate
before calling it persistent divergence. Finite loss alone is insufficient when
all optimizer steps were skipped. A process exit does not establish platform
release; retain the same-UID stopped readback separately.

For a failed run, prepare/diagnose and release resources through existing routes;
do not blindly retry, start a second job while cleanup is unresolved, or promote
emergency weights to a resumable checkpoint. Use
[bounded-runtime-preparation.md](bounded-runtime-preparation.md) for the actual
deadline/recovery implementation and [round-records.md](round-records.md) for
retention/export. Scientific decisions follow the explicitly selected reviewer:
fixed Pro conversation or the active coding agent in local-agent mode. Preserve
the same evidence/quality requirements and applicable BMAD decision validator;
neither mode grants execution.

In progress reports, separate queue wait, platform/container startup, project
preflight, extension preparation, model updates and evaluation. Long wall time
can contain zero model steps. For startup failures, report the exact named
check/exception when captured, otherwise state that the cause remains unknown.
Follow the observable-preflight section of bounded runtime preparation to fix
diagnostic gaps before another paid run; adding diagnostics does not authorize it.

For an unknown learning defect, choose a test that separates its likely causes
before paying for a longer run. A tiny representative input can check label/shape/
mask/unit consistency and one forward/backward/actual parameter update. If those
are valid yet learning remains doubtful, a bounded tiny-data memorization study
can distinguish some pipeline/optimization faults from population generalization;
consider augmentation, regularization and ambiguous labels when interpreting it.
Failure to memorize is a clue, not proof of insufficient capacity, and success
does not establish generalization. Do not require monotonically decreasing loss
on every stochastic step or golden-score equality from a partially trained model.

Use fast local contract tests for local defects and a bounded real integration
probe for the changed platform path. Select affected evidence export, restore,
review and stop checks so downstream failures are visible. Development-space
success cannot certify a component task's submission/mount/start/release path.
Keep longer representative integration checks for the claims that need them;
do not force the entire production rubric into every short exploratory run.

A small workflow diagnostic needs the minimum traceable record and applicable
checks, not all project accuracy/visualization deliverables. Record omitted
checks as not applicable/unmeasured with a reason. A valid short comparison can
provide model-performance evidence at its measured scope. Only the project's
required confirmation protocol supports its corresponding full-quality claim.
