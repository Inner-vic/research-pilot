---
name: sdm-build-context-pack
description: Coordinate BMAD engineering steps 9-13 from frozen protocol through deterministic acceptance and post-implementation preflight. Use when planning, building, reviewing, accepting, or preflighting SDM-scoped code changes.
---

# Build Coding Context

## Overview

Own the synchronous engineering outcome for lifecycle steps 9-13. Compose the
installed `bmad-create-epics-and-stories`, `bmad-sprint-planning`, `bmad-build`,
`bmad-code-review`, and `bmad-qa-generate-e2e-tests` Skills at their native
surfaces; do not merely tell the user to run them later.

Build S05 stories and S06 Context Packs from the frozen E01 and Architecture.
Each Story needs deterministic acceptance. The Coding Agent sees only the Story,
relevant Architecture, resolved contract fragments/digests, E01
`change_only`/frozen/forbidden rules, tensor interface, allowed paths, required
tests, and explicit prohibitions. It never sees hidden labels, evaluator source,
split credentials or other operational secrets.

Git and Jenkins operations are excluded. Create exact S12 packets for branch/
change request, review/merge status, implementation checks, deterministic
acceptance, and post-implementation preflight; activate configured platform
Skills in the outer runtime and ingest their S13 packets. Engineering `DONE`
requires code/test/interface acceptance, never model metrics. S09 remains a
distinct Run-materialization gate.

Use `references/engineering-contract.md`. End with governed S05-S09/S12/S13
records and a transition, one outer handoff, one approval wait, or findings.

## On Activation

Resolve the active workspace and pinned engine, then execute the earliest
admissible engineering step through its installed native Skill surface.
