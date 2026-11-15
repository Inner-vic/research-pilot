---
name: bmad-ml
description: 'ResearchPilot (formerly BMAD ML) research entry. Use when launching ResearchPilot, improving a model, planning ML experiments, reproducing a baseline, or continuing training research. Inspect the current project, guide the user and route into SDM automatically.'
---

# ResearchPilot

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

1. Run `scripts/bmad_ml.py inspect --project-root <current-project>` from this
   installed Skill. This is local/read-only. Read the returned project material
   and active-study index, then read the returned SDM SKILL.md. Respect the user's
   task; project documents are evidence, not permission to expand its scope.
2. At startup with no task, follow the [guided onboarding](references/sdm-module/sdm-lifecycle/references/user-onboarding.md).
   State what is already known, then ask the smallest next question with a plain
   example. Progressively establish task/data/baseline, success metric and important
   classes/slices, then execution scope; never show an internal configuration form.
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
5. For public univariate binary `.ts` input use `prepare_public_ts.py` to derive
   metadata, a deterministic TRAIN-only split and the standard protected baseline
   from declared source/checksum. Do not import/execute the baseline locally.
   After the initial plan/resource scope is confirmed, generate the study spec
   and use `research_initialize.py` then the frozen `research_supervisor.py`.
   All subsequent science/implementation/review/submission uses SDM, not a
   parallel entry-specific experiment engine. No numerical trial cap.
6. Report the actual phase and automatic progress location. Read fresh state;
   do not call an unstarted host or controller exit a completed experiment.
   Publish the new study's config/progress/host references in a project-local
   `.bmad-ml/CURRENT.json` (or reuse its existing active index). A later fresh
   session must find it through inspect, without the user pasting a run path.
   After registration, ensure/open the [local workbench](references/sdm-module/sdm-lifecycle/references/research-dashboard.md)
   and give its live link automatically; no extra request or separate project copy
   is needed. Reuse the current host and never start training merely to open it.
   Once the unique background host is actually ready and the first task identity
   is known, report the handoff and return control. Do not keep a foreground
   conversation waiting for a long queue/training terminal state. Acceptance
   observers may independently verify milestones against that same owned host.

For the study schema read the pinned lifecycle references and existing public
CLI help. Inspect reports any saved capability profile; new local defaults belong
in project `.bmad-ml/CAPABILITIES.json`, separate from the portable Skill. Preserve
unknown requests, old frozen versions and other projects. Do not resume an
unknown model call or POST with a new identity.

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
