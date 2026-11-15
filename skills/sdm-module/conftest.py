"""Process-global isolation for lifecycle runtime binding tests."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from types import ModuleType

import pytest


LIFECYCLE_MODULE_NAMES = (
    "canonical",
    "contracts",
    "journal",
    "ledger",
    "projection",
    "authority",
    "handoffs",
    "workspace",
)
_MISSING = object()


@pytest.fixture(autouse=True)
def _restore_lifecycle_import_boundary() -> Iterator[None]:
    """Keep genesis-pinned in-process runtimes inside one test case."""

    original_path = list(sys.path)
    original_modules: dict[str, ModuleType | object] = {
        name: sys.modules.get(name, _MISSING)
        for name in LIFECYCLE_MODULE_NAMES
    }
    original_dont_write_bytecode = sys.dont_write_bytecode
    try:
        yield
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            sys.modules.pop(name, None)
            if module is not _MISSING:
                sys.modules[name] = module
        sys.dont_write_bytecode = original_dont_write_bytecode
