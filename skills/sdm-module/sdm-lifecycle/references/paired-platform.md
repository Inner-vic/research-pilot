# Detached paired jobs on LPAI

For observed native response mappings, project telemetry and independent release
watcher qualification, read [native-lpai-qualification.md](native-lpai-qualification.md).
The optional `lpai-role-quote` mode replaces direct gpus/hourly_cost selectors
with canonical role totals and a scoped bound quote. Original field mode remains
available for other explicitly verified response dialects.

Use after [parallel-comparison.md](parallel-comparison.md), when the project
permits independent overlapping jobs. This adapter submits existing components;
it does not create components or override a one-task policy. A shared single-GPU
development space is not two independent allocations.

## Bind and confirm the group

Freeze both normal project requests, source/config snapshots, distinct workspaces
and outputs. Validate the comparison plan, including GPUs across every role.
Fill [paired-execution.template.json](paired-execution.template.json); bind the
adapter and project hook bytes using the existing `project_driver` binding rules,
and bind the CLI executable bytes. Inspect local CLI help and actual read-only
response shapes. Templates contain no native mappings, credentials or authority.

Present both arms, combined task/GPU/hourly-price/GPU-hours/cost/wall ceilings,
partial-start/peer-failure policies and cleanup/lease protection for confirmation.
Record the current confirming user message and exact configuration in
[paired-authorization.template.json](paired-authorization.template.json).
A confirmed file records approval; it does not authenticate a human or create
permission. Existing single-task permission does not authorize an overlapping pair.

`paired_execution.py plan --config <config> --output <group-state>` returns
`contract_digest` and `plan_digest`; use them in the bound approval. The contract
excludes only `authorization_ref`, avoiding circular hashing. `run --apply
--allow-live` is the live submission entry. Do not invoke it for preparation/CPU
tests. `status` reads saved state. `advance --apply --allow-live` only reconciles
existing jobs; `stop --apply --allow-live` requests group cleanup. Resume with the
same configuration, approval, directory and original deadlines, not a replacement
group. Submit calls/state writes stay serialized; detached jobs can overlap.

The coordinator owns one OS lock. The project must also provide exclusive
admission or reservation against competing coordinators. Capacity readback is
not an atomic platform reservation and cannot prevent unrelated quota races.

## Native CLI and project hooks

`scripts/lpai_paired_adapter.py` uses shell-free `lpai-agent` argv only: `projects
get/start`, `tasks list/get/stop`. Component lookup uses its name; start uses UID,
unique job name, prepared `--body-file`, all five task-info fields, and `--describe
bmad-paired-request:<request_key>`. Expected duration is informational, not a stop
timer. Task lookup is bounded/paginated by namespace, owner and name filter.
Adoption/stop additionally verifies exact job name, namespace, owner, component
UID and request marker. If the native platform cannot read back that marker,
report this adapter unsupported until a verified mapping exists. Never blindly
retry non-idempotent start after timeout, malformed output or process crash.

Selectors are nonempty JSON arrays of keys/list indices. Required sections:
`component` (uid, namespace), `start` (uid), `list` (items, total), `task` (uid,
name, namespace, owner, component_uid, request_marker, status, gpus, hourly_cost).
Explicit `phase_map` maps observed platform values to the documented task phases.
GPU counts/prices must mean the entire allocated task across master/workers,
including startup/idle, not one worker or utilization. Unknown fields/statuses,
incomplete pagination or guessed pricing block admission/readback.

The sealed project package exports `create_hooks(settings)` with:

- `preflight(request, component_json)`: verify ordinary source/config/data,
  mounts, hardware isolation, project concurrency, normal S12 and native payload
  semantics. Return `ready`, `normal_preflight_ref`, `project`, fresh `observed_at`,
  and `external_allocations`: other owned tasks within this project's admission
  scope, with exact UID, total gpus and hourly_cost. Exclude
  `request.owned_task_uids`. Do not read other projects. Preserve the ordinary
  preflight receipt; generic hash checks cannot inspect model/payload semantics.
- `body_ref`: absolute JSON path and SHA-256. `body_binding` must equal
  `{request_key, deadline, arm_digest}` using `request.declared_arm_digest`.
  Link those exact bytes to frozen source/config, allocations and mounted inputs.
- `lease_guard`: `{deadline, scope: "allocation-release", independent: true,
  evidence_ref}`. Verify a scheduler TTL or independently supervised reaper that
  stops and **releases the allocation**, even if the coordinator disappears.
  Bind its identity, deadline and scoped stop authority. A duration field,
  promise, heartbeat automation or process-only timer is insufficient. Without
  this verified capability, admission remains blocked.
- `observe(request, task_json)`: return exact uid/request_key, fresh observed_at,
  explicit released and release_evidence_ref when true. Return `health` with
  uid/request_key/observed_at/last_progress_at/string flags. Reuse project
  numerical checks and retained logs/checkpoints. Terminal status alone is not
  release proof. Missing required health evidence triggers cleanup.

Hooks execute in timed CPU subprocesses; native CLI calls have bounded timeouts.
Production hook process-group cleanup requires POSIX: use a Linux execution
host. Byte pins do not sandbox trusted code or authorize broader access. Hooks
must not submit/stop jobs themselves. Do not retain credentials/raw CLI stderr in
state. Body, adapter, CLI and hook bytes are rechecked before use.

## Budget and recovery behavior

Time starts before submission and includes queue, compile, run, evaluation, idle
and cleanup. Each saved arm deadline and the group wall/approved deadline survive
restart. CLI/hook elapsed time counts; clock rollback stops the group. Cleanup
starts before deadline/spend ceilings using the declared reserve. No automatic
model retry, additional GPUs, renewed lease or raised budget is implied.

Cost/GPU-hours accrue conservatively from submit intent until verified release,
using the greater of declared/observed allocation plus billing margin. These are
coordination bounds, not measured billing or a guarantee against delayed platform
cleanup/rounding. Before each submit, combine both arms and other owned allocations
against task/GPU/hourly-price limits. Actual excess allocation, shared observation
failure, group deadline/budget or ownership uncertainty stops the group.
Local failures follow arm_failure_policy; second-arm admission failure follows
partial_start_policy. A completed independent peer remains an unpaired result.
Parallel start skew is bounded; serial execution has no parallel launch window.

Persist intent before start/stop. Unknown start retains its key and enters
`cleanup-required`: lookup can adopt exactly one matching UID. Zero, ambiguous or
incomplete results never authorize resend. Observe every known UID before further
admission. Stop acknowledgment is not release; read back the same identity.
Stop retry requires fresh observation, a minimum interval and a declared maximum
(1–3); it never restarts training. Unknown/unreleased jobs remain visible and
block new compute after the cleanup reserve expires. Independent lease protection
remains responsible if the local watcher exits.

Binding drift blocks changed code execution. The CLI preserves original owned
identities as cleanup-required; reconcile via the original verified operation,
then prepare a fresh attempt. Retain error/state files. The latest 1000 events
and total event count are kept; request/UID/deadline/stop counters remain durable.
After interruption use advance/stop until release is actually verified.

`bounded_component_guard.py --deadline <epoch> --terminate-grace <seconds>
--receipt <json> -- <absolute-program> <args...>` optionally protects the POSIX
model process group: TERM, bounded wait, then kill surviving owned children.
Its receipt explicitly says `allocation_release_verified: false`. It supplements
verified platform release protection; it cannot satisfy that admission requirement.

Terminal platform executions are `executions-terminal-evidence-required`, with
scientific qualification `not-assessed`. Collect each arm's ordinary evidence and
accepted S13, then [export comparison records](experiment-comparison.md) and send
to the fixed Pro chat. Release compute before waiting for Pro. CPU fixtures prove
software behavior, not live leases, billing, data/image/DDP, model quality or Pro.
