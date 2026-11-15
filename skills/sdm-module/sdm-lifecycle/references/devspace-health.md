# Opt-in workload health and CPU trial

Use existing `plan.health_policy`, included in the exact resource/command grant.
The installed stdlib health code is bundled into the existing capsule. No new
node, daemon, scheduler or project adapter is introduced. Absent policy retains
the original behavior; never change a running/frozen task to add this feature.

Policy version1 requires all fields: `stop_authorized:true`, `protocol`, `metric`,
`direction:maximize|minimize`, finite `baseline`, `failure_boundary`, positive
`minimum_change`, `minimum_samples`, `bad_evaluations` and `bad_updates` (both>=2),
`max_overflow_skips`, `interval_seconds`, `sample_timeout_seconds`, relative
`checkpoint_file` and `predictions_file`. This is a declared decision contract,
not permission to change thresholds without the project owner's scope.

Optional `progress_timeout_seconds` must exceed `interval_seconds` and belongs
to the same frozen plan authorization. It measures time since cumulative optimizer
updates last advanced, independently of evaluation traffic. Set it to accommodate
gradient accumulation, validation and long steps; there is no new universal default
or retrofit to existing tasks. Its expiration is `optimizer_progress_timeout`,
not proof of divergence. A fresh metric alone never proves optimizer progress.

Maximize collapse means metric<=failure_boundary AND baseline-metric>=minimum_change;
minimize means metric>=failure_boundary AND metric-baseline>=minimum_change.
Only consecutive same-protocol/name evaluations with sufficient valid samples
count; missing/incompatible data breaks that chain, never means metric zero.
Finite applied updates reset nonfinite-update counts. Skipped AMP overflow has a
separate progress budget; isolated nonfinite loss is evidence, not sustained
divergence. Exceeding the explicit skip budget means no optimizer progress, not
proof that model weights are corrupt. Bind and validate the baseline externally;
the capsule cannot prove a workload's self-reported metric is scientifically valid.

The workload atomically writes `health-sample.json` with exact
`binding` from `SDM_EXECUTION_BINDING`, strictly increasing `sequence`, cumulative
`optimizer_updates`, boolean `update_applied`, `loss`, optional `amp_overflow`,
`protocol`, `metric_name`, `metric_value`, `valid_samples`, and raw-byte
`checkpoint_file_sha256`/`predictions_file_sha256`. Invalid/partial JSON, regressed
updates, stale sequence or incompatible metrics do not qualify healthy evidence.
An explicit sample timeout stops with `health_observation_timeout`, distinct from
proven divergence. Zero compatible evaluations at process end fails qualification
even if the workload exited0. Set cadence/timeout to actual evaluation frequency;
latest-sample observation does not guarantee every training step was observed.

Health state/timeline is under `health/`. Last healthy snapshot requires a finite,
comparable metric and finite applied update plus matching source file digests.
First anomaly is retained independently and never a recovery candidate. After a
recoverable isolated anomaly, healthy snapshots continue advancing; a later bad
episode captures its own first sample in `current_episode`, paired with the latest
healthy sample. The historical first anomaly is not silently relabeled as current.
Recording
or snapshot failure must not suppress an already reached stop decision. Snapshots
are bounded to8MiB each for this CPU trial; arbitrary GPU checkpoint formats/sizes
and complete model/optimizer/RNG correctness are not qualified by this feature.

The authoritative pair is one atomically replaced ZIP: `healthy.snapshot.zip`,
`anomaly.snapshot.zip` or `episode.snapshot.zip`. Each contains checkpoint,
predictions and bound metadata; both sources are validated before commit. The
compressed bundle is also limited to8MiB, alongside each source's8MiB limit and
the caller's collection budget. Flat checkpoint/before/after files are compatibility
exports, not an atomic recovery contract. A failed commit leaves the prior committed
pair; a source failure never replaces the last healthy metadata. Comparison and
collection validate the exact bundle/hash/members/binding, not flat exports.

End-of-process qualification requires compatible evaluations, actual optimizer
progress and a complete committed healthy snapshot without an unresolved latest
snapshot failure. Exit0 with missing evidence is failed qualification with an
explicit reason, not proven training divergence. Transient snapshot failures may
recover on a later valid sample. Collection failure still permits resource stop;
keep evidence-incomplete distinct from model quality. The reviewer receives health
qualification and snapshot errors alongside the process outcome.

Prediction JSON must contain exact `protocol`, same `sample_ids`, same `targets`,
finite numeric `predictions` (<=256). The guard writes `comparison.json` with
delta and a shared-scale SVG (GT black, healthy blue, anomaly red). Changed sample
identity or unavailable prediction evidence is recorded as unknown, not silently
compared. This is a diagnostic view, not a root-cause conclusion or visual quality
approval. The original workload, healthy and anomaly evidence remain separate.

The capsule kills its owned process group on the decision; the coordinator then
collects health evidence, includes termination reason in review, and performs the
existing authorized resource stop/readback. Snapshot failure never claims success;
missing required project artifacts remain incomplete. This is no guarantee that
platform allocation stops after the whole host dies, and is not billing evidence.

Portable finite trial preparation: `prepare_cpu_trial.py --output NEW_ABSOLUTE_DIR
--authorize-local-cpu --authorization-ref ACTUAL_USER_GRANT`, optionally `--mode
collapse` for an explicitly injected metric fault. Preparation makes only a
local-CPU Study and no actual review response; execute through the public entry.
The two-round mode uses real Agent assessment after each actual result; it cannot
claim background Codex wakeup because this offline demo has no live message binding.
