# Research strategy and plateau diagnosis

## Best candidate is distinct from the latest result

For component pipeline v4, the host reconstructs `candidate-registry.json`
from hash-verified comparable results and rechecked predictions. Strict metric
improvement replaces the incumbent; exact ties retain the earlier candidate.
Keep its original result, recipe/Manager plan and checkpoint reference. This is
the best observed adaptive-validation candidate, not proof of statistical or
untouched-test superiority. Preserve regressions and their costs.
When a plan restores a historical base, distinguish the result that triggered
the decision from the recipe used as its experimental control. Report both
the chronological-predecessor delta and `delta_from_experimental_base` with
the approved base UID. A gain over an inferior trigger is not the gain over
the selected incumbent. Retain measured execution cost separately from estimates.

Every proposal names `research_revision.candidate_decision`: `continue`,
`rollback`, `redesign` or `stop`, a verified `base_task_uid`, reasons against
alternatives, a falsifying observation and expected value relative to cost.
Compare against the incumbent as well as the immediate parent and initial
baseline. Continuing an inferior branch needs positive information value;
do not make another parameter adjustment merely because it was the last run.
Rollback means restore a known recipe as the base for a new informative
intervention, not repeat its UID, deploy it or silently warm start its weights.
Redesign must change a substantive model/representation/training/data assumption;
similar aggregate scores alone do not demonstrate an architectural ceiling.
Both reviewers and the implementation worker receive actual selected-base code.

Pipeline v4 requires a project-specific metric target and explicit termination
policy. Do not invent a submission budget when the user requests continuous
iteration until the target. `decision_policy.version=2` selects
`termination:{mode:"until-target",source_ref:USER_INSTRUCTION}` and records
`budget:{known_task_uids:[...]}` for accounting without a trial cap.
`remaining_submissions:null` means no numerical limit, never exhaustion. Continue
from actual results while the target is unmet; goal attainment is the normal
research exit. User pause/withdrawal and unresolved original execution identities
still stop actions through their existing gates. Failed scientific hypotheses
require a new informative plan, not an invented count-based exit.

Only an explicitly scoped bounded run uses legacy decision policy v1's
nonrenewing submission budget. Count every known newly submitted UID, including invalid or failed
attempts, not just accepted scientific rounds. Goal attainment and budget
exhaustion are separate exits: a reached target is `goal-met`, an exhausted
budget below target is `stop-research` with goal unmet. Always retain the best
candidate. Legacy minimum round counts qualify an execution chain only; they
are not a quality target. User pause and unresolved original intents retain
their original guards. No number of bad rounds automatically forces a new
architecture or authorizes another submission.

## Continuous goal-level iteration

Separate the project quality goal from the lifetime of one frozen experiment or
parameter study. `research_iteration:{version:1,objective:...,targets:[{field:...,
operator:">=",value:...}]}` opts a new frozen Study into continuous research.
The project supplies metric units and targets; do not invent a universal score,
trial count or stop after a fixed number of near-identical results. A missing
target can be clarified while planning/implementation continues.

In the existing result A/B, `next_step.research_direction` chooses
`continue-plan`, `refine-current`, `revise-plan`, `diagnose`, `goal-met` or `stop-research`, with
registered evidence, the questioned assumption, intervention level and expected
decision value. Assess comparable diminishing returns, train/validation behavior,
failure structure and unmet objectives to select the scale. Explain why another
parameter trial is informative against a more substantial alternative. Consider
model, representation, training method and data/measurement changes when relevant;
do not force architecture replacement from a clustered aggregate score alone.
Use `continue-plan` for an already specified arm of the same comparison; a
representation control is not automatically hyperparameter tuning. It still
requires the original eligible parameter, remaining scope and proposal gates.

For a direction change, close and clean up the current execution scope; keep its
scientific result. The original worker records `research_complete:false` and
automatically opens the existing condition-driven proposer/critic gate. Its
`research-plan.json` and `implementation-request.json` are bound intermediate
outputs. The active execution Agent must implement/diagnose the selected plan,
use original code review and proposal/preflight/resource contracts for the new
frozen version, execute remotely, collect and reassess. Keep repeating this loop
while the objective is unmet and authorized progress is possible; do not yield
the project as completed because a proposal or local Study was archived.

Use `--research-replan` with a fresh read-only replan configuration to reassess
already consumed historical results without changing their frozen runs. The same
original A/B, shared sources, supplements, receipts and conditional exits apply.
`agent_continuation_required:true` directs implementation, not a new user approval
or a platform permission. Actual metric checks reject a claimed goal-met below a
declared target. A stop with an unmet goal must retain its factual reason (for
example an external blocker or withdrawn scope), never masquerade as success.
Switching the method does not automatically renew an execution budget or expand
resource authority; the new exact plan still uses the user's actual project scope.

When a critic needs evidence, resolve the stated decision gap rather than sending
the unchanged proposal again. Existing registered files can be read with bounded
supplements. New external facts or changed implementation require a fresh frozen
evidence subject retaining the exact prior objections and known call outcomes;
do not rewrite its original snapshot. Supply complete relevant code or explicit
line fragments when the shared-input limit omitted decisive logic. A concrete
checkpoint/mapping/provenance can establish an initialization hypothesis, while
its future accuracy remains unknown until the experiment. Review traces produced
after a snapshot are reconciled by the host, not promised as preexisting evidence.

Use for diminishing gains, persistent error groups, uncertain module value,
ablations or a proposed research-direction change. These are analysis modes in
the existing lifecycle, not new numbered nodes or execution authority. Concrete
targets, thresholds, model scope and budgets belong to the project. This guide
is self-contained; an agent need not open the source papers to follow it.

## Choose the question before the next configuration

When comparing training duration, freeze the learning-rate schedule independently
of the stopping epoch. Predeclare a matched-prefix check and distinguish final-
epoch deterioration from best-checkpoint selection gain: the maximum over a
longer trajectory cannot provide a reachable deterioration test by itself.
Use metric resolution when implementing inclusive thresholds, and keep invalid
execution separate from an otherwise valid but nonmatching prefix.

For A/B controls, a valid JSON response and a real call receipt are insufficient
if the Schema accidentally constrains the rationale itself. Keep field-specific
constraints independent of shared mutable Schema templates. Negative controls
must isolate their intended rule from earlier integrity failures; otherwise a
passing test can conceal a missing scientific check.

### Follow evidence to the assumptions that may have failed

Start from the strongest available observation, a known defect or a consequential
change; there is no universal order of model, data and optimization checks. Ask
which conditions made the existing method work and whether they still hold.
A changed task, representation, dataset or module interface can invalidate an
old assumption; a recurring error can motivate this question without a known
change. Do not invent a change or treat an upstream change as proof of causation.

Treat the current Base as a comparison reference whose suitability can be
questioned. Ask what information and structure the task requires and how the
method obtains it. Relevant properties can include
geometry/topology, local detail, context, temporal dependence, invariances,
supervision and deployment constraints; do not audit every property for every task.

Trace the actual path: observations → representation → feature acquisition and
alignment → fusion → prediction/objective → measured outcome. A mismatch may sit
between individually capable modules. Increasing backbone capacity or global
context does not by itself restore a broken spatial correspondence, and a model
that has not learned under a given recipe has not thereby reached its ceiling.

Use credible observations to choose between implementation/data/measurement
defects, learning/optimization limitations, and representation/information/model
assumption mismatch. Do not always blame the architecture first or require a
long diagnostic ladder before a justified architecture comparison. Keep only
decision-relevant alternative mechanisms inside the current plan, and select
an action whose outcomes distinguish them. Options include a direct repair,
sampling/fusion change, representation/objective change, backbone or family
replacement, as well as tuning. Pretraining, capacity, resolution and adaptation
can confound a replacement's benefit; a better candidate is not proof of its
proposed causal explanation. Name what is supported and what remains uncertain.

Proposer A should connect observation/change → possibly failed assumption →
mechanism → intervention → outcomes that change the next choice. Critic B should
challenge the weak links, relevant counterevidence and competing explanations,
including Base suitability where relevant. Neither must invent a mechanism for
a demonstrated defect or resolve every causal uncertainty before comparing useful
candidates. Use the existing proposal and review, not another checklist, graph
or required form. Preserve the original Base and actual scope when a different
model is selected.

### Continue the current research line before opening another

Restore the agreed question, selected plan, latest usable observations and current
decision. Continue that line by default: question → selected plan → observation →
assessment → justified revision → next action. A plan revision, a parameter trial,
an execution retry and an engineering work item are not automatically new research
branches. Contract, browser and performance checks within one implementation plan
are work/evidence under that plan, not separate scientific directions.

Consider plausible alternative explanations inside the current proposal, then
select the next action with the best decision value. Do not turn every explanation,
diagnostic category or reviewer suggestion into an execution chain to verify in
sequence. Useful existing evidence or a direct repair may already settle the choice.
Matched control/treatment runs can serve one question without becoming separate
research lines; keep the comparison and aggregate budget explicit.

Open a branch only when actual evidence or a decision-changing uncertainty calls
for a separately scoped question that continuation of the current plan cannot
adequately resolve. State its trigger, the question/outcomes that change the main
decision, scope/cost and stop condition, and whether results return to the main
plan, replace it or cause abandonment. Reuse the existing proposal/review record;
this is not a new form, numbered node or execution permission. Explain any necessary
parallel work; do not assume it is valuable just because resources are available.

The proposer and a distinct critic model should first challenge and improve the
selected plan. More review turns should narrow decisive uncertainty, resolve
contradictions and improve the next action, rather than produce a longer menu of
trials. Keep unselected alternatives as reasons/notes, not branches with implied
execution commitments. When the main line loses support, retain the negative result
and explicitly stop or switch; mainline continuity is not a reason to persist blindly.

Record parent plan/revision and the result that motivated continuation. Show this
explicit lineage as the main exploration view. Keep supporting material, ordinary
discussion, checks and retries attached in details; expose a real branch when it is
needed. Preserve a complete-history view, hidden-record counts and contrary evidence.
Do not invent sequential edges from timestamps or relabel historical work silently.

The shared planning prompt applies to new projects and every successor, not a
particular model, provider or score. Its consumer is the next executor: the
selected action needs a sourced objective, a reason it beats material alternatives,
the uncertainty or defect it addresses, and observations that change the next
choice. Use only the research modes that matter to the case. Do not force a
candidate count, a diagnostic phase, a trial or a universal performance threshold.

Distinguish valid observations, model quality, platform completion and execution
readiness. A usable early checkpoint evaluation can guide a successor even if
the planned last epoch was not reached or the job later failed. Unknown resume
integrity blocks that resume; it does not invalidate retained metrics or forbid
a separately scoped fresh-start proposal. Required execution and cleanup gates
still apply before dispatch. An unmet target does not imply a known mechanism.

Initial plan requests, locally produced evidence requests and generic Loop
analysis requests pin the common guidance as `planning_prompt_version` and
`planning_guidance`. Fresh offline/optional Pro prompts use
`sdm-research-review/v6`; historical requests and v1/v2/v3/v4/v5 bundles retain their
original prompt bytes. The code binds instructions and factual provenance; it
does not determine scientific truth or certify that a suggested experiment is
valuable. Pro is one optional model backend. Use real model A/B calls and their
sources for proposal critique; a saved Pro binding does not select it. Choose
the backend for a fresh attempt and keep that choice during its dialogue.

Restore accepted evidence and research history. Record the selected mode, reason
and supporting evidence references in a project-owned study:

| Mode | Relevant evidence | Useful next output |
| --- | --- | --- |
| Stabilize | Actual input/runtime/collector failure or unreliable measurements. | Repair and affected checks; no scientific plateau claim. |
| Improve | Comparable observations support a bounded improvement hypothesis. | One falsifiable successor within the existing Loop contract. |
| Diagnose | Apparent saturation or recurring errors with unresolved explanations. | Error breakdown and the smallest discriminating observation. |
| Study | A concrete question with interpretable controls. | A bounded ablation, data, optimization or representation study plan. |
| Confirm | A promising candidate with adequate promotion evidence. | Targeted repeats, full-scope comparison and deployment checks. |

One disappointing score does not establish saturation. Check metric direction,
unit/aggregation, evaluator, data/split, coverage, checkpoint selection and relevant
training conditions first. Gains below the project's meaningful effect/noise range,
recurring error groups or exhausted evidence for the current direction are reasons
to reassess. Missing uncertainty or comparable history means plateau is uncertain;
propose diagnosis rather than declaring a ceiling. Do not invent a universal trial
count, metric target or seed-repeat count. Switching modes never renews a budget.

An exploratory trial can be useful without increasing the main score when its
predeclared outcomes discriminate competing explanations. Record information
gained and the resulting next choice separately from model quality. A crash or
invalid comparison is inconclusive about the scientific hypothesis, even when
it exposes an engineering defect.

## Keep original and current research baselines

Retain the original Base. A current best configuration is a candidate research
baseline until comparison and promotion criteria are met. Bind source/config/
data/evaluator/checkpoint identities, metric scope, selection procedure and
observed variation. Reuse trustworthy measurements when appropriate; naming a
baseline does not require another run or imply formal baseline locking. Never
rewrite earlier baseline artifacts.

Before concluding that no usable research reference exists, inspect a bounded
project-owned index of completed experiments, not only the latest diagnostic.
Classify references by source, initialization, data/evaluator scope and observed
optimization progress. An older, larger run may be a useful provisional reference
without being a matched control. For checkpoint-based fine-tuning, look for a
retained before-update evaluation of that starting checkpoint on the same scope:
a better endpoint than another trial can still be worse than initialization.
If no such record is found, state the searched scope and uncertainty; do not
invent a starting score, claim an exhaustive absence or launch evaluation merely
to fill the index. This inventory supports choosing research work, not a new
mandatory baseline run or a gate on unrelated tasks.

Prefer paired comparisons on the same examples and declared variation settings.
Retain per-run values, failures and search effort, not just each variant's best
score. Initialization, sampling, augmentation and hyperparameter search all can
affect the apparent gain; seeds alone do not cover them. Use existing variation
evidence or budgeted targeted repeats for a candidate; otherwise report a
provisional comparison. Controls, tuning and confirmation repeats all count as
compute attempts. Protect any held-out confirmation set from repeated selection.

Before adopting a candidate, compare its supported benefit with added training
and serving cost, memory, latency, implementation complexity and maintenance.
Use the project's constraints and accept unresolved tradeoffs explicitly. A small
gain is not automatically worth a new module, and removing an unhelpful component
can be progress without a score gain. A provisional research baseline may support
further investigation without being a formally qualified release.

## Diagnose with existing evidence first

Break errors down by relevant classes, conditions, distance/scale, boundaries,
confusion directions and valid-support counts. Report denominators and uncertainty;
few rare examples or unavailable predictions cannot justify a general conclusion.
Retain full-scope results alongside focused slices. Tiny convenience subsets do
not represent the target population. Do not add slice gains together to explain
a nonlinear aggregate metric.

Consider measurement/pipeline defects, data or label coverage, optimization,
generalization, representation and objective mismatch when supported by evidence.
Distinguish the optimized loss, reported metric and actual promotion/product
target. If they disagree, quantify the mismatch before adding model complexity;
objective/evaluation changes still need current project scope and new comparison
bindings. See [methodology-reference.md](methodology-reference.md) for the source
principles and their applicable settings.
Curves and support checks can narrow explanations, but do not prove them. Use
verified failure examples; producing new predictions still needs current scope.

Maintain hypothesis IDs, mechanisms, supporting and contrary evidence, confounders,
questions, discriminating result branches and continue/abandon conditions. Record
supported, rejected, inconclusive or invalid-for-comparison with its evidence.
Revisit rejected directions only when a new fact or changed condition explains why
the previous result may not apply; a rewritten prompt is not new evidence.

Prefer a direct repair for a demonstrated defect, an existing valid measurement
for a settled question, and a discriminating observation for a consequential
unknown. Explain why a rejected alternative is not worth the current cost;
record the new evidence required to revisit it. Reducing LR, extending duration,
changing class weights, expanding GPU allocation and adding modules are hypotheses
or engineering choices, never default prescriptions for a disappointing score.
If task objectives trade off, expose the tradeoff instead of inventing a utility
function or choosing the metric that makes a candidate look best.

After a run, compare the predeclared observation with the actual result and update
the hypothesis/history before choosing another action. No quality gain may still
resolve a useful question; an invalid comparison provides no verdict on the
scientific hypothesis. Repeating failed directions without changed evidence is
not useful iteration. Only an actually executed successor with its result closes
the execution loop; a plan or an accepted review alone does not.

### Evaluate the prompt, not its formatting

Use fixed, diverse case inputs to compare a short baseline prompt and the current
prompt under the same model/tool conditions. Include partial valid runs, unknown
mechanisms, measurement/data faults, interacting settings and a case where the
goal is already met. Score unsupported diagnoses, avoidable repeated computation,
missed alternatives, actionable/falsifiable comparisons, outcome-dependent choices
and human corrections. Retain all outputs and evidence citations. Check separately
whether selected real experiments improved model quality or useful knowledge.
An author-reviewed example or CPU integrity test is not an independent behavioral
comparison, and neither proves broad improvement in experimental effectiveness.

## Design interpretable studies

For short-to-long training decisions, read [staged-training.md](staged-training.md).
Record the stage plan reference in the study; compare actual progress and resolve
schedule/initialization semantics before interpreting a gain or extending a run.
Its evidence-based decisions use these same research modes and existing actions.

Copy [research-study.template.json](research-study.template.json) into the project's
own study directory. Nulls and empty lists expose missing input. This is a planning
record, not a task, Loop configuration, governed S02 or executable request.

### Translate the question into a fair comparison

Choose the claim before choosing which settings to freeze:

- **Fixed-recipe effect:** change the studied component while preserving the
  recipe. This is a cheap, interpretable first observation; report that it says
  nothing conclusive about the component's best attainable performance.
- **Best achievable under a declared budget:** tune interacting settings for each
  alternative under an explicit fair search policy. Keep every trial, failed
  region and selection rule. Compare the selected recipes with appropriate
  confirmation, not one alternative's tuned winner against an untuned control.

For each relevant setting, record its role and reason: the scientific factor,
an interacting nuisance setting, or a fixed condition. These roles depend on the
question, not on the parameter name. A model-size change can require separate LR
or weight-decay tuning; changing optimizer can change the useful LR range even
when the field has the same name. Fix a nuisance setting when evidence supports
weak interaction or when the caveat is acceptable for the budget. If separate
tuning is unaffordable, narrow the claim instead of declaring the architecture
inferior. A removal ablation measures a recipe-conditional effect unless the
remaining model was fairly retrained/tuned; interactions can reverse conclusions.

The single-variable automatic successor is an execution contract. It is not a
scientific rule that nuisance settings must always remain fixed. Use a declared
sequence of project-owned trials for a coupled study, within current authority;
do not pack several settings into one compound variable to bypass the contract.

### Learn from the study before ranking winners

Inspect attempted configurations, infeasible regions and actual train/validation
curves, not just the winning metric. For a sweep, plot metric versus each varied
parameter with failed points visible and identify the selected recipe per variant.
When nuisance settings were tuned, also compare each scientific setting's best
observed recipe, labeling unequal/sparse search and selection bias. A plot is an
analysis aid, not a substitute for repetitions or proof of a global optimum.

| Study finding | Decision it changes |
| --- | --- |
| Best valid points cluster at a range boundary | The optimum may be outside the search. Propose a justified range/scale change rather than another architecture. If the neighboring region is numerically unstable, investigate stability instead of expanding blindly. |
| Too few points, unequal tuning effort or a large unexplored region | Weaken the conclusion; narrow the research question or allocate an affordable balanced search. More trials are a proposal, not a requirement. |
| Many infeasible points | Classify runtime/input defects separately from numerically infeasible recipes. Repair defects or refine the relevant conditional range; retain failures and cost rather than deleting them as outliers. |
| A larger candidate loses while its best trial overfits | Check interacting regularization and selection before rejecting capacity. Poor optimization can accidentally regularize a model; a smaller LR win does not by itself identify the best architecture. |
| Curves improve at the budget edge, plateau early, or change rank across budgets | Decide whether more depth, fewer steps, a revised schedule or additional candidates at the same duration is informative. Follow staged-training; do not automatically lengthen the winner. |

After this validity/search review, evaluate the original hypothesis. Preserve
which explanation each result supports or contradicts and which question remains.
No outcome-dependent change to the declared study can rewrite its original claim;
use a new revision and preserve the exploratory analysis.

### Spend on the uncertainty that affects the decision

Distinguish within-run checkpoint noise, retraining variation, finite evaluation
support and variation from choosing hyperparameters. Repeating the selected recipe
with new seeds estimates conditional retraining variability; it does not repeat
the search procedure or establish its reliability. Resampling frozen predictions
probes evaluation uncertainty, not optimizer/search variation. Select the cheapest
valid observation addressing the decisive gap rather than prescribing more seeds
for every problem. If it is unaffordable, retain a provisional conditional claim.

Pair control/candidate observations on declared examples/randomization where
appropriate. For correlated sequences/scenes, identify a defensible independent
sampling unit before using uncertainty estimates; treating all neighboring frames
as independent can overstate evidence. Do not apply an IID bootstrap mechanically,
change a locked split or claim a confidence level without a justified method.
The sampling-unit choice is a domain adaptation, not a threshold supplied by the
benchmark paper. Final adoption needs uncertainty proportionate to its impact,
not complete statistical certainty for every exploratory choice.

### Select the discriminating study

Distinguish the factor under study, settings needing adjustment for fair comparison,
and fixed conditions. Declare whether variants use the same fixed recipe or are
separately tuned under the same declared search budget. A fixed-recipe comparison
does not establish each architecture's attainable best performance. Declare the
tuning policy before seeing outcomes and report unequal or absent tuning.

| Study | Design and interpretation boundary |
| --- | --- |
| Inference intervention | Disable/replace a component in an existing checkpoint with a valid path. Measures dependence/sensitivity, not retrained attainable performance. |
| Retraining ablation | Declare initialization, retraining scope, training/tuning budget and evaluator. Effects depend on the recipe; removal can change capacity, compute or optimization. |
| Interaction | Predeclare variants and paired contrasts. Both/A-only/B-only/neither is useful only when meaningful and affordable. Do not sum isolated deltas as independent contributions; missing cells leave interaction unknown. |
| Data/label | Audit actual coverage/labels first. Keep evaluation references fixed; changed labels require a new data version. Label agreement is not a metric's mathematical upper bound. |
| Optimization/generalization | Use actual convergence, train/validation and slice results. Separate insufficient optimization from capacity or overfitting; one curve cannot establish the cause. |
| Ideal-input substitution | Use privileged intermediate information only in an authorized diagnostic. Identify where it enters and changed distributions. A gain is a clue, not a deployable score or guaranteed reachable bound. |
| Simplification/literature alternative | State the failure mechanism, applicability and deployment cost; test the smallest distinguishing change. A published gain does not transfer automatically. |

Rank experiments by discrimination between live explanations, decision impact,
feasibility and cost. State different next choices for the main possible outcomes.
If no outcome changes a decision, clarify another purpose or defer the experiment.
Use available evidence and no-model checks where sufficient. Small runs screen
feasibility; confirm scientific gains at the project's required experiment scope.

Existing automatic Loop successors still change exactly one allowed variable and
retain their bounded round budget. A matrix is advisory: do not bundle changes
into a compound variable, silently retune unrelated settings or exceed the adapter
contract. Studies needing code changes, tuning or independent trials require
separate project-owned attempts with explicit contrasts and current scope. Obtain
any required authorization for the concrete study/resource plan and reuse it only
within those bounds. Concurrency follows project policy, not matrix size.
For a ready independent control/treatment pair, use
[parallel-comparison.md](parallel-comparison.md) to propose sequential or parallel
scheduling, explicit group resource caps and per-arm recovery. A comparison group
does not convert the existing automatic Loop into a multi-successor executor.

## Pro discussion and outcome

Use the fixed conversation and [pro-review-dialogue.md](pro-review-dialogue.md).
Ask Pro to assess mode, metric gap/reliability, competing explanations and the
most discriminating study or evidence question. Include contrary and negative
results. Do not require a model modification or a first-response decision.

The final response retains existing BMAD fields and exact evidence/context binding.
An optional `research_assessment` or study reference in `next_experiment_proposal`
is advisory. The current validator does not certify a matrix or causal conclusion.
Use `stop-analysis` for unexecuted studies, incomplete evidence or work outside the
single-variable route. The existing validator accepts `stop-budget` only when
the request's `remaining_rounds` is zero. A time/cost stop with rounds remaining
uses grounded `stop-analysis` and states the exhausted execution boundary; never
edit the immutable round count to force acceptance.
Only an eligible bounded successor uses `run-next-round`. A mode selection or plan
does not approve compute, change project restrictions or declare a target achieved.

After a trial, compare predeclared predictions with actual observations and retain
a new hypothesis assessment. Decide to continue, change direction, confirm or stop.
Promotion needs the project's metric, subgroup, uncertainty and deployment checks;
a lucky best run or orchestration success cannot substitute for them.
Select these checks by the candidate's actual claim using [task-modes.md](task-modes.md).
When estimating variability, record the varied factors, statistic and method;
otherwise state it is unmeasured. Study cost includes unsuccessful/preliminary
trials and search effort as well as the selected runs.

## Persistent missing classes in retained predictions

Before proposing more training for zero-recall groups, use available bound
predictions to separate absent GT support, no exported prediction and predictions
assigned to other classes. Bind scene/frame, GT and prediction bytes across
conditions; report class support by scene as well as points. A class can have
zero true positives while being predicted elsewhere, so count both. Select an
explicit coordinate/grid matching rule; reject ambiguous duplicates rather than
silently choosing a label. Keep any new diagnostic distinct from official metrics.

Sparse visualization exports can drop empty, unsupported or nonfinite outputs.
Do not label all missing exported points as a specific empty class or infer raw
scores, masks or optimizer behavior from their absence. If comparing height on
common exported points, report each run's support, intersection and exclusions;
the conditional error does not replace the original evaluator's full metric.
Use these findings to choose a targeted supervision, support or output-mechanism
question. Zero scores alone do not identify a root cause or justify longer runs.

## Methodology provenance

Adapted from insight-seeking studies and fair budgeted comparisons in the
[Deep Learning Tuning Playbook](https://github.com/google-research/tuning_playbook),
and variation-aware comparison in [Accounting for Variance in Machine Learning
Benchmarks](https://arxiv.org/abs/2103.03098). The mode routing, project records,
Pro dialogue and execution boundaries are BMAD-specific procedures. Links are
attribution; the operative instructions are above.
