"""
Implements the `p4-stack list` command.
"""
import typer
import logging
from rich.console import Console
from rich.tree import Tree
from P4 import P4 # type: ignore

from ..core.p4_actions import (
    P4Connection,
    P4Exception,
    P4LoginRequiredError
)
from ..core.graph import AdjacencyList, build_stack_graph

log = logging.getLogger(__name__)
console = Console(stderr=True)


def _build_rich_tree(
    node: int,
    graph: AdjacencyList, 
    parent_tree: Tree,
    p4: P4
) -> None:
    """Recursively builds a rich.Tree for a given stack."""

    node_label = f"► [bold]{node}[/bold]"
    child_tree = parent_tree.add(node_label)

    children = sorted(graph.get(node, []))
    for child in children:
        _build_rich_tree(child, graph, child_tree, p4)

def list_stack() -> None:
    """
    Fetches and displays all stacked pending changelists for the user.
    """
    try:
        with P4Connection() as p4_conn:
            p4 = p4_conn.p4

            graph, child_to_parent, all_pending_cls = build_stack_graph(p4)
            log.debug(f"graph: {graph}")
            log.debug(f"children_to_parent: {child_to_parent}")
            log.debug(f"all_pending_cls: {all_pending_cls}")
            if not all_pending_cls:
                console.print("No stacked changes found.")
                return
            
            # Find all roots (pending CLs that are not children of another pending CL)
            all_children = set(child_to_parent.keys())
            root_nodes = sorted(list(all_pending_cls - all_children))
            log.debug(f"all_childrens: {all_children}")
            log.debug(f"root_nodes: {root_nodes}")

            if not root_nodes:
                console.print("No stack roots found.")
                log.warning(f"Graph has nodes but no roots. Graph: {graph}, ChildMap: {child_to_parent}")
                return

            rich_tree = Tree(
                f"Current Stacks for {str(p4.user)}:", # type: ignore
            )

            for root in root_nodes:
                _build_rich_tree(root, graph, rich_tree, p4)

            console.print(rich_tree)
            
    except P4LoginRequiredError as e:
        console.print(f"\nLogin required: {e}")
        raise typer.Exit(code=0) # Graceful exit, not an error
        
    except P4Exception as e:
        console.print(f"\nPerforce Error: {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\nAn unexpected error occurred: {e}")
        raise typer.Exit(code=1)