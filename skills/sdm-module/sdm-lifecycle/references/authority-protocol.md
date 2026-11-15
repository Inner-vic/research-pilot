# Authority protocol

Authority uses Ed25519 public keys encoded as canonical PEM and strict unpadded-base64 signatures. Each consumed attestation stores the complete public-key bytes and content-digested trust snapshot used for the decision.

The signed canonical subject includes action, every governed ID and digest, role, stable principal ID, key digest, issued and expiry instants, nonce, and expected revision. Validate the exact action-to-role mapping, subject equality, signature, key digest, trust membership, validity interval, and effective revocation at recorded `consumed_at`.

## Authenticated platform producer identity

S12 and S13 keep the configured platform integration separate from the natural person or service principal that signs its result. `binding_identity` is the immutable configured Skill binding selected by the coordinator. S13 `producer_identity` repeats that binding exactly; it identifies the integration path, not an evidence author. S13 also requires a non-empty `producer_principal_id`: the stable identity of the platform producer that signs the result. The principal ID must not equal the configured Skill binding identity.

The `platform.result` attestation uses role `platform-producer`, and its top-level signed `principal_id` is exactly S13 `producer_principal_id`. The expected subject also binds `binding_identity` and `producer_principal_id` in `governed_ids`, plus the exact request packet, producer-owned S13 payload, configured Skill digest, adapter-contract digest, idempotency key, and dispatch correlation. Verification uses the coordinator-pinned trust snapshot and requires that the signing key authorize that principal for `platform-producer`; a result cannot nominate its own trust root or consumption instant.

The raw S13 packet, accepted ledger event, and projected handoff record preserve both identities under their distinct field names. Secure replay recomputes the expected subject and compares the authenticated event metadata, including both `producer_identity` and `producer_principal_id`, before accepting the historical result. Missing principals, principal substitution, binding substitution, and using only the Skill binding name as the purported signing principal fail closed.

Whenever a platform result contributes scientific evidence authorship, the coordinator records the verified `producer_principal_id` as the stable author ID. It never substitutes `producer_identity`, a Skill name, an unverified result field, or a self-reported role for that verified principal.

## Signed authorship provenance v1

Actions with scientific separation constraints bind coordinator-derived authorship provenance inside the signed subject. The exact extension is:

```json
{
  "extensions": {
    "dev.bmad.sdm.provenance": {
      "schema": "sdm-authority-provenance/v1",
      "artifacts": [
        {
          "domain": "experiment|implementation|evidence|evaluator-owner|data-split-approver",
          "artifact_id": "E01",
          "instance_key": "the exact ledger instance key",
          "artifact_digest": "sha256:<64 lowercase hexadecimal digits>",
          "authors": ["stable-principal-id"]
        }
      ]
    }
  }
}
```

Every artifact row has exactly those five fields. Rows are unique and sorted by `(domain, artifact_id, instance_key)`; `authors` is a non-empty, sorted, duplicate-free array of stable principal IDs. The allowed artifact families are `E01`, `E02`, `E03`, `E04`, `E06`, and `E07` for `experiment`; `E05` and `S05` through `S09` for `implementation`; `R01` through `R04`, `R06` through `R08`, `D01` through `D04`, and `S10` for `evidence`; exactly `G10` for `evaluator-owner`; and exactly `R05` for `data-split-approver`. G10 and R05 cannot be relabeled into another domain.

The coordinator derives each author ID and artifact digest from authenticated artifact and event history before constructing the expected subject. The `evaluator-owner` row binds the exact selected G10 instance and owner principal IDs. The legacy domain name `data-split-approver` now identifies R05 contributors: new manifests record the authenticated producer principal in `author_principal_ids`; historical manifests can retain `approver_principal_ids`. Producing a split does not imply approving it, and split approval is not a STEP03 requirement. A caller-provided author, unauthenticated free-text role, or S10/D05 role field is not authorship evidence. Exact expected-subject comparison and the Ed25519 signature bind the derived author set and artifact bytes together; replay reconstructs the same subject rather than trusting persisted self-description.

Required provenance domains are exact, not minimum subsets:

- STEP7 `readiness.approve`: `experiment` only.
- STEP19 `readiness.approve`: `experiment`, `implementation`, `evidence`, `evaluator-owner`, and `data-split-approver`.
- `freeze.approve`: `experiment` only.
- D05 `decision.D05`: `experiment`, `implementation`, `evidence`, `evaluator-owner`, and `data-split-approver`.

For STEP19 and D05, the reviewer identity is the verified signed `principal_id`. The signed and trust-authorized `role` still satisfies the action-to-role policy, and distinct-principal/distinct-role checks remain in force, but a self-reported artifact role never substitutes for principal identity.

Replay validates authority at the authenticated historical consumption instant. Later expiry or rotation does not invalidate valid history; revocation effective at or before consumption does. Current human-governance transitions securely read the current snapshot beneath the genesis-pinned trust-root identity, independently of the workspace-created platform-producer snapshot. Two-authority gates require distinct stable principal IDs; separate roles, keys, or signatures controlled by one person do not establish independence.

Freeze, readiness, Amendment, Deviation, cancellation, exception, D05, engine migration, and closure use the exact rules in `assets/authority-policy.json`. Missing OpenSSL or trust configuration is a configuration block, never an approval bypass or evidence-corruption finding; historical replay preserves that classification.

## Closed-world constraint execution

`authority-policy.json` partitions every declared constraint between exactly one executable owner. Constraint names, action names, role lists, and minimum cardinalities are unique and structurally validated. An unknown constraint, a missing execution owner, a policy/executable provenance-contract mismatch, or an incomplete declared constraint set is a configuration failure; it never degrades to an ignored rule.

The authority verifier owns and executes:

- `distinct-principal-ids`
- `evaluator-owner-disjoint-from-implementation-authors`
- `principal-not-experiment-author`
- `reviewer-not-experiment-author`
- `principal-not-implementation-author`
- `principal-not-sole-evidence-author`
- `signer-not-data-split-approver`
- `signer-not-implementation-author`

The coordinator/lifecycle transition executes all state predicates declared by
the retained `assets/authority-policy.json` before invoking the signature gate.
These bind exact scope, child dispositions, immutable protocol history, observed
facts, reciprocal links, engine digests, and independent principals as applicable.
Unknown constraints remain a configuration block; no predicate is silently ignored.

`principal-not-experiment-author` rejects a required authority principal found in the bound Experiment author closure. At freeze, `reviewer-not-experiment-author` applies to the trust-authorized `independent-protocol-reviewer`; it does not prevent a research owner from authoring the Experiment while separately satisfying the owner role. At STEP19, `signer-not-implementation-author` rejects an Evidence Reviewer signer found in the complete implementation author closure. For D05, `principal-not-implementation-author` applies the same stable-principal comparison to the scientific result approver.

`signer-not-data-split-approver` rejects a D05 result-approver signer found in the authenticated R05 approver closure. `evaluator-owner-disjoint-from-implementation-authors` rejects STEP19 or D05 whenever the stable G10 owner-principal set intersects the complete implementation-author set. These checks compare authenticated stable principal IDs and exact artifact digests; the number or spelling of role strings never proves independence.

`principal-not-sole-evidence-author` compares each signed reviewer principal with the union of stable authors over the complete bound evidence closure. STEP19 or D05 is rejected when that union is exactly the reviewer's principal ID. Co-authorship with at least one different stable evidence author satisfies this particular constraint; other independence, role, provenance-completeness, and action-specific gates still apply.

Diagnostics redact private keys, credentials, hidden labels, evaluator contents, operational tokens, and signed-URL query values. Safe field paths, public-key digests, artifact digests, and rule identities remain visible.
