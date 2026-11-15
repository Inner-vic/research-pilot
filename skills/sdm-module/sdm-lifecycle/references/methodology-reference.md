# ML methodology reference and adaptation

Use this index when a research or workflow question needs these methods. The
operative BMAD procedures are in the linked local guides; opening every source
is not a prerequisite for a routine task. Primary sources were checked on
2026-10-14. Living guide URLs are not immutable versions; pin a concrete revision
when a project depends on a specific experimental prescription.

| Primary source | Useful method | BMAD adaptation and location | Applicability boundary |
| --- | --- | --- | --- |
| [Deep Learning Tuning Playbook](https://github.com/google-research/tuning_playbook) | Insight-seeking trials, distinguishing study factors from tuning/fixed settings, fair budgeted comparison. | Declare the question, controls, tuning policy, result branches and total trial budget in [research-strategy.md](research-strategy.md). | Recipes and hyperparameters are domain-dependent; the source does not authorize a BMAD run or guarantee a gain. |
| [Playbook: training length](https://developers.google.com/machine-learning/guides/deep-learning-tuning-playbook/number-of-steps) | Many short trials, fewer longer trials; reconsider short-run conclusions and LR decay when increasing duration. | [staged-training.md](staged-training.md) separates route probes, screening, longer comparisons and confirmation; records actual updates, schedule horizons and evidence-based transition criteria. | Early ranking and short-budget hyperparameters need not transfer. Example duration fractions are not project defaults. |
| [Hyperband](https://www.jmlr.org/papers/v18/16-558.html) | Selective allocation of a declared resource to candidate configurations using early stopping. | Use explicit screening budgets and retain pruned/failed candidates and uncertainty in staged-training; consider longer checks for unverified early ranking when useful. | This Skill does not implement Hyperband or automatic pruning; no guaranteed final ranking or permission for parallel/search jobs follows. |
| [Accounting for Variance in Machine Learning Benchmarks](https://arxiv.org/abs/2103.03098) | Variation beyond initialization alone; meaningful benchmark comparisons. | Retain per-run values and relevant variation sources; use provisional claims until budgeted confirmation in research-strategy. | No universal repeat count, noise threshold or project target is inferred from the paper. |
| [Google Rules of ML](https://developers.google.com/machine-learning/guides/rules-of-ml/) | Sound pipelines, measured goals, training/serving consistency, reassessment when growth slows. | Separate metric/objective/promotion claim; investigate input/evaluation mismatches before a research ceiling. Apply task modes and research-strategy. | Many examples concern online recommendation/ranking; transfer the diagnostic principle, not their features or numerical rules. |
| [The ML Test Score](https://research.google/pubs/the-ml-test-score-a-rubric-for-ml-production-readiness-and-technical-debt-reduction/) ([paper](https://storage.googleapis.com/gweb-research2023-media/pubtools/4156.pdf)) | Data/model/infrastructure testing and monitoring for production readiness; fast integration checks and recovery debugging. | Select affected data/model/restore checks, preserve failure evidence, and separate workflow tests from candidate quality in [task-modes.md](task-modes.md) and execution-recovery. | The production rubric is a review aid, not 28 mandatory exploratory gates or BMAD certification. Tests must address an actual contract/claim. |
| [NeurIPS Paper Checklist](https://nips.cc/public/guides/PaperChecklist) | Reproducible settings, clearly defined uncertainty and compute accounting for reported claims. | Retain data/split/config/selection details; name uncertainty factors/methods when estimated, and account for controls, tuning and failed attempts in research-strategy and parallel-comparison. | A publication checklist does not make every trial a paper or require new compute when existing evidence answers the question. |
| [Git worktree documentation](https://git-scm.com/docs/git-worktree) | Independent branch checkouts in a shared repository. | [Worktree isolation](worktree-isolation.md) binds ownership, freezes exact source/config and verifies retirement/restore; comparison records retain those identities. | Worktrees share metadata and do not isolate GPUs/environments/caches. Managed chat worktrees use host lifecycle tools; the local helper is for project-owned checkouts. |

## Use methods at the right depth

In the [v2 execution graph](five-phase-orchestration.md), these methods affect
executable nodes 24/25, 34 and 32/33 rather than only narrative guidance. Research
records cover factor/control roles, actual progress, schedule/resume, evaluation
selection, variability and total cost, with evidence or explicit unknowns. The
validator rejects unsupported positive assertions, inconsistent schedule horizons
and unverified exact-resume claims; a workflow probe cannot confirm a candidate.
The agent and selected reviewer still judge scientific adequacy. No fixed metric,
epoch count or search algorithm is inferred by these checks.

The training-length and Hyperband sources were additionally checked on 2026-10-15.
BMAD's transition table and planning template are local operational adaptations,
not algorithms or numerical thresholds prescribed by those sources.

The 2026-10-15 implementation review also read Playbook scientific-tuning sections
(parameter roles, search boundaries/infeasibility, curves and adoption), the
variance paper sections 3.1–3.3 (conditional estimates versus repeated selection),
and ML Test Score Infra 2/3/5 (model tests, integration and debuggability).
These are the provenance for the role-dependent comparison, search diagnosis,
uncertainty collection and targeted learning checks in the operational guides.
Fresh generated Pro prompts now carry those procedures; citations are not a
substitute for applying them to a study's actual question.

Before adopting advice, state the actual question, source's intended setting,
expected value, extra effort and concrete procedure it changes. Generalize only
the applicable mechanism. Keep a tested procedure self-contained; cite provenance
and limitations here rather than copying a manual into the main Skill.

For plateau diagnosis, distinguish improving an optimized loss from improving
the metric and satisfying the project's real target. A mismatch may call for an
objective/evaluation review within allowed scope. First resolve known input,
collector or protocol defects; a clean pipeline is necessary evidence, not proof
that the model hypothesis is correct.

For a changed data/model path, select checks that can expose its defect: input
support/shape/unit consistency, a relevant computation, bounded healthy optimizer
updates, or actual save/restore. Run model-dependent checks only in current scope.
For known-failure recovery, reproduce the smallest affected contract and retain
original evidence. A CPU fixture or mocked stop proves its local test boundary,
not real GPU health, platform release or a live Pro return.

For a result used to support a substantive claim, record how the checkpoint was
selected, the comparison protocol, known variability and compute spent finding
the candidate. If uncertainty is estimated, state what was varied and whether
the reported quantity is a spread, standard error or interval, with its method.
If it is not estimated, say so and limit the claim. Include failed/preliminary
search cost in study accounting; listing only the winning run hides effort.

These are BMAD adaptations. Project criteria and existing execution authority
still govern their use. Add or refine a general rule from an observed case,
then validate its affected behavior; preserve prior versions and conclusions.
