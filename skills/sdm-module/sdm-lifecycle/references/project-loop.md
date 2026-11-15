# Bounded project Loop

Use `scripts/run_project_loop.py --project <loop-project.json> --output <loop-root>`
when the user authorizes multi-round diagnosis. Bind an explicit project driver
and [Loop adapter](loop-adapter.md); the generic lifecycle owns budgets, immutable plans,
authenticated observation analysis, one-variable changes and resource release.

Three rounds is a development default, not a generic application limit. Configure
positive max_rounds and loop_timeout_seconds explicitly; segmented studies may
use null max_rounds with explicit stop criteria. Keep one attempt per round. Projects declare their
variables, metrics and fixed comparison conditions. Code and data identities are
required; seed, checkpoint, evaluator, hardware and sample scope are declared when
the project comparison depends on them. Optional missing conditions are recorded.
The initial round observes the initial configuration. Each successor changes
exactly one allowed parameter, with observed
facts, hypothesis and parent decision digest written before execution. Compare
descriptively; tiny samples cannot qualify business effectiveness.

Default decisions use the active model and the
[model analysis protocol](model-loop-analysis.md): inspect real logs, project
metrics and previous predictions; propose alternative hypotheses and one bounded
experiment or stop. Project metrics are configurable, not a universal gradient
gate. New generic Loops require model analysis. Old immutable RoadModel runs
without `loop_adapter` retain their original compatibility path; do not use
that fallback to bootstrap new projects.
Stop at budget, incomplete/uncertain execution, failed evaluation or a model stop
decision. A completed exit0 numerical failure can be retained and studied; it is
never relabeled success. Missing model analysis does not fall back to fixed rules.

Read actual reports only from normally accepted S13 packets, checking workspace,
status, packet hash and observation hash. Pin per-round parameters in S12 and
check preparation and effective runtime values. Reuse exact data evidence only
when content/subset digests still match. A parameter change must not alter model
code, data or unrelated resolved configuration.

The legacy non-segmented Loop owns resource release. Keep the personal space running between
active rounds; stop after terminal Loop outcomes or errors, save environment when
required, and verify the same UID stopped. Inner rounds explicitly delegate
cleanup, rather than claiming resources have already been stopped. Native gaps
may use the already-authorized UI bridge; report that limitation.

Legacy resume after retained cleanup performs no new resource acquisition. A failed or
unverified release requires normal platform reconciliation before more execution;
keep the earlier cleanup receipt intact. After verified release, revalidate saved
observations and finish analysis/planning without re-running the project pipeline.
A missing next-round observation requires a fresh authorized attempt rather than
renewing the old budget. Unknown task effects may be analyzed but cannot support
a run-next-round decision. For segmented studies, completed rounds replay only
verified evidence; a new round uses a new scope-bound resource proposal and
confirmation. Read [long-loop-dependencies.md](long-loop-dependencies.md) before
selecting segmented cleanup or cross-trial reuse. This does not resume a released allocation.

Plans, observations and decisions are immutable within a Loop. Same-entry resume
reuses verified nodes and packets. A started action without a receipt requires
platform reconciliation; do not blindly redispatch. No schedules, main merges,
publishing, extra retries or hidden fourth rounds are created by this entrypoint.
