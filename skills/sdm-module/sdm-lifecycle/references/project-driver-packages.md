# Project driver packages and execution hosts

Keep using `project_driver`; no parallel project-provider interface is required.
Detached paired scheduling is an optional platform extension described in
[paired-platform.md](paired-platform.md). It reuses these byte-binding rules for
the platform adapter and project-owned hooks; existing ordinary drivers and
their S12/S13 evidence paths retain their contracts.
The historical `{path,digest}` binding remains supported. For new drivers that
import their own runtime, collectors or data-policy helpers, freeze the complete
adapter directory. Keep model code, weights, credentials and run outputs outside
this small directory. Platform modules remain in their separately installed,
request-bound Skill tree.

## Preparing a package

```text
python scripts/package_project_driver.py --source <adapter-source-directory> --entry driver.py --file driver.py --file runtime.py --file data_policy.py --output <fresh-project-package-directory>
```

The command copies only explicitly selected files into `adapter/`, verifies bytes
and emits `adapter-binding.json` outside that sealed directory. It never executes
the files, updates an installation or modifies source files. Existing targets,
escaping/linked source files, ambiguous case names and excessive file/byte counts
are rejected. Failed preparation is not an active run; preserve it and use a new
output location after correcting inputs.

The emitted configuration extends the existing binding:

```json
{
  "project_driver": {
    "path": "/absolute/project/package/adapter/driver.py",
    "digest": "sha256:<entry-file-hash>",
    "files": {"driver.py": "sha256:<hash>", "runtime.py": "sha256:<hash>"},
    "bundle_digest": "sha256:<canonical-files-object-hash>"
  },
  "driver_runtime": {"mode": "native", "python": "/absolute/venv/bin/python3"}
}
```

`files` must equal every regular file under the adapter directory, including the
entry. No unbound extra file, missing helper or changed dependency is accepted.
Links/junctions, case collisions, `__pycache__`, more than 64 files, any file over
10 MiB or a total over 32 MiB fail. `bundle_digest` hashes the sorted, compact
UTF-8 JSON files object. These hashes pin bytes; they do not sandbox project code,
prove import closure or authorize it to read other projects. Project/platform
access still follows the user's current scope and installed bindings.

## Native and Windows drivers

`driver_runtime` is optional. Without it, historical Windows/WSL command and path
behavior is preserved. New configurations may choose `native` Python with native
paths, or `windows-wsl` with an absolute WSL-accessible Windows Python executable.
The same `--project`, `--node`, `--receipt` driver contract and existing task graph
apply. A project must declare the objectives it actually supports.

For new explicit runtimes or package bindings, `run_project_pipeline.py` routes
each task and cleanup through `project_driver_launch.py`. It revalidates the
package before starting the child and executes an argv directly, without a shell.
A binding failure saves a failed task receipt with `execution_started: false`;
it cannot silently use the old installed driver. Child completion alone still
does not replace accepted S13 for reproduction/training/evaluation. Native CPU
fixtures explicitly report synthetic observations and failed model execution.

Generic Loop adapters cannot override `driver_runtime` in a per-round patch.
Loop still validates the pinned driver before a round and cleanup; project-owned
cleanup/retention commands retain their existing contracts. Use the guarded
launcher for commands that execute package code. If frozen cleanup code has
changed, report cleanup-required and reconcile the resource using its original
owner identity; do not invent a replacement cleanup command.

## Incremental platform extraction

The LPAI candidate provides `devspace_transport.py`: generic platform query,
source staging receipt comparison, advertised SSH endpoint parsing and SSH argv.
It contains no model, dataset selection, loss, class or metric policy. Old helpers
and collectors remain untouched for historical bindings.

The prepared project-owned RoadModel package contains the model runtime, minimal
configuration helper, runtime inventory, bound execution collector, selected-data
collector, data policy/audit/producer and task driver. Local project modules take
precedence over installed legacy modules. Collectors receive explicit
`--platform-scripts` and verify S12 and operation contracts against that installed
platform tree, never against their own project directory. This package is a
migration deliverable, not an activated configuration or a live platform result.

Adopt it only through a fresh configuration and normal installation/binding
checks. Keep earlier requests, signatures, manifests and clean trained source
checkouts unchanged. Scientific execution and real platform validation remain
separate from package import/CPU software checks.
