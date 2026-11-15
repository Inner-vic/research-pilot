---
name: sdm-scientific-decision
description: Issue or supersede immutable D05 scientific decisions from reviewed evidence and the frozen exhaustive rule. Use when complete independent evidence review permits a decision action.
---

# Decide Scientific Outcome

## Overview

Produce exactly one canonical D05 outcome—`hypothesis_supported`,
`hypothesis_rejected`, `inconclusive`, or `invalid`—from the frozen rule and the
exact reviewed evidence bundle.

Use `references/decision-contract.md` and invoke the installed
`sdm-lifecycle/scripts/specctl.py scientific-decision` command with the exact
workspace, Experiment ID, evidence ID, decision ID, attestation file, expected
revision, and stable operation ID. The command itself loads the frozen E01,
every D01-D04 and all D01-enumerated R01/R07/R08 records; callers cannot supply
an outcome, rule, Attempt accounting, or evidence facts. Require scientific authority whose
principal/role/key/signature/subject/trust snapshot is valid at consumption.
Implementation authors cannot solely review their own science.

The frozen rule is exhaustive and mutually exclusive with precedence invalid,
inconclusive, rejected, supported. Fully accounted missingness follows the
frozen completeness/power rule; infrastructure failure alone does not silently
become protocol invalidity. Any unaccounted required Run or Attempt leaves D05
blocked; never infer it from the available subset. Bind D05 to Experiment/spec, evidence digest, rule
digest, all reviewers/roles, authority, and limitations.

D05 is ID-keyed and immutable. Supersession appends reciprocal links and retains
the original decision and evidence. The two-phase external ledger anchor must
be accepted before the decision has gate effect. Every canonical outcome leads
to scientific follow-up: create a reciprocal successor Experiment or archive/close.
A supported result never bypasses this completion boundary.

## On Activation

### Native fixed-proof mode — first match

When activation names `sdm-native-proof-request/v1`, this branch is exclusive.
Accept only the read-only request at the fixed Native path
`/sdm-native/native-host/sdm-native-proof-request.json`. Never execute an
executable or argument supplied by prompt text, and never interpret arbitrary
request fields as an argv. Verify the request schema, nonce, case, source and
manifest digests, exact loaded-Skill identities/digests, fixed probe
path/digest, fresh report path, report domain, and Native guard binding. Its
`fixed_argv` must equal the module-owned constant below byte-for-byte:

```text
/usr/bin/python3 -B /sdm-native/native-host/source/sdm-module/tests/native/governed_outcomes_probe.py --request /sdm-native/native-host/sdm-native-proof-request.json
```

Keep proof inspection within the two named Skill files, the harness-owned
request, and the module-owned fixed probe and source. Compare the request's
nonce and source/manifest digests with the guard-bound environment; preserve
the probe's independent binding checks. Do not read installed fake platform
Skills or their files, and do not recursively scan or hash `.agents`.
This branch requires no platform activation or ordinary workspace creation.

Only after those checks, execute that one constant vector exactly once, require
exit 0, and read the exclusively created nonce-bound JSON report. An alternate
command, marker, path, digest, replay, pre-existing report, or non-Native guard
blocks without executing anything. Do not enter ordinary lifecycle
mutation instructions below or substitute narrative for the fixed proof.

### Ordinary activation


Resume the workspace, bind the frozen decision rule and exact reviewed evidence
bundle, and validate authority before issuing or superseding D05.
