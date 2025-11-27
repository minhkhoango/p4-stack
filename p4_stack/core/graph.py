"""
Contains all logic for building and traversing the
CL dependency graph.
"""

import re
import logging
from collections import defaultdict
from P4 import P4  # type: ignore
from typing import cast

from .types import (
    AdjacencyList,
    ReverseLookup,
    RunChangesS,
)
from .p4_actions import P4OperationError

log = logging.getLogger(__name__)

DEPENDS_ON_RE = re.compile(r"Depends-On:\s*(\d+)")


def build_stack_graph(p4: P4) -> tuple[AdjacencyList, ReverseLookup, set[int]]:
    """
    Builds the full dependency graph for the current user's pending CLs.
    Only includes pending changelists - submitted or not found CLs are excluded.
    Returns (graph, child_to_parent, pending_cls).
    """
    # See all pending changes from user --me means -u $P4USER, -l fetch full desc
    try:
        pending_cls = cast(
            list[RunChangesS], p4.run_changes("-s", "pending", "-l", "--me")  # type: ignore
        )
    except Exception as e:
        log.error(f"Failed to run p4 changes: {e}")
        raise P4OperationError(f"Failed to fetch pending changelists: {e}")

    # Build a set of all pending CL numbers for quick lookup
    pending_cl_nums = {int(cl["change"]) for cl in pending_cls}

    graph: AdjacencyList = defaultdict(list)
    child_to_parent: ReverseLookup = {}

    for cl in pending_cls:
        cl_num = int(cl["change"])
        desc = cl["desc"]

        match = DEPENDS_ON_RE.search(desc)

        if match:
            parent_num = int(match.group(1))
            # Only add the relationship if parent is also pending
            if parent_num in pending_cl_nums:
                graph[parent_num].append(cl_num)
                child_to_parent[cl_num] = parent_num

    return graph, child_to_parent, pending_cl_nums


def get_stack_from_base(
    base_cl: int,
    graph: AdjacencyList,
) -> list[int]:
    """
    Get the full stack to process in parent-first order (BFS)
    starting from a given base CL.
    """
    stack_to_process: list[int] = []
    queue = [base_cl]
    visited = {base_cl}

    # Get the full stack to process in parent-first order (BFS)
    while queue:
        current_cl = queue.pop(0)
        stack_to_process.append(current_cl)

        for child in graph.get(current_cl, []):
            if child not in visited:
                visited.add(child)
                queue.append(child)

    if not stack_to_process:
        log.error(f"CL {base_cl} not found in pending stack graph.")
        return [base_cl]

    return stack_to_process


def get_stack_for_cl(
    cl_num: int,
    child_to_parent: ReverseLookup,
) -> list[int]:
    """
    Gets the full stack (root-to-tip) that a given CL belongs to.
    """
    stack: list[int] = [cl_num]

    # Walk up the tree to the root
    current_cl = cl_num
    while current_cl in child_to_parent:
        parent_cl = child_to_parent[current_cl]
        stack.append(parent_cl)
        current_cl = parent_cl

    # The stack is now tip-to-root, so reverse it
    stack.reverse()
    return stack
