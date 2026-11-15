# Platform handoff protocol

`assets/platform-operations.json` is the sole excluded-operation allowlist. The outer coordinating Agent resolves the configured identity to exactly one contained `.agents/skills/<identity>/SKILL.md`, verifies its complete binding identity and Skill digest, activates it with immutable S12, and returns S13. Coordinator Python never imports, shells out to, or performs the external operation.

One logical request has one immutable request ID and idempotency key. Delivery is at least once and reuses those bytes. A composite `all_of` gate creates one request per operation. The owner deduplicates, and the coordinator accepts at most one exact terminal S13. Only `successful`, `failed`, and `cancelled` are producer terminal statuses. `configuration_required`, `invoke_error`, `dispatch_unknown`, and `reconciliation_required` are coordinator dispositions and are never fabricated as S13.

S12 binds the operation, binding and adapter-contract digests, workspace/revision, gate/step, all applicable Experiment/Run/Attempt/artifact subjects, required evidence types and cardinalities, portable paths, creation time, and expected result schema. S13 repeats every anchor plus dispatch correlation, producer identity, receipt digest, exact evidence, and terminal status.

Every S13 also carries an Ed25519 `producer_attestation`. Its `platform.result` subject uses role `platform-producer` and the stable `producer_principal_id`, which must differ from the configured Skill binding identity. It binds the complete result payload (before the attestation/receipt seal), the immutable S12 digest, binding Skill tree digest, adapter-contract digest, idempotency key, result ID, and dispatch correlation. The receipt digest then binds the embedded attestation to the full packet. Verification accepts only the producer snapshot pinned in the workspace-creation payload; the producer cannot select a trust root or consumption time, and a later human-governance trust rotation does not rewrite the platform binding. Replay and formal Resume/Validate repeat verification at the ledger-recorded consumption instant. Recomputing a receipt digest cannot turn manually fabricated evidence into a signed platform result.

A no-result wait remains durable. A retry creates a new request/Attempt linked to the old one. Supersession and cancellation append events without changing old packets. A late result is recorded but cannot advance a superseded or terminal request. Conflicting duplicate results block. `dispatch_unknown` requires reconciliation using the original operation ID; no redispatch is permitted until exact external effect is proven or disproven.

Manual evidence is never a completion path. An uncertain external effect also
forbids blind redispatch: reconcile the original immutable request and operation
ID before any governed retry or supersession.
