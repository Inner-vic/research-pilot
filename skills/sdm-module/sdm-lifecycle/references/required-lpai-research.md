# Required official LPAI and SDM continuous research

The current task uses improved SDM as its research entry and the OFFICIAL `lpai`
Skill as a required platform capability. ResearchPilot workbench and conversation binding consume the same research records;
do not recreate the separate IterLab project. A custom adapter or SDK manager does not become the official Skill.

Official distribution entry supplied by the user:
https://lpai.lixiang.com/lpaiweb/xspacex_vc-chassis/subApp/agent/overview

Resolve the installed official `lpai` Skill and read its current instructions.
Use its actual command whitelist and confirmation semantics; keep credentials
inside official tools. SDK/API extensions are separately authorized capabilities,
not automatically part of that official Skill. Do not disguise an extension as
the official path or use it to bypass an unresolved approval.

## Project requirement

New current-task profiles declare platform_requirements version=1, platform=lpai,
required_skills=[{name:lpai,skill:{path,sha256},manifest:{path,sha256}}]. Both paths
are absolute, exact installed SKILL.md/manifest.json in the same directory. The
manifest names lpai and records its actual version/build. Custom sdm-lpai cannot
fulfil the requirement. The host file registry/discovery supplies these paths;
do not guess an installed root from a source checkout.

The public `run_project_pipeline.py --project PROJECT --inspect-required-skills`
validates these local identities without platform, model or execution calls.
This does not attest model activation, official signature, login, platform
permission or current resource availability. Outer Codex still actually reads
the Skill before platform work. Do not inflate the local check into acceptance.

New task execution checks the bound requirement before resource access; existing
intents remain readable/resumable for original-identity reconciliation/cleanup.
Goal-level replans inherit the policy into their child config, saved plan and
implementation request. It cannot silently disappear when a Study finishes.
Old frozen projects without this new field remain historical/legacy policy;
do not hot-edit them or relabel them as newly qualified current-task profiles.

## Continuous research ownership

Use the existing research_worker and original coordinator: actual terminal
observation → original artifacts → Generator–Critic result assessment → durable consumption
and archive → next experiment or goal-level replan. A finished Study is not a
finished research goal. The actual target and stop reason determine completion.

Author and critic share raw evidence, revise or request decision-relevant
evidence, and exit on approve/revise/needs-evidence/reject/stalled without a fixed
scientific discussion round count. Source/code readiness and the original
authorization remain necessary before new computation. No new numbered node is
needed for the required platform Skill or its identity check.

An approved replan requesting implementation remains unfinished research work:
Codex implements it, freezes a new valid profile and returns through the original
review/preflight/Guard. It is not permission to auto-submit an unfinished payload.
The original native implementation, completion receipt, freeze and dispatch paths
remain connected. Actual end-to-end acceptance still requires their real receipts;
do not claim completion from an implementation-request.json alone.

Keep completed/paused studies stopped, do not reset budgets or old task IDs,
and do not use chat timers or frontend messages as continuation gates. Cases
already meeting their goal are retained as evidence, not restarted for a demo.

## Official CLI component route

The original manager supports explicit `component_transport: official-cli` with
the required official Skill policy. Bind known component UIDs to their exact
names in `component_names`: this CLI's `projects get` accepts a name, not a UID.
Manager readback must still verify the UID, namespace and owner. This mode never
falls back to SDK creation/submission. Start passes all five task-information
flags visibly; estimates are identified as estimates, not observed durations.

Retain the original Guard and request key when start returns pending_approval.
This is not a queued training task and does not prove a user received a card.
Read-only recovery may discover an exact owner/name/parent candidate, reject
ambiguity, then verify its full configuration before accepting its UID. Zero
candidates means keep pending, never reapply. A platform task's actual readback
is execution evidence, not a fabricated approval receipt. Preserve the frozen
runtime; a justified reconciliation fix uses the existing read-only repair
contract, without changing the plan, authorization or original intent.

`component_results.py` verifies complete declared artifact chunks and digests,
original verified task/payload identity, terminal success, split and checkpoint
selection, and recomputes macro-F1/accuracy from predictions without executing a
model locally. Its marker input is an explicit producer contract, not a universal
parser for arbitrary tasks. Missing logs or conflicting artifacts cannot count
as a valid round. Record actual results before choosing a successor; do not
pre-script two improvements or count submitted/failed tasks as trained rounds.

A dataset hash belongs to the exact asset bytes, not its logical dataset name.
Verify the chosen public mirror's byte provenance before queue submission; an
equivalent archive/TSV encoding may have a different hash. Preserve failed
pre-fit attempts and verify numeric/label equivalence before freezing a repair.
Do not remove the hash gate or count an input repair as a scientific improvement.
