# Experiment contract

E01 binds identity, the exact S01 Product Requirement/JIRA digest, the exact
S03 Product Brief/PRD/Architecture `{kind, uri, digest, revision}` links, owner, question,
falsifiable hypothesis, mechanism, uncertainties, non-goals, baseline/treatments,
`change_only`, frozen/forbidden variables, data/geometry, budget, seeds, metrics,
slices, exhaustive decision and missingness rules, evidence, completion, and
authority. E03 is conditional HPO. E04 enumerates Runs. E06/E07 are ID-keyed
append-only objects with exact original digest, timing boundary, materiality,
scope, rationale, authority/disposition, and downstream effects. Successor links
must be reciprocal and avoid circular content hashes.

STEP06 must derive S01 and S03 from S02's immutable `source_refs`; selectors
are equality assertions and cannot introduce a different planning object.
