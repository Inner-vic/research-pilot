# Execution stages, waits and recovery

For two detached LPAI control/treatment jobs, use
[paired-platform.md](paired-platform.md): restore both original UIDs/deadlines,
reconcile uncertain submits, and verify each allocation's release. One successful
arm or stop acknowledgment never clears the other arm's cleanup.

Use this guide with [task-modes.md](task-modes.md) and the existing project/task/Loop
and Pro references. These five phases organize the practical chain and recovery;
they do not create a new five-gate protocol. The detailed node mapping can evolve
through explicit versioned changes while preserving existing evidence/history.

## Continue from the durable stage

| Phase | Restore/read | Output and exit condition | Exception or wait |
| --- | --- | --- | --- |
| Task and context | Project policy/defaults, current authority, source/data/model, existing attempt and pending review. | Clear purpose, scope and missing choices. | Collect only material missing facts; do not restart completed work on continuation. |
| Research and execution plan | Applicable retained evidence, task modes, research strategy and training-operations. | Concrete bounded profile/plan and current confirmed execution scope when compute is requested. | No resources while preparing. A quote is not availability; an unsupported required check remains a gap. |
| Execution and monitoring | Fresh preflight, owned UID/allocation, immutable request, task-pipeline, runtime and cleanup contract. | Actual terminal outcomes, retained artifacts/timing and separate same-UID release readback. | Deadline/numerical/fatal failure: stop, preserve and clean up. Unknown submission or stop: reconcile; no blind retry or reacquisition. |
| Evidence and Pro review | Retained observations and artifact index; fixed session, accepted context and active review request. | Questions or exact final response passed through existing validation; compute already released. | Failed archive delivery: retry delivery alone. Pending/uncertain Pro send: inspect the same chat. Invalid response: retain and request targeted correction; no context advance. |
| Next decision | Validated response, remaining gaps and current execution boundary. | Supported next plan, stop or evidence request with a concrete continuation point. | A question/recommendation is not execution authority. Unresolved cleanup blocks new compute; bounded successor needs fresh preflight. |

Restore small authoritative files first: project policy, installed binding,
attempt summary/cleanup, and pending-review status. Read large logs or scan source
only for a specific unresolved question. Reuse exact data/source/config evidence
whose relevant inputs are unchanged; platform availability and current execution
authority still need a fresh check. Do not regenerate full planning documents or
repeat completed task nodes merely because the conversation resumed.

Invoke byte-bound installed Python CLIs with `-B` and propagate
`PYTHONDONTWRITEBYTECODE=1` to child processes. Parent `-B` alone does not affect
a newly launched Python child. Check source/installed equality after validation;
generated or changed `.pyc` files can invalidate the exact tree binding even
when source code is unchanged. Preserve drift evidence and use the normal
installer to restore the registered tree; do not edit the stored digest to hide
a mismatch or mutate historical bound workspaces.

## Bound work without rushing scientific analysis

Distinguish the per-driver timeout, total task/attempt wall time, owned compute
lease/cost limit, cleanup reserve, and external Pro dialogue. Record them in the
project profile and include setup/transfer in the resource budget. Use the actual
public settings in [task-pipeline.md](task-pipeline.md), not unexplained helper
defaults. Recovery does not renew a saved deadline or an approved resource lease.

When the task wall time is exhausted, keep terminal accounting and cleanup; a
missing model analysis remains missing. A new attempt needs a current plan and
authority, not a changed timestamp in the old attempt. If local termination or
platform release is uncertain, report that exact finding instead of saying the
task stopped. Clear failure and next-action evidence is preferable to repeated
unbounded tool calls.

For external waits, persist the exact request/UID, last observed state and next
check, then return control or continue independent work. Use increasing intervals
when unchanged, bounded tool observations and a concise progress update during
active work. A local observation window ending means pending, not remote failure.
Only a new observation or corrected input justifies a retry. New facts reset the
relevant investigation, not every completed phase. Do not create an automation,
support ticket or new Pro conversation solely to avoid a wait.

Browser tab handles are temporary; the fixed chat URL and bound request are the
durable identity. If a tab disappears, reopen that URL and reconcile the current
user turn, attachments and response state. An `Unknown error` after clicking send
does not prove delivery or failure. Retain the error and observe the same chat:
a request still only in the composer after reload can be sent once after this
non-delivery is recorded; a visible submitted turn or active generation must
not be resent. Transport recovery never reacquires compute or advances context.

When an owned development space restarts, its cached notebook/console can still
show an old run or a disconnected kernel. Refresh the space connection and use
a connected session before executing the new bound attempt. Old console output
is historical evidence, not a fresh GPU or process observation. Reconnection
and upload still consume the original startup/resource lease; do not reset it.

## Small-experiment validation before scaling

Choose the smallest experiment that answers the current framework question.
For routing, timeout, malformed receipt, recovery and pending-review bugs, use
isolated CPU subprocess tests first. A tiny CPU training/serialization diagnostic
can verify local compute/output handling; it is not RoadModel training, GPU/DDP
compatibility, a successful platform S13 or a live Pro response.

Then, when the project input/runtime and bounded resource proposal are ready,
use a small project-owned GPU profile to verify the actual platform chain:
preflight → model run → authenticated results → archive/release → fixed Pro
dialogue → decision validation → next plan. Do not substitute software fixtures
for any of those external facts. Do not start a large allocation to test a purely
local coordinator bug. A failed small run is useful evidence; finite loss with
no optimizer updates still does not qualify healthy training.

Keep a compact validation record: installed Skill/config digests, actual entry
commands, per-node time/outcomes, fault observed, verified release or missing
release, resume dispatch counts, review state and untested platform boundaries.
Retain each original failed attempt. Improve the relevant instruction/code from
that observed case, install through normal setup, and rerun the affected checks.
