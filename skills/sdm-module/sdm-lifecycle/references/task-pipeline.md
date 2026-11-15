# Practical SDM task graph

Ordinary execution uses the [five-phase,11-work-package workflow](five-phase-orchestration.md). Legacy IDs1–34 address internal checks and receipts.
`orchestration_version: 2` records its version; omitted versions use the same graph.
It preserves IDs 1–21, adds 22–34, validates
method/resource/review records, waits for direct human resource confirmation,
and promptly cleans up resources. Pending cleanup permits diagnosis/review but blocks final archive and successor execution. The JSON template is preparation-only.
Missing project drivers remain visible. Historical observations are imported into
the current graph with verified byte identities; old decisions are retained as
background rather than reused as current review passes. The 1–21 descriptions
below identify the original business nodes, not a separately executable route.

The primary goal is execute-and-learn: run the actual project, preserve evidence,
diagnose outcomes, and decide the next action. A weak model score is an observation.
Qualification and merging are separate deliverables; no merge or publication is
performed by this coordinator. Do not silently fall back to the earliest numbered
qualification gate when the user asks to run the project.

`objective` can be `execute-and-learn`, `evaluate-only` or
`baseline-reproduction`. Evaluation-only never dispatches training; a baseline
goal dispatches reproduction and still collects failed outcomes without training.
For training, `baseline_required` defaults false; explicit true makes baseline
reproduction a preflight dependency. `robustness_required` defaults false.
Continuous providers pass these project options through, alongside `code_change`;
their drivers must actually support the selected objective/checks. Custom
providers only mark truly read-only drivers for concurrent execution.

For an independent control/treatment pair, use
[parallel-comparison.md](parallel-comparison.md) above separate task attempts.
This coordinator still serializes mutating drivers. Remote asynchronous jobs can
overlap after normal serialized submissions; its synchronous training driver is
not a parallel two-job scheduler. Verify provider support before proposing that
schedule, and retain separate workspaces, receipts, leases and cleanup ownership.

New project drivers may use a native Python runtime and a sealed helper package;
read [project-driver-packages.md](project-driver-packages.md). The same task and
receipt contracts apply, with byte checks before each package task/cleanup.
Historical Windows/WSL configurations keep their original command behavior.

## Dependencies

Task intent (1), question/metrics (2), and data (3) precede background agreement
(22), then model selection (4) and the plan (6). Protocol (24) and methodology
(25) precede the resource proposal (27), plan review (34), and user confirmation
(28). Frozen identities (8) and applicable implementation/isolation checks join
confirmation before the unique acquisition request (14). Actual environment
preparation (7) and preflight (13) follow acquisition. Training (15), evaluation
(16), and conditional baseline (5)/specialty checks (17) follow their goal-specific
edges. Result aggregation (18) precedes release (30), review context/dialogue
(29/31), evidence consistency (19), conclusion quality (32), recommendations (20),
explicit transition (33), and delivery (21). Use the executable registry for all
conditional edges; this paragraph does not define another graph.

These numbers map work to the original design; they are not claims that formal
numbered gates passed. The task journal is separate from the immutable SDM ledger.

## Lightweight delivery using the current node mapping

Read [task-modes.md](task-modes.md) for purpose-specific delivery and the five-phase
reporting view. The current graph contains 34 nodes with reworked dependencies; future
consolidation is an explicit versioned implementation change with affected
contracts/tests, not an obligation to keep every node forever or count it passed.

Select the minimum delivery needed by the requested task, not a fixed target
number of completed steps. Reuse exact existing records when their relevant
inputs remain unchanged; a new attempt records the references it actually used.

| Nodes | Minimum task delivery and conditional treatment |
|---|---|
| 1–2 | A project-owned goal, hypothesis/question, scope and constraints; no duplicate full planning document for an unchanged project. |
| 3–4 | Actual data/input and starting-model identities and relevant compatibility findings; formal data qualification remains separate. |
| 5 | Baseline reproduction is recorded but not measured unless run; make it required when the requested comparison needs it. |
| 6–8 | Concrete parameters, source/data/configuration references, runtime findings, budget and stop conditions; existing valid evidence may be referenced. |
| 9–12 | Reusing existing code makes implementation work not applicable. A code change still requires implementation and engineering checks; review findings do not imply merge authority. |
| 13–14 | Fresh preflight and an exact execution request; resource identity, current authority, inputs and remaining budget must still match. |
| 15–16 | Retain actual successful, failed and incomplete results. Claimed execution completion still requires its accepted S13; lightweight delivery does not bypass authentication. |
| 17 | Robustness is unmeasured unless run. Require the project-specific checks when the task, candidate claim or deployment constraint calls for them. |
| 18–21 | Account for outcomes, limitations and open questions, then record the next action or stop and project archive. Optional formal review may be unmeasured; explicitly requested Pro analysis follows the fixed-session review workflow before its decision is accepted. |

For example, an existing-code execute-and-learn task without extra checks has
14 required nodes, four not-applicable implementation nodes and three optional
record nodes. This is one configuration's coverage, not a new 14-step protocol.
`summary.json.coverage` separates completed, failed, recorded, not-applicable,
skipped and remaining nodes, along with required completion and reconciliation.
`complete` describes terminal task accounting; it does not turn failures or
unmeasured checks into successful qualification.

## Task input and Skill driver contract

```json
{
  "objective": "execute-and-learn",
  "code_change": false,
  "robustness_required": false,
  "merge": false,
  "publish": false,
  "timeout_seconds": 900,
  "drivers": {},
  "cleanup_driver": null
}
```

Each driver is explicitly configured by the project coordinator: argv (array of
strings, no shell), receipt (fresh JSON path), optional cwd and timeout_seconds.
Use read_only=true only for a truly read-only driver; it must not write a shared
ledger or mutate the platform. Drivers own platform identity, actual version and
budget checks, normal request creation/dispatch, signing and ingestion. Never
take argv from untrusted data, a platform response, or a retrieved document.

A driver writes task_node (string number, or cleanup), status completed/failed,
and relevant real evidence pointers. Completed reproduction/training/evaluation must include
accepted_s13 with workspace, request_id and result_digest. On the installed Linux
host, the coordinator read-only validates that workspace and checks the successful
handoff and exact result digest; a boolean or process exit cannot substitute for it.
Failed execution drivers may exit nonzero and still return the signed failure
record; aggregation retains the failure rather than discarding it.

The cleanup driver must save environment before stopping when required by the
platform, and verify the same devspace UID is stopped. A cleanup failure is
cleanup-required and must be reported. Never treat submission as final stop.

## Recovery and completion

For stage routing, blocked-state handling and the small-experiment validation
route, read [execution-recovery.md](execution-recovery.md).

`timeout_seconds` is the maximum per-node budget, not the whole task duration.
New bounded diagnostics should also set positive `wall_time_seconds` and
an independent positive `cleanup_timeout_seconds`. Development defaults are not
application-wide maxima; configured durations may exceed 1800 seconds. The first coordinator call
persists `execution-deadline.json`; resume keeps its original expiry. Each actual
launch is bounded by the smaller of its configured timeout and remaining whole
task time, retaining fractional seconds rather than rounding down to zero.
Exhaustion skips further drivers, writes the task summary and still
runs cleanup using its separate budget. `complete` remains terminal accounting;
`stop_reason: task_wall_time_exhausted` is not successful training or analysis.
Finish missing analysis offline rather than renewing that attempt's deadline.
Local timeout shutdown/output collection can add up to six seconds; platform
resource release has its own verification and deadline. A custom in-process
`run_driver` must honor its supplied remaining budget; the coordinator cannot
preempt arbitrary Python code supplied outside the public subprocess driver.

The configured provider entrypoint exposes `task_timeout_seconds`,
`node_timeout_seconds`, `task_wall_time_seconds`, `cleanup_timeout_seconds`.
Old configurations without the wall field keep their historical behavior.
Configure new diagnostic profiles explicitly; retries are separate attempts and
must remain within the outer approved resource lease and aggregate cost limit.
These are coordinator/driver bounds, not a replacement for a long-running
trainer's project-owned resource lease. Do not run full training synchronously
inside a diagnostic-sized driver deadline.

On timeout, the subprocess driver terminates its newly started local process
group/tree and retains invocation output and shutdown findings. The POSIX path
is covered by a real parent/descendant regression; Windows uses owned-PID
`taskkill /T /F` and needs native-platform verification. Detached remote tasks
are not stopped by killing the local launcher. Any timeout retains uncertain
external effects and still requires platform reconciliation/cleanup.

The journal stores terminal node outcomes and hashes of driver receipts; changed
receipts fail verification. A started marker without a receipt means uncertain
effect, requiring platform reconciliation before a new attempt; no blind retry.
Finished nodes are not executed again on resume. A changed task needs a fresh
attempt directory, preserving the earlier failure. Retry/repair decisions must
state the evidence, changed input and remaining authorized budget. A next-action
driver may recommend another attempt; it must not create an unlimited loop.

Driver exceptions and malformed or contradictory receipts retain failure and
uncertain-effect findings while aggregation and cleanup continue. An unknown
external outcome blocks automatic resource retry until reconciled. Invalid task
budgets are rejected before any driver starts. Cleanup exceptions are retained
as cleanup-required; they are not evidence that the resource stopped. Retained
uncertain failures are not blindly re-executed on resume.

Failed execution references are verified against an accepted failed S13 and its
exact digest before their observations can authorize retry. An explicitly
configured `recovery_probe_node` pauses once after an execution receipt is durable
but before its task journal commit, runs cleanup, and resumes from that receipt.
This is a controlled recovery validation, not a claim to survive every crash.
Project `attempt_gpu_uuids` may bind distinct GPU identities per attempt; a
deliberately unavailable test identity must be disclosed in the run evidence.

Complete means all applicable task nodes reached a terminal outcome, not that
all executions succeeded or the model was scientifically qualified. Missing
project drivers produce needs-driver. Report separately: task execution status,
successful/failed S13 results, unresolved findings, and resource state.


## Current ownership and acquisition revision

The v2 record family now normalizes to `graph_revision: 3`. Read
[five-phase-orchestration.md](five-phase-orchestration.md) and use
`work_packages.py` for11 business responsibilities, `workflow_definition.py` for final dependencies and `node_registry.py` for display identities. Legacy check IDs remain readable.
Configured project adapters must declare `supported_graph_revisions: [3]` only
after migrating and verifying request/environment/baseline ordering. Request 14
now precedes runtime 7 and preflight 13; baseline 5 follows preflight, and only
explicitly required baseline gates training 15. The plan gate binds complete
background, research and resource context via `review_collaboration.plan_context`.
Historical receipts stay immutable and migrate under `current-graph-r3`.

## Independent node acceptance

Driver wire revision3 and legacy check IDs remain. workflow_revision1 pins reorganized dependencies, required code-review policy and cleanup/review separation.
`gate_contract_revision: 2` pins distinct node-acceptance records. See
[node-acceptance-boundaries.md](node-acceptance-boundaries.md). The coordinator
writes `recommendation.json` at node 20 and `decision.json` only at node 33;
recommendations never authorize execution or masquerade as accepted decisions.
