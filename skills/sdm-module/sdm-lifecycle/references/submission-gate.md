# Shared submission and lifecycle acceptance

New ordinary studies use initialization v3/workload v2 and the
[shared SDM acceptance adapter](sdm-governance.md). Both first and successor
submissions retain this existing operational verifier, adding actual contract,
baseline, experiment-kernel and nonrenewing budget acceptance before the original
Manager effect. The core registers Run/Attempt durably before apply. Existing
Guard intent still reconciles without new reviews or another submit.

The following operational controls remain applicable; they are not a claim of
formal SDM artifact/ledger qualification. Legacy freezes keep their own policy.

Gate v1 is the rc6.37 compatibility policy. The rc6.38 candidate initializer
emits gate v2 and the [shared original S01/S02 checks](sdm-governance.md).

SDM remains the governing model-development framework. The component adapter
consumes existing review, payload, authority and gate validators; it does not
issue D05 or replace the complete governed lifecycle.

## First and successor execution

`research_initialize.prepare()` emits `submission_gate:{version:2}` and
`initial_submission:{version:2,authorization:REF,confirmed_plan:REF}`. Preparation
freezes the first payload and original Manager plan; it calls no model and
submits no task. Explicit dataset preparation remains a separately journaled,
authorized asset operation.

The resident prepares two original `proposal-review` jobs: scientific review of
the exact initial intent, then code/evaluation/execution review with the accepted
science receipt. `reviewing-initial` records `initial_review_phase=science|code`.
Both dialogues use the existing Generator–Critic producer/critic, shared context, supplements
and condition-driven outcomes; two review subjects do not mean two fixed turns.

The supplied initial code is already frozen. A critique may clarify its
justification, but cannot silently edit its plan, payload or resources. If these
must change, block that intent and prepare new bound inputs through Codex; do not
approve a different intervention in prose or mutate the accepted files.

Successors keep the existing result-driven science review, native implementation,
freeze, current preflight and code review. Both first and successor submissions
enter `ComponentPipeline.dispatch_submission()` before a new Manager write.

## Shared checks and original owners

| Duty | Existing logic consumed |
| --- | --- |
| Scientific and code approval | `proposal_model_runner.drive(..., verify_only=True)` replays the original subject, model calls, canonical replies and accepted receipts; `approved=true` alone is insufficient |
| Exact reviewed execution | Original payload decoder and verification check every role command, wrapper and file/code digests; approval must bind this exact Manager intent and accepted plan |
| Fixed comparison and allowed scope | Decoded parameters must retain the comparison, execution identity and required Skill policy; epochs must remain inside the original delegation |
| Plan and user authority | `lpai_manager.authorized_plan()` shares the original Manager's schema, configuration/runtime and exact plan-authorization checks; the review never supplies authority |
| Original intent rules | Original S01/S02 schema/semantics plus lifecycle row gates via `research_intent` and `lifecycle_rules`; shared by review, replay and both submissions |
| Execution controls | Retained six-condition transport/review/authority tree; its name does not establish other SDM duties |
| Platform effects | Original `Manager.apply()` rechecks current component configuration and owns the unchanged Guard/write boundary |

`component_submission.py` adapts these facts. Its gate is the applicable
submission precondition for this route, not a claim that all S/G/B/E/R/D objects,
full formal signatures/anchors, independent test or statistical acceptance exist.

`submission-acceptance.json` records the gate digest, exact Manager plan and
authorization, science/code review references and decoded payload proof. It is a
submission acceptance record, not scientific success, platform approval or proof
that training happened. It is revalidated before a new write; immutable
acceptance records cannot be rebound to changed inputs.

## Pause, restart and unknown outcomes

- New Guard: check pause, replay both reviews without a model call, validate the
  exact intent and original authority, check pause again, then call Manager.
- Existing intent/pending/completed Guard: reconcile that same request. Never
  open new review/model intents, regenerate a request key or re-post to obtain an
  answer. Reconciliation is observation, not a fresh execution permission.
- Initial review uses the existing persistent Inbox and proposal-review mode.
  Pause/resume and recovered model completion retain its project/root, original
  job/call identities and `reviewing-initial` stage.
- A rejected or stalled initial intent remains blocked. Invalid code, missing
  evidence or unauthorized resources cannot be converted to approval by resume.

Old frozen studies retain their interpreter and receipts. New source does not
migrate or repair them. Legacy initial v1 is not accepted for a fresh write by
this runtime; a pre-existing Guard intent may still be reconciled. Existing
legacy continuations retain their explicitly frozen policy and must not be
relabeled as having passed this new gate.

## Validation boundary

Control tests exercise the actual initializer/freeze, original review/canonical
receipt replay, original gate evaluator, Manager authorization and Guard with
explicit synthetic model/platform transports. Coverage includes first/successor
gate identity, missing/forged/changed reviews, changed plan/component, authority,
pause during replay, restart, unknown-write reconciliation and large raw-source
retention. These are offline software checks, not a new ML/platform qualification.

The original asset/owner-schema closure is now packaged and frozen. Windows
read-only rule consumption is supported; the full journal/authority/transition
engine and original SDM steps 3–21 convergence remain pending. Do not infer D05 acceptance.
