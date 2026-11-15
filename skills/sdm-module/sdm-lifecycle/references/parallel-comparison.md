# Paired experiments with optional parallel execution

Use when a control and treatment are already specified and neither depends on
the other's result. This is a study-level scheduling option over two ordinary
project attempts, not another lifecycle or a parallel automatic Loop successor.
Retain each attempt's original 21-node coverage and normal S12/S13 checks.

For detached LPAI CLI coordination, exact payload/UID binding, total budget,
monitoring and recovery, read [paired-platform.md](paired-platform.md). Actual
execution requires verified project policy, independent release protection,
native mappings and concrete group approval.

## Choose sequential or parallel deliberately

Start from the project's concurrency policy. If none is established, propose
sequential execution with one active compute task. A project limit of one cannot
be lifted by a study template, a Pro recommendation or permission to edit a Skill.
Present a concrete two-task allocation and obtain changed-scope confirmation when
required. Retain the approved group plan and resolved provider configurations.

Parallel training requires independent allocations and a provider that can submit
detached jobs, query their exact identities and reliably stop each. Submit calls
and shared ledger/CAS writes stay serialized; remote training may overlap after
submission. A synchronous driver or a single-GPU shared devspace does not gain
parallel capability from `read_only=true` or another agent. If the actual provider
cannot support detached independent attempts, report that adapter gap and use
only an approved sequential fallback. Never launch two writers in one workspace.

Two remote jobs do not require two model agents. A single coordinator can own
the group, with independent arm records and monitoring. If delegated analysis
is separately authorized, it does not receive extra submission authority.

## Freeze a comparison group

Copy [comparison-group.template.json](comparison-group.template.json) into the
project study directory and fill it from verified project state. Bind a group ID,
control/treatment IDs, question, changed factor, resolved parameter values,
source/config identities, data/split, initialization, evaluator and metric
definition. Keep effective batch, training/update budget, checkpoint-selection
policy and randomness pairing fixed as the declared comparison requires.
Record unavoidable differences and review their effect before calling the runs
comparable; the simple paired preflight expects exact declared conditions.

Each arm needs a fresh attempt, distinct workspace and output roots, job name,
component/task UID, logs, checkpoints, receipts and cleanup ownership. Shared
immutable datasets/source are allowed. Do not edit either source/config once
submitted or share mutable caches that can change another arm's inputs. Verify
actual mounts/path identities and GPU allocation isolation at normal preflight;
string comparisons alone cannot detect symlink aliases or shared GPU contention.

Use `scripts/comparison_preflight.py --plan <group.json>` before requesting
confirmation. It checks a two-arm plan's declared single-factor contrast,
comparison conditions, path separation and aggregate bounds. Its output is
planning-only, never an execution admission, and it does not authenticate user
approval, inspect source diffs, reserve resources or validate a scientific result.
The filled template is not a provider configuration. Bind converted native
configurations and use existing normal requests rather than executing this file.

## Account for the whole group

Count all allocated GPUs across master/worker roles, and both arms' setup,
compilation, evaluation, idle time, cleanup and billing uncertainty. Record
per-arm duration/cost ceilings plus group peak GPUs, simultaneous tasks, peak
hourly price, total GPU-hours, total cost and wall-time ceilings. Pending/starting
allocations and other owned tasks also consume the project envelope; recheck them
against live state immediately before each submission. No automatic retry or
adding cards is implied by an unused ceiling.

For two equally long jobs with separate equal allocations, sequential elapsed
compute time is approximately `t_control + t_treatment`; ideal parallel time is
approximately `max(t_control, t_treatment)`. Peak hourly spend adds during overlap.
Total GPU-hours remain the sum of both arms in either mode. Total cost need not
increase solely from overlap, but queue delays, interference, a dearer tier,
allocation rounding and idle waits can change it. Treat these as estimates, not
platform reservations or guaranteed speedups.

## Run, reconcile and stop

Before the first submission, persist the approved group digest, each arm's intent
and hard deadline, aggregate reservation and the failure policy. Submit through
normal project/installed platform operations one arm at a time, retain exact UIDs
and verify the receipt before submitting the other. Inspect uncertain submission
outcomes by identity; do not resend or acquire a replacement blindly.

If the second arm cannot start, apply the predeclared partial-start policy: stop
the first, or let it finish within its existing allocation bound as an unpaired
observation. Do not extend the first lease while waiting or call it a completed
comparison. Start skew and changed platform conditions remain in the record.

Monitor both arms with their individual numerical/progress checks and the group
cost/deadline. For a local model failure, stop the failing arm and follow the
declared policy for its peer. A shared input defect, uncertain resource ownership
or group budget violation pauses the whole group and reconciles both resources.
`stop-peer` is the conservative proposal when no failure policy was supplied;
`finish-peer` needs a bounded, useful independent result and confirmed scope.
No failed/missing arm authorizes an automatic replacement.

Persist per-arm pending/running/terminal/cleanup status. On continuation, reconcile
existing UIDs, receipts, deadlines and owned allocations before any mutation;
finished work is not resubmitted. Verify cleanup for every allocated UID even
when its peer failed. Release compute before waiting for Pro. Group status is
`waiting`, `incomplete`, `invalid-comparison` or `paired-results-ready` according
to actual evidence; process exit or one successful arm cannot establish the last.

## Compare and hand back to the fixed Pro conversation

Collect each arm through its ordinary evidence/accepted S13 path. Preserve paired
results, actual settings, failure/queue/throughput records and total cost, including
unavailable evidence. Use [experiment-comparison.md](experiment-comparison.md) to
export a fresh bound group revision over ordinary round records. Missing/failed
observations and incomparable conditions remain distinct; export does not certify
science. One accepted S13 never stands for both jobs. Report per-arm
values and signed differences with metric direction; retain slices, support and
variation. A failed arm leaves the contrast inconclusive even if its peer improved.

Send one group summary and both evidence indexes to the project's fixed Pro chat.
Ask it to test the predeclared explanation, assess confounders and request further
evidence if necessary. Store the group assessment in project state. A final BMAD
decision still binds an ordinary request/context and authorizes no further compute;
the current validator does not certify the group. Only a later eligible ordinary
successor may enter the existing bounded single-variable Loop.
