---
name: sdm-freeze-experiment
description: Irreversibly freeze an independently approved E01 protocol with exact receipts, authority, canonical digest, and two-phase external ledger anchoring. Use when E02 protocol readiness has passed and freeze is requested.
---

# Freeze Experiment

## Overview

The outcome is a byte-immutable frozen E01 whose scientific content is the same
candidate content approved by E02, plus derived freeze metadata and S14
attestations. Use the installed `sdm-lifecycle/scripts/specctl.py freeze`
surface with workspace, Experiment ID, attestation, and expected revision.

Before invocation, establish exact baseline/data/split/calibration/evaluator/
budget identities, current Git/Jenkins receipts, independent freeze authority,
and the candidate digest. The command appends a provisional freeze event,
creates `git.anchor-ledger-head`, and has no gate effect until the outer
source-control Skill returns the exact S13 and a distinct `anchor.accepted`
event is appended. Crash or uncertain dispatch remains pending reconciliation;
never create a second logical anchor request.

After freeze, do not mutate E01 or move it back to draft. Route changes to E06,
E07, or a real reciprocal successor using the installed experiment-spec Skill.
Return the control-plane JSON, one exact outer handoff, one human wait, or a
sourced block—never a hand-written freeze claim.

## On Activation

Resolve the exact E01, E02, current revision, authority attestations, and
required platform receipts before invoking the control plane.
