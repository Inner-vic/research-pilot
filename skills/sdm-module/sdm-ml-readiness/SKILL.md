---
name: sdm-ml-readiness
description: Assess the three distinct SDM readiness surfaces and gate only an active SDM workspace. Use when SDM-scoped sprint planning, experiment freeze, or Run materialization needs its applicable readiness decision.
---

# Assess ML Readiness

## Overview

Act as the independent readiness gate for one governed SDM workspace. The
outcome is an executable, sourced result—not a prose assertion—that a planner,
freeze authority, or Run materializer can consume without this conversation.

Load module config from the four-layer TOML resolver. If
`sdm_active_workspace` is empty during a sprint-planning hook, return a no-op.
Otherwise resolve the exact installed `sdm-lifecycle` Skill and use its
`scripts/specctl.py`; never import a sibling through a relative path.

Keep these surfaces orthogonal:

- S04 planning readiness gates only SDM-controlled sprint planning and creates
  `ml-readiness-report.json`, `blocking_gaps.md`, and
  `recommended_engineering_stories.md`.
- E02 immutable protocol readiness gates freeze and binds the canonical E01
  candidate digest, contracts, baseline, data, evaluator, budget, independent
  review, and current platform receipts.
- S09 post-implementation preflight gates Run materialization after engineering
  acceptance; it never reuses S04 or E02 as proof.

Use `references/readiness-contract.md` for exact evidence and routing. If a gate
needs data-quality or Jenkins work, create the S12 request through `specctl`,
activate the configured outer platform Skill, and ingest only its exact S13.
Blank bindings return `configuration_required`; manual evidence is forbidden.

Return one of: a passing governed artifact, one human-approval wait, one exact
platform handoff, or a sourced block. Never claim readiness from prose.

## On Activation

Resolve module configuration and the active workspace, select exactly one of
S04, E02, or S09, and use only that surface's executable rules.
