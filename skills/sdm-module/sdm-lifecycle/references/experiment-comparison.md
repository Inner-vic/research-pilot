# Project-owned experiment comparison records

Use for control/treatment results, sequential or parallel. Keep original Base and
ordinary round records. A comparison is a derived evidence view, not an execution,
formal baseline promotion or accepted Pro decision. Design follows
[research-strategy.md](research-strategy.md).

Bind the question, factor and conditions in
[comparison-group.template.json](comparison-group.template.json). Each arm's
`role` explicitly selects control/treatment, independent of list order or arm ID.
`record_identity` selects its exact run/round; `source_commit` and `config_digest`
select intended source/config. Optional `source_snapshot_ref` binds a private
[worktree-isolation.md](worktree-isolation.md) snapshot by path and byte digest.
Commit equality alone does not bind data or training parameters.

Use existing `round_reporting.py` exports (`sdm-round-record/v1`) and fill
[comparison-export.template.json](comparison-export.template.json) with the plan,
both record references, metric directions/units and any actual-field selectors.
Paths are absolute with exact byte digests. A missing arm stays `null`.
Default actual paths are `provenance.data.version`, `provenance.data.split_digest`,
`metric_definitions`, `provenance.fixed_conditions.<condition>` and
`provenance.fixed_conditions.config_digest`. Adapters populate actual fields or
declare selectors in `actual_condition_paths`; selectors never supply facts from
the plan. Retrospective records can remain incomplete.

```text
python -B <installed-skill>/scripts/comparison_reporting.py --spec <comparison-export.json> --output <fresh-group-revision>
```

The exporter checks planned vs actual identity, parameters, source/config,
data/split, initialization, evaluator, training budget, batch, precision, randomness
pairing, checkpoint selection and metric definitions. The declared variants differ
in one factor. More complex studies need their own explicit contrasts. It preserves
failures/missing values and compares finite numbers, excluding booleans.

Outputs are `comparison-input.json`, `comparison.json`, `report.md` and
`export-binding.json`. They retain input locations/digests, outcomes, conditions,
receipt verification and limitations. Re-entry checks output hashes and preserves
bytes/timestamps. Changed evidence/input needs a fresh revision. A partial local
export can finish from the same bound inputs without rerunning an experiment.

| Status | Meaning |
| --- | --- |
| waiting | Missing or nonterminal arm. |
| incomplete | Terminal failure/incomplete run or missing comparison evidence. |
| invalid-comparison | Known identity/condition/metric/contrast mismatch; no delta. |
| paired-observations | Consistent completed exports; successful S13 references not all revalidated. Differences are descriptive. |
| paired-results-ready | Consistent exports with each training/evaluation S13 reference checked through the existing read-only validator. Qualification remains unassessed. |

Round exports are derived, unsigned records. Checking their S13 references does
not authenticate every exported field or replace normal original-evidence checks.
A snapshot reference checks source/config identity; the worktree helper validates
retained bundles and current execution inputs before dispatch.

Report `treatment - control` and a direction-adjusted gain, preserving units/scope.
Failed, missing or inconsistent pairs have no delta. One pair does not establish
causality, a stable gain or project success. Keep actual slices/variation; do not
invent intervals, cost, timing overlap or absent observations.

Send one summary, both evidence indexes and the predeclared question to the fixed
Pro chat. Preserve contrary/failed results; Pro may continue requesting evidence.
Ordinary request/context binding and final decision validation still apply. Archive
the assessed revision and link the next study/successor. Neither export nor report
grants compute, synchronization or promotion; delivery repair is independent of runs.
