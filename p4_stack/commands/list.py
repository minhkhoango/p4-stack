# p4_stack/commands/list.py
import typer
from ..p4_actions import P4Connection, P4Exception, P4LoginRequiredError
from ..graph_utils import build_stack_graph, StackedChange
from typing import List
from rich.console import Console
from rich.tree import Tree

console = Console(stderr=True)

def _print_stack_tree(roots: List[StackedChange]) -> None:
    """Uses rich.Tree to pretty-print the stack forest."""
    
    if not roots:
        console.print("[yellow]No stacked changes found.[/yellow]")
        return
        
    tree = Tree(
        "[bold]Current Stacks:[/bold]",
        guide_style="cyan",
    )
    
    stack_to_visit: List[tuple[StackedChange, Tree]] = [
        (root, tree) for root in roots
    ]
    
    while stack_to_visit:
        node, parent_tree_node = stack_to_visit.pop(0)
        
        # Add the current node to the tree
        node_label = f"► [bold]{node.cl_num}[/bold]: {node.short_desc}"
        child_tree_node = parent_tree_node.add(node_label)
        
        # Add its children to the stack
        for child in sorted(node.children, key=lambda c: int(c.cl_num)):
            stack_to_visit.append((child, child_tree_node))
            
    console.print(tree)

def list_stack() -> None:
    """
    Fetches and displays all stacked pending changelists for the user.
    """
    try:
        with P4Connection() as p4:
            console.print(f"Fetching pending changes for @{p4.user}...")
            raw_changes = p4.get_pending_changelists()
            roots = build_stack_graph(raw_changes)
            _print_stack_tree(roots)
            
    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0) # Graceful exit, not an error
        
    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}")
        raise typer.Exit(code=1)