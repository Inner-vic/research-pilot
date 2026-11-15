# Explicit governed qualification


- **Create** — call `specctl.py lifecycle create --project-root <project> [--workspace <workspace>] --payload <intent> --expected-revision 0`. When `--workspace` is omitted, the command uses `<resolved-sdm_output_root>/workspaces/<workspace_id>` and reports `<resolved-sdm_output_root>/reports`; an explicit override is resolved only against the supplied project boundary, never the current directory. The command reads the installed `[modules.sdm]` configuration itself, pins the four configured Skill identities and exact installed trees. Intent payloads cannot override runtime bindings. It initializes a workspace with the supplied protocol (default `exploratory`) and identifies the earliest admissible step. Resolve non-empty personal `sdm_active_workspace` through the same project-bounded rule and refuse a missing, ambiguous, linked, special, or escaping identity before activating the sprint-planning hook.
- **Update** — classify the proposed change before mutation. A frozen semantic change must become E06, E07, or a reciprocal successor Experiment.
- **Resume** — validate and replay from genesis, then choose the earliest admissible action by `(lifecycle_step, subject_type, canonical_instance_key)` and report ordered alternatives.
- **Advance** — execute only the next declared transition: synchronously invoke the owning installed SDM Skill, return one human authority wait, create S12 platform handoff packets, complete deterministic control-plane work, or return a sourced block.
- **Validate** — use the read-only validator. For formally anchored workspaces require a current trusted anchor result. Do not repair, normalize, recover, touch, or rewrite anything during validation.
- **Migrate engine** — call `specctl.py lifecycle migrate --workspace <workspace> --payload <migration> --expected-revision <revision> --operation-id <id>`. The payload names an absolute, non-rebound complete successor `assets/` directory, its exact engine-contract digest, and the two required attestations. The coordinator copies the content-addressed bundle and atomically commits `engine.migrated`, projection, and memlog under the old engine. Historical events keep their original pinned interpreter; an unresolved handoff, stale CAS, missing authority, incomplete bundle, or digest substitution blocks with no activation.
- **Interrupts** — route Amendment, Deviation, supersession, D05 supersession, retry, cancellation, exception, archive/closure, deprecation through their named lifecycle commands and authority rules.
- **Handoffs** — read `references/handoff-protocol.md` and, for real GitLab/LPAI/Jenkins integration, `references/platform-integration.md`. Python creates and validates envelopes only; the outer Agent activates the exact configured platform Skill and returns the result. `scripts/platform_handoff_plan.py` is a read-only S12 routing aid, not a dispatcher or S13 producer.

For a real-case Skill acceptance run, follow the execution provenance checks in
`references/platform-integration.md`. A platform action recalled from an earlier
conversation is not evidence that this Skill selected or completed the action.

For an existing model or baseline, treat its code, prior measurements, and experiment plan as source material for the current task. A newly created SDM workspace still begins at STEP01: create an exact S01 requirement record and bind genuine, immutable BMAD Product Brief, PRD, and Architecture identities through S03 before claiming STEP01 completion. Preserve an existing S01/S03/S02 closure if one already exists. Do not invent BMAD document digests, mark an intent paragraph as a governed S01, skip rows by changing the cursor, or issue a later-step platform request merely to bypass missing planning evidence. A baseline-only parameter experiment can keep the architecture fixed and state the single permitted variable; it does not require inventing a new model design.

## Mandatory boundaries

The four external binding domains own only data labeling/quality, Git, Jenkins, and HPO/training/locked or robustness evaluation. The lifecycle ends in scientific follow-up and archive/close; retain G12 and parity review as research constraints. Never execute a Skill binding as a shell command. Never substitute manual evidence for a missing result.

Formal freeze, D05, and closure use provisional event, exact external head anchor, then `anchor.accepted`; the provisional event has no gate effect. At-least-once delivery reuses one immutable request and idempotency key. Retry creates a linked request, supersession leaves the prior request intact, and unknown external outcomes require reconciliation before retry.

`assets/command-contracts.json` v2 is executable policy, not documentation. Its
parser, read/write scopes, locks, conditional journals, external-call
allowlists, emitted events, and handled exits must equal observed public CLI
behavior. Do not claim an unreachable exit or an unobserved side effect.

After every terminal D05 outcome, including formal supported results, step 21
offers a reciprocal successor Experiment or archive/close. No model state gates
this choice. Unsupported historical engine bundles are rejected before mutation;
never rewrite an old ledger as a 21-step workspace.

## Completion contract

Return the exact JSON object emitted by `specctl.py`. Success is `status=pass`; an unsatisfied gate or human/platform wait is `status=blocked`. Include current revision, deterministic next action, ordered alternatives, created packet paths when any, and sorted sourced findings. Never claim completion from narrative output.

## References

- `references/lifecycle-protocol.md` — lifecycle order, readiness boundaries, replay, and interrupt rules
- `references/canonical-data.md` — strict JSON-compatible YAML and canonical digest rules
- `references/handoff-protocol.md` — immutable S12/S13 exchange and reconciliation
- `references/platform-integration.md` — candidate GitLab, LPAI data/ML, and Jenkins Skill bindings and current readiness
- `references/authority-protocol.md` — trust snapshots, signatures, historical validation, and separation
- `assets/command-contracts.json` — exact CLI read/write/effect contract and handled exits
- `assets/verification-manifest.json` — stable acceptance and regression coverage identifiers
- `references/round-records.md` — per-round templates, verified artifact retention and authorized personal-branch Git delivery

For failed segmented cleanup reconciliation and opt-in long-history context compression,
read [session-history-recovery.md](session-history-recovery.md).

