---
name: sdm-create-ml-contract
description: Create and validate governed G01-G13 ML contracts. Use when data, geometry, calibration, synchronization, ontology, augmentation, tensor, evaluation, robustness, deployment, or traceability semantics need closure.
---

# Create ML Contract

## Overview

Produce versioned G01-G13 contracts that downstream engineering and experiment
work can execute without guessing. Stable semantics live here instead of being
copied into each Experiment. Read `references/contract-ownership.md` and use the
owned schema/template assets.

Resume the governed workspace through the installed `sdm-lifecycle` control
plane. Drafts may change through events until published; every published,
terminal, deprecated, or superseded version remains immutable and addressable.
Validate references, digest/cardinality, compatibility, cycles, and the
semantic validator named by the central artifact catalog before advancing.

Actual labeling status and data-quality checks are excluded operations. Create
one immutable S12 per operation, activate only the configured data-platform
Skill in the outer runtime, and ingest its exact S13. A blank binding or
malformed/stale result is a sourced block, never a manual-evidence branch.

Finish only with governed G01-G13 versions and a control-plane transition, one
approval wait, one platform handoff, or explicit findings.

For an immutable historical dataset, G05 may bind `validity.mode=snapshot` to
the exact dataset, split membership and calibration content digests, without an
expiry date. A missing physical accuracy report must not become a zero bound.
Use `uncertainty={status: not-measured, scope: diagnostic-only, reason: ...}`
only for an explicitly scoped diagnostic snapshot. This permits execution but
requires scientific evidence to remain underpowered; the coordinator rejects a
decisive scientific conclusion under an unmeasured frozen G05.

## On Activation

Resume the exact workspace, load its pinned artifact catalog, and select one
contract identity and version transition.
