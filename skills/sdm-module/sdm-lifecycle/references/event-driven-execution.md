# Completion-driven background execution (opt-in v1)

Use for new frozen integrations that need execution while the desktop chat is
idle or busy. Do not resume a chat timer or queue a synthetic user message.
The independent dashboard stays read-only.

For the new component pipeline and persistent research context, use the opt-in
v2 described in [component pipeline](component-pipeline.md). The v1 contract below
retains isolated execution tasks and its original frozen identity; do not migrate
active v1 services in place or infer component qualification from its controls.

## Ownership and sequence

1. The owned Runner publishes terminal `process.json`, then
   `completion-event.json` with the exact attempt binding and state SHA-256.
   This reports process termination, not artifact verification or study success.
2. A persistent host-initiated SSH stdout stream runs the frozen remote
   `worker.py --follow-completion ROOT BINDING`. Linux inotify subscribes before
   the initial read. A reconnect replays the durable event. If the Runner died
   between terminal-state and event publication, the stream reconstructs the
   identical event from the bound terminal state; it never reruns computation.
3. `scripts/research_event_service.py --config SERVICE.json` owns one OS lock,
   transactionally receives events, rejects conflicting terminal events and
   serializes registered tasks. A condition variable wakes execution on arrival;
   no interval-based model invocation or front-chat message is involved.
4. For research tasks, a locally registered `coordinator` invokes the original
   public entry with frozen project/runtime digests. The original locks, Generator–Critic
   receipts, archive/consumption and Guard retain authority. A process-exit event
   cannot assert review completion or authorize another experiment. If another
   observer owns collection, preserve its identity and reconcile instead of
   starting a second controller.
5. The registered background Codex context receives `ExternalMessage` tool output
   through the official pinned Python SDK. Instructions come from local frozen
   registration, never the event. SDK `thread/turn` IDs and actual terminal events
   distinguish received/preparing/running/completed/failed/unknown. Code tasks
   explicitly choose workspace-write; Generator–Critic ModelRunner remains read-only.
6. The dashboard reads the operational delivery SQLite database in `mode=ro` and
   streams changes through SSE. It shows model IDs, current activity, final text,
   source errors and service availability. Its HTTP routes cannot execute work.
   This operational inbox is not a second scientific result ledger.

## Registration and control

The `sdm-event-service/v1` configuration pins absolute `runtime_root` and every
top-level Python source hash in `runtime_manifest`, `root`, `cwd`, `codex_home`,
`sdk_root`, CLI path/SHA-256, model, timeout (30–3600 seconds), sandbox and a
`tasks` mapping keyed by exact execution binding. Each task declares instructions,
evidence scope and optional UTF-8 evidence path/SHA pairs. Events cannot supply
commands, prompts, model selection or writable roots. SDK 0.163.0 and the selected
official CLI must be explicitly qualified. This version inherits host user config
with recorded overrides; it does not claim a globally isolated model profile.

An optional `source_argv` is a registered argv array, not shell text. For SSH use
the original approved host/key and strict known-host checking; no public HTTP
listener or remote callback credential is required. The HTTP `/completion`
receiver binds only 127.0.0.1, requires its generated bearer token, rejects browser
Origins and commits before returning 202. It is an integration input, not a
dashboard execution API. Never expose `ready.json` tokens in frontend snapshots.

The coordinator registration has absolute `project`, `entry`, `output`, exact
`project_sha256`, complete Python `runtime_manifest`, and explicit
`allow_devspace_live`. Normal `continue` mode retains the existing execution
scope and route-specific platform gates. `completed-replay` **forbids live scope**,
requires a pinned completed/released execution-state SHA, and invokes only the
new public `--research-status` view. That view verifies retained result bytes,
numeric targets and the consumed review's project binding without executor,
platform or model imports. It cannot consume a pending review or launch work.

For a Python-free console window use the existing hidden-process convention and
redirect logs; run the registered service with `pythonw -B` on Windows. Management:

```text
python -B scripts/research_event_service.py --config SERVICE.json --control status
python -B scripts/research_event_service.py --config SERVICE.json --control pause
python -B scripts/research_event_service.py --config SERVICE.json --control resume
python -B scripts/research_event_service.py --config SERVICE.json --control stop
```

Management authenticates the live PID/project/config before controlling the
service. Pause retains pending events and does not stop an active experiment.
Both source transport and SDK helpers join existing Windows Job containment
before loading executable code. A lost service owner terminates its local child
tree; this is not a claim that remote/provider work was cancelled. Restart
recovers a completed receipt or marks interrupted active work unknown; it never
blindly repeats a command/model call. Unknown tasks require evidence reconciliation.

Bind the frontend project with `event_execution: {config: ABS_SERVICE_JSON,
sha256: FILE_SHA256}` and matching `research_observation.project_id`. Keep the
scientific store and registered evidence roots unchanged. A closed frontend or
failed SSE connection cannot gate execution.

## Qualification limits and observed failure

Local acceptance is explicitly non-ML: Linux arithmetic process → kernel event
stream → actual SDK code/command task → result artifact and frontend. A retained
CIFAR terminal-view replay is not another GPU experiment or proof of pending
review consumption. Existing original-worker consumption has separate evidence.

WSL DrvFS (`/mnt/d/...`) did not deliver the live directory notification in the
first probe; the terminal file survived and reconnect readback worked. The native
ext4 control passed. Qualify the target devspace filesystem before using inotify
there; do not silently replace a failed push with periodic chat wakeups. Source
disconnect is visible and same-identity reconnect is safe; automatic network
reconnect supervision and OS startup registration are not implemented in v1.

Per-binding task registration and source selection are explicit in this version.
Automatic registration of newly frozen plan versions, native unattended
start/stop, and the component-library route need their own actual qualification.
Do not remove `independent_native_lifecycle_not_qualified_keep_active_agent`,
migrate an old frozen runtime in place, or label the whole research project
unattended on the strength of this transport/control acceptance.
