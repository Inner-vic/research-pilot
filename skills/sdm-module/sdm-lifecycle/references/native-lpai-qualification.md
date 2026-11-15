# Qualifying the native LPAI paired path

Read with [paired-platform.md](paired-platform.md). These are read-only observations
on 2026-10-14; other dialects need explicit mapping and verification.

## Observed native responses

Own component/task get returns `status: ok, data: ...`. Identity fields live under
`data`. List returns `data.items`/`data.total`, with raw task items; choose
`list_item_envelope: data` to normalize them through the same get mapping.
Generated names append `-<canonical UID first eight characters>` to the requested
base. `job_name_policy: lpai-uid-suffix` validates the whole UUID and exact suffix,
plus namespace, owner, component UID and request marker. Never adopt by name alone.
`data.describe` reads back historical descriptions. A newly sent request marker
and normal successful start response remain live-qualification gaps.

`allocation_mode: lpai-role-quote` totals role `replica * resource.Gpu` under
`data.data.lpai_task.roles`. Observed `Gpu` already totals each instance's package
GPUs; `resource.replica` is not another multiplier. Lower-case `gpu` can be zero
in a populated legacy package. Pin this dialect explicitly and reject ambiguity.
No direct hourly-price field appeared in task get. Bind a verified
`sdm-lpai-role-quote/v1` quote: namespace/cluster/queue, sorted role-signature digest,
currency/hourly_cost, observed_at/valid_until and source_ref. Missing, expired or
mismatched quotes block live use; estimates never prove measured billing/rounding.

`release_mode: task-pods-deleted` queries exact-UID `tasks pods`, supplying
`_bmad_native_pod_readback` to the project hook. Release requires same owner/
namespace/task UID, terminal task, nonempty compute pods and every `step: task`
pod deleted with finish records. Succeeded alone, empty pods or another task's
pods do not qualify. Historical release was read-only verified; new stop latency
is untested. Sanitized snapshots are replay inputs, not submission payloads.

Historical A800 start returned pending_approval and an approval ID, not a task.
Preserve that identity, reconcile and never resend because no task appeared.
Pending stop approval also differs from stopped: retain its ID, continue readback,
and do not generate duplicate approvals. Unresolved cleanup blocks replacement.
No ticket or colleague message is implied by this wait.

These responses came through the Agent CLI/tool-service entrypoint. They do not
prove a notification was delivered, a native GPU task exists, or ordinary web
submission has a universal Feishu approval requirement. On 2026-10-14 the user
reported no notification; the owned component's native page showed the normal
Execute entrypoint and `not_run`. Treat gateway requests and native queued jobs
as separate states. Preserve unresolved gateway metadata without describing it
as a confirmed platform job or a permanent global computation lock. Reconcile
actual task identity/allocation and the native workflow before deciding whether
a separately authorized successor is admissible; do not bypass a real approval.

## Development spaces are a different provider

The 2026-10-14 UI query offered an A100 40G shared development-space package
at CNY 0.59/hour in `ns-lpai-demo`; the component-task form in that queue
offered only a CPU package. These are observations to refresh before use,
not permanent availability or pricing rules. The paired adapter above targets
component tasks; it does not implement development-space submission/release.
Do not use a development-space quote as a task role quote or claim its stop
qualification covers component tasks.

The owned development-space get uses `data.metadata.uid`, owner labels,
`data.spec.resources.resourceComboId/resourceComboNum/resourceQueue` and
`data.status.phase`. Shared `gpu: -1`/`unlimited` is not an exclusive allocation
count. Bind the package and program device cap separately, then observe actual
GPU UUID/free memory after an authorized start. Its configured idle culling was
240 minutes; this cannot enforce a 15- or 30-minute probe. A development-space
lease needs its own bounded, identity-checked stop/readback path. A stopped
historical space does not prove the next live stop completes within its reserve.

## Project preflight and health

The sealed project-owned RoadModel hooks import no model, submit no training and
mutate no platform resource. They check concurrency policy, frozen source/profile/
body, fresh ordinary S12 verification, data/image/mount/DDP/GPU/output isolation,
exact role count and pinned RoadModel commit. The existing project driver must
produce and verify S12 through its ordinary path; hooks neither sign one nor
substitute a synthetic receipt. Missing checks remain blocked.

RoadModel still permits one active compute task. Parallel capability cannot
elevate that permission. Do not choose a treatment or reuse old 40-card payloads
to fill a software template. Actual frozen arms follow a supported research
decision and current resource proposal. Preparation leaves native arm selection
and execution authorization pending.

Running-task telemetry binds uid/request_key, observation/progress timestamps,
loss, gradient-element counts and optimizer records. RoadModel distinguishes a
single AMP overflow from consecutive corruption; finite-element norm overflow
is separate. Thresholds are project-owned. Archive parsing cannot claim fresh
health. The archived probe has five corrupt-gradient steps, zero healthy updates.
Its exported code folder is a delta; the full 409-file frozen tar/manifest provides
complete source. Verify members/hashes in a fresh owned directory without executing
model code. Cache directory link checks within one verification to avoid needless
repeated filesystem reads, while checking every declared file hash.

## Independent release watcher

`paired_release_watch.py --config-ref <bound-config-ref.json> --state-root
<original-group> --heartbeat <guard-json> --apply [--allow-live]` runs under an
independent Linux supervisor. It never submits, renews deadlines or creates an
automation. Authority remains the exact confirmed group's stop-owned-until-released
scope. CPU fixtures/preparation do not authorize launching a real live watcher.

The watcher restores original UIDs/deadlines, uses the same OS lock and bounded
cleanup, and verifies each release. Busy lock is a temporary observation, not a
binding failure or reason to duplicate work. Heartbeat checks contract, freshness,
Linux process birth identity (PID reuse protection), independent process and
deadline coverage. Binding drift stops replacement-code execution. Missing guard
health blocks admission. After cleanup expiry unresolved jobs stay cleanup-required.
Its supervised host and credentials must remain available; it cannot guarantee
recovery from host loss or unapproved platform stop.

CPU fault injection kills the coordinator while a separate watcher releases two
mock tasks at the original deadline. This proves local process independence, not
the actual stop gateway or GPU release. The RoadModel hook also requires a bound,
scoped **verified live release qualification** before admission; this batch emits
none. Qualification must show the same approved stop path completes and reads
back GPU release within the reserve, including any required approval.

Before paid compute: fresh quotes and frozen actual arms, concrete scope/budget
confirmation, ordinary preflight, independent supervisor, then separately authorized
bounded live validation. Collect per-arm S13 and
[comparison exports](experiment-comparison.md) before fixed Pro dialogue.
