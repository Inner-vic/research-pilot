# Durable Pro review contract

Use `scripts/pro_review_workflow.py` for project-scoped review state. The browser
or a verified connector handles transport. This module never calls inference,
sends browser messages, starts resources or submits ML work.

## Input and state

Bind the observed canonical conversation with `pro_review_session.py bind`.
The source request is `sdm-loop-analysis-request/v1`. A project-owned context
file uses `sdm-pro-review-context/v1` with these fields:

- `project`, `workflow`: exact session identities;
- `fixed_conditions`: a nonempty object of frozen source/data/runtime conditions;
- `excluded_explanations`, `remaining_unknowns`: arrays of strings;
- `current_authorization`: `execution_authorized`, `model_inference_allowed`,
  `training_submission_allowed`, all false for this review-only route;
- `new_experiment_observations`: boolean; context confirmations are not observations;
- `gap_matrix`: optional unique `gap_id` objects with status
  `open`, `partially-closed` or `closed`.

The immutable prepared request binds source request digest, evidence byte hashes,
project/workflow/chat, review context and prior capsule/version/digest. Delivery
intent pins all attachment hashes. Changed evidence requires a fresh request.

`review-workflow.json` uses `sdm-pro-review-workflow/v1`, a local revision,
one active review and retained states. Normal transitions are
`prepared → delivery-in-progress → waiting-for-review → received → validated → planned`.
A sent receipt establishes waiting; `delivery-uncertain` requires same-chat
reconciliation. `rejected` retains a failed raw attempt and accepts a corrected
response to that request. Process locks and atomic fsynced JSON protect updates;
the OS releases locks on process termination.

In a multi-turn review, evidence questions are intermediate dialogue, not inputs
to `ingest`, `record`, `plan` or `export-loop-result`. Retain them separately using
[pro-review-dialogue.md](pro-review-dialogue.md); the review remains pending and
the accepted context revision stays unchanged. When evidence changes, reconcile
the completed question response, retire the pending request and prepare a linked
replacement. This uses existing states and commands; no automatic dialogue
dispatcher or new machine state is implied.

## Public commands

```text
python scripts/pro_review_workflow.py prepare --root <session-root> --request <source-request.json> --context <review-context.json> --evidence <evidence.txt> --observed-url <actual-url> --expected-revision <revision>
python scripts/pro_review_workflow.py begin-delivery --root <session-root> --review-id <id> --expected-workflow-revision <workflow-revision>
python scripts/pro_review_workflow.py delivery --root <session-root> --receipt <sent-receipt.json>
python scripts/pro_review_workflow.py delivery --root <session-root> --uncertain-reason <observed-problem>
python scripts/pro_review_workflow.py ingest --root <session-root> --response <raw-response.json> --receipt <return-receipt.json>
python scripts/pro_review_workflow.py plan --root <session-root> [--review-id <id>]
python scripts/pro_review_workflow.py status --root <session-root>
python scripts/pro_review_workflow.py recover --root <session-root>
python scripts/pro_review_workflow.py cancel --root <session-root> --review-id <id> --reason <reason> --expected-workflow-revision <workflow-revision> [--reconciliation <observed-terminal-receipt.json>]
```

Sent and return receipts require exact `url`, `model: "Pro"`,
`verified_visible: true` and `request_digest`. These observations must come from
the authorized transport; they are not cryptographic model attestations. Passing
a receipt does not send anything. Do not manufacture an observation from policy.

For new transport, call `begin-delivery` once immediately before the outer agent
sends. It persists transport intent. Repeating it is blocked; an interruption
between intent and confirmed delivery recovers as `delivery-uncertain`, including
when the intent exists but the state still reads prepared. Reconcile in the same
chat before retrying. Legacy direct `delivery` receipts remain readable; they do
not retroactively prove that a pre-send intent existed.

## Retiring a pending request

`cancel` retires local review work and clears its active slot; it does not send a
message, stop remote generation, delete artifacts or change the fixed chat. Use
the current workflow revision from `status`, distinct from the accepted context
revision. Cancellation keeps the accepted context and session head unchanged.

A prepared request with no transport intent can be cancelled locally. After a
transport intent, sent receipt, uncertain delivery or rejected response, retain
a fresh same-chat observation using `sdm-pro-review-reconciliation/v1`:

- exact `url`, `model: "Pro"`, `verified_visible: true`, `request_digest`;
- `evidence_ref` naming an actual project-relative file beneath the session root,
  with `evidence_sha256` matching its bytes; links and escaping paths are rejected;
- timezone-aware `observed_at`, no more than 300 seconds old;
- `outcome`: `not-delivered`, `response-complete` or `generation-stopped`.

Unknown/still-running outcomes do not release the slot. A not-delivered assertion
contradicting a sent receipt or response attempt is rejected. These are observed
transport facts, not signed remote attestations. Do not create a terminal receipt
from a timeout or assume the command itself stops generation.

`received` work must first recover acceptance; accepted/planned work follows the
normal plan route, not cancellation. Cancellation saves an immutable checkpoint
and a byte-pinned copy of the transport observation before updating state.
An interruption there is recovered once; other writers
must recover it before changing the request. Duplicate identical cancellation is
idempotent; reason/proof changes are rejected. Late responses addressed explicitly
to a cancelled review are retained separately and cannot advance context, export
a Loop result or alter an active successor.

To replace a cancelled request, `prepare --supersedes-review-id <cancelled-id>`
binds its cancellation digest into a fresh request ID, even when the source
experiment is unchanged. Reusing the exact cancelled ID is rejected. The new
request uses the same fixed conversation; its prompt distinguishes the superseded
request. This creates no execution authority and does not replace its original
Loop request or scientific evidence.

The Pro response includes BMAD fields plus exact typed `session_context_revision`,
`session_context_digest` and `continuation_acknowledgement`. That object contains
`status`, `objective`, `current_scope`, exact `fixed_conditions` and
`excluded_explanations` from the context, string-array `remaining_unknowns`, and
boolean `no_new_observations` opposite to the context observation flag.
Preserve supplied gap IDs; without new supporting observations do not raise
closure. To assess new static evidence, prepare an explicit new bounded context.

Acceptance emits `sdm-pro-context-capsule/v2`: these context fields, accepted facts,
hypotheses, gaps, next plan, original references and historical authorization
snapshot. Old v1 capsules/records are unchanged. A snapshot grants no future
authority. Rejections never advance revision; identical accepted responses do
not advance twice. Recovery reconciles the original request, response hash and
accepted head after response/record/capsule write interruptions.

`sdm-pro-review-plan/v1` is always planning-only. Current authority, budget, actual
parameters, fresh platform preflight and normal S12/S13 checks remain necessary
outside this route. Pro commands never become provider instructions.

## Generic Loop handoff

New configs may use `analysis_mode: "external-review"` with exact
`external_review: {project, workflow, session_root}`. Loop records accepted
observations, writes the immutable analysis request, returns `awaiting-analysis`
and cleans up its owned resource through the existing driver. The review itself
does not invoke Loop or acquire resources.

After validation, export a cross-bound receipt:

```text
python scripts/pro_review_workflow.py export-loop-result --root <session-root> --review-id <id> --source-request <round-analysis-request.json> --output <round-root>/external-review-result.json
```

Resume re-verifies the signed observations and runtime snapshot, does not call
the execution pipeline again, verifies the review's exact source request and
binding, and creates an explicitly bound validation view. The original Pro
response remains unchanged. A run-next-round decision returns `planning-required`
and never dispatches a successor here. Legacy synchronous immutable configs keep
their original behavior. Software fixtures prove orchestration, not live Pro
inference or scientific quality.
