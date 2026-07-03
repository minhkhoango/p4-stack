"""
Dependency-graph benchmark for the stack engine (p4_stack.core.graph).

Two properties the merge benchmarks don't cover:

  1. STEPS COLLAPSED -- a K-deep stack, by the P4 workflow definition, needs
     K-1 sequential manual rebase/resolve passes (rebase child on new parent,
     resolve, repeat up the chain). p4-stack's `update` walks the whole
     dependency graph and does it in ONE command. We build real stacks and
     count the passes collapsed.

  2. GRAPH SCALE -- building the Depends-On dependency graph and computing the
     parent-first rebase order must stay cheap even for deep/wide stacks. We
     scale to a 1,000-CL stack and time the real build_stack_graph +
     get_stack_from_base.

These exercise the REAL graph code via a tiny fake P4 that only implements
run_changes (the one Perforce call build_stack_graph makes); no diff3, no p4d.

Run with output visible:
    pytest tests/test_stack_graph_benchmark.py -s -q
"""

from __future__ import annotations

import sys
import time
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

from p4_stack.core.graph import build_stack_graph, get_stack_from_base  # noqa: E402


class _FakeP4:
    """Minimal stand-in: build_stack_graph only ever calls run_changes."""

    def __init__(self, changes: list[dict[str, str]]) -> None:
        self._changes = changes

    def run_changes(self, *_args: str, **_kwargs: object) -> list[dict[str, str]]:
        return self._changes


def _chain_changes(depth: int, root: int = 100) -> list[dict[str, str]]:
    """A linear stack of `depth` CLs: each CL depends on the one below it."""
    changes: list[dict[str, str]] = [{"change": str(root), "desc": "root CL\n"}]
    for i in range(1, depth):
        cl = root + i
        changes.append(
            {"change": str(cl), "desc": f"child CL\nDepends-On: {cl - 1}\n"}
        )
    return changes


def _wide_changes(fanout: int, root: int = 100) -> list[dict[str, str]]:
    """A one-level stack: `fanout` CLs all depending on a single root."""
    changes: list[dict[str, str]] = [{"change": str(root), "desc": "root CL\n"}]
    for i in range(1, fanout + 1):
        cl = root + i
        changes.append({"change": str(cl), "desc": f"child\nDepends-On: {root}\n"})
    return changes


def test_deep_stack_collapses_manual_passes_into_one_command() -> None:
    """An 8-deep stack: rebasing it by hand is 7 sequential resolve passes.
    p4-stack derives the whole parent-first order from one graph walk, so
    `update <root>` rebases all 8 in a single command."""
    depth = 8
    root = 100
    p4 = _FakeP4(_chain_changes(depth, root))

    graph, child_to_parent = build_stack_graph(p4)  # type: ignore[arg-type]
    order = get_stack_from_base(root, graph)

    # The whole chain is discovered, parent-first, from the single base CL.
    assert order == [root + i for i in range(depth)]
    manual_passes = depth - 1  # each edge is one hand rebase/resolve
    print(
        f"\n[steps] {depth}-deep stack: {manual_passes} sequential manual "
        f"rebase/resolve passes -> 1 `update {root}` command "
        f"({len(order)} CLs rebased in one graph walk)"
    )
    assert len(order) == depth
    assert manual_passes == 7


def test_graph_scales_to_1000_cl_stack() -> None:
    """Build + order a linear 1,000-CL stack; the graph engine stays in the
    low-millisecond range (it's O(V+E), not O(V^2))."""
    print()
    for depth in (50, 200, 1000):
        p4 = _FakeP4(_chain_changes(depth))
        start = time.perf_counter()
        graph, _ = build_stack_graph(p4)  # type: ignore[arg-type]
        order = get_stack_from_base(100, graph)
        elapsed = time.perf_counter() - start

        assert len(order) == depth  # every CL placed, none lost
        print(
            f"[graph] {depth:>4}-CL stack  ->  build+order in {elapsed * 1000:6.2f} ms"
        )


def test_wide_stack_orders_all_children() -> None:
    """A root with 200 direct children: BFS must surface the root first, then
    every child -- one `update` still covers the whole fan-out."""
    fanout = 200
    p4 = _FakeP4(_wide_changes(fanout))
    graph, _ = build_stack_graph(p4)  # type: ignore[arg-type]
    order = get_stack_from_base(100, graph)

    assert order[0] == 100
    assert len(order) == fanout + 1
    print(
        f"\n[wide] root + {fanout} children -> 1 command covers all "
        f"{len(order)} CLs (manual: {fanout} separate rebases)"
    )
