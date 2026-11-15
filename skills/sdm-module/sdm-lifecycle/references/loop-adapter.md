# Project-owned Loop contract

Keep configuration and the executable adapter in the model project's repository.
BMAD owns coordination, accepted receipt verification, bounded model decisions,
recovery, reporting and cleanup. The adapter owns actual variable application,
platform locators and the signed result layout. Changing its bytes requires a new
run. Normal Skill installation and new S12/S13 bindings apply after Skill updates.

New run configuration includes:

- `project_driver` and `loop_adapter`: `{path: <absolute Linux file>, digest: sha256:...}`.
- `initial_parameters` and `allowed_variables`: exact same nonempty key set;
  booleans, finite bounded numbers/integers, or enums with explicit `choices`.
- `project_metrics`: observation paths such as `evaluation.scores.rmse`;
  `metric_definitions` describes unit/direction/meaning and
  `report_metric_mapping` maps display names to actual fields. No universal
  quality threshold or gradient metric is added.
- `comparison_conditions`: unique `name`, `training_field`, optional
  `evaluation_field`, `expected`, `required` (default true), `across_rounds`
  (default true). At least two required conditions carry roles `code` and `data`.
  Other identities and comparisons come from the project. Cross-round changing
  outputs, such as a newly trained checkpoint, use `across_rounds: false`.
- `parameter_observation_field` (default `loop_parameters`) identifies actual
  effective runtime parameters, not merely requested arguments.
- `continuation_requirements`: optional `{stage, field, equals}` conditions from
  actual observations; `evaluation_required` defaults true. These constrain a
  successor, never suppress failure recording or diagnostic analysis.
- `max_rounds`: 1–3; one attempt per round; `loop_timeout_seconds`: at most 1800;
  `decision_mode: model-bridge`. No predefined successor sequence.
- `cleanup_requirements`: project-defined receipt field/value checks. The driver
  reconciles the same owned resource and releases it on terminal outcomes.
- `result_records`: personal project repository/branch, retained artifact index,
  optional catalog and exact scoped Git sync. No main merge or publishing.

The byte-pinned Python adapter exports four functions:

1. `prepare_round(config, parameters, index, root, fixed)` returns runtime-specific
   config overrides and writes immutable project locators. It cannot override
   coordination authority, budgets, variables, comparisons or result destination.
2. `observation_witness(root, summary, stage)` returns `packet_path`,
   `report_pointer`, `digest_pointer`. Pointers are dot paths or lists of dict keys
   and list indexes. BMAD checks the accepted workspace S13, exact packet bytes
   and observation hash before interpreting the report.
3. `retention_driver(config, root, host)` returns an explicit argv/timeout or None;
   archive real artifacts before cleanup. Transfer failure is visible separately
   and does not discard signed results or block diagnostic reasoning.
4. `cleanup_driver(config, output, host, driver)` returns the existing task-driver
   execution spec (`argv`, `receipt`, `timeout_seconds`). It owns same-UID cleanup;
   UI bridges remain explicit when the platform CLI cannot finish the action.

Adapters are trusted project code, not instructions extracted from model logs.
Do not infer measured values from configuration. Required comparison failures
prevent a new controlled training round but still reach analysis, delivery and
cleanup. Optional gaps are recorded. Same-entry resume verifies immutable plans
and results, reuses task and cleanup receipts, and does not silently retransfer
archived artifacts. Transfer and Git delivery repairs are independent of GPU retries.
Freeze the adapter's returned runtime configuration once per round. Resume reuses
that byte-pinned snapshot rather than recomputing branch heads from later delivery
receipts; normal report commits must not silently change an existing attempt.

The forecast fixture exercises integer depth, enum optimizer and nested RMSE
without RoadModel fields. It is a software contract test, not evidence of a
second project's real platform run. RoadModel's real validation remains in its
personal repository.
