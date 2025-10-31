# p4_stack/commands/review.py
import typer
from ..p4_actions import P4Connection, P4Exception, P4OperationError, P4LoginRequiredError
from ..graph_utils import build_stack_graph
from .update import _find_node_in_graph, _get_stack_from_node
from rich.console import Console

console = Console(stderr=True)

def review_stack(
    cl_num: str = typer.Argument(
        ...,
        help="The base changelist number of the stack to create a review for."
    ),
) -> None:
    """
    Creates a single Swarm review for an entire stack.
    """
    
    temp_cl: str | None = None
    try:
        with P4Connection() as p4:
            console.print(f"Finding stack from base [bold]{cl_num}[/bold]...")
            raw_changes = p4.get_pending_changelists()
            roots = build_stack_graph(raw_changes)
            base_node = _find_node_in_graph(cl_num, roots)
            
            if not base_node:
                raise P4OperationError(
                    f"Changelist {cl_num} not found in pending stacks."
                )
            
            stack_nodes = _get_stack_from_node(base_node)
            p4.revert_all()

            temp_cl_desc = (
                f"[p4-stack] Review for stack {cl_num}: {base_node.short_desc}"
            )
            temp_cl = p4.create_new_changelist(temp_cl_desc)
            
            console.print(
                f"Created temporary changelist [bold]{temp_cl}[/bold] for review."
            )
            
            for i, node in enumerate(stack_nodes):
                console.print(
                    f"  Unshelving {node.cl_num} ({i+1}/{len(stack_nodes)})..."
                )
                p4.unshelve(node.cl_num, temp_cl)
                
            console.print(f"Creating Swarm review for [bold]{temp_cl}[/bold]...")
            p4.create_review(temp_cl)
            
            console.print(
                "\n[bold green]Swarm review created/updated successfully.[/bold green]"
            )

    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0) # Graceful exit

    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}")
        raise typer.Exit(code=1)
    finally:
        if temp_cl:
            console.print("Cleaning up workspace and temporary changelist...")
            try:
                with P4Connection() as p4:
                    p4.revert_all()
                    p4.delete_changelist(temp_cl)
                console.print("Cleanup complete.")
            except P4Exception as e:
                console.print(
                    f"[yellow]Warning:[/yellow] Failed to auto-cleanup "
                    f"temporary CL {temp_cl}. "
                    f"Please delete it manually. Error: {e}"
                )