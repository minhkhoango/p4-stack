# p4_stack/commands/submit.py
import typer
from ..p4_actions import P4Connection, P4Exception, P4OperationError, P4LoginRequiredError
from ..graph_utils import build_stack_graph, set_depends_on
from .update import find_node_in_graph, get_stack_from_node
from typing import Dict
from rich.console import Console

console = Console(stderr=True)

def submit_stack(
    cl_num: str = typer.Argument(
        ...,
        help="The base changelist number of the stack to submit."
    ),
) -> None:
    """
    Submits an entire stack, from base to tip, as a linear history.
    """
    
    try:
        with P4Connection() as p4:
            console.print(f"Finding stack from base [bold]{cl_num}[/bold]...")
            raw_changes = p4.get_pending_changelists()
            roots = build_stack_graph(raw_changes)
            base_node = find_node_in_graph(cl_num, roots)
            
            if not base_node:
                raise P4OperationError(
                    f"Changelist {cl_num} not found in pending stacks."
                )
            
            stack_nodes = get_stack_from_node(base_node)
            submitted_cl_map: Dict[str, str] = {}
            
            console.print(f"Found stack with {len(stack_nodes)} changes. Starting submit...")
            
            for node in stack_nodes:
                current_cl_num = node.cl_num
                parent_cl_num = node.parent_cl
                
                if parent_cl_num and parent_cl_num in submitted_cl_map:
                    new_parent_cl = submitted_cl_map[parent_cl_num]
                    console.print(
                        f"  Updating [bold]{current_cl_num}[/bold] to depend on "
                        f"[bold]{new_parent_cl}[/bold]..."
                    )
                    
                    cl_spec = p4.get_changelist(current_cl_num)
                    new_desc = set_depends_on(node.description, new_parent_cl)
                    cl_spec['Description'] = new_desc
                    p4.update_changelist(cl_spec)
                
                # Unshelve the files before submitting
                console.print(f"  Unshelving files in [bold]{current_cl_num}[/bold]...")
                p4.unshelve(current_cl_num, current_cl_num, force=True)
                
                # Delete the shelved files so we can submit
                try:
                    p4.p4.run('shelve', '-d', '-c', current_cl_num)
                except P4Exception:
                    pass  # Ignore if shelved files were already deleted
                
                console.print(f"Submitting [bold]{current_cl_num}[/bold]...")
                new_submitted_cl = p4.submit_changelist(current_cl_num)
                console.print(
                    f"  -> Submitted as [bold green]CL {new_submitted_cl}[/bold green]"
                )
                submitted_cl_map[current_cl_num] = new_submitted_cl
                
                # Revert any remaining files in the workspace
                try:
                    p4.revert_all()
                except P4Exception:
                    pass  # Ignore if no files to revert
                
            console.print("\n[bold green]Stack submitted successfully.[/bold green]")
            
            pending_cls = [node.cl_num for node in stack_nodes]
            if typer.confirm(
                f"Delete {len(pending_cls)} obsolete pending changelists?"
            ):
                console.print("Cleaning up pending changelists...")
                for cl in pending_cls:
                    try:
                        # Delete shelved files first if they exist
                        try:
                            p4.p4.run('shelve', '-d', '-c', cl)
                        except P4Exception:
                            pass  # Ignore if no shelved files
                        p4.delete_changelist(cl)
                    except P4Exception as e:
                        console.print(f"  [yellow]Warning: Could not delete CL {cl}: {e}[/yellow]")
                console.print("Cleanup complete.")

    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0)

    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]An unexpected error occurred:[/bold red] {e}")
        raise typer.Exit(code=1)