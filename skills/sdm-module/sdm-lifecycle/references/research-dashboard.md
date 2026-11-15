# Research dashboard

Use when the user asks to see research progress, experiment records, metrics or
research lineage. Keep guidance and new instructions in the current Codex chat.
This optional local Web service projects existing SDM records and exposes one
explicit user action: pause-study, delegated to the original frozen controller.
It never submits training, invokes models or owns a coordinator. User pause/continue controls delegate to the original SDM host or the restricted restore broker.

1. Use the project's active-study index from inspect. Resolve bundled scripts
   relative to this lifecycle directory, not an old source checkout.
2. Read project `.bmad-ml/dashboard/ready.json` if present. Verify its project_root,
   data_read_only flag (legacy services use read_only) and `/health` service/instance/PID before reusing its URL.
   A stale ready file is not proof of a live service. Do not stop a process whose
   identity cannot be established. Do not create a second study to populate a UI.
3. If no matching dashboard is listening, start `scripts/research_dashboard.py
   --project-root PROJECT --port PORT` in a hidden background process using the
   project's configured Python interpreter. Default port is 8792; on conflict
   choose a free port, preserving any unrelated service. The script writes the
   ready file only after binding. Capture stdout/stderr locally.
4. Open the ready file's full URL (including fragment token) in a browser and
   verify the visible project/status. Give the user a clickable link. Do not
   ask the user to run a CMD launcher, type a command or select internal nodes.

Only loopback 127.0.0.1 is served. Data endpoints require the session token,
Host and Origin checks. The token is delivered through the URL fragment, stored
in browser session storage and removed from the visible URL; never publish it
externally. No CDN or external analytics. Record edits and all PUT/PATCH/DELETE requests are rejected. The sole POST
control route is `/api/study/pause`, described below.

The dashboard discovers `.bmad-ml/CURRENT.json` and one-level subdirectory
CURRENT indexes. It validates study config/result digests, reads registered
result.json files and opens delivery.sqlite in read-only mode. Registered text
evidence is previewed only after its digest matches. Invalid results are omitted
with an explicit integrity issue. Artifact reads/metric arithmetic do not train
or evaluate a model. Never load weights in this service.

The Web view separates two complementary projections:
- Mainline: the currently proposed/submitted experiment, or latest completed
  experiment, plus only its actual selected-candidate ancestors. It never uses
  chronological adjacency as scientific ancestry. A result that triggered a
  proposal can be a separate evidence reference. The old ordered history list
  remains selectable inside this view.
- Research lineage: reuse the user's IterLab X6/Dagre component. Include all
  registered positive/negative trials and outcomes, recoverable historical
  reviews, analysis, implementation, diagnosis and blocked records. Show explicit
  recorded relationships; graph branches do not schedule research. Distinguish
  a source result from the proposal/code work that follows it. Group/fold/search
  affect presentation only, never delete evidence. Unrecorded thoughts/chat
  messages are not invented as nodes.

Bind the page to project_root and the registered SDM study_id. Display the
actual persistent background implementation thread_id separately. This is not
synchronization of the foreground Codex chat transcript. Never guess an origin
chat ID or emit a fake chat deep link. Loading the Skill performs inspection;
only when an actual study is created and its CURRENT index is published does
it appear automatically. Reopening the Skill reuses that identity. The Web
service projects the existing study and has no independent research database.

For Chinese users show Chinese labels, parameters and evidence-backed Chinese
briefs by default. Project-local `.bmad-ml/dashboard/zh-briefs.json` binds a local
summary to the original intervention's SHA256; it is a display summary, not a
verbatim translation, original model receipt or new scientific conclusion.
Generic type labels must identify themselves as indexes. Code/model identifiers
and deliberately opened original technical evidence remain unchanged for
verification. No Web-driven translation/model service is authorized. Do not
silently create one or send research materials to a translation provider.

SSE refreshes the visible snapshot when SDM files change. Its bounded file
observation is presentation only: it cannot wake Codex or drive the research
loop. Original supervisor/model/Guard receipts remain authoritative. Timers stay
paused. Unknown/blocked states must be displayed honestly; this UI does not fix
or conceal core execution failures. Host status is the last supervisor record,
not a guarantee that its process is alive.

New source/install versions include this optional module. Existing frozen
research runtimes need no migration to be viewed. The user may continue using
Codex without running the dashboard. To stop the dashboard, first verify its
ready/health/process identity, then stop only that owned Web process; never stop
the research host. Guidance edits remain in Codex. Host restoration is delegated to the core broker. Pause/resume is a
separate authenticated delegation; research records remain read-only. UI style attribution is in dashboard/NOTICE.txt.


## User pause takes precedence

When the user says a validation study is no longer needed or asks to pause,
immediately stop new progression under its original identity. Do not keep
iterating toward the quality target, restart to test a button, or treat a paused
study as goal-met. Snapshot the platform task and outstanding calls separately.
An already completed task needs no platform stop. An already submitted workload
can continue independently: do not claim the Web pause stopped that workload.

The page header exposes **暂停研究** for an active registered study. Its request
binds study_id, original config SHA256 and a fresh UUID. The authenticated local
server resolves paths from registered indexes, verifies the original live host
and frozen public CLI bytes, then delegates drain + pause. If the current job
is reviewing a plan or code, its exact original review control is also paused,
preventing a fresh model call after the already-issued call returns. Retain its
actual completion receipt; do not kill/replay an unknown model effect.

Show **暂停收尾中** until the original drain reports quiescence; then **已暂停**.
Model/workload outcomes and goal status stay unchanged. The result journal is
project-local `.bmad-ml/dashboard/controls/<UUID>.json`. Repeated requests reuse
that identity; an unresolved intent with no proven pause gate reconciles rather
than blindly retries. A stale config, unknown study, extra action, changed
script or foreign origin is rejected. The pause route never resumes research, stops platform resources or creates a new controller.

Codex may use the same frozen `research_event_service.py --config CONFIG
--control drain`, then `--control pause`, and the original registered
`run_project_pipeline.py --project REVIEW_PROJECT --output REVIEW_ROOT
--pause-research-worker` for an active review. A final `--control stop` safely
exits the local host after drain and writes the explicit stop marker, preventing
supervisor auto-restart. This is a user pause, not deletion or completion.
Recovery requires a later explicit user instruction and original-identity
reconciliation. Preserve timers in their already paused state.


## Checked resume

For a live, quiescent, user-paused host frozen with `research_pause.py`, the same
header shows **继续研究**. It posts only study_id, config_sha256, pause_generation
and UUID. The bridge calls the original frozen `research_event_service.py --config
CONFIG --control resume --expected-pause GENERATION --request-id UUID`. Codex uses
that same public contract. No Web model runner, scheduler or independent task store
is created. The resume contract cannot change the goal, plans, resources or grants.

The original host checks quiescence and original job/context outcomes first. A
paused proposal review gets an immutable per-call resume proof: original requests
and completed receipts remain unchanged. Resume changes only mutable steering;
completed calls are consumed under their original bindings, unknown calls block.
The original pipeline then registers one resumptive coordinator delivery under the
same review root, context and original Guards. Repeated UUIDs return the original
receipt, and stale pause generations are rejected. Partial/unknown effects require
reconciliation, never a new UUID to retry a model or submission.

A stopped/offline host is restored by `research_restore.py`, never by posting a
message to Codex. The asynchronous receipt binds project, registered index, original
config bytes, pause generation and UUID. The broker itself is frozen before launch;
closing the page or Web server does not interrupt the owned restore process.

Preflight checks the original frozen runtime, Context, original delivery receipts
and active model intents. Durable completions are reconciled through the original
runtime; unknown effects stop restoration and cannot be retried under another ID.
A capable frozen host starts in the original store. A legacy component-v4 host uses
the existing `research_runtime.adopt` helper at its proved known-task boundary,
retaining study ID, Context/thread, source task/Guard and all verified history.
Old code and history remain immutable. Completed old review outputs remain raw
registered evidence; their approvals do not grant execution authority to new code.

The new supervisor starts behind PAUSED/DRAINING gates. Only after exact ready
identity and quiescence are verified is the index atomically published and checked
resume requested. The page shows restoration phases; cancellation remains available
and drains already-started work. Queued, unissued jobs are paused without invented
terminal results and resume under their SAME IDs. A host pause also gates internal
review calls. Same-UID artifact recollection verifies full artifact/plan hashes and
reuses a row; it never counts another experiment or accepts changed task artifacts.

Codex can invoke the same public control with `research_restore.py --project-root
PROJECT --request-file REQUEST.json`. The request contains exactly study_id,
config_sha256, pause_generation and request_id, as exposed by the current snapshot.
No goal, resource, data or authorization change is accepted in this request.

The current validation study remains paused during engineering. Isolated real HTTP/
process startup is not a new ML experiment. GPU/multi-card and long-queue training
still require real workload acceptance; never substitute a fixture for that claim.

Progress/negative-result context is generated from verified candidates. A worse
latest result cannot evict the incumbent. Compare remaining gap, progress and failed
interventions before choosing continue/rollback/redesign; no trial-count stop and
no mandatory architecture replacement. Registered supplements read only uncovered
ranges, retaining byte limits, exact source hashes and unresolved missing fields.
Chinese explanatory prose belongs in original authorized calls. Existing raw English
receipts remain evidence; local summaries are not verbatim translations.


### Visible continue entry and compact layout

Every quiescent user-paused study shows **继续研究**. Supported live hosts resume
through their checked API. Offline/stopped hosts enter the asynchronous restore
broker and display progress plus **取消恢复**. Unsupported or unresolved boundaries
show a concrete blocked reason; do not claim a new controller was started or send
chat instructions automatically. The copyable manual instruction is only a fallback
for unsupported legacy routes, not the normal component-v4 path.

The top-left project navigation toggle is independent of the Codex app's sidebar.
Default to folded at widths <=1000px, with user expand/fold choice saved locally.
On phones folding hides project navigation; on wider windows retain compact project
icons with explicit accessible names and tooltips. It never changes study identity.

### Product layout and appearance

The graph occupies the remaining workspace height; focus mode hides navigation
and retains the original research control. Escape exits focus. Study and view
remain in the URL. Initial graph framing follows the latest observed plan/result,
including completed studies, without inventing an active execution position.

Appearance supports light, dark and system themes with a local preference and
pre-paint bootstrap. Information, appearance and refresh live at the sidebar
bottom. Collapsed projects use folder icons, status dots and full-name tooltips;
expanded navigation uses full project names. Source/connection/session details and
component notices live in the information dialog rather than permanent page copy.
These preferences and layout actions never start or resume research.

Control feedback must refresh even when the scientific snapshot revision is
unchanged. Save the original in-flight request in session storage before POST;
a response timeout or reload queries `/api/study/control?request_id=UUID` only.
This authenticated receipt read is independent of study snapshot availability.
Unknown effects retain their request identity and never trigger automatic POST
retries. A stopped/failed request must not leave a permanent loading button.

Experiment details keep class/supporting metrics collapsed by default. Their
P/R/F1 and one-vs-rest accuracy are derived from verified confusion counts with
explicit axes and undefined denominators; class deltas require matching recorded
protocol/source/split/selection, labels and support. Optional evaluator-reported
IoU is never fabricated. Current binary collection remains task-specific.

Binding is project-root + registered study ID + active config, with remote task
UID/Guard and durable results. Loading the Skill does not create a background
conversation or study. After initial study registration the Web discovers the
index. A recorded origin Codex chat ID is optional provenance, not execution
authority or transcript synchronization; the native implementation Context has
its own thread identity and may already exist. Use the information panel to
distinguish these.

### Local process disclosure

The default lineage projects plans and measured results. There is no global
full-process mode or parked/rejected filter. Click a plan node to automatically expand its registered process group and all
scientific descendant groups; reference-only links do not expand other branches.
Repeated activation closes that branch; selecting another plan closes the previous
branch and opens the new one. These node actions change visibility only. Membership follows accepted-plan paths; successor work is not presented
as the implementation of the result that triggered it. A lower metric, requested
revision or evidence request is not an abandoned/rejected research branch.

For a cloned source checkout, the project Skill shim can run scripts/setup.py
--no-install once to prepare the local SDK and workbench. Afterwards reuse the
saved .bmad-ml/ENVIRONMENT.json; ensure-dashboard.py checks/reuses the original Web
identity. Neither helper starts a study. Empty workspaces show onboarding.


### Ordered layout and explicit disclosure

Clicking a plan card itself (including keyboard activation or a search result)
automatically opens its full scientific descendant branch and registered process
groups. Repeated activation closes it; selecting another plan replaces the opened
branch. Process/result selection does not recursively
expand an unrelated branch. There are no separate header/card process expand or
collapse buttons. Reference-only edges never pull in other branches. Opening
clears search dimming and frames the selected branch; activating the same plan
again closes it. It does not
fold scientific descendants or change records. Current-location controls center
the actual projected target, without including the research root as context.
Draft candidate_decision.base_task_uid is its recorded baseline; missing identity
must not be replaced with the latest result or incumbent by guesswork.

The SDM host opts into an ordered tree layout: plan and measured result share a
vertical column, scientific baseline descendants progress downward, siblings
from the same baseline are ordered by experiment number from left to right.
Local process layers preserve recorded edge direction. Reference edges do not
determine coordinates. Parents center over the whole subtree span; asymmetric
research histories can remain asymmetric. No chronology or causal edge is
invented to obtain a symmetric picture. Vendored graph opt-in hooks retain
default IterLab behavior for other hosts and keep its notices and licenses.


### Embedded canvas space

Lineage uses one compact row for search, the current state and its information
popover, plus contextual record actions. There is no separate graph title/action
row or status strip. Counts, coverage and layout guidance remain in that popover.
The single focus control lives with the bottom graph tools; the native current
location control remains there too. Normal navigation stays compact and focus
mode retains the original research control. Moved controls return to their homes
before destroying a map, so changing studies or views never loses a button or
creates duplicate controls. This is presentation only and never starts research.
