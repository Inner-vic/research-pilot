# Short training, longer comparisons and candidate confirmation

Read when selecting training length, reviewing learning curves or deciding whether
to spend more on a candidate. Apply within the existing project execution and research
modes; these are experiment fidelities, not additional lifecycle nodes. Start with
the least costly observation that can answer the current question. A requested
normal/full experiment is not silently replaced by screening.

## Choose a question and an observation budget

| Scope | Question answered | Evidence required for its next decision |
| --- | --- | --- |
| Workflow/model probe | Does this actual route load inputs, update the optimizer and retain usable diagnostics? | Actual healthy updates, measured memory/timing, applicable save/restore and same-UID release. No model-quality claim. |
| Short screening | Which hypothesis or candidate merits a more representative comparison? | Comparable control/candidate observations, meaningful evaluation support, curves and explicit limits. A weak early score can remain inconclusive. |
| Longer comparison | Do the short-run findings persist with more optimization and representative conditions? | Rechecked ranking, meaningful effects versus observed variation, critical slices and resolved schedule/initialization semantics. |
| Candidate confirmation | Does the selected candidate meet the project's stated target reliably? | Project-required training/evaluation scope, variation, held-out checks and applicable deployment evidence. Missing checks remain open. |

Do not require every row for every task. Reuse applicable evidence. Many short
trials and fewer longer trials is a default research strategy, not permission for
a search, a fixed trial count or automatic successive halving. A diagnostic with
ten updates is not a short scientific comparison merely because it completed.

Copy [staged-training.template.json](staged-training.template.json) into project
planning and link it from the research study. Fill only the relevant stages and
retain unknowns. Before any compute, resolve a concrete stage plan: hypothesis,
control/candidates, data fraction and sampling, starting checkpoint, training
budget, evaluation/checkpoint cadence, selection rule, decision criteria and
aggregate resource/time/cost bounds. This planning record is not a runner input
or a new decision schema. Its labels are not automatic Loop actions.

Choose the first useful scientific observation point from existing curves,
warmup/decay boundaries, evaluation coverage and affordable runtime. If those are
unknown, first propose a bounded calibration; do not guess an epoch count or
promise the final metric. Count successful optimizer updates and examples seen
alongside epochs, microbatches, skipped AMP updates and wall time. Smaller data,
batch or accumulation changes can alter what one epoch/update represents.

Epoch counts such as 20, 100 and 300, budget ratios and promotion thresholds belong
to the project. If an illustrative ladder is proposed, mark it provisional and
revise between studies using actual curves. Separate per-trial budgets from the
study's total, including controls, failed trials, tuning and confirmation.

## Keep the comparison interpretable

Within one declared comparison, use the same intended training budget and preserve
data/split, evaluator, initialization policy, effective batch, precision, randomness
pairing, evaluation points and checkpoint-selection rule except the factor under
study. Use [experiment-comparison.md](experiment-comparison.md) for actual bindings;
a planning field never supplies missing runtime evidence. If training length is
the studied factor, declare that contrast explicitly. Differently tuned recipes
need a declared fair tuning budget, not a claim that all other settings were fixed.

Compare at matched actual progress. When one arm stops early, a matched retained
checkpoint can provide a qualified partial observation; it does not convert the
unfinished planned comparison into a completed pair. Treat time-to-quality and
cost-to-quality as distinct comparisons with matched hardware/measurement rules.
Subset-screening gains remain subset claims until checked on the required scope.

Keep diagnostic validation data separate from protected confirmation data.
Repeatedly choosing candidates/checkpoints on a set makes it a selection set;
do not present it as untouched held-out confirmation. Preserve all attempted
candidates and selection history, not only the winning curve or best seed.

## Decide at a declared review point

Allocate the next budget between breadth (more discriminating candidates at the
present duration), depth (longer training) and resolving uncertainty (targeted
repeats/evaluation). Ask what each option could change in the decision. Extend
duration when the next material question cannot be answered at the present budget,
or when checking transfer is itself that question. An improved short score alone
does not make extending the winner the most informative choice. Early-runtime
questions may already be answered; generalization/regularization questions may
need later optimization. Preserve this reasoning in the stage assessment.

Predeclare the metric direction, minimum useful effect, relevant variation,
critical slice tolerance, adequate support and observation window using project
evidence. Where these are unknown, record the gap and use provisional decisions;
do not substitute a universal percentage, patience or significance test. Repeated
looks are exploratory unless a suitable sequential analysis was specified.

| Observation | Appropriate next choice and qualification |
| --- | --- |
| Input/preflight failure, OOM, nonfinite computation, no actual updates, unusable evaluation or mismatched bindings | Stabilize/diagnose. Retain the failure and reconcile resources; it does not reject the model hypothesis. No blind retry or compensating extra GPUs. |
| Healthy training, but still in warmup, too little slice support, missing control or uncertain variation | Gather the decisive evidence or propose a bounded extension/repeat. Do not eliminate solely on early rank or invent confidence. |
| Comparable improvement meets the predeclared practical criterion, with no unresolved critical regression | Consider a longer comparison, another discriminating trial at this duration, or targeted confirmation. Choose by the next unresolved question; state contrary evidence, transfer risks and what spending more resolves. This is further study, not target achievement. |
| Repeated comparable harm/no useful effect after the relevant observation window, with adequate support | Stop or deprioritize this direction under the declared rule. If short-to-long rank transfer is unverified, label the rejection provisional and consider a budgeted slow-starter check when valuable. |
| Training and validation are still improving at the boundary | More optimization may be useful; propose extending a matched comparison with a suitable schedule. Boundary improvement alone does not prove superiority. |
| Training improves while validation degrades | Check overfitting, selection/split and objective mismatch before extending. More epochs are not the default remedy. |
| Apparent plateau, oscillating rank, or gains smaller than unresolved variability | Diagnose data/support, optimization, evaluator and recurring error groups; consider targeted repeats or interpretable ablation via research-strategy. Do not add modules just to produce a next run. |
| Longer-scope result supports the candidate and relevant confirmation evidence is adequate | Prepare the project's confirmation/promotion decision. Missing height, rare-class, robustness or deployment evidence cannot be replaced by an aggregate score. |
| Hard deadline, cost boundary or loss of a reliable stop path | Stop/cancel and reconcile the owned allocation. Preserve partial evidence; scientific promise does not extend the lease. |

Early ranking is a screening signal, not a guarantee of final ranking. When short
and longer trials disagree, retain both and revise the screening policy. Select a
small representative longer-budget check when justified, rather than automatically
discarding every slow-starting architecture or launching every candidate. Do not
generalize an early instability fix into proof of late-stage stability.

## Resolve scheduler and recovery semantics before extending

Distinguish two valid but different experiments:

- **Prefix of a normal recipe:** keep the original scheduler horizon and stop at
  an earlier actual step. Useful for early runtime/optimization evidence; a prefix
  has not experienced the full recipe's late decay or convergence.
- **Recipe tuned for a short budget:** use an explicitly shortened schedule. Useful
  for that budget; its ranking may not transfer. A long run needs a newly declared
  schedule, not many extra steps at the old schedule's near-zero learning rate.

Record the scheduler's unit and horizon, warmup, decay, stop-at budget and observed
learning rate. Extending the schedule is a changed experimental condition: version
the plan and compare fairly, rather than silently altering a running task. The
existing one-variable automatic Loop cannot disguise training length plus schedule
changes as one compound variable; prepare a supported project-owned study instead.

For continuation, verify checkpoint source/config/data identity, weights, optimizer,
scheduler, AMP scaler, counters and relevant RNG/sampler state. Retain a verified
load/restore result where the recovery claim needs it. Weights-only restart is a
new initialization condition, not exact resume. Restoring optimizer state does not
by itself justify modifying its scheduler horizon. Decide explicitly between
compatible continuation and a fresh matched run; preserve parent checkpoint and
both budgets. A platform pause may continue billing; a process exit is not release.

At a stage boundary, save what the confirmed scope permits, release resources and
review evidence before further compute. Use the existing training-operations and
bounded-runtime paths; a stage clock cannot reset an attempt's absolute deadline.
Within an explicitly confirmed multistage plan, reuse that authority only for its
defined stages and aggregate budget. No automatic lease renewal, retry or paid
waiting for Pro follows from a stage transition.

## Pro review and efficient recovery

Send the fixed Pro session the question, declared stage, actual progress and curves,
control/candidate comparison, LR/initialization semantics, support, known variation,
cost, prior predictions and unresolved gaps. Include failed/contrary observations.
Ask which decision-table condition applies and which evidence could change it.
Pro can ask for more information; an unmeasured effect is not a required guess.

Retain a stage assessment with evidence references, validity, selected/deferred
candidates, reason, uncertainty and the next concrete observation. Final ingestion
uses the existing decision contract: an unexecuted study, missing decisive evidence
or unsupported change uses `stop-analysis`. Use `stop-budget` only when the
request's `remaining_rounds` is zero; a time/cost limit with rounds remaining uses
`stop-analysis` with that concrete reason, preserving the original request.
Only an eligible, bounded single-variable successor uses `run-next-round`.
Neither a template nor a confident review authorizes dispatch.

Record timings separately for queue, startup, preflight/compilation, healthy updates,
evaluation, evidence export, Pro turns and cleanup. Diagnose the slow phase instead
of charging idle waiting to model training. Persist the exact stage/attempt/review
binding and next action before a handoff. Resume collection/export/review from
retained evidence; do not rerun a model to repair a failed upload or missing report.
When the same blocker repeats, retain the attempts and the concrete unblock action
instead of retrying unchanged. See [execution-recovery.md](execution-recovery.md).

## Provenance

The staged-duration and schedule-transfer principles adapt Google's
[training-length guidance](https://developers.google.com/machine-learning/guides/deep-learning-tuning-playbook/number-of-steps).
[Hyperband](https://www.jmlr.org/papers/v18/16-558.html) motivates selective resource
allocation; BMAD does not implement that algorithm here. The evidence routing,
record template, Pro interaction and execution boundaries above are BMAD
procedures. See [methodology-reference.md](methodology-reference.md) for limits.
