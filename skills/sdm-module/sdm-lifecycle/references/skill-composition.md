# Composable Skills and lifecycle nodes

Use when adding or reorganizing capabilities. A node describes when a task needs
work; a Skill owns how to perform a cohesive capability. One Skill can serve
several nodes and standalone requests. Do not create one Skill per node or load
every capability at lifecycle activation.

## Ownership and current composition

| Layer | Owner | Boundary |
| --- | --- | --- |
| Coordination | sdm-lifecycle | Select nodes, restore state, route capabilities, bind results, preserve authorization and recovery. |
| Existing governed workflows | sdm-ml-readiness, sdm-create-ml-contract, sdm-lock-baseline, sdm-create-experiment-spec, sdm-freeze-experiment, sdm-build-context-pack, sdm-evidence-review, sdm-scientific-decision | Keep their applicable contracts, authorities and immutable artifacts; ordinary capability results do not replace governed receipts. |
| Existing planning/review personas | sdm-agent-research-planner, sdm-agent-experiment-spec, sdm-agent-geometry-contract, sdm-agent-evidence-review | Specialized reasoning roles; do not duplicate their contract work in new generic Skills. |
| Installation | sdm-setup | Transactional setup and the existing exact core roster. |
| Resource investigation companion | bmad-resource-investigation | Account-visible catalogue and bounded queue/ownership diagnosis; supplies node 26 or monitor-triggered evidence. |
| Provider Skills | Configured installed platform capabilities, such as LPAI or Git/GitLab | Provider-specific calls and access semantics; no model-specific defaults. |
| Project adapter | Current project | Training/evaluation hooks, data and metrics, model code, run evidence and resource choices. |

The resource investigation companion is independently installed/discovered outside
the core SDM source directory and exact governed roster. It is a separately owned
capability, not an unregistered extra core Skill. Core discovery/transaction
contracts remain unchanged. Resolve companions by installed Skill identity through
host discovery, never relative sibling source imports. A missing required
companion yields a named needs-skill result; do not silently execute a source
copy. Explicit paths/versions for a host belong to project installation records.

Node 26's coordinator contract stays in
[resource-investigation.md](resource-investigation.md). Its procedure has one
maintained owner in bmad-resource-investigation. The companion does not require
the coordinator for a standalone read-only question; this avoids a routing cycle.

## Extract incrementally

Separate a capability when it has a distinct trigger, inputs/outputs, tool or
permission boundary and independent usefulness. Keep tightly coupled templates or
short guidance under their owner instead of creating empty or redundant Skills.
Do not split executable modules merely because their documentation is long.

For a migration, inventory callers and references first; choose one maintained
owner, move the complete procedure, and retain a thin compatibility route at the
old path. Preserve node IDs, accepted records and pinned historical packages.
Keep governed/shared executable contracts in place until every relevant caller
and binding is explicitly migrated and verified. Do not change scientific rules,
execution authority or platform permissions as a side effect of reorganizing text.

Resource discovery is the first extracted companion. Git/worktree management,
fixed Pro dialogue and experiment comparison are candidate next batches; they
remain with their current owners until their transport, state and executable
dependencies are mapped. Do not invoke hypothetical future Skills.

## Check the handoff

The coordinator passes purpose/scope, applicable node/attempt identity, input and
evidence references, constraints and relevant deadlines. The capability returns
observations/findings, source bindings, claim limits, missing information and a
permitted next action. Provider results and project results keep their own types.
A capability report cannot invent an S13, grant user confirmation or certify a
model improvement.

Validate both direct and coordinator-routed discovery, local references, missing
dependency behavior and source/install identity. A document/routing check does
not establish live provider success. Keep prior installation receipts and use
the supported installation/update path for each owner; do not edit installed core
files directly or merge unrelated pending source work into the publication.
