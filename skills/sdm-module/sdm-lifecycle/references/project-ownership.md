# Project-owned adapters and records

BMAD owns task coordination, bounded Loop analysis, immutable handoff verification,
failure recovery, cleanup and generic result templates. The project owns model
code, data selection, metric meaning, allowed experiments, task drivers and all
round records. LPAI and GitLab Skills own generic platform operations.

## Capability composition and case boundaries

BMAD defines when and why to invoke a capability, the inputs and evidence it
requires, its result states, and the next action or recovery within user scope.
Delegate platform/repository mechanics to available provider Skills or adapters;
do not hard-code one project's workflow into those generic capabilities.

| Layer | Reusable responsibility | Project-owned binding |
| --- | --- | --- |
| BMAD orchestration | Select stages, coordinate Git/platform/Pro, preserve evidence, enforce confirmed budgets and decide continuation. | Current goal, allowed actions and accepted project plan. |
| Git capability | Inspect ownership and ancestry, isolate work, freeze source/configuration, record changes and recover safely. | Repository, remote, branch/worktree names, allowed paths and exact revisions. |
| Platform capability, including LPAI | Resolve resource requests and quotes, query task/events/logs, perform authorized submit/stop and verify release. | Namespace, cluster, queue/package, identity, resource envelope and deadlines. |
| Model-project adapter | Map the project's train/eval/data/artifact interfaces into those contracts. | Model code, labels, metrics, data, checkpoints, runtime hooks and outcome thresholds. |

For a Skill improvement, state the reusable behavior being changed and its
acceptance evidence before expanding case analysis. Distinguish CPU contract
verification, real-provider integration and model-quality evidence in the report.
A case may expose a generic diagnostic gap without requiring another training
run or a model fix. Keep detailed case observations and candidate implementations
in project records; promote only the general decision rule, interface or recovery
behavior into the Skill. Preserve existing historical bindings during extraction.

The case's GPU model/count, class IDs, target metrics, source paths and resource
failure hypothesis are not generic defaults. A provider limitation is an explicit
capability result with supporting evidence, not a reason to keep adding
project-specific exceptions to BMAD.

## Adapter and record bindings

Source worktree ownership, frozen inputs and retirement follow
[worktree-isolation.md](worktree-isolation.md). Project-owned paired reports follow
[experiment-comparison.md](experiment-comparison.md). Private source bundles stay
outside tracked records; exact source/config versions remain distinct from reports.

New configured pipelines may bind a project-owned adapter:

```json
"project_driver": {
  "path": "/absolute/project/tools/sdm/project_task_driver.py",
  "digest": "sha256:<actual-file-sha256>"
}
```

The coordinator checks the actual driver bytes before constructing commands and
again for cleanup. Changing it requires a fresh configuration/run; never alter an
existing configuration, accepted receipt or installed immutable Skill to match.
Historical RoadModel configurations without this field retain their original
installed driver. This compatibility does not select the target of new records.

For project-owned runtime/collector dependencies, read
[project-driver-packages.md](project-driver-packages.md). The existing binding
may pin a complete adapter directory; new native/Windows runtimes revalidate it
at each task and cleanup launch. Packaging does not execute or activate a run.

`result_records.repository`, `directory`, `remote_url`, `branch`, and `owner`
name the project result destination. Keep private run evidence, large logs and
weights outside tracked files, with verified location and digest in each record.
Migration copies record bytes; signed evidence, old runtime paths and trained
commit remain unchanged. BMAD keeps a framework summary and project links.

Reporting commits advance a branch. A project adapter may separately pin a clean
source checkout at the actual trained commit and the current personal branch
head; never substitute a report commit for the model source version.

Extraction is incremental: the RoadModel task driver is now project-owned;
legacy runtime/data collectors remain in the LPAI adapter for old bindings.
A sealed project-owned migration package now prepares those dependencies for new
configurations; it has not been activated or scientifically executed. Retain the
legacy copies until their callers and immutable bindings are retired.
