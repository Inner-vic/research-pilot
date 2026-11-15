# Fixed Pro conversation and context management

One project/workflow uses one active Pro conversation. A review round is a local
request ID, not a new chat. Existing older chats remain historical references;
do not send the same review to multiple chats or accept whichever answers first.
Bind the current user-visible conversation when adopting an existing workflow.
Changing a binding is a separate user-authorized handover, not an upload retry.

## Local source of truth

Use a project-owned ancillary review directory, separate from governed S12/S13
ledgers. `session.json` binds project, workflow, canonical URL, conversation ID,
model and current revision. `records/` contains immutable hash-linked accepted
review references. `contexts/` contains immutable versioned context capsules.
Original requests, manifests and responses remain in their original bundles.
The durable public workflow is described in
[pro-review-contracts.md](pro-review-contracts.md). Use it for new pending requests;
the session helper remains the binding/accepted-history layer.

Keep the session-root locator with the project's workflow state or review index.
On each activation, resolve the authorized project/workflow and load that locator,
then read the existing binding, accepted head and latest capsule. Never select a
different project's session or infer the active session from browser recency.
If an established review workflow has no binding, reconcile its receipts and
current user-visible conversation before adopting it; absence of a locator is
not permission to silently create a replacement. Reusable Skill instructions
contain the procedure, not a particular project's URL, revision or experiment.

```text
python scripts/pro_review_session.py bind --root <session-root> --project <project> --workflow <workflow> --url <canonical-chat-url>
python scripts/pro_review_session.py check --root <session-root> --observed-url <actual-browser-url> --expected-revision <revision>
python scripts/pro_review_session.py record --root <session-root> --bundle <accepted-review-root> --observed-url <actual-browser-url> --expected-revision <revision>
```

These commands are local checks, not a browser dispatcher. Read the actual URL
and selected model in the UI; never substitute the expected URL for an observation.
`record` rechecks evidence and typed facts, requires the Pro delivery receipt to
match the binding, and advances revision only after validation. Revision mismatch,
different URL or context drift stops the route. Binding an already bound directory
to another conversation fails. Serialize requests per workflow; wait for the active
request to finish or reconcile it before issuing another.

Retain an immutable delivery intent before sending (request/context digests,
expected revision, bound URL and selected attachment hashes).
Use the workflow's `begin-delivery` before the actual send; interrupted intent
requires same-chat reconciliation. To retire pending work, use its CAS-bound
`cancel` route and retain any required terminal observation. See
[pro-review-contracts.md](pro-review-contracts.md) for the exact states and proof.
After the UI confirms delivery, retain a sent receipt; after retrieval and validation, retain
a completion receipt referring to the original response and validation report.
If sending or retrieval has an uncertain outcome, inspect that same conversation
for the pending request and response before retrying. Do not duplicate a request
or change chats to escape an uncertain delivery. These are orchestration duties;
the local binding helper does not implement a browser queue or server-side lock.

For the one-time adoption of an already accepted review, use `record --bootstrap`
at revision 0. Its old response predates the capsule echo fields. Bootstrap fails
after the initial adoption; ordinary future reviews must echo the current capsule.

## Context in the same chat

At a phase boundary or when prior assumptions are corrected, send the latest
capsule with the new immutable request and evidence delta. It carries objective,
validated facts, hypotheses and limitations, current gap matrix, next plan,
source references and historical budget. Add current user scope separately; a
capsule or Pro decision never grants execution authority. Preserve historical gap
IDs and hypothesis status, including rejected explanations and unknowns; do not
rename gaps merely because a fresh analysis uses G1..G8 as temporary labels.

Use a compact human-readable view when the machine capsule is large: current
objective, fixed conditions, key facts, rejected explanations, open questions,
permitted next action and evidence references. Bind it to the capsule digest.
The new prompt must state the expected context revision/digest and request digest.
Require the response to echo `session_context_revision` and
`session_context_digest` as extra fields, and compare them before acceptance in
addition to the ordinary request/fact checks. Do not trust an echo as proof of
complete comprehension; review the returned reasoning against the evidence.

When introducing or replacing a capsule, require an explicit continuation
acknowledgement of objective, fixed conditions, excluded explanations, unresolved
questions and current permitted scope. State whether new experiment observations
exist. Preserve stable gap IDs and the scope of each closed subcheck: a closed
arithmetic check does not close an archived-prediction gap with a similar label.
A topic combining supported static findings and unknown runtime behavior can
remain partially closed; neither part proves the other. Without new supporting
evidence, a continuation acknowledgement or successful structural validation
must not upgrade closure or causal confidence. Keep a project-specific structural
and scope check with the ordinary BMAD request/fact and session-version checks.
Preserve rejected raw responses outside accepted artifacts and obtain a corrected
Pro response in the same chat; never repair its scientific claims locally.

A summary helps focus the model; it does not delete prior messages, reset a
ChatGPT context window, expose exact token occupancy, or guarantee all earlier
attachments remain accessible. If Pro cannot read a required file, reattach that
exact bounded evidence in the same chat. Do not resend every historical file by
default, but never omit the evidence needed to verify the current claim.

## Handover only when needed

If the UI cannot continue the conversation, file access cannot be restored, or
repeated context failures prevent a grounded result, preserve the pending request
and prepare a checkpoint: current capsule/digest, accepted review chain, stable
gap and hypothesis IDs, execution scope, budget, required evidence and unresolved
request state. Report the concrete limitation and obtain user authorization before
opening a replacement. Record old/new IDs and the handover reason; mark the old
conversation historical, then verify the successor understands the checkpoint
before using its decision. Do not run old and new chats as competing active brains.
