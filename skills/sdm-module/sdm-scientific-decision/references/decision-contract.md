# D05 contract

Bind decision ID, Experiment/E01 digest, reviewed D01-D04/S10 bundle, frozen rule
and rule-evaluation trace, canonical outcome, selected treatment if any,
limitations, all planned Run/Attempt accounting, reviewers and roles, consumed
authority/trust snapshot, provisional event hash, anchor
request/result, accepted event, and supersession links. The terminal D05 always permits scientific follow-up
or authorized archive/close, independently of research protocol metadata.

Every current-engine D05 includes `decision_context` with `frozen_rules`,
`planned_runs`, `attempts`, and `evidence`. The central
`contracts.evaluate_scientific_decision(frozen_rules, planned_runs, attempts,
evidence)` evaluator must recompute the exact outcome and rule digest before
publication; later read-only `specctl validate` reruns that semantic validator.
It consumes governed boolean predicate facts and never computes or fabricates
platform metrics. Missing/nonterminal accounting blocks; system failure remains
an Attempt fact; protocol invalidity is separate; valid underpowered evidence
is `inconclusive`.
