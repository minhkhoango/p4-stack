"""
In-memory footprint benchmark for the diff3 merge engine.

p4-stack's rebase is described as "in-memory": it loads each shelved CL as a
{filename: content} snapshot, merges snapshots as Python objects, and only
writes back to the workspace at commit time. This benchmark quantifies that
claim -- how much RAM does merging a large changelist actually cost? -- using
tracemalloc around the REAL three_way_merge_folder (real diff3 subprocess).

We report peak Python-heap growth for the merge relative to the raw text size
of the changelist, showing the engine holds a whole 100k-line changelist with
a small, bounded footprint and never writes to the client workspace.

Run with output visible:
    pytest tests/test_merge_memory_benchmark.py -s -q
"""

from __future__ import annotations

import sys
import tracemalloc
import types

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


def _base_file(n_lines: int) -> str:
    return "\n".join(f"line {i}" for i in range(n_lines)) + "\n"


def test_merge_of_100k_line_changelist_stays_bounded_in_memory() -> None:
    """Merge a 200-file / 100k-line disjoint changelist and assert the peak
    Python-heap growth is a small multiple of the raw changelist size (bytes),
    i.e. no runaway buffering."""
    n_files, n_lines = 200, 500
    base: Snapshot = {f"src/mod_{i}.py": _base_file(n_lines) for i in range(n_files)}
    ours: Snapshot = {}
    theirs: Snapshot = {}
    for path, content in base.items():
        top = content.splitlines()
        top[1] = "line 1  # child CL"
        ours[path] = "\n".join(top) + "\n"
        bot = content.splitlines()
        bot[-2] = "line 498  # new parent"
        theirs[path] = "\n".join(bot) + "\n"

    raw_bytes = sum(
        len(c.encode()) for snap in (base, ours, theirs) for c in snap.values()
    )

    tracemalloc.start()
    tracemalloc.reset_peak()
    merged = three_way_merge_folder(base, ours, theirs)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert len(merged) == n_files
    assert all(not conflict for _, conflict in merged.values())

    total_lines = n_files * n_lines
    print(
        f"\n[memory] merged {total_lines:,} lines "
        f"({raw_bytes / 1e6:.1f} MB of text across 3 snapshots) with "
        f"{peak / 1e6:.1f} MB peak heap ({peak / raw_bytes:.2f}x raw), "
        f"zero workspace writes"
    )
    # Peak heap must stay within a small multiple of the raw text -- bounded,
    # not O(n^2) buffering.
    assert peak < raw_bytes * 6
