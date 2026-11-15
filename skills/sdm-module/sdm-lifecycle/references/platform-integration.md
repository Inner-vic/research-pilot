# Governed S12/S13 platform integration

## Scope

This contract applies to the original21-step governed qualification route.
Ordinary34-node project executors may call configured official SDK/CLI adapters;
their logs and resource receipts are not automatically accepted governed S13.
For current implementation/evidence coverage, consult the release's platform
status matrix. Do not treat an old project probe as current account availability
or authorization.

## Four external responsibilities

| Binding | Responsibility |
|---|---|
| data_platform_skill | Labeling status, data version/split and quality facts |
| source_control_platform_skill | Git operations and exact external ledger anchors |
| ci_platform_skill | CI orchestration and engineering check evidence |
| ml_execution_platform_skill | HPO, training, locked and robustness evaluation |

Resolve the configured installed Skill through the BMAD host and authenticate
its exact identity/tree against the workspace-pinned binding. Candidate source
directories and a successful CLI login are not registration or producer trust.
Use the setup registration checker and normal install/update to bind the selected
identities; do not edit a pinned workspace's runtime configuration to bypass a gate.
The release does not supply personal credentials or production signing keys.

## Request, operation and result

1. Resume the governed workspace and obtain its immutable S12 using specctl.
   Bind request digest, operation, object identity, authority and idempotency key.
2. Inspect the chosen platform Skill's supported operation and configured runtime.
   Prefer the official SDK/CLI; use native UI only for a documented capability gap,
   preserving authorization, exact object identity and readback. See
   [external-capability-routing.md](external-capability-routing.md).
3. The outer Agent activates that exact platform Skill. The lifecycle's formal
   control plane creates/validates envelopes rather than executing arbitrary
   provider commands. Never execute a Skill binding as a shell command.
4. Preserve actual operation results, original errors, task/asset/ref locators,
   full requested evidence and terminal accounting. Unknown effects reconcile
   the same operation identity before retry; no duplicate write from an uncertain
   result. Failed or partial collection remains incomplete, not successful.
5. A trusted stable producer principal, distinct from the binding identity,
   signs the complete S13 against the workspace-pinned producer trust snapshot.
   Ingest through the normal handoff command; only accepted validation advances
   the governed gate. A signature alone, CLI text or narrative is insufficient.

Do not provide test-only fake producers or fill missing facts to make signing pass.
Changing a later configuration cannot authenticate an old result. A real platform
approval restriction needs provider evidence; generic human scope confirmation
does not require a fabricated Feishu request.

## Read-only integration inspection

`scripts/platform_handoff_plan.py --catalog` lists the existing formal operation
routing; `--request S12_PATH` inspects a coordinator-generated request. Neither
mode invokes the provider or produces a signed S13. The operation catalog is
independent of ordinary34-node capability numbering.

Track four separate stages: installed registration, implemented collection,
authenticated producer output and accepted gate evidence. Empty bindings return
configuration_required; a provider missing fields remains integration_pending.
Neither is a successful platform terminal status. Model-specific repositories,
branches, data and metrics belong to project configuration, not this reusable contract.

## Acceptance and historical provenance

A real-case acceptance record identifies the loaded Skill trees, repository/source
identity, each actual specctl command/result, S12 path/digest, selected provider
operation, raw result locator, and accepted S13 if any. Verify them against the
ledger and retained platform evidence. Troubleshooting outside this chain is
exploratory evidence, not proof that a governed step passed.

Historical project-specific probes remain in the source project's development
archive, including docs/sdm/evidence. They are not shipped as operational commands
or active links in the portable module. They do not establish a current resource
queue, a completed producer or new execution authority.

Read [handoff-protocol.md](handoff-protocol.md) for immutable exchange and retry,
[authority-protocol.md](authority-protocol.md) for trust and role separation, and
[governed-lifecycle.md](governed-lifecycle.md) for the formal completion contract.
