---
name: sdm-evidence-review
description: Build and independently review complete D01-D04 evidence across every planned Run and Attempt. Use when execution, evaluation, and robustness evidence has reached terminal accounting.
---

# Review ML Evidence

## Overview

Produce an immutable evidence bundle and S10 review that makes every planned
Run/Attempt, failed/cancelled/invalid fact, E07 deviation, metric, artifact, and
missing item visible. Use installed `sdm-lifecycle` commands `build-evidence`
and `verify-evidence`; never recompute platform-owned formal metrics.

Load all five self-contained lenses in `references/`: geometry, leakage,
baseline fairness, statistical validity, and deployment parity. Reviewers must
be independent of the sole implementation author. D01 indexes exact URI/digest
evidence; D02 summarizes without unattributed numbers; D03 binds the locked
analysis implementation, estimand, uncertainty, pairing, multiplicity, power,
and missingness; D04 indexes systematic failure cases without leaking protected
data.

An unaccounted required Run/Attempt blocks D05. A fully accounted valid but
underpowered terminal set follows the frozen inconclusive rule. Failed system
Attempts are retained and do not by themselves invalidate the protocol. Never
cherry-pick successful Runs or let best-run-only reporting pass.

Return governed D01-D04/S10 and an exact transition, a request for missing work,
an independent-review wait, or sourced findings.

## On Activation

Resume the workspace, replay the complete Run and Attempt inventory, and load
all five review lenses before evaluating completeness.
