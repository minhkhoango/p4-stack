"""
Timing benchmark for the diff3 3-way merge engine (p4_stack.core.rebase).

This is a benchmark, not a unit test of behavior: it exercises the REAL `diff3`
subprocess (no mocks) over a realistic stacked-changelist rebase and measures how
long the automatic conflict resolution actually takes. It backs the résumé claim
that p4-stack turns a manual, minutes-long rebase into a machine-time operation:
the merge *computation* across a whole changelist completes in well under a
second, so the human never hand-resolves the non-overlapping edits at all.

It also confirms the two properties that make that speed meaningful:
  * disjoint edits from the child CL and the moved parent auto-merge with NO
    conflict (both edits survive), and
  * genuinely overlapping edits are still flagged as conflicts (diff3 -m -E),
    so "fast" never means "silently wrong".

Run just this file with timing visible:
    pytest tests/test_rebase_benchmark.py -s -q
"""

from __future__ import annotations

import sys
import time
import types

# The merge engine lives in rebase.py, which imports p4python (`from P4 import
# P4`) at module load. The functions we benchmark (three_way_merge_folder /
# _three_way_merge_file) are pure diff3 and never touch Perforce, so if
# p4python is not installed we stub the `P4` module just enough to import. This
# only activates when P4 is genuinely absent; it changes no product code.
if "P4" not in sys.modules:
    try:
        import P4  # type: ignore  # noqa: F401
    except ModuleNotFoundError:
        _p4_stub = types.ModuleType("P4")
        _p4_stub.P4 = type("P4", (), {})  # type: ignore[attr-defined]
        _p4_stub.P4Exception = type(  # type: ignore[attr-defined]
            "P4Exception", (Exception,), {}
        )
        sys.modules["P4"] = _p4_stub

from p4_stack.core.rebase import three_way_merge_folder  # noqa: E402
from p4_stack.core.types import Snapshot  # noqa: E402

# A substantive changelist: 15 files x 300 lines = 4,500 lines rebased at once.
N_FILES = 15
N_LINES = 300
# Generous machine-time ceiling. The real merge is milliseconds; this bound only
# guards against a catastrophic regression (e.g. an accidental O(n^2) blowup),
# not normal machine-to-machine variance.
MAX_SECONDS = 5.0


def _base_file(n_lines: int) -> str:
    return "\n".join(f"line {i}" for i in range(n_lines)) + "\n"


def _edit_near_top(text: str, marker: str) -> str:
    lines = text.splitlines()
    lines[1] = marker
    return "\n".join(lines) + "\n"


def _edit_near_bottom(text: str, marker: str) -> str:
    lines = text.splitlines()
    lines[-2] = marker
    return "\n".join(lines) + "\n"


def test_full_changelist_rebase_auto_resolves_in_machine_time() -> None:
    """Rebase a 15-file changelist where the child CL edited the top of each
    file and the moved parent edited the bottom -- disjoint hunks that diff3
    merges without human intervention -- and assert it completes in seconds."""
    base: Snapshot = {f"src/mod_{i}.py": _base_file(N_LINES) for i in range(N_FILES)}
    ours: Snapshot = {
        path: _edit_near_top(content, "line 1  # edited by child CL")
        for path, content in base.items()
    }
    theirs: Snapshot = {
        path: _edit_near_bottom(content, f"line {N_LINES - 2}  # edited by new parent")
        for path, content in base.items()
    }

    start = time.perf_counter()
    merged = three_way_merge_folder(base, ours, theirs)
    elapsed = time.perf_counter() - start

    # Every file present and auto-resolved with no conflict...
    assert len(merged) == N_FILES
    assert all(not has_conflict for _, has_conflict in merged.values())
    # ...and BOTH disjoint edits survived the merge (a real 3-way merge, not a
    # pick-one-side shortcut).
    for content, _ in merged.values():
        assert "edited by child CL" in content
        assert "edited by new parent" in content

    total_lines = N_FILES * N_LINES
    print(
        f"\n[bench] auto-rebased {N_FILES} files / {total_lines} lines via diff3 "
        f"in {elapsed * 1000:.0f} ms"
    )
    assert elapsed < MAX_SECONDS


def test_overlapping_edits_are_still_flagged_as_conflict() -> None:
    """The same engine must NOT silently auto-merge a true conflict: when the
    child and the new parent edit the SAME line, diff3 -m -E flags it."""
    base: Snapshot = {"src/conflict.py": _base_file(50)}
    lines = base["src/conflict.py"].splitlines()
    lines[25] = "line 25  # edited by child CL"
    ours: Snapshot = {"src/conflict.py": "\n".join(lines) + "\n"}
    lines[25] = "line 25  # edited by new parent"
    theirs: Snapshot = {"src/conflict.py": "\n".join(lines) + "\n"}

    merged = three_way_merge_folder(base, ours, theirs)
    content, has_conflict = merged["src/conflict.py"]

    assert has_conflict is True
    assert "<<<<<<<" in content
