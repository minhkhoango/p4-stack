# p4_stack/commands/submit.py
import typer
from ..p4_actions import P4Connection, P4Exception, P4LoginRequiredError
from typing import List
from rich.console import Console
import re


DEPENDS_ON_RE = re.compile(r'Depends-On:\s*(\d+)', re.IGNORECASE)

console = Console(stderr=True)

def submit_stack(
    stack: List[str] = typer.Argument(
        ...,
        help="The stack of changelists to submit, from base to tip.",
    ),
) -> None:
    """
    Submits an entire stack of changelists, from base to tip.
    
    Patches dependency descriptions *only if* the p4-stack 
    'Depends-On:' tag is found. Otherwise, just submits linearly.
    """
    
    if not stack:
        console.print("[red]Error:[/red] Must specify a stack of CLs.")
        raise typer.Exit(code=1)
    
    try:
        with P4Connection() as p4:
            current_parent_cl_num = None # Tracks the *new* submitted CL
            
            console.print(f"Found stack with {len(stack)} changes. Starting submit...")
            
            for pending_cl_num in stack:
                
                # If this isn't the first CL, patch its description
                if current_parent_cl_num:
                    console.print(
                        f"  Updating [bold]{pending_cl_num}[/bold] to depend on "
                        f"[bold]{current_parent_cl_num}[/bold]..."
                    )
                    
                    try:
                        cl_spec = p4.get_changelist(pending_cl_num)
                        
                        def _set_depends_on(description: str, new_parent_cl: str) -> str:
                            clean_desc = DEPENDS_ON_RE.sub("", description).strip()
                            return f"{clean_desc}\n\nDepends-On: {new_parent_cl}"
                        
                        cl_spec['Description'] = _set_depends_on(
                            cl_spec.get('Description', ''), 
                            current_parent_cl_num
                        )
                        p4.update_changelist(cl_spec)
                    except P4Exception as e:
                        console.print(f"[yellow]Warning:[/yellow] Could not update description "
                                        f"for {pending_cl_num}. Submitting anyway. Error: {e}")
                
                # Unshelve the files before submitting
                console.print(f"  Unshelving files in [bold]{pending_cl_num}[/bold]...")
                p4.unshelve(pending_cl_num, pending_cl_num, force=True)
                
                # Delete the shelved files so we can submit
                try:
                    p4.p4.run('shelve', '-d', '-c', pending_cl_num) # type: ignore
                except P4Exception:
                    pass  # Ignore if shelved files were already deleted
                
                console.print(f"Submitting [bold]{pending_cl_num}[/bold]...")
                new_submitted_cl = p4.submit_changelist(pending_cl_num)
                console.print(
                    f"  -> Submitted as [bold green]CL {new_submitted_cl}[/bold green]"
                )
                
                # This new CL is the parent for the *next* CL in the loop
                current_parent_cl_num = new_submitted_cl
                
                # Revert any remaining files in the workspace
                try:
                    p4.revert_all()
                except P4Exception:
                    pass  # Ignore if no files to revert
                
            console.print("\n[bold green]Stack submitted successfully.[/bold green]")
            
            if typer.confirm(
                f"Delete {len(stack)} obsolete pending changelists?"
            ):
                console.print("Cleaning up pending changelists...")
                for cl in stack:
                    try:
                        p4.p4.run('shelve', '-d', '-c', cl) # type: ignore
                    except P4Exception:
                        pass
                    try:
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