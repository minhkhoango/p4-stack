# p4_stack/commands/create.py
import typer
from ..p4_actions import P4Connection, P4Exception, P4LoginRequiredError
from ..graph_utils import build_stack_graph, find_stack_tip, set_depends_on
from rich.console import Console

console = Console(stderr=True)

def create_stack(
    description: str = typer.Argument(
        ...,
        help="The description for the new changelist."
    ),
) -> None:
    """
    Creates a new changelist stacked on the current tip.
    
    Moves all files from the default changelist into the new one.
    """
    try:
        with P4Connection() as p4:
            # 1. Check for files in the default changelist
            files_in_default = p4.get_files_in_default_changelist()
            if not files_in_default:
                console.print(
                    "[yellow]Warning:[/yellow] No files in default changelist. "
                    "Nothing to create or move; exiting."
                )
                raise typer.Exit(code=0)
            
            # 2. Find the current stack tip
            raw_changes = p4.get_pending_changelists()
            roots = build_stack_graph(raw_changes)
            tip_cl = find_stack_tip(roots)
            
            # 3. Prepare the description with Depends-On
            final_desc = description
            if tip_cl:
                final_desc = set_depends_on(description, tip_cl)
                console.print(f"Stacking on current tip: [bold]{tip_cl}[/bold]")
            else:
                console.print("Creating new stack root.")
                
            # 4. Create the new changelist
            new_cl = p4.create_new_changelist(final_desc)
            
            # 5. Move files from default to the new CL
            if files_in_default:
                depot_paths = [f['depotFile'] for f in files_in_default]
                p4.reopen_files(new_cl, depot_paths)
            
            console.print(
                f"\n[bold green]Success![/bold green] "
                f"Created changelist [bold]{new_cl}[/bold]."
            )
            console.print(
                f"Run [bold]'p4 shelve -c {new_cl}'[/bold] to save your changes."
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