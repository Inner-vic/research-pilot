# Confirm the complete resource envelope

For new ordinary tasks, apply this procedure through nodes 26–28 and 34 of
[five-phase-orchestration.md](five-phase-orchestration.md). Inventory the actual
account's provider-specific catalog before selecting a queue. Component and
development-space availability remain separate observations; a development-space
alternative is a new bound proposal, not a silent route substitution. Confirmation
comes directly from the human conversation, never a generic instruction to send
a Feishu request. Preserve real provider approval restrictions without bypassing
them or treating an unverified gateway response as a universal platform rule.

Read with [training-operations.md](training-operations.md) before a paid allocation
or when available GPUs and actual scheduling disagree. This supplements existing
resource confirmation and provider preflight; it adds no numbered workflow node
and grants no execution permission. Model/project values remain project-owned.

For the step-by-step read-only discovery and queue investigation, use
[resource-investigation.md](resource-investigation.md). Start from the current
account's visible catalogue, not a previous case's resource names or capacities.

## Resolve, observe, explain, confirm

1. Restore the current user scope and exact resolved provider configuration.
   Identify provider kind, namespace, cluster/region, opaque queue ID plus display
   name, allocation mode and package/catalog identity. A similarly named queue,
   another cluster or a development-space package is not interchangeable.
2. Record **each role's actual per-instance request**: instances, GPU exact type,
   count and memory, CPU request/limit, RAM request/limit with units, network/RDMA,
   node selectors/affinity and required storage/data locality. Separate allocated
   devices from model processes/device caps, and package capacity from packages
   per instance. Normalize native fields before summing: some dialects already
   store the full per-instance GPU count. Sum CPU/RAM/GPU across all roles and
   concurrent authorized arms. Preserve per-instance topology as well as totals.
3. Observe three distinct layers where the provider exposes them: queue headroom,
   workspace/user quota headroom, and actual joint placement of the complete
   per-node requests. Include competing pending work, reservations/maintenance,
   priority and guaranteed/preemptible rules if available. Positive aggregate
   CPU/RAM/GPU counts do not establish a suitable whole node. Logical limits,
   current used/total, physical availability and a reservation are different facts.
4. Attach source, observation time, validity/freshness and the exact request
   digest to each observation. Use `unknown` for unavailable dimensions. A current
   snapshot cannot establish last night's shortage; an empty audit/event result
   cannot prove unrestricted scheduling. A stale historical-page queue label can
   fail metadata resolution: reconcile its opaque ID with the owned component
   and current catalogue before declaring the queue disabled. Do not expand
   credentials or query unsupported node APIs to fill a gap.
5. Assess configuration suitability separately from catalogue legality. Identify
   fixed package values and permitted custom dimensions. An unsaved form accepting
   smaller CPU/RAM is only catalogue/form evidence; it does not prove provider
   admission, runtime fit, throughput, lower price or shorter queueing. Explain
   demand from project data loading/workers, caching/prefetch, compilation and
   measured peaks where available. Inspect every concurrently constructed loader,
   including validation/visualization loaders and eager iterator/prefetch behavior;
   `num_workers` for one loader is not the whole-process worker count. Process
   count is not an equivalent minimum core count. Unmeasured peaks remain unknown. Preserve
   scientific batch/optimizer/scheduler conditions when proposing resource changes.
6. Present one resolved proposal with findings and unknowns, then reuse/obtain
   confirmation only within the exact scope. Known incompatible inputs or quota
   shortages stop the affected dispatch. Unknown physical placement may use a
   user-confirmed bounded queue wait under existing policy; it must not be
   reported as ready/reserved. A catalogue check, CPU test or this helper's
   positive assessment never authorizes a new resource acquisition or retry.

Use [resource-confirmation.template.toml](resource-confirmation.template.toml) as
the collection record. Keep detailed snapshots in the project's evidence package.
Select project defaults when the user has no preference, with a recorded basis;
do not turn a single model's CPU/RAM/GPU or queue timing into global defaults.

## Optional read-only calculation

`scripts/resource_feasibility.py --input <normalized-resource-evidence.json>
--output <fresh-assessment.json> [--max-evidence-age-seconds <project-window>]`
only summarizes and compares existing authenticated snapshots. It has no platform
client, model import, executor or automatic retry. Keep its output outside governed
S12/S13 receipts; it neither signs nor substitutes one. The input is normalized
project evidence, not a training payload:

- `request`: nonempty `provider`, `namespace`, `cluster`, `queue_id`,
  `scheduling_mode`, and `roles` with unique `name`, positive `instances`,
  `cpu_cores_per_instance`, `memory_gib_per_instance`, and integer
  `gpus_per_instance` plus exact `gpu_type` for GPUs. Include relevant package,
  network/storage/placement and limit fields in this envelope so its digest binds
  them. Supply actual resolved requests, not another copy of speculative defaults.
- `observations`: `scope` (`catalog`, `queue_headroom`, `space_headroom` or
  `joint_placement`), `status: observed`, `source_ref`, timezone-aware `observed_at`
  and `valid_until`, and `request_digest` from `request_digest(request)`.
- Queue/space headroom observations contain `available.cpu_cores`,
  `available.memory_gib` and `available.gpus_by_type` keyed by exact type. A real
  observed zero is zero; a missing key is unknown. Catalogue uses boolean
  `supported`. Joint placement uses boolean `fits_full_request`, established for
  all roles together; do not invent this boolean from aggregate free cards or
  independently fitting arms that could compete for the same nodes.

When a provider explicitly defines available resources as the dimension-wise
minimum of queue and workspace headroom, one `effective_headroom` observation
with the same `available` dimensions may replace those two views. Preserve that
definition in its evidence. It still says nothing about joint node placement or
which underlying quota caused a shortfall. Do not combine overlapping headroom
sources without reconciling them; the helper keeps such overlap unknown.

Outputs distinguish `known-constraint`, `unknown` and `fits-observed-snapshot`.
Changed request digests, stale/future evidence, missing dimensions and conflicting
snapshots remain unknown. The CLI freshness window defaults to 300 seconds for
calculation convenience; configure it for the project/provider, and refresh again
before any actual authorized dispatch. Shared/unlimited allocation dialects need
an explicit provider mapping; do not convert an unknown GPU count to zero and
claim this exclusive-allocation calculation qualified the shared provider.

## Queueing, diagnosis and recovery

Resolve a queue-wait limit, startup/compile limit, minimum useful remaining model
window, cost exposure and cleanup reserve inside the original absolute resource
deadline. Record how the provider bills pending/init time; unverified billing is
unknown. A failed wait does not silently restart the clock or authorize switching
to another queue, increasing GPUs/priority, selecting preemptible service or
reducing RAM. Preserve identities and request cancellation through the approved
same-UID route before a replacement, even if the job is still pending.

On unexpected queueing, capture same-task events and stage times, then perform
bounded scoped queries for specific CPU/RAM/GPU/selector/quota reasons. Control
watcher messages such as “retrying status check” do not prove a training retry.
Separate a generic `FailedScheduling` observation from a proven named shortage.

When a dashboard shows idle GPUs but the task remains pending, compare observations
at the same time and scope: dashboard definition/filter, GPU type, cluster, queue,
workspace/user eligibility and selected provider package against the actual task
request. Keep a user-reported count labelled as reported until its page/filter and
timestamp are captured. Cluster-wide unused devices, schedulable queue capacity
and capacity available to this exact request are separate quantities. Do not
convert an idle count into a reservation or infer defective hardware from it.

Classify the contradiction using supported evidence: quota/priority or reservation,
CPU/RAM shortfall, full-node or gang placement, selector/storage locality,
unavailable nodes, or unresolved. A priority-blocking event establishes that
scheduler explanation, but does not identify the scarce resource or establish
hardware health. If the matching idle-capacity view still conflicts with repeated
task events, retain a suspected platform capacity/scheduling inconsistency with
the two source snapshots, task identity, exact request and missing dimensions.
Do not assert a hardware root cause without provider evidence, repeatedly submit
probes, or change resources merely to hide the contradiction. Keep the incident
packet project-owned and the diagnostic method reusable.

If supported interfaces provide no detailed reason, retain that limit and a
project-owned escalation evidence packet; contacting others is a separate action.
Do not repeatedly poll unchanged records or start a job just to test a hypothesis.

Prefer original events/logs over automatic diagnostic labels. Code/config text
containing `CUDA out of memory` is not evidence of a runtime OOM. Require an actual
exception and execution-stage context; preflight failure, pending placement and
model numerical/metric failure have distinct causes. Preserve the automated
report as a hypothesis when it conflicts with original evidence. Update the
project's next proposal and the relevant Skill guidance from measured findings.
