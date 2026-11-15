# Per-round results and artifact retention

Each executed round has a project-owned `result.json` and generated `report.md`.
For control/treatment results, retain those records and export a separate bound
revision using [experiment-comparison.md](experiment-comparison.md). Failed or
incomplete arms remain visible; group export never rewrites original rounds.

New project defaults and branch maintenance are in
[project-maintenance.md](project-maintenance.md). Set `result_records.navigation`
true to update `<directory>/catalog.md` across this project's runs, including
failure/pending records and branch/commit links. Sync adds that fourth fixed
file with `--include-navigation`. Old configurations without this option keep
their original file set; project READMEs are never overwritten automatically.
Use `scripts/round_reporting.py` to export from actual round observations and
analysis; never invent missing metrics, start times or successful receipts.
The template is executable in `round_reporting.build` / `markdown`, keeping
machine and human records consistent. Fixed fields are identity, objective,
hypothesis, source/data/environment, parameters/change, observed outcomes,
project metrics, receipt/artifact references, analysis, retention and limitations.
`project_metrics` selects project-specific metrics; optional
`report_metric_mapping` names actual fields. Missing values are recorded, not a
new scientific gate. Failed or interrupted rounds also receive records.

Follow [training-operations.md](training-operations.md) for the minimum record
needed before a successor. A small diagnostic can use a short record with links
to existing evidence; do not require a repeated full qualification dossier.

Configure `result_records` in project inputs with `repository` (the authorized
local personal checkout), optional `directory` (default `docs/sdm/runs`), and
`sync` (explicit Git synchronization authorization). For sync also provide
`remote_url`, `branch`, `owner`, `python` and installed platform `sync_script`.
For a Linux coordinator invoking a Windows platform helper, `repository` is the
Linux-visible checkout path and optional `sync_repository` is its Windows path;
`python` / `sync_script` are the executable/argument paths usable by that process.
`report_data` carries the real dataset identity/version, never a guessed version.
Default is local-only export when no synchronization authorization is supplied.

Layout: `<directory>/<run_id>/index.md` plus `round-NN/result.json` and
`round-NN/report.md`. Git records training completion separately from export or
commit time. Retrospective export of existing runs is not a new execution.

Before stopping a space, retrieve the complete reported logs, actual resolved
configuration and every reported training checkpoint using installed
`lpai-s12-platform/scripts/archive_round_artifacts.py`. It uses the exact native
UID, advertised SSH endpoint, explicit project key, bounded paths and transfer
budget. It verifies signed content digests against remote files and retained
copies, records remote mount facts and retains everything without auto-deletion.
A verified copy on the configured host survives devspace shutdown; remote mount
type alone is not a promise of remote lifecycle retention. Keep private artifact
directories outside tracked Git paths. Missing or failed transfers remain visible
as `partial`; keep originals, do not overwrite, delete or rerun training to cover
the gap. Cleanup still saves environment then stops the space. Retrieval helper
never starts resources or trains. Re-entry verifies existing copies.

Only small result records go to Git; raw data, large logs/checkpoints and secrets
stay in original platform storage and/or the verified host archive. Public
records retain artifact URI, SHA256, sizes and local retention locations. Review
project text/commands for credentials before sync; the helper also rejects known
key/token/credential-URL patterns. This scan is not a general secret classifier.

Use installed `gitlab-s12-platform/scripts/sync_round_records.py` for delivery.
It verifies authenticated owner, exact personal origin/push URL and current
non-main branch. It commits only this round's two files and index, excluding
unrelated dirty/staged files; pushes without force and confirms remote HEAD.
Result-only pushes use `ci.skip`. It does not create MR, merge, or issue governed
Git S13. Retained signed S13s remain the execution authority.

Delivery/sync errors are separately recorded in `record-delivery.json` /
`git-sync.json`. Retry delivery alone; no duplicate training. Analysis-pending
exports may be updated when analysis arrives. `run_project_loop.py` invokes
retention before analysis and cleanup, exports observed/failed rounds, and
synchronizes configured reports. This does not rewrite historical engine state
or install changed Skill bytes into a previously bound host.
