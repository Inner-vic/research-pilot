# Shared SDM acceptance in the 13-step lifecycle

New studies use the same `sdm-research-initialization/v3` and controller.
Project integrations use their frozen project facts; qualified binary-component
studies retain workload v2. See [project integration](project-integration.md). Older initialized/frozen studies keep their original policy;
legacy v2 is not a fallback for bypassing a missing new obligation.

`research_acceptance.py` is the shared acceptance adapter to the existing SDM core.
It reuses original contracts, row checks, decision evaluation, proposal review,
Manager/Guard, Context and result verification. It does not issue signed formal
transitions or claim full original 21-step qualification.

## Project integrations

`project_governance` authors applicable original contracts and B01 from the actual project descriptor. The SAME object/semantic/row checks, experiment kernel, scientific rule, review, dispatch and completion govern both profiles. G07 now explicitly represents continuous targets as well as the original discrete classes; its owner schema and catalog binding evolve together. G02 still requires the exact G07/G08 closure. Old frozen normative inputs are unchanged. This is an explicit ResearchPilot extension of the retained original template, not an unchanged upstream schema.

Project functional checks and deterministic prediction recounts adapt evidence; they do not replace acceptance. Both result profiles call the same original submission-identity verifier. The sections below retain binary-profile-specific requirements; they are not imposed on arbitrary projects.

## Inputs from actual project facts

Retain ordinary initialization inputs and actual S01/S02 content in
`research_intent`. Add this agent-prepared fragment, using real pinned refs and
scope; do not ask the user to invent artifacts or missing authority:

```json
{
  "sdm_governance": {
    "schema": "sdm-component-governance/v1",
    "data_source": {"path": "ABSOLUTE_TRAINING_SOURCE", "sha256": "EXACT_SHA256"},
    "max_submissions": 2,
    "variables": {"independent": "declared intervention", "dependent": "fixed validation macro F1"},
    "author_principal_ids": ["actual-research-owner"]
  }
}
```

Workload v2 retains adapter `binary-component/v2`, training_loss_hook,
seed_values and source_ref, adding
`engineering_preflight:{version:1,max_steps:32,examples_per_class:2}`.
This bounded tiny-fit cost belongs to the proposed/authorized remote task.
Limits are 8–64 total updates including the two checkpoint-continuation updates,
and 2–4 examples per class. The initializer does not
run it locally. A custom training loss must be an nn.Module without separately
learned parameters; state must be registered for checkpoint restoration.

`component_governance_input` derives original G01/G02/G03/G07/G08/G09/G10/G13
and B01 objects from actual data, original S02, pinned source and model bytes.
It is an authoring adapter: every object still goes through original validation.
G08 records the actual identity transform. G02 closes exact applicable references
and full artifact digests. S02's explicit applicability must match. The adapter
checks finite univariate binary input, fixed length, actual source/split hashes,
baseline constants, model identity and protected trainer hash without importing ML. The qualified producer must match
the retained public binary trainer template. Required model factories are explicit
function definitions with compatible positional signatures; dynamic imported-only
exports require an explicit wrapper rather than bypassing the ABI check.

For this qualified exploratory adapter, spatial geometry, calibration,
synchronization, robustness qualification and deployment are explicitly outside
scope, with reasons retained for G04/G05/G06/G11/G12. A task requiring them must
gain an actual adapter/acceptance path; it cannot silently remove those duties.
The present component adapter does not qualify other workloads or formal HPO.

## One acceptance path

1. Original S01/S02 schemas, semantics and row gates run before initialization
   effects and in real review/replay. S02 binds the complete S01 artifact digest.
2. Original applicable G object schemas/semantics and exact source closure are
   checked, with original B01 row 4. `check_experiment_kernel` projects original
   E01 scientific fields, uses the original variable checks and row 6 gate,
   and shares `validate_scientific_rule` with the original decision evaluator.
   This is an explicit ordinary-work adaptation, not a fabricated full E01
   with B02/R04/R05 anchors. Scope, variables, hypothesis and budget remain frozen.
3. First and successor submissions use the existing `component_submission`
   verifier: exact scientific/code review replay, actual payload, comparison,
   original Manager plan, user delegation and authority. Both reviews bind the
   same governance seed. Actual initial model and protected trainer must match.
   Initial baseline establishment is explicit; successors need a verified baseline.
4. The core freezes each Run scientific kernel from the actual accepted S02 and
   successor intervention, using the same original kernel validator. The predecessor
   is retained; revised framing never silently rewrites its frozen hypothesis.
   The core persists immutable Run/Attempt registration before Manager.apply.
   It extends the existing candidate budget to bounded fresh studies; one bound
   limit counts the initial task and successors, including failed/unknown intents.
   Unknown Guard intent reconciles under its original identity, never resubmits.
5. The remote protected trainer checks tensor shape, finite outputs/loss/gradients,
   bounded tiny-fit and exact checkpoint continuation before full fitting. It
   restores model, optimizer, criterion, RNG and training modes. Result.json
   retains proof within the existing four-artifact result ABI. Failed preflight
   stops full fitting; syntax and model agreement cannot supply this proof.
6. Original result verification checks provider/task/payload identity, complete
   artifacts, protected split and metric recomputation. Failed terminal evidence
   remains in accounting without becoming a scored candidate or being mislabeled
   as a proven infrastructure fault.
7. Before any accepted next direction or closure, original Generator–Critic replay
   covers the exact retained results. The Critic returns protocol, statistics,
   failure_cases, reproducibility and deployment dispositions plus reasons.
   The adapter constructs S10 from actual critic provenance and runs original
   object/row 19 checks. Unresolved deviations block approval; a revision may
   retain them. Unsupported deployment cannot receive a fictional pass.
8. The original scientific decision evaluator consumes the frozen rule and all
   registered terminal Attempts. Adaptive fixed validation without a confirmatory
   protocol yields inconclusive. Operational target attainment stays separate.
   An accepted successor returns through the same implementation and dispatch path.

## Execution order and shared evidence

The 13 activities describe obligations rather than 13 separate jobs. Remote
functional preflight requires an authorized container: Run registration/task
allocation therefore precedes the remote portion of step 8; full fitting stays
behind that gate. This ordering is explicit, not a claim that all preflight
was already complete before submitting the container.

The owning frozen core validates recovery and publishes `acceptance.json`, bound
to exact configuration, state and runtime manifest digests. Progress and Web
consume that same projection. A stale/missing/corrupt projection is unavailable,
not silently accepted. The Web never rejudges a frozen study under a newer
checker. Coverage is cumulative and references a specific Run; prior evidence
cannot accept an unexecuted successor. Current runtime position remains a
separate factual pointer within the same lifecycle.

## Preserved boundaries and remaining qualification

The native bundle/freeze retains the original eleven engine assets, eight common
schemas and seven owner schemas. Canonical engine identity and raw manifests
are checked. Windows reads them through the existing safe handle adapter;
full original journal/workspace transactions retain their supported Linux host.

Normal component evidence is not signed S12/S13/S14, formal Git anchoring,
B02/B03 platform lock qualification, complete D01–D05 formal artifact publication,
or a confirmatory statistical/generalization result. Those claims require their
actual original acceptance paths. Nothing mints a formal receipt or user click.

Original frozen runtimes, Context, Guards, requests and evidence remain unchanged.
Installing or opening the updated entry does not start a study or resume timers.
Software control fixtures use explicit model/platform substitutes; only a new
scoped, authorized remote run can verify the emitted ML preflight and real flow.


## Remote preflight ordering and retained prepared submissions

Static payload/platform checks run before submission. The authorized component
task allocates the remote environment for step 8, runs bounded functional checks,
restores the initial state, and only then fits the full model. Steps 8 and 9 thus
overlap in physical scheduling. Allocation is not preflight acceptance; evidence
coverage remains pending until actual proof is recovered. The global authorization
must also bound the sum across tasks (the real two-task check used 16 per task).

The first successor shares its producer Attempt with the initial result. Its
immutable submission/core receipts are namespaced by the exact Manager-plan hash,
preserving the baseline receipt. A narrowly checked prepared-submission handoff
requires an exited owner, completed native/jobs, the proved path collision and a
still-new original Guard. It retains the original plan, authorization, Context,
registered Runs and budget in a new frozen owner. Existing review receipts replay
through their original frozen verifier; any scope/budget difference or original
rejection blocks the handoff. It cannot settle or replace an unknown request.
