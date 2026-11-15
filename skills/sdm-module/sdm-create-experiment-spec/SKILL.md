---
name: sdm-create-experiment-spec
description: Create E01 experiment protocols, E03 HPO search specs, E04 run plans, E06 amendments, E07 deviations, and reciprocal successor Experiments. Use when governed scientific protocol drafting or change classification is needed.
---

# Create Experiment Spec

## Overview

Turn S01/S02 plus governed contracts and baseline records into a falsifiable
protocol that can yield supported, rejected, inconclusive, or invalid science.
Unspecified protocol mode defaults to `exploratory` and non-promotable.

Use `references/experiment-contract.md` and the owned templates/schemas. Keep
Experiment, Run, Attempt, Pipeline, and Model Version identities separate.
Freeze candidate hashing removes only the permitted derived fields; primary
metrics, test split, evaluator semantics, minimum meaningful effect, decision
rules, and every other semantic byte remain in the digest.

E03 fixes algorithm, search space, objective, budget, failure policy, tuning
parity, selection replay, and formal-test isolation. E04 enumerates every arm,
seed, trial, independent confirmation Run, and budget. A best search trial alone
never supplies confirmatory evidence. Retain every trial and every failed or
cancelled Attempt so selection and resource accounting remain replayable.

Classify frozen changes by authenticated ledger sequence and CAS revision at
first affected Run dispatch/start: authorized non-material pre-Run changes are
E06; observed execution differences are E07; material, mixed, or post-result
semantic changes require a reciprocal successor Experiment. Originals remain
byte-identical.

## On Activation

Resume and validate the governed workspace, load its pinned contracts, and
select draft, amendment, deviation, or successor intent before writing.
