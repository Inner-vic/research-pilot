"""Scanner-stable workspace entry points in addition to transaction tests."""

from ledger import replay
from workspace import resume_workspace, validate_workspace


def test_resume_is_full_replay_and_returns_earliest_action(workspace) -> None:
    replayed = replay(workspace)
    resumed = resume_workspace(workspace)
    assert resumed["revision"] == replayed.revision
    assert resumed["state"]["ledger_head"] == replayed.head_hash
    assert resumed["action"]["step"] == 1
    assert resumed["action"]["disposition"] == "blocked"
    assert validate_workspace(workspace)["revision"] == replayed.revision
