# Lifecycle protocol

`assets/lifecycle-steps.json` is the sole ordered 21-step lifecycle spine. A step advances only when its executable gate tree passes. A step may complete deterministic work, synchronously route to its owning SDM Skill, wait for one authority action, or create one S12 per required platform operation. An instruction or prose response is never evidence.

Excluded work is triggered only through the four genesis-pinned bindings: `data_platform_skill` for the data platform, `source_control_platform_skill` for the source-control platform, `ci_platform_skill` for the CI/Jenkins platform, and `ml_execution_platform_skill` for HPO, training, locked evaluation, and robustness evaluation. The coordinator writes the immutable S12 packet and waits; outer Agent Skill activation executes the binding and returns an S13 carrying producer authentication material. Ingestion authenticates it against that workspace-bound request. The coordinator never imports or calls the configured Skill and there is no manual-evidence bypass.

Keep these dimensions orthogonal:

- Experiment: draft, readiness-review, frozen, executing, evidence-review, scientifically-terminal, archived, cancelled.
- protocol channel: preserve frozen research channel metadata, including the historical `promotion` label and `promotable` field, solely as classification. Exploratory is the default, and regression uses protected fixed baselines/G10 tolerances. These fields create no operational capability or activity beyond step 21.
- implementation: backlog, in-progress, review, done.
- Run: planned, dispatched, running, evaluated, terminal; every retry is a new Attempt under the same Run.
- validity and scientific result: invalidity is not system failure; D05 is exactly `hypothesis_supported`, `hypothesis_rejected`, `inconclusive`, or `invalid`.
- coordinator/handoff: pending, dispatched, result-ingested, cancelled, reconciliation-required, reconciled, superseded.

Planning readiness (S04), protocol readiness (E02), and post-implementation preflight (S09) are distinct and cannot satisfy each other. E04 binds the stable candidate digest of E01 content. Step 8 is a sequence of current receipts, provisional domain event, external anchor request/result, anchor acceptance, and terminal event; its provisional, frozen extension, and terminal payload persist the exact candidate-to-frozen-envelope digest map consumed by Run materialization. Step 15 conditionally executes HPO search and selection, retains every trial/Attempt, and then runs independently seeded confirmation training. The frozen search plan and selection rule are expectations; R07/R08 and the S13 result bind actual trial configurations, objectives, partitions, seeds, environments, outcomes, resource use, and artifact inventories. Replay compares plan to actual and materializes confirmation from the selected configuration without treating the best search trial as confirmation evidence.

S03 is an immutable BMAD planning index, not a set of path strings. Its Product Brief, PRD, and Architecture entries are exact `{kind, uri, digest, revision}` objects with normalized query-free `bmad://` URIs and non-floating revisions; its `source_refs` binds exactly one S01 Product Requirement. S02 binds that same S01/S03 closure. Step 6 re-opens S02 and requires the caller's S01/S03 selectors to equal that closure byte-for-byte before E01/E04 can be published, so an Experiment cannot substitute another PRD or Architecture.

Resume first validates the pinned engine, full ledger, artifacts, graph, authority, packets, and projection. It chooses the earliest admissible action by `(lifecycle_step, subject_type, canonical_instance_key)` and returns every other admissible action in that order. It does not trust a partial ledger or caller-supplied origin.

Frozen changes are classified by event sequence and CAS revision relative to the first affected Run dispatch/start. A pre-Run non-material accepted change is E06; an observed execution departure is E07; a material or post-result semantic reframing requires a reciprocal successor Experiment. Originals and all terminal, published, frozen, or superseded records remain byte-identical.

D05 requires D01 to partition every planned Run into its required Attempts and to account exactly once for every Attempt's terminal status, validity, and system-failure fact. Failed, cancelled, invalid, HPO-trial, confirmation, and locked-evaluation Attempts remain in accounting even when they do not contribute scientific evidence. Accounted but insufficient valid evidence follows the frozen inconclusive rule. The dedicated scientific-decision command publishes the terminal D05 and atomically completes Step 20; generic advance cannot choose an outcome. Step 21 offers reciprocal scientific successor work for every terminal outcome,
including formal supported results. Archive/close is an authorized alternative.
D05 supersession retains immutable reciprocal decision links. Closure accounts
for every child Run, human wait, handoff, and exception and uses the two-phase
anchor. Terminal cursor 22 means all 21 numbered activities are complete.


T01 is rebuilt from authenticated events and artifact references. It admits only governed artifact types plus Run and Attempt, requires every node ID to match its declared type, and must trace Product Requirement/JIRA and BMad planning objects through Experiment, Run/Attempt, receipts, evidence, and D05. Each typed downstream node retains its exact replay-derived upstream identities, so a same-type endpoint substitution fails even when cardinality still looks valid. Its artifact instance revision must equal the projection's derived workspace revision. Public lifecycle, handoff, and migration mutations validate the graph before and after their commit boundary. Missing, substituted, duplicate, cyclic, or asymmetric edges block the affected gate without changing state.

Artifact validation runs central and owner schemas before artifact-specific semantics. Common identity, URI/digest, and signature-encoding rules still report findings, but semantic interpretation is skipped when the structural shape is invalid; malformed nested values therefore fail closed rather than escaping as runtime errors. A standalone artifact check proves the supplied envelope, closed shape, digests, and artifact-local relationships only. Platform receipt authenticity and exact closure against the workspace are replay properties: they require the genesis-pinned trust material, exact S12/S13 correlation, artifact reads, ledger replay, projection/T01 validation, and the applicable transition gate.

Unsupported historical engine bundles must be rejected before any mutation.
Never bulk-migrate frozen workspaces or reinterpret their original ledger bytes.
