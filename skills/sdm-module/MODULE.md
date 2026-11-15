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

## Two execution routes

### Project execution and iteration

Ordinary tasks use `sdm-lifecycle/scripts/run_project_pipeline.py` and project-owned
configuration. RoadModel generators default to `legacy`. Explicit `exploration`
revision2 repairs the withdrawn rc3 design: retain original operational drivers
and conditional policies; group only four pairs of record-only checks, preserving
original validators, producers and durable acceptance. Local differential tests
do not establish real-platform qualification. Preserve duties and
conditional checks while reducing repeated material/display work. See
the [34-node audit](../../docs/delivery/NODE-CONSOLIDATION-AUDIT-20261026.md) for each decision.
Formal 21-step qualification remains a separate, explicitly selected route.

Read lifecycle's `references/five-phase-orchestration.md` for detailed phase1→5
diagrams, five responsibility tables and R/W/V numbering. Its stages are:
问题定义与需求梳理 → 方案设计与实验规划 → 实验开发与训练验证 →
结果评估与评审 → 结论输出与迭代决策.

### Explicit governed qualification

`specctl.py` retains the original21-step qualification protocol, immutable
artifacts, signed S12/S13 handoffs, pinned authority and replay. It is selected
when governed qualification is requested, not a prerequisite to every parameter
experiment. The ordinary workflow does not rewrite a pinned21-step ledger.
Their numeric IDs are different namespaces; ordinary nodes22–25 do not restore
the retired release/deployment activities of an older governed engine.

D05 support/rejection/inconclusive/invalid outcomes remain separate from automation
completion. Successor research and authorized archive/close preserve original
protocols. Deployment constraints remain research inputs, not a deployment service.

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
