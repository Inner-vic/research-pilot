# Independent worktrees and frozen execution source

Use for independent code directions or a control/treatment pair. Parameters alone
do not require new branches. Reuse an owned active worktree for the same direction;
preserve dirty files and identify the exact base commit. Never copy uncommitted
changes implicitly or substitute a later reporting commit for trained source.

For Codex-managed worktrees, use the host's create/attach/restore/archive tools
and their returned identities/snapshots. Do not remove managed directories with
the local helper. For project-owned local Git checkouts, use
`scripts/worktree_isolation.py` with [worktree-plan.template.json](worktree-plan.template.json).
Creation stays within authorized project preparation scope. `--apply` records
mutation intent, not human approval or compute authorization.

```text
python -B <installed-skill>/scripts/worktree_isolation.py plan --plan <project-plan.json> --record-root <private-arm-records>
python -B <installed-skill>/scripts/worktree_isolation.py create --plan <project-plan.json> --record-root <private-arm-records> --apply
python -B <installed-skill>/scripts/worktree_isolation.py freeze --plan <project-plan.json> --record-root <private-arm-records> --configuration <resolved-config-file> --apply
python -B <installed-skill>/scripts/worktree_isolation.py validate --plan <project-plan.json> --record-root <private-arm-records>
```

The local helper requires an exact available commit, explicit fresh branch,
allowed worktree parent, exact target and private records outside that checkout.
It records plan digest, repository/common Git identity, branch/path, ownership
nonce, phase and source-dirty finding. A Git metadata marker proves local helper
ownership; a directory name alone does not. Process locking keeps one writer.
Re-entry reconciles the same worktree and preserves edits. Missing ownership after
an uncertain creation blocks adoption rather than blindly repeating the command.

Each attempt needs separate outputs/logs/checkpoints and mutable build/cache paths.
Worktrees share Git metadata and do not isolate interpreters, GPUs or mutable
mounts. Check those through project preflight. Shared immutable data is allowed.
The helper does not fetch, push, switch main, merge, reset a branch or start compute;
Git hooks are disabled. Checkout filters and tracked symlink/submodule snapshots
need project-specific support; unsupported cases remain capability gaps.
Hidden assume-unchanged/skip-worktree index flags block freeze/retirement rather
than allowing a clean Git status to conceal different source bytes.

Before binding a run, commit intended code and freeze commit/tree plus resolved
configuration bytes. Source must contain no modified, untracked or ignored files;
keep generated runtime files outside it. Private records retain a verified Git
bundle, config copy and digest-bound manifest. Bundles can contain repository
history; keep them private, outside tracked Git files and Pro attachments.
Validate the frozen HEAD, config and bundle before normal project preparation/S12.
New source/config needs a fresh snapshot/binding, retaining old versions. Freeze
detects drift; it does not physically lock files or replace actual runtime binding.
No source/config edits occur during a run bound to that snapshot.

Bind source/config and optional snapshot reference in
[experiment-comparison.md](experiment-comparison.md). Data, image, adapter,
evaluator and resource identities stay separate.

Before retirement, the coordinator checks no active/pending model task or collector
relies on this checkout, and archives external outputs through existing rules.
The Git helper does not query platform leases; removal never means GPU release.
Local `retire ... --apply` requires exact owned registration, unchanged frozen HEAD,
clean source and verified bundle/config. It uses normal `git worktree remove`
without force, retains branch/archive/records and verifies exact path/registration
absence. Modified, untracked, ignored, foreign or unarchived work blocks removal.
Preserve it, or commit/archive through authorized project work; never force cleanup.

`restore ... --apply` recreates a retired checkout only if its retained branch still
points to the frozen commit. Moved/deleted/checked-out-elsewhere branches require
reconciliation, without resets. The bundle retains source independently of the
checkout. Restoring elsewhere/from another repository is a separate concrete
operation. Recovery reconciles lifecycle intents and ownership before mutation.

Primitive semantics: [Git worktree documentation](https://git-scm.com/docs/git-worktree).
Model correctness, remote concurrency and production readiness are separate claims.
