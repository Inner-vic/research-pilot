# Prepare a constrained runtime without dispatching training

Use this reference when the requested work includes low-resource parameters,
checkpoint recovery or a bounded model memory/throughput probe. It supplements
the existing project driver and task pipeline; it does not add a new execution
provider or require unrelated qualification gates.

## Project-owned preparation

Restore the current code/data/configuration identities and authorization stage.
Keep model-specific source changes, parameter profiles and test artifacts in the
authorized personal project. Preserve the original baseline and record runtime
changes separately. Seal changed source bytes and profiles for deployment;
preparation, deployment, GPU validation and formal training are distinct states.
Never infer permission to start a probe or training from a preparation request,
a prior environment-only authorization, or a Pro response.

When reducing devices, derive the intended effective batch and record sampler,
epoch-tail accumulation, optimizer/scheduler clocks, loss logging and data-order
differences. Equal batch arithmetic does not prove numerical equivalence. A
probe may use different accumulation for bounded performance measurement only
if that deviation is explicit and its weights/results cannot become the Base.

## Recoverability

State the supported recovery boundary. For epoch-boundary recovery, use an
epoch-bounded loader, explicit worker/random-state policy and drained gradients;
replay an interrupted epoch rather than claiming arbitrary-batch recovery.
Bind snapshots to the actual source/data/runtime protocol. Retain optimizer,
scheduler, AMP scaler, random generators, counters and best-selection state.
Prefer primitive metadata and restricted tensor-state loading. Publish a
verified atomic snapshot before advancing its latest pointer; failed writes
must preserve the previous recoverable snapshot. Never silently import legacy
or emergency weights as a full-state resume. Record retention/quota changes.

Verify the relevant failure modes on CPU where possible. CPU state/serialization
checks do not establish GPU kernel correctness, device memory, throughput or
bitwise reproducibility of the actual project model.

## Observable preflight and failure capture

Project launchers must identify each applicable check rather than combine topology,
source, input and output checks into an anonymous assertion. Record a stable
`check_id`, expected and observed values, pass/fail, elapsed time and stage. Use
explicit exceptions for runtime admission checks; `python -O` must not disable
them. Check only inputs required by the project: declared device/node mapping,
source revision/cleanliness and selected file hashes, dataset index and initial
weights, writable fresh output, space and remaining lease. Keep actual runtime
versions distinct from the requested image: platform startup hooks can mutate
packages. A successful checkout/download does not prove mounted content or model
compatibility. Never relax a mismatch just to reach model launch.

`scripts/runtime_diagnostics.py` provides stdlib `RuntimeDiagnostics` and
`PreflightError` for project-owned launchers. It does not run a model, read an
environment, submit a job or enforce resource release. Freeze/copy this helper
with the project driver package and record its digest. Start structured stdout
events before accessing a results mount. Attach an owned local fallback journal
where available; after the results mount is verified, attach its fresh journal
and replay the early events. Existing journals are preserved. A failing sink
removes only that sink; the original error and terminal event remain on stdout
and other usable sinks. Record the named check, exception type/errno and safe
traceback frame locations. This helper deliberately excludes raw exception
messages, locals and source text; supply only selected non-sensitive observations,
never dump an environment, credentials or an arbitrary subprocess payload.

Keep primary failure, cleanup error and result-write error as separate events;
cleanup/persistence exceptions must not replace the first cause or prevent the
terminal record. Record model dispatch only after the model child was actually
created; allocation, wrapper startup, imports and extension builds are not
training progress. Model dispatch alone is not a healthy optimizer update.
Continue same-UID platform cleanup/readback even if all guest journals fail.

Before a repaired launcher is used on paid compute, exercise applicable CPU
fixtures: passing input checks, individual topology/source/hash/mount/space
failures, unavailable output, expired deadline, preservation of old output,
secondary cleanup/write failures, and child termination without retry. Keep the
failed submitted command and raw response immutable; prepare a separate version.
Do not assign an old anonymous failure to a specific check based only on a CPU
reproduction. A terminal attempt's budget is not permission to submit the repair.

## Bounded execution and cleanup

Bind one execution lease to the approved project/resource UID, permitted device
identity, profile/source digests and absolute deadline. Include startup, local
extension preparation, model checks and cleanup in the whole resource budget.
Protect the complete launcher, including source verification, mounted-file reads
and checkpoint writes, rather than timing only the training subprocess. Recompute
remaining time from the absolute lease before each extension or trainer dispatch.
Keep the cleanup intent on an owned persistent control path independent of the
results mount; writing a diagnostic exit log must not prevent recording cleanup.
Use a deadline enforcer independent of the model process; test non-cooperative
children and coordinator interruption. Kill only the new owned process group.
Do not execute argument vectors retrieved from platform output or model review.

Process exit and resource release require separate evidence. Restore the normal
platform cleanup driver and require a fresh same-UID stopped readback. A CLI
response of `pending_approval`, a stop request file, or a timeout is not a stopped
resource. Within already granted authority, an agent can handle the native UI
action request through the normal logged-in self-service controls and then let
the driver verify it. Do not invent an unapproved API or claim unattended cleanup
is validated when a human/agent UI action is still needed.
Keep the attended stop deadline independently of successful guest-side artifact
writes. Resource release must still be attempted if source validation, output
creation, result persistence or the launcher itself fails.

Retain unresolved stop requests; do not reuse their resource for later training
until stale-request handling is established. If cleanup fails, report
cleanup-required and keep successor dispatch blocked for that owned resource.

## Probe proposal

Use [training-operations.md](training-operations.md) and its resource collection
template to collect and confirm the concrete probe. Project defaults supply a
proposal when the user has no preference; they do not authorize resource start.

Before asking to execute, make the proposed probe reviewable: exact source/image,
real input index and initial weights, permitted devices, candidate parameters,
warmup/measured steps, memory limits and measurement coverage, hard deadline,
stop triggers, checkpoint/output location, price basis and billing uncertainty.
Use a fresh output and explicit probe identity. Stop on unsupported inputs,
uncertain resource identity, OOM, invalid numerical evidence or deadline; do not
turn a failure into an unbounded retry or a new scientific experiment.

For a shared device, nominal memory is not free memory or a reservation. Record
the fresh eligible-device memory snapshot and bind only one approved device.
Keep candidate batch sizes and their memory heuristics in the project profile;
do not loosen a sent profile's threshold or substitute a device silently. A
failed resource preflight is a real diagnostic observation, but is not a model
run, an OOM, a completed experiment or an accepted S13 result. Release the
resource before Pro review. If a smaller profile is justified, prepare a separate
bounded diagnostic with its own current authority, byte identities, fresh memory
check and resource deadline. Review turns do not consume ML iteration counts;
resource acquisitions and their costs remain recorded even when no model ran.

Do not manufacture a successful training/evaluation observation to pass the
normal run-next-round completeness checks. Use stop-analysis with a separate
repair/preparation proposal for an incomplete attempt. After the proposal passes
review and preparation checks, the authorized outer agent may execute a new
diagnostic; this does not grant the scientific Loop permission to skip its gates.

Archive the measured results and cleanup receipts through the existing task
pipeline. Leave missing evidence visible. A short training-speed sample cannot
prove full evaluation throughput or 300-epoch completion. When Pro analysis is
requested, reuse the project's fixed review session and supplement evidence;
the returned recommendation still passes the existing BMAD decision checks.
Keep model-process status separate from wrapper/controller status and propagate
required child failures through each launcher. A zero wrapper exit is not proof
of successful training; require the expected microstep count, numerical findings,
completed save receipt and resource release before reporting probe completion.
Publish a final probe result only after its intended checkpoint save succeeds.
Legacy emergency weights remain diagnostic artifacts, never a full-state resume
or a baseline. Verify failure propagation using a real failing CPU child where
possible; preserve the original erroneous runtime receipt after repairing code.

Finite loss does not establish finite gradients or successful optimization. Check
nonfinite loss before diagnostic backward/step. Distinguish AMP step/skip proxies
from measured parameter changes. Record missing scaler, pre-clip gradient and
frame-identity evidence explicitly; source defaults do not reconstruct historical
runtime values. Retain partial memory/timing measurements as partial: skipped
optimizer steps may omit optimizer-state allocation and cannot establish steady
throughput or the memory required by a healthy run.
When preserving browser evidence, retain the complete relevant control/output
state, not only an accessibility diff that may omit unchanged values. Disclose
whether a capture shows the original live output, a cached display after stop,
or a fresh measurement; do not assign an unrecorded historical timestamp.

## Observation boundaries and early rank failures

Place target diagnostics at the actual consumer boundary after target production
and input mapping, before the loss call. A raw-batch field absent before forward
does not establish that the loss received no target. Record missing, None,
non-tensor, empty, malformed and nonfinite inputs distinctly, plus applicable
valid-support counts. Observers must not replace inputs, mutate targets or consume
RNG. A processed tensor digest binds that observed tensor, not the original GT
file, frame, augmentation or RNG state. Static routing and CPU sentinel tests do
not reconstruct historical dynamic values.

Declare whether a probe tests a finite execution path or investigates AMP scale
fallback. Stop-on-first-nonfinite and a bounded fallback observation answer
different questions; keep overlay, launcher and log-monitor policies consistent.
Do not silently relax one to obtain a successful run. A post-optimizer hook counts
step calls, not measured parameter changes, convergence or full-state recovery.

Write per-rank stage/target/pre-clip evidence incrementally before operations that
may throw; retain exception type and relevant frames without dumping environments
or arbitrary payloads. Collect every expected rank even after child failure or
cleanup. Missing, partial, foreign or malformed rank reports remain failures;
do not wait indefinitely for a success-only barrier or terminal file. Keep the
original nonzero failure when persistence fails, and retain stdout/fallback
evidence where available. CPU tests should exercise a real failing child and
the changed collector's missing-rank path. They do not prove distributed GPU
behavior or resource release. Re-freeze all changed helper bytes and regenerate
the actual transport command before any separately authorized integration run.
