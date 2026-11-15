# Project branches, results and goal selection

Read this for a new project or continuing its execution history. These defaults
apply across projects; names, repository identity, metrics and platform adapters
come from the current project, never from a previous validation project.

## Establish the project destination

Identify the authorized project Fork/checkout, authenticated account, current
work branch and permitted operations. Inspect current state and preserve dirty
files. Reuse a maintained branch when continuing the same line of work; a new
branch is useful for a genuinely separate code change, not for every parameter
round. Separate branches for review remain available when the user needs them.
For isolated edits and freeze/restore/retirement, use
[worktree-isolation.md](worktree-isolation.md); preserve dirty source and owned
records. For paired outcome summaries, use
[experiment-comparison.md](experiment-comparison.md) over the retained round files.

For an authorized new personal branch, use the installed GitLab Skill's
`prepare_fork_branch.py` with exact source/Fork URLs, source SHA and target name.
Retain its creation result. Names and commit authors cannot establish creator
identity. A Fork can inherit hundreds of branches; do not relabel or remove them
as personal experiments. Do not change a project's default branch merely because
the work branch exists. If the user asks, use the native project-setting API and
verify only `default_branch` changed, preserving branch/tag refs and the source
project. Already-authorized cleanup does not need another confirmation.

When cleanup is requested, use the GitLab Skill's `archive_owned_branch.py`.
Supply the exact personal origin, account, active branch, old branch, expected
old HEAD, fresh `archive/...` tag and a private output directory outside the
checkout. Default invocation is read-only; `--apply` is appropriate only within
the user's cleanup authorization. It requires a native creation event for that
account/branch, rejects current/default/protected branches, makes a verified
bundle, confirms the remote tag, then removes only the guarded old ref. Other
remote refs must remain unchanged. Local branches and dirty files remain intact.

Unknown ownership is recorded and left alone; it must not block training. An
archive failure is a maintenance finding, not permission to repeat computation.
For an uncertain write or an existing archive tag, inspect the recorded phase,
bundle and exact remote refs before retry; never blindly rerun `--apply`. If native
creation history is unavailable, retain the branch and explain the limitation.

## Project-owned results

Configure `result_records` using the project checkout. For new projects, prefer
`directory: experiments` and `navigation: true`; preserve existing layouts on
historical runs. `repository`, `remote_url`, `branch` and `owner` describe this
project's destination. `sync: true` requires current authorization to commit and
push there; absent authorization, export locally and report delivery pending.
Provide `python` and the exact installed GitLab `sync_script` when synchronizing.

The generic exporter writes `<directory>/<run_id>/round-NN/result.json`, its
readable `report.md`, the run's `index.md`, and optional project `catalog.md`.
Catalog includes failed and pending rounds from all runs in that project,
current branch and commit-history links. It does not overwrite a project's
README. Link to it from the project entry once; maintain it automatically on
each subsequent export. `--include-navigation` adds only this catalog to the
round sync's fixed file allowlist. Delivery errors can be retried independently
of training. Missing optional metrics stay missing; no fabricated values.

Keep source/data/configuration/runtime identities, real results, accepted
receipts, deviations, analysis and artifact locations/digests. Keep data, large
logs, weights and private identities out of Git; retain verified copies before
resource shutdown. BMAD owns generic machinery and framework summaries/links;
the project owns business adapters, metrics and detailed results. A report commit
is not the trained source version. Do not rewrite historical signed evidence or
old installations when changing framework code.

## Choose work by the requested outcome

Use [task-modes.md](task-modes.md) for five-phase progress, purpose-specific minimal
delivery and conditional robustness/baseline work. Task mode and experiment scale
are separate; project resources decide the compatible profile, not global Skill
defaults. Preparation-only work never invokes a training objective for reporting.

The public dependency coordinator accepts `execute-and-learn`, `evaluate-only`
and `baseline-reproduction`. It executes only the applicable graph, not all 21
numbered gates in sequence. Training requests default to no mandatory baseline
reproduction or robustness test. Use `baseline_required: true` or
`robustness_required: true` when the task requires the respective evidence;
`code_change: true` adds implementation and engineering checks. Optional reviews
record findings; merging has its own separately requested authority.

`run_project_pipeline.py` passes these choices to the graph rather than forcing
the settings of a validation project. For a different objective, its project
driver must explicitly declare `supported_objectives`. Additional optional
checks use `additional_task_nodes`; mark `read_only_task_nodes` only when the
driver truly has no platform/ledger mutation. Unknown custom drivers default to
serialized execution. Concurrent read-only work and independent baseline checks
must not become a predecessor of training unless explicitly required.

Real blockers concern the affected operation: unavailable/wrong code or data,
incompatible input/runtime, missing execution authority, exhausted budget, or
uncertain external effects. Weak metrics, optional qualification findings and
failed report delivery do not discard outcomes or halt subsequent diagnosis.
Failures flow to summary, decision and cleanup. Resource owners save environment
when required, stop resources and verify the actual state on terminal outcomes.

The original numbered protocol remains available for that requested deliverable;
do not claim its gates passed from task execution coverage. Generic scheduling
does not make every project's adapter implemented. In particular, the current
bounded Loop retains legacy AMP/LR and RoadModel comparison assumptions: a new
project needs its actual parameter/runtime adapter checked before using that
entrypoint. Do not present this as a universally ready optimizer.

## Select compute from the current project

Apply [training-operations.md](training-operations.md) before new paid execution.
Keep project-specific constraints and defaults in the model repository's owned
branch, with provenance. An external agent context may link them but must not be
the only copy. The resource collection template does not replace a resolved
provider configuration, platform preflight or existing execution authority.

Choose card count before each new attempt from the relevant project's training
example/configuration, available compatible resources and current cost scope.
Keep the chosen count in project configuration, not a universal Skill default.
Treat a user-approved GPU count ceiling as an upper bound, never as the default
allocation or a reason to fill a package. Resolve the project's maximum active
compute tasks and check its existing running/pending attempts before dispatch.
Choose the smallest compatible allocation with useful measured throughput and
acceptable cost; explain a larger choice from actual evidence. Count all roles,
instances and packages against the ceiling, including an otherwise idle master.
Retain a measured failure instead of automatically increasing cards or retries.
Distinguish per-node processes from total allocated cards, and package count from
card count. Record the actual allocation and quote; using fewer processes in a
larger GPU package does not reduce that package's allocation or price.

Changing card count requires a new resolved profile: per-device batch,
accumulation, effective total batch, sampler, optimizer/scheduler clocks, memory,
throughput and budget must be checked. Equal batch arithmetic alone does not
prove numerical equivalence. Do not scale a running attempt unless that project's
elastic execution and recovery protocol has been implemented and verified.
