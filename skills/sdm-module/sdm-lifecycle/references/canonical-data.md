# Canonical data and replay

Ingress is UTF-8 strict JSON or YAML restricted to the JSON data model: string keys, arrays, strings, booleans, null, signed 64-bit integers, and finite numbers. Reject duplicate keys, anchors, aliases, tags, merge keys, implicit timestamps, non-NFC text, non-finite numbers, out-of-range integers, unknown fields, and nonportable paths. The only generic extension point is an `extensions` object keyed by reverse-DNS names.

Canonical bytes are NFC UTF-8 JSON with sorted keys, no insignificant whitespace, JSON escapes, and normalized finite decimal spelling. Digests use lowercase `sha256:<64hex>`. Candidate E01 hashing removes only `status`, `candidate_digest`, `frozen_digest`, `frozen_at`, `freeze_attestations`, and `event_revision`.

Genesis pins canonical bytes and digests for every schema, catalog, policy, event contract, transition, and command contract in `_engine/<engine_contract_digest>/`. Replay starts at genesis and never substitutes current installed assets. An upgrade appends an authorized migration boundary and a new immutable engine bundle.

Let `H0=SHA256(canonical(genesis))`. For event `i`, compute `Hi=SHA256(UTF8("sdm-event-v1\\0") || raw_digest(H{i-1}) || canonical(event_without_event_hash))`. Replay recomputes all hashes, semantics, transitions, authority, and graph edges. Projection is a rebuildable CAS cache, never a source of truth.

Validation uses no-follow, stable-identity traversal and rejects symlinks, multi-linked files, mount escapes, special nodes, and concurrent change. Its before/after snapshots cover bytes, mode, size, mtime, ctime, ledger, projection, memlog, packets, setup targets, and relevant external state. Portable atime equality is not promised.
