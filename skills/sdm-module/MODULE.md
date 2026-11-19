# Specification-Driven ML Development

`sdm` is a native BMad module for machine-learning research and experiment
execution. BMAD provides product planning and engineering workflows; SDM keeps
requirements, protocols, implementation, Run/Attempt facts, evidence and decisions
distinct. The module supports project training, evaluation, reproduction and
result-driven iteration through configured execution backends.

Current integration status: core orchestration is implemented; LPAI integration
has partial real evidence and requires further expansion and acceptance. CPU
tests describe an evidence scope, not a CPU-only edition or a GPU qualification.

## Native topology

The fourteen source responsibilities remain below `skills/sdm-module`. Daily
users install ONE `bmad-ml` Skill containing the actual SDM research scripts,
references and role guidance. Activation stays in the current Codex conversation;
no launcher or per-project registration is required. Native packaging uses
`scripts/build-native-skill.py`. Legacy register_entry/setup remain maintenance
tools for old installations. See [installation](../../docs/delivery/INSTALLATION-MODES.md).

The old full-module setup retains its14 direct-child Skills. Source is
under `skills/sdm-module/`; the everyday native package has one user entry.
Schemas, templates, scripts and references remain below the owning Skill.

| Skill | Kind | Responsibility |
|---|---|---|
| sdm-setup | Setup | Install, update, validate, recover and uninstall the native module |
| sdm-lifecycle | Coordinator | Route project execution, iteration, recovery and explicit governed qualification |
| sdm-ml-readiness | Workflow | Check the applicable readiness surface; retain governed gate ownership |
| sdm-create-ml-contract | Workflow | Define and validate data, geometry, tensor, evaluation and traceability contracts |
| sdm-lock-baseline | Workflow | Define, reproduce and manage governed baselines |
| sdm-create-experiment-spec | Workflow | Draft protocol, search/run plans and classify frozen changes |
| sdm-freeze-experiment | Workflow | Freeze an approved governed protocol with exact authority and anchoring |
| sdm-build-context-pack | Workflow | Connect frozen research inputs to BMAD implementation and engineering preflight |
| sdm-evidence-review | Workflow | Account for planned runs/attempts and coordinate governed evidence review |
| sdm-scientific-decision | Workflow | Issue or supersede the governed scientific decision |
| sdm-agent-research-planner | Stateless role | Frame falsifiable research questions, comparisons and next studies |
| sdm-agent-experiment-spec | Stateless role | Steward experiment protocol and change semantics |
| sdm-agent-geometry-contract | Stateless role | Resolve spatial, calibration, synchronization and tensor semantics |
| sdm-agent-evidence-review | Stateless role | Independently scrutinize governed evidence and limitations |

The four `sdm-agent-*` names denote original stateless specialist roles, not
independently deployed services. They confer no platform execution or signing
authority. Resolve installed siblings through host configuration rather than
importing a sibling by a relative source path.

## One SDM lifecycle, multiple execution interfaces

The current 13-step lifecycle is the ResearchPilot project core and evolves from SDM. Activities
and groups may be added, removed, split, merged or reordered when justified by
actual inputs, outputs and dependencies. Read [the lifecycle](sdm-lifecycle/references/sdm-core.md).
Original 21-step engine IDs remain stable provenance, not a fixed current directory.
See [the evolution analysis](sdm-lifecycle/references/sdm21-to-13-evolution.zh-CN.md) for each
merge, actual code/gate scope, observed effects and remaining integration gaps.

`research_initialize.py` and ComponentPipeline supply current continuous-study
execution; `run_project_pipeline.py` dispatches project-specific modes and drivers;
`specctl.py` retains the full governed ledger/authority/anchor protocol. These are
execution interfaces under the same scientific framework. They do not yet share
all acceptance transitions: new component studies consume original S01/S02 checks,
while integration of original SDM steps 3–21 remains pending. Full ledger execution requires
the original supported host and authority/trust configuration.

Generator–Critic deliberation supports problem/protocol, code and evidence work.
Manager/Guard implements authorized effects and original-request reconciliation;
Context, inbox, supervision and recovery preserve execution continuity. The UI
projects the same facts. D05 remains distinct from automation completion or a
metric target. Frozen semantic changes and successors retain original rules.

The eleven project work packages and legacy graph IDs are delivery/driver views,
not a replacement lifecycle. RoadModel keeps its default legacy graph; explicit
exploration revision2 merges four record-only pairs and retains original drivers.
Their numeric IDs must not be interpreted as SDM step IDs. Details remain in
[the implementation view](sdm-lifecycle/references/five-phase-orchestration.md).

## Platform boundaries

| Formal binding | Responsibility |
|---|---|
| data_platform_skill | Labeling and data-quality facts |
| source_control_platform_skill | Git operations and ledger anchors |
| ci_platform_skill | CI orchestration and engineering evidence |
| ml_execution_platform_skill | HPO, training, locked and robustness evaluation |

For formal work, the coordinator binds S12 to an installed Skill and authenticates
S13 against the pinned producer trust. A prose result or signature alone is not
full acceptance. Ordinary executors may call supported official SDK/CLI operations
through their configured adapter; they do not implicitly generate governed receipts.
Official SDK/CLI precedes native UI fallback; neither route bypasses authorization.

Platform packages, credentials, resource queues and organizational signers are
provided by the user. Empty configuration waits for the affected capability.
See the release's platform-status matrix instead of assuming one working case
qualifies all component/development-space/GPU routes.

## Installation, outputs and updates

sdm-setup owns seven module settings, native registration, host locks, exact digests,
transactional staging, backup and recovery. Installation is separate from project
execution and does not activate monitoring. Update via setup, preserving personal
configuration, histories and frozen runtimes; unknown external writes reconcile
before retry. Never update a frozen task by overwriting its executable snapshot.

Project workspaces own repository registration, original materials and per-round
results. The module owns reusable implementation and contracts. Preserve failed,
cancelled and incomplete attempts; missing observations remain unknown. Conditional
checkpoint recovery must prove actual restored state, not merely a readable file.

## Completion and validation

Execution outcome, evidence completeness, model quality, official resource stop and
billing are separate facts. Project commands return their own structured status;
governed commands retain their exact specctl completion contract. A next-step
recommendation is not an executed next experiment.

The release includes a local CPU installation/iteration example and contract tests.
Prior partial LPAI evidence is retained by the development project. Real GPU/DDP/AMP,
large framework checkpoints, long host failures and billing require their own
acceptance. Use isolated hosts for source verification; do not operate live tasks
to make an offline check appear complete.
