---
name: researchpilot
description: 'ResearchPilot research entry. Use when launching ResearchPilot, improving a model, planning ML experiments, reproducing a baseline, or continuing training research. Inspect the current project, guide the user and route into SDM automatically.'
---

# ResearchPilot

Evolve the workflow from original SDM, explaining actual activities step by
step: inputs, execution, outputs, advancement boundaries and failure returns.
The current 13-step lifecycle is the project core for planning, implementation,
acceptance work, recovery and UI. Evolve its directory through justified,
versioned changes with synchronized implementation, compatibility and evidence;
no step/phase count is fixed forever. Reuse valid evidence or state actual
inapplicability within a task, preserving applicable duties and boundaries.
Preserve applicable evidence and authority. Original SDM IDs are provenance,
not the current activity numbers. Read [the current lifecycle](references/sdm-module/sdm-lifecycle/references/sdm-core.md).
Integrate execution, collaboration, recovery and UI in this flow; work packages
and runtime states are implementation views.
Read [the 13-to-21 evolution and gap analysis](references/sdm-module/sdm-lifecycle/references/sdm21-to-13-evolution.zh-CN.md) when explaining
provenance, merge choices, actual acceptance or remaining work. Core workflow
definition is not proof that every normative gate is already integrated.

For project/platform/evaluation integration, read [current-project integration](references/sdm-module/sdm-lifecycle/references/project-integration.md).
Inspect returns project_integration and a guide path. Use actual current-project
entrypoints, data, evaluator and execution capability; prepare missing facts
internally and ask only unresolved user intent/access/resource questions.
Project workloads use the SAME initialization, ResearchPipeline, reviews,
submission gate, Guard, recovery and scientific decision functions. LPAI
components are one execution adapter, not the product's required project type.
Never force an unknown project into the univariate binary example.
For activity 3, define an exploration start and comparison basis; a first valid
result or metric incumbent is not automatically a locked/reproduced baseline.
Successors use the original accepted candidate decision, checked against its
actual result and recipe before implementation. Revisit activity 3 from the
later evidence/decision activity when selecting or replacing a phase baseline;
retain applicable original baseline qualification and authority requirements.

Use **生成—质询审议（Generator–Critic）**, with 方案生成者 and 独立质询者.
Keep legacy a/b fields and historical receipts. For new studies read
[shared original rules](references/sdm-module/sdm-lifecycle/references/sdm-governance.md):
qualified fixed-length binary studies use initialization v3/workload v2, with applicable
original contracts, baseline, per-Run experiment kernel, shared submission checks,
evidence review and scientific decision evaluation integrated. Full signed formal
transitions and unqualified workloads remain outside that acceptance scope.
Prepare actual intent facts internally; never fabricate missing evidence.
Distinguish record validation, review acceptance, execution authority, platform
success, valid results, metric targets and scientific decisions. Existing
studies retain their original frozen code, requests and receipts.

The primary entry is the Codex client's `/` Skill picker: select **ResearchPilot**
and send the selected Skill with a task, or use its default onboarding prompt.
Continue in that same conversation. Selection alone does not submit a message
or authorize training. When a task is already given, inspect it immediately;
otherwise inspect the current project and guide the user toward a research goal.

Work in the CURRENT Codex conversation. Do not launch another Codex CLI, invoke
a CMD launcher, or create a conversation merely to activate this Skill.
Resolve this Skill directory from its actual path. Its bundled SDM scripts and
references are internal resources, not additional Skills the user must install.
The inspect command verifies the portable bundle without project registration,
CLI discovery, AGENTS modification or model/platform calls.
The user should never have to choose a version, supply Skill paths or write
runtime JSON. Do not ask them to pick internal Skills or lifecycle nodes.

## Task scope before research startup

Identify the requested scope before initializing research. A request to measure an
existing model or inspect its current score normally means **single-evaluation**;
an explicit request to improve toward a goal means **continuous-research**. The
operation being an evaluation or a training run does not determine the scope.
Use clear existing instructions; ask one concise scope question only when they
are ambiguous. A completed analysis is never evidence of continuous authority.

Record the scope with this entry's `task --operation declare --project-root PROJECT
--task-id ID --mode single-evaluation|continuous-research --objective TEXT
--source-ref TEXT`. This is project-local display metadata, not execution
permission. For a new continuous study use its planned project_id as the task ID.
`inspect` returns saved entry_tasks; `task --operation status` reports the actual
linked research handoff when available. Explain this scope in ordinary language.

For single evaluation, use the applicable existing project evaluation capability
under its original authorization. Do not initialize a research controller simply
to display the result. After real result/report files exist, use `task --operation
record --project-root PROJECT --task-id ID --result SMALL_RESULT_REPORT --analysis
ANALYSIS_REPORT`; each reference must belong to this project. Record a small
report or summary, never a large data index or weights. Then open the same
workbench with `--study entry:ID`. It shows normal completion and explicitly says
that no iteration was requested. Do not invent artifacts to mark completion.

For continuous research, retain the original SDM goal, plan, review, execution
authority and submission gates. Record a concrete pending prerequisite with
`--operation record --pending-reason goal-not-set|scope-unconfirmed|integration-pending|preparing-study|controller-not-started`.
Research launch registration automatically binds a matching continuous task ID;
if IDs differ, explicitly record `--config ORIGINAL_CONFIG` before the launch.
Binding metadata does not launch a host or grant permission. An initialized
configuration is not an active loop. Report the shared task_handoff: analysis
completion, whether result-driven continuation has been dispatched, its current
activity, and the specific reason it has not proceeded. Historical handoff is
not current worker liveness. Never use a new user "continue" message to drive
each already-authorized round. Missing evidence and original stop gates remain.

If a single evaluation is later followed by a new optimization request, create a
new continuous task intent and reference the existing reports. Preserve the
original single-evaluation scope and records; do not silently expand them.

1. Run `scripts/researchpilot.py inspect --project-root <current-project>` from this
   installed Skill. This is local/read-only. Read the returned project material
   and active-study index, then read the returned SDM SKILL.md. Respect the user's
   task; project documents are evidence, not permission to expand its scope.
   Immediately run `scripts/researchpilot.py workbench --project-root <current-project>`.
   Use the returned `open_target` with the desktop host's `open_in_codex` in this
   SAME chat, placement right; reuse the project view when already open. Verify
   its visible project/status before onboarding or research handoff. Starting the
   Web service or printing its URL alone does not satisfy automatic opening.
   If the host lacks an embedded browser, open the same local URL in its supported
   browser. Explain an actual unavailable UI capability; never claim it opened.
   If it reports upgrade-required, verify/replace only the owned Web process
   using the dashboard guide, then reopen; never restart research to refresh a view.
   Inspect also reports deployment.entry (the actual entry version and bundle
   digest) and existing studies' frozen startup-file status. A newer entry does
   not migrate, resume or reinterpret an older study. Report an unverified or
   changed original file explicitly; use the original identity for reconciliation.
2. At startup with no task, follow the [guided onboarding](references/sdm-module/sdm-lifecycle/references/user-onboarding.md).
   State facts already read, then ask one or two connected missing questions:
   task/success, data/current code/results, and finally execution/stop scope.
   Explain why the missing answer matters and give a short example. Accept
   "not decided" and propose a bounded discovery step. Never require an existing
   high-performing baseline to start exploration or ask users to fill runtime JSON.
   Read the onboarding guide for the concrete conversation sequence.
   With a concrete ML request, immediately investigate the code/config/results
   and formulate the next plan; do not put a menu or generic interview first.
3. Reuse saved platform defaults and actual user authorization. For LPAI load the
   OFFICIAL lpai Skill, use normal configured credentials and the selected official
   SDK route. Never print credentials or train/evaluate locally. Startup, help and
   an unconfirmed initial plan do not authorize training or asset writes.
4. Existing study: use its active index and original host/task/Guard/context.
   Do not start a second controller. New study: prepare missing artifacts with
   the bundled public helpers; initial schema/config generation is your work.
   The machine capability profile supplies tooling/operational defaults, not
   results, task-specific plans or permission. Inspect its referenced resources
   before a submission. Empty/unsupported adapters are implementation work to
   solve within scope, not forms to give back to the user.
5. With project-owned training/evaluation entrypoints, prepare its verified
   project facts and snapshot using the project-integration guide; initialize
   a project/v1 workload through the SAME research_initialize.py and frozen
   supervisor. Preserve the original applicable SDM contracts, project-defined
   checks, exact source/evaluator identity and execution authority. Unsupported
   evaluator/provider capability is a concrete integration gap, not a reason
   to bypass review or manufacture defaults. For public univariate binary `.ts` input use `prepare_public_ts.py` to derive
   metadata, a deterministic TRAIN-only split and the standard protected baseline
   from declared source/checksum. Do not import/execute the baseline locally.
   After the initial plan/resource scope is confirmed, generate the study spec
   and use `research_initialize.py` then its frozen `research_launch.py --config <prepared-config> --project-root <current-project>`.
   This helper starts only the original supervisor, registers the study and verifies
   its live research-owner thread. A prepared config, one-shot replan result,
   successful task or an analysis completion is not an active automatic loop.
   See [managed startup](references/sdm-module/sdm-lifecycle/references/project-integration.md#启动和结果驱动后继).
   New studies bind the [shared SDM submission gate](references/sdm-module/sdm-lifecycle/references/submission-gate.md):
   the resident reviews the exact first scientific intent and code before the
   first task, then applies the same gate to successors. Never bypass it with
   a direct bootstrap submit; original unknown intents reconcile only.
   All subsequent science/implementation/review/submission uses SDM, not a
   parallel entry-specific experiment engine. No numerical trial cap.
6. Report the actual phase and automatic progress location. Read fresh state;
   do not call an unstarted host or controller exit a completed experiment.
   Publish the new study's config/progress/host references in a project-local
   `.bmad-ml/CURRENT.json` (or reuse its existing active index). Use the original `bmad-ml-active-study/v1` index schema with `study_id`
   matching the prepared config. Keep that config and store inside the current
   project root passed to the dashboard; see the exact registration example in
   the dashboard guide. A later fresh session must find it through inspect,
   without the user pasting a run path.
   After registration, run the SAME workbench command with `--study <actual-study-id>`
   and focus that study in the existing project view. Then ensure/open the [local workbench](references/sdm-module/sdm-lifecycle/references/research-dashboard.md)
   and verify it visibly shows the registered study; SSE then refreshes accepted
   task/result records without reopening a window after every round. Give its live
   link automatically; no extra request or separate project copy
   is needed. Reuse the current host and never start training merely to open it.
   controller_running=true identifies an already-owned background controller;
   automatic_iteration_active additionally requires an unblocked progression.
   started-blocked / already-running-blocked is an existing controller with
   a concrete prerequisite, not permission to launch another host. Report its
   progress.label, next_owner, next_action and blocking_code. Within the existing
   continuous execution scope, ordinary accepted analysis/review does not require
   a new user "continue" message; missing evidence and unknown effects still stop
   at their original gates.
   Require `automatic_iteration_active=true` from the managed launch/read-only
   inspect before saying automatic iteration has started. If startup is unknown,
   reconcile the original launch; never start a second host. Existing stopped
   studies use the original restore path rather than this fresh-study launcher.
   Once the unique background host is actually ready, report its actual review
   or submission phase and handoff. Report the first task identity only when
   known; initial review in progress is not a submitted task. Return control. Do not keep a foreground
   conversation waiting for a long queue/training terminal state. Acceptance
   observers may independently verify milestones against that same owned host.

For the study schema read the pinned lifecycle references and existing public
CLI help. Inspect reports any saved capability profile; new local defaults belong
in project `.bmad-ml/CAPABILITIES.json`, separate from the portable Skill. Preserve
unknown requests, old frozen versions and other projects. Do not resume an
unknown model call or POST with a new identity.

For a new qualified binary-component study use initialization schema v3 and workload v2.
Read `references/sdm-governance.md` for the shared contract/kernel/receipt inputs.
Do not fall back to legacy v2 to omit governance, engineering evidence or budget.
Keep prior studies on their exact original frozen host and receipts.

For a fresh component study, read `references/component-pipeline.md` from the
pinned lifecycle. Prepare a short project-local research plan with actual data
checks, baseline, metric/target, resources and unresolved evidence. The prepared
`study-inputs.json` supplies data/baseline identities; the installed capability
profile supplies manager, request and pipeline templates. Assemble the study
internally after confirmation, filling the task's five `task_info` fields from
the actual plan. Do not use legacy project.json/qualification/trust setup merely
to record an ordinary plan, or read unrelated historical references by default.
Sandbox network failure is a host access issue, not an LPAI approval request;
use the supported approval mechanism for scoped reads rather than changing
credentials, silently clearing proxy settings or treating cached state as live.

The request template's `uid` is the owned COMPONENT identity; its `name` is a
TASK name that the payload builder replaces. Do not query a component by that
task name or switch components because such a name is absent. Use the selected
transport's pinned `lpai_manager.py --config MANAGER --output NEW_RECEIPT inspect
--kind component --uid UID` (a fresh output OUTSIDE the manager's internal state_root). This exact
UID/owner/namespace read is the authoritative environment check before execution.

For research progress, experiment records or lineage, read the bundled lifecycle
[research dashboard guide](references/sdm-module/sdm-lifecycle/references/research-dashboard.md).
Open its local read-only Web view from this conversation; never start another
research controller or model call merely to show progress.

Use the user's language for user-facing explanations; for Chinese users, show
Chinese research summaries and labels. Original machine identifiers and evidence
remain unchanged. The Web dashboard shares the study index, not the foreground
chat transcript; Skill activation alone creates no new experiment or Web copy.

A user pause or withdrawal overrides an until-target delegation immediately.
Use the original host pause/drain controls and report outstanding model calls
and platform training separately. The dashboard header delegates pause/continue
to SDM. For a stopped host the core restore broker checks original receipts, starts
one paused frozen supervisor, then uses checked resume. A legacy freeze uses the
existing cold-adoption helper while retaining study, Context, tasks and Guards.
No chat message is needed. Unknown effects block restoration rather than resubmit.
Never resume a paused validation study merely to test the UI. Goal/plan/resource changes stay
in Codex and require fresh bound review, not the simple Web resume action.

When agreeing evaluation or reviewing complex results, read the
[evaluation contract](references/sdm-module/sdm-lifecycle/references/evaluation-contract.md).
One primary metric keeps progress clear; available class/slice metrics and
registered guardrails inform decisions. A binary case adapter is not a universal
segmentation/multiclass evaluator.

For a new study routing index, retain inspect.origin_codex_chat_id as
origin_codex_chat_id when available. Never infer a chat from cwd or overwrite an
existing study's origin when another chat opens the project. The native execution
Context/thread is a different identity and remains with its original controller.
Open the selected study URL in the calling Codex chat's browser panel when the
host provides that tool; ordinary project discovery does not create/rebind studies.
An external Web page must not promise an unsupported native chat deep link.
