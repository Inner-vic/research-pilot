"""Scanner-stable ledger entry points in addition to the replay matrix."""

from canonical import digest
from ledger import build_event, event_hash, genesis_document, genesis_hash


def test_event_hash_binds_previous_head_and_canonical_body(asset_bundle) -> None:
    genesis = genesis_document("workspace", "2026-01-01T00:00:00Z", asset_bundle)
    previous = genesis_hash(genesis)
    event = build_event(
        sequence=1,
        event_type="workspace.created",
        previous_hash=previous,
        engine_contract_digest=asset_bundle.engine_contract_digest,
        actor={"type": "coordinator", "id": "sdm-lifecycle"},
        subjects={"workspace_id": "workspace"},
        payload={"protocol": "exploratory"},
        operation_id="create",
        occurred_at="2026-01-01T00:00:00Z",
    )
    without_hash = dict(event)
    claimed = without_hash.pop("event_hash")
    assert claimed == event_hash(previous, without_hash)
    assert claimed != event_hash(digest({"different": "head"}), without_hash)

