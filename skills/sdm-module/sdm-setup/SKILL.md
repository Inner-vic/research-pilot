---
name: sdm-setup
description: Install, update, recover, validate, or uninstall the Specification-Driven ML Development module on a BMad host. Use when SDM configuration or native Skill registration is needed.
---

# Configure SDM

## Overview

This Skill is the installation surface for the `sdm` module. For the full governed host it collects
the seven questions in `assets/module.yaml`, confirms their values, and invokes
`scripts/setup.py`; it never edits host files ad hoc.

## On Activation

For ordinary research install ONE self-contained `bmad-ml` Skill folder in a
native project/user Skill directory. Build it from the source root with
`scripts/build-native-skill.py`; owned upgrades use `scripts/install-native-skill.py`.
Activation stays in the current Codex conversation, without a CMD or new CLI.
It does not require per-project registration or a Codex executable to inspect.
Read [installation instructions](../../../docs/delivery/INSTALLATION-MODES.md).
Keep official LPAI tooling as an external required platform capability.

The client `/` picker displays **BMAD ML** with its bundled UI metadata and
default guide prompt. Send that selection in the existing conversation. Do not
install a custom slash-command framework or open a separate Codex client.

`register_entry.py` retains only `validate` and `recover` for historical project
records; its `install` operation is retired. `bootstrap_entry.py` reports that
the launcher has been retired and performs no installation or model call.
Never generate CMD files or require these old tools for Skill activation.
Existing studies keep their frozen runtime, original host and authorization.

The following governed setup instructions are LEGACY maintenance for an
explicit full Linux/WSL host request, not normal research onboarding.

For a Windows checkout without an installed Linux host, use Linux Python to run
`scripts/prepare_linux_host.py --source-root <checkout> --host-root <new-absolute-Linux-path> --answers-file <seven-answer-json>`.
Read the seven settings below first. New hosts default to
`--governance-profile lightweight`; choose
`--governance-profile strict` when business policy requires independent reviewers
and separated roles. This choice is written into source policy before setup,
then pinned by the installed engine digest. A missing profile in an old engine
retains strict historical behavior. Lightweight records decisions using the
authenticated research owner, preserving provenance and coordinator constraints
without demanding another approver. It does not disable factual evidence,
signature verification, complete accounting or prohibited-rule checks.
This helper replaces the manual Linux-host
copy recipe, refuses existing targets and `/mnt` targets, copies only BMad/SDM
seed files, and delegates installation and validation to `setup.py`. It copies
no workspace, trust bundle, or platform credentials. Preserve a failed host;
use `setup.py recover` for an interrupted installation, never overwrite it.
Then activate the installed host Skill and resolve its configuration before
creating a real workspace. Missing production trust is a sourced block;
never install fixture keys to continue a real-case acceptance run.
For a user-authorized personal experiment without company signing services,
run `scripts/personal_trust.py init --trust-root <configured-public-root>
--private-root <separate-private-Linux-root> --principal <actual-personal-principal>`.
This generates fresh Ed25519 material, pins the real OpenSSL verifier, and tests
signing/verification and tamper rejection. It grants only research-owner and
platform-producer roles to that same personal principal; it does not invent an
independent reviewer or company authority. Use `validate` with the same arguments
to recheck; init refuses existing roots. Keep the private root outside the Git
checkout and public trust root, never display/commit its bytes. Public personal
trust permits real genesis and authenticated personal evidence, but does not
waive complete operation evidence or S13 producer implementation. Independent
review gates apply only to a pinned strict business profile. Do not retrofit
trust or a profile into an existing workspace's pinned snapshot.
For a complete producer-generated successful S13 payload, use
`scripts/sign_personal_result.py --lifecycle-root <installed-sdm-lifecycle>
--request <immutable-S12> --result <unsigned-producer-result>
--trust-root <pinned-public-root> --private-root <private-root>
--principal <actual-owner> --output <new-private-output-file>`.
The helper rejects missing operation evidence before accessing signing material,
validates personal trust, and uses the installed coordinator's full result
validator before emitting canonical S13. It does not collect platform facts,
deduplicate dispatches, approve a split, or ingest a result. The outer platform
producer must establish those facts and use the workspace-pinned trust snapshot;
the normal `handoff ingest` is the only acceptance surface. Never provide invented
evidence just to make the signer run. An emitted signature alone is not acceptance.

Pass repeated `--platform-skill <identity>` only for explicitly selected candidate
Skills to include in the new host. The helper checks their trees and copies no
operator configuration. Use a separate seven-answer file for scoped integration
bindings rather than changing shared default answers.

1. Resolve the project root and read `assets/module.yaml` and
   `assets/module-help.csv`.
2. Select exactly one command: `install`, `update`, `uninstall`, `recover`, or
   `validate`. A normal first activation means `install`; an existing exact SDM
   install means `update`. Never guess across a partial or digest-mismatched
   install.
3. For install/update, collect all seven answers. Empty platform Skill bindings
   are valid; the first affected lifecycle operation will return
   `configuration_required`. Do not offer manual evidence as a substitute.
   Empty is a default, not an engine requirement for an unfinished producer.
   For explicitly scoped integration development, inspect candidate registration
   with `scripts/check_platform_bindings.py --project-root <absolute-root>
   --binding <domain>=<identity>` (repeat for selected domains), then use the
   normal installer to register the exact checked identities. Registration does
   not prove an operation is implemented or an S13 can be accepted. Report
   unsupported operations and missing producer/trust separately; never blank
   all four domains merely because one producer is unfinished. Real acceptance
   still requires complete authenticated results and the production trust bundle.
4. Resolve the current installed digest with `validate`. Use
   `--expected-install-digest absent` only for a clean install; update and
   uninstall must use the exact reported digest.
5. Invoke the CLI below and treat only its JSON result as authoritative.

```bash
python3 -B ./scripts/setup.py COMMAND \
  --project-root PROJECT_ROOT \
  --answers ANSWERS_JSON \
  --skills-source MODULE_SOURCE \
  --expected-install-digest DIGEST_OR_ABSENT
```

`recover` needs only `--project-root`. `validate` accepts an optional
`--skills-source` and performs no writes. Never pass secrets as answers: trust
roots contain public trust material, while platform bindings are exact installed
Skill directory names.

The installer owns only `{project-root}/_bmad/custom/config.toml`,
`{project-root}/_bmad/custom/config.user.toml`,
`{project-root}/_bmad/custom/bmad-sprint-planning.toml`,
`{project-root}/_bmad/_config/bmad-help.csv`,
`{project-root}/_bmad/module-help.csv`, and the exact fourteen
copied `.agents/skills/sdm-*` directories. Update also removes the two retired
previously owned Skill directories in the same transaction. Read-only validation
of a complete legacy installation returns its exact digest and `upgrade_required`;
use that digest to update or uninstall. New answer payloads contain only the seven
current settings, and update replaces the module config and discovery rows. It uses one host lock, a recovery
journal, staged same-filesystem replacements, and digest compare-and-swap.

## Result handling

- `pass` / exit 0: report the installed digest and changed targets.
- `fail` / exit 2: correct malformed input or an invalid source.
- `blocked` / exit 3: reconcile an interrupted transaction or satisfy a gate.
- `conflict` / exit 4: re-read the current install digest; do not overwrite.
- `configuration_required` / exit 5: collect the missing runtime setting.
- `internal_error` / exit 70: preserve stderr and run `recover` before retrying.

Do not run setup against the live project during module verification. Clean-host
tests must use a temporary host and prove discovery under `.agents/skills`.
