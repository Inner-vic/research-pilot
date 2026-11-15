from __future__ import annotations

import pytest

import journal
from canonical import canonical_bytes, digest_bytes
from journal import JournalError, Transaction, WorkspaceLock, atomic_write, contained, file_digest, recover, safe_relative


def _workspace_identity(root) -> dict[str, object]:
    descriptor = journal.os.open(root, journal._DIR_FLAGS)
    try:
        return journal._workspace_identity(descriptor)
    finally:
        journal.os.close(descriptor)


def _commit(transaction: Transaction) -> None:
    with WorkspaceLock(transaction.root, exclusive=True, create=True) as lock:
        transaction.commit(lock=lock)


def _recover(root) -> bool:
    with WorkspaceLock(root, exclusive=True, create=True) as lock:
        return recover(root, lock=lock)


def test_safe_relative_and_containment_reject_escape_nodes(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    for value in ("/absolute", "../outside", "a/../outside", "."):
        with pytest.raises(JournalError) as caught:
            safe_relative(value)
        assert caught.value.code == "unsafe_path"
    (root / "escape").symlink_to(tmp_path)
    with pytest.raises(JournalError) as caught:
        contained(root, "escape/value.json")
    assert caught.value.code == "symlink_escape"


def test_transaction_commits_all_writes_with_cas_and_modes(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir(mode=0o700)
    before = root / "before.json"
    before.write_bytes(b"old")
    old_digest = file_digest(before)
    transaction = Transaction(root, "operation-1")
    transaction.add("before.json", b"new", expected_digest=old_digest, mode=0o640)
    transaction.add("nested/new.json", {"value": 1}, exclusive=True)
    assert transaction.writes[0].mode == 0o640
    _commit(transaction)
    assert before.read_bytes() == b"new"
    assert (root / "nested/new.json").read_bytes() == canonical_bytes({"value": 1})
    assert not (root / "_journal.json").exists()
    assert _recover(root) is False


def test_transaction_cas_conflict_is_fail_closed(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "state.json"
    target.write_bytes(b"current")
    transaction = Transaction(root, "operation-cas")
    transaction.add("state.json", b"replacement", expected_digest=digest_bytes(b"stale"))
    with pytest.raises(JournalError) as caught:
        _commit(transaction)
    assert caught.value.code == "cas_conflict"
    assert target.read_bytes() == b"current"

    oversized = root / "oversized.json"
    with oversized.open("wb") as stream:
        stream.truncate(journal.MAX_DOCUMENT_BYTES + 1)
    with pytest.raises(JournalError) as too_large:
        file_digest(oversized)
    assert too_large.value.code == "document_resource_limit"
    with pytest.raises(JournalError) as payload_too_large:
        Transaction(root, "oversized-operation").add(
            "new.json", b"x" * (journal.MAX_DOCUMENT_BYTES + 1),
        )
    assert payload_too_large.value.code == "document_resource_limit"


def test_late_cas_failure_leaves_no_prejournal_debris_and_same_operation_retries(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    guarded = root / "guarded.json"
    guarded.write_bytes(b"current")

    transaction = Transaction(root, "reusable-operation")
    transaction.add("new/first.json", b"first", exclusive=True)
    transaction.add("guarded.json", b"next", expected_digest=digest_bytes(b"stale"))
    with pytest.raises(JournalError) as caught:
        _commit(transaction)
    assert caught.value.code == "cas_conflict"
    assert not (root / "_journal.json").exists()
    assert not (root / "_transactions").exists()
    assert not (root / "new").exists()
    assert guarded.read_bytes() == b"current"

    retry = Transaction(root, "reusable-operation")
    retry.add("new/first.json", b"first", exclusive=True)
    retry.add("guarded.json", b"next", expected_digest=digest_bytes(b"current"))
    _commit(retry)
    assert (root / "new/first.json").read_bytes() == b"first"
    assert guarded.read_bytes() == b"next"


def test_committed_journal_recovers_after_cleanup_interruption(tmp_path, monkeypatch) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    original_cleanup = journal._cleanup_at

    def interrupt_cleanup(*_args):
        raise RuntimeError("simulated process interruption after commit marker")

    monkeypatch.setattr(journal, "_cleanup_at", interrupt_cleanup)
    transaction = Transaction(root, "operation-recover")
    transaction.add("result.json", {"terminal": True}, exclusive=True)
    with pytest.raises(RuntimeError):
        _commit(transaction)
    assert (root / "result.json").read_bytes() == canonical_bytes({"terminal": True})
    assert (root / "_journal.json").exists()

    monkeypatch.setattr(journal, "_cleanup_at", original_cleanup)
    assert _recover(root) is True
    assert not (root / "_journal.json").exists()
    assert _recover(root) is False

    substitution_root = tmp_path / "journal-substitution"
    substitution_root.mkdir()
    journal_value = {
        "schema": "sdm-journal/v1",
        "operation_id": "cleanup-substitution",
        "workspace_identity": _workspace_identity(substitution_root),
        "phase": "planning",
        "writes": [],
        "created_parents": [],
    }
    authenticated = canonical_bytes(journal_value)
    (substitution_root / "_journal.json").write_bytes(authenticated)
    displaced = substitution_root / "authenticated-journal.json"
    real_replace = journal.os.replace
    swapped = False

    def substitute_before_quarantine(source, destination, *args, **kwargs):
        nonlocal swapped
        if source == "_journal.json" and not swapped:
            swapped = True
            (substitution_root / "_journal.json").rename(displaced)
            (substitution_root / "_journal.json").write_bytes(b"attacker")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(journal.os, "replace", substitute_before_quarantine)
    with pytest.raises(JournalError) as substituted:
        _recover(substitution_root)
    assert substituted.value.code == "journal_conflict"
    assert displaced.read_bytes() == authenticated
    quarantined = list(substitution_root.glob("._journal.json.cleanup-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == b"attacker"


def test_recovery_rejects_corrupt_or_diverged_journals(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "_journal.json").write_text('{"schema":"wrong","writes":[]}', encoding="utf-8")
    with pytest.raises(JournalError) as corrupt:
        _recover(root)
    assert corrupt.value.code == "journal_corrupt"

    identity = _workspace_identity(root)
    identity["inode"] = int(identity["inode"]) + 1
    (root / "_journal.json").write_bytes(canonical_bytes({
        "schema": "sdm-journal/v1",
        "operation_id": "wrong-root",
        "workspace_identity": identity,
        "phase": "planning",
        "writes": [],
        "created_parents": [],
    }))
    with pytest.raises(JournalError) as wrong_root:
        _recover(root)
    assert wrong_root.value.code == "journal_corrupt"

    existing = root / "existing"
    existing.mkdir()
    (root / "_journal.json").write_bytes(canonical_bytes({
        "schema": "sdm-journal/v1",
        "operation_id": "crafted-parent",
        "workspace_identity": _workspace_identity(root),
        "phase": "planning",
        "writes": [],
        "created_parents": ["existing"],
    }))
    with pytest.raises(JournalError) as crafted_parent:
        _recover(root)
    assert crafted_parent.value.code == "journal_corrupt"
    assert existing.is_dir()

    fifo_journal_root = tmp_path / "fifo-journal"
    fifo_journal_root.mkdir()
    journal.os.mkfifo(fifo_journal_root / "_journal.json")
    with pytest.raises(JournalError) as fifo_journal:
        _recover(fifo_journal_root)
    assert fifo_journal.value.code == "journal_corrupt"

    def prepared_fifo_workspace(name: str, *, fifo_target: bool) -> object:
        prepared_root = tmp_path / name
        prepared_root.mkdir()
        operation = name
        stage_dir = prepared_root / "_transactions" / operation
        stage_dir.mkdir(parents=True)
        stage = stage_dir / "0000.stage"
        target = prepared_root / "target.json"
        if fifo_target:
            journal.os.mkfifo(target)
            stage.write_bytes(b"after")
        else:
            journal.os.mkfifo(stage)
        (prepared_root / "_journal.json").write_bytes(canonical_bytes({
            "schema": "sdm-journal/v1",
            "operation_id": operation,
            "workspace_identity": _workspace_identity(prepared_root),
            "phase": "prepared",
            "writes": [{
                "target": "target.json",
                "stage": f"_transactions/{operation}/0000.stage",
                "before_digest": None,
                "after_digest": digest_bytes(b"after"),
                "mode": 0o600,
                "exclusive": True,
            }],
            "created_parents": [],
        }))
        return prepared_root

    fifo_target_root = prepared_fifo_workspace(
        "fifo-target", fifo_target=True,
    )
    with pytest.raises(JournalError) as fifo_target:
        _recover(fifo_target_root)
    assert fifo_target.value.code == "recovery_conflict"

    fifo_stage_root = prepared_fifo_workspace(
        "fifo-stage", fifo_target=False,
    )
    with pytest.raises(JournalError) as fifo_stage:
        _recover(fifo_stage_root)
    assert fifo_stage.value.code == "recovery_stage_invalid"


def _valid_journal_record(index: int = 0, *, target: str = "target.json") -> dict[str, object]:
    return {
        "target": target,
        "stage": f"_transactions/operation-corrupt/{index:04d}.stage",
        "before_digest": None,
        "after_digest": digest_bytes(b"after"),
        "mode": 0o600,
        "exclusive": True,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["workspace_identity"].update(
            inode=value["workspace_identity"]["inode"] + 1,
        ),
        lambda value: value["writes"].__setitem__(0, {}),
        lambda value: value["writes"][0].__setitem__("mode", True),
        lambda value: value["writes"][0].__setitem__("exclusive", 1),
        lambda value: value["writes"][0].__setitem__("before_digest", "sha256:BAD"),
        lambda value: value["writes"][0].__setitem__("stage", "arbitrary.stage"),
        lambda value: value["writes"].append(_valid_journal_record(1, target="target.json")),
        lambda value: value["created_parents"].append("../outside"),
    ],
)
def test_recovery_normalizes_malformed_write_records(tmp_path, mutate) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "target.json"
    target.write_bytes(b"untouched")
    value = {
        "schema": "sdm-journal/v1",
        "operation_id": "operation-corrupt",
        "workspace_identity": _workspace_identity(root),
        "phase": "prepared",
        "writes": [_valid_journal_record()],
        "created_parents": [],
    }
    mutate(value)
    (root / "_journal.json").write_bytes(canonical_bytes(value))
    with pytest.raises(JournalError) as caught:
        _recover(root)
    assert caught.value.code == "journal_corrupt"
    assert target.read_bytes() == b"untouched"
    assert (root / "_journal.json").exists()


def test_workspace_lock_conflict_is_explicit(tmp_path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    with WorkspaceLock(root, exclusive=True, create=True):
        with pytest.raises(JournalError) as caught:
            with WorkspaceLock(root, exclusive=False):
                raise AssertionError("second lock must never be acquired")
        assert caught.value.code == "lock_conflict"

    transaction = Transaction(root, "root-rebind")
    transaction.add("result.json", b"locked-root-only", exclusive=True)
    original = tmp_path / "workspace-original"
    with WorkspaceLock(root, exclusive=True) as lock:
        root.rename(original)
        root.mkdir()
        (root / "_lock").write_bytes(b"")
        with pytest.raises(JournalError) as rebound:
            transaction.commit(lock=lock)
        assert rebound.value.code == "workspace_rebound"
    assert not (root / "result.json").exists()
    assert not (original / "result.json").exists()


def test_atomic_write_exclusive_does_not_replace_existing_bytes(tmp_path) -> None:
    target = tmp_path / "immutable.json"
    atomic_write(target, b"first", exclusive=True)
    with pytest.raises(JournalError) as caught:
        atomic_write(target, b"second", exclusive=True)
    assert caught.value.code == "target_exists"
    assert target.read_bytes() == b"first"


def _swap_stage_before_publish(root, monkeypatch) -> None:
    real_link = journal.os.link
    swapped = False

    def racing_link(source, destination, *args, **kwargs):
        nonlocal swapped
        if destination == "result.json" and not swapped:
            swapped = True
            stage = root / "_transactions" / "stage-race" / "0000.stage"
            displaced = stage.with_suffix(".authenticated")
            stage.rename(displaced)
            stage.write_bytes(b"attacker")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(journal.os, "link", racing_link)
    transaction = Transaction(root, "stage-race")
    transaction.add("result.json", b"authenticated", exclusive=True)
    with WorkspaceLock(root, exclusive=True, create=True) as lock:
        with pytest.raises(JournalError) as raced:
            transaction.commit(lock=lock)
    assert raced.value.code == "recovery_stage_invalid"
    assert (root / "result.json").read_bytes() == b"authenticated"
    assert (root / "_journal.json").exists()

    monkeypatch.setattr(journal.os, "link", real_link)
    mutation_root = root.parent / "stage-mutation-workspace"
    mutation_root.mkdir()
    mutated = False

    def mutating_link(source, destination, *args, **kwargs):
        nonlocal mutated
        if destination == "result.json" and not mutated:
            mutated = True
            stage = (
                mutation_root / "_transactions" / "stage-mutation"
                / "0000.stage"
            )
            stage.write_bytes(b"attacker-bytes")
        return real_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(journal.os, "link", mutating_link)
    mutation = Transaction(mutation_root, "stage-mutation")
    mutation.add("result.json", b"authenticated", exclusive=True)
    with WorkspaceLock(mutation_root, exclusive=True, create=True) as lock:
        with pytest.raises(JournalError) as changed:
            mutation.commit(lock=lock)
    assert changed.value.code == "recovery_stage_invalid"
    assert (mutation_root / "result.json").read_bytes() == b"authenticated"
    assert (mutation_root / "_journal.json").exists()


@pytest.mark.parametrize("fault", ["write", "fsync"])
def test_atomic_write_cleans_temporary_file_on_early_fault(tmp_path, monkeypatch, fault) -> None:
    target = tmp_path / "atomic.json"
    target.write_bytes(b"old")

    def fail(*_args, **_kwargs):
        raise OSError(f"injected {fault} failure")

    monkeypatch.setattr(journal.os, fault, fail)
    with pytest.raises(OSError):
        atomic_write(target, b"new")
    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob(".atomic.json.tmp-*"))
    if fault == "write":
        race_root = tmp_path / "stage-workspace"
        race_root.mkdir()
        monkeypatch.undo()
        _swap_stage_before_publish(race_root, monkeypatch)
