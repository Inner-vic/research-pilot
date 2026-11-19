# Resource investigation: lifecycle node binding

This reference owns lifecycle routing and node 26's contract. The reusable
read-only investigation procedure is owned by the independently discoverable
`bmad-resource-investigation` companion Skill, outside the governed SDM roster.
Resolve it through the host's installed Skill discovery by its exact name and
read its SKILL.md; do not import a sibling source directory or run a source copy
as a substitute. LPAI and other provider Skills still own actual platform calls.
If the companion is not installed, return `needs-skill: bmad-resource-investigation`
for this capability; unrelated workflow steps can continue within their scope.

## Node 26: account resource inventory and scheduling investigation

In the generic project graph v2 design, reuse node **26**, stable machine name
`account-resource-inventory`; do not add another node for the same capability or
renumber historical tasks. The node belongs to research/planning. It supplies
resource evidence to node 27 (resource/budget proposal), followed by proposal
review and user confirmation before acquisition. Its ordinary prerequisite is
node 22 (background agreement), which defines account/project scope and purpose.

| Contract | Required behavior |
| --- | --- |
| Trigger | Planning requires actual resource choices; a selected provider is pending unexpectedly; the user asks for resource, ownership or queue investigation. |
| Inputs | Authorized purpose/scope; provider/account binding when known; existing task UID or resource requirements when relevant; current observations and any active attempt's original deadline. Unknown catalogue identities are discovered, not guessed. |
| Work | Delegate to the installed `bmad-resource-investigation` Skill; do only the applicable branches. Pure discovery does not require an existing task, competing-task inspection or paid probe. |
| Outputs | Project-owned inventory snapshot with source/time/scope and catalogue coverage; when investigating a task, its request/stage, selected objects/events, related demand, findings, hypotheses, gaps and next permitted action. Retain account/owner evidence only where needed. |
| Completion | The selected read-only investigation is documented and its available evidence is traceable. Separately record whether each resource option is observed-available, unavailable or unknown. An investigation can finish with an unresolved cause; it cannot claim admission, reservation or release. |
| Waiting/blocking | Missing authentication/scope or unsupported provider queries produce a named limitation. An unavailable/unknown option does not become available because the node finished. Missing data required by a downstream proposal keeps that downstream operation blocked. |
| Recovery | Preserve the attempt/task identity and prior snapshots. Append a fresh investigation revision; do not rewrite already accepted inventory bytes, proposal digests or historical results. No retry, task change or new lease is implied. |

For the v2 runner, the planning projection remains the existing project-owned
`orchestration.records.inventory` (`sdm-orchestration-inventory/v1`), with its
account/source, observation/expiry and resource options. Detailed investigation
evidence is linked from that project record or its source index; the guide does
not introduce a new runner record schema. If the actual catalogue yields no
options, report completed discovery with no usable options, but do not fabricate
an option to make the runner's nonempty-inventory validation pass.

Execution node 15 (monitoring), or the applicable evaluation/provider monitoring
path, can invoke this capability on unexpected waiting or contradictory resource
signals. This is a bounded diagnostic subflow, not a dependency edge from 15 back
to 26: preserve the acyclic execution graph and do not rewind completed planning
nodes. Use a new project-owned diagnostic record referring to the same attempt
and task. Stop/cleanup under the original confirmed deadline remains active and
must not wait for the investigation to finish. Any changed resource proposal
requires a new planning revision and the applicable confirmation.

A standalone read-only investigation can select this node's procedure using the
user's explicit investigation scope. Do not run unrelated background, model,
training or review nodes, or mark them complete to satisfy a training graph.
Legacy task histories keep their original graph and can call the same procedure
at their existing resource/preflight or monitor checkpoint.

This node specifies Agent orchestration using available provider operations;
an inventory record validator does not perform discovery. A runner without a
bound read-only collector remains needs-driver for automated collection. Document
guidance and CPU record checks do not establish a live platform integration.


## Capability handoff

Pass the authorized purpose and account/project scope, task identity when relevant,
existing evidence references and the active attempt deadline. The companion
returns observations with sources/times, catalogue coverage, confirmed findings,
hypotheses, unknowns and next permitted actions. Investigation completion never
means resources are available or execution is authorized. Preserve the planning
inventory schema and do not reinterpret a narrative report as a platform receipt.

The companion can be used directly for a standalone resource question. Its output
does not require creating a lifecycle workspace, allocating compute, opening a
Pro chat or completing unrelated governed artifacts.
