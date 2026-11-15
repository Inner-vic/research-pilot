---
name: sdm-lock-baseline
description: Define, reproduce, lock, deprecate, or replace governed B01-B04 baselines. Use when formal protocol readiness needs a baseline or its lifecycle changes.
---

# Lock Baseline

## Overview

Create B01 semantic identity, immutable B02 lock, ID-keyed B03 reproduction
records, and authorized B04 deprecation records. Baseline names never substitute
for exact code, config, dataset, split, calibration, evaluator, environment, and
checkpoint anchors.

Use the installed lifecycle control plane and
`references/baseline-contract.md`. Git snapshot, ML-platform reproduction, and
locked evaluation are excluded operations: create separate S12 requests for
`git.snapshot`, `ml.baseline-reproduction`, and `ml.baseline-locked-evaluation`,
activate their configured Skills outside Python, and require every exact S13 in
the composite `all_of` gate. Reproduction means governed metrics within G10
tolerance—not merely loading a checkpoint.

Execution-provider checks follow the selected backend. LPAI development-space
reproduction requires actual space/source/config/data/checkpoint identities,
environment and metrics; it does not require a Jenkins instance or job.
Historical Jenkins operations and pinned workspaces retain their original
meaning. Never relabel a historical receipt as a new ML-platform result.

Never rewrite a B02/B03/B04. A retry creates a linked request/Attempt; a new
baseline identity or reproduction creates a new record. Deprecation must name a
real replacement, prevent cycles, retain historical references, and apply the
policy pinned by any already-frozen Experiment.

## On Activation

Resume the workspace, resolve the baseline identity and requested lifecycle
action, then evaluate the pinned B01-B04 rules.
