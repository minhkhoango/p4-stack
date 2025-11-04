# p4_stack/commands/update.py
import typer
from typing import List
from ..p4_actions import (
    P4Connection, P4Exception,
    P4ConflictException, P4LoginRequiredError,
    P4OperationError
)
from rich.console import Console
import sys


console = Console(stderr=True)

def update_stack(
    stack: List[str] = typer.Argument(
        ...,
        help="The stack of changelists to update, from base to tip.",
    ),
) -> None:
    """
    Updates a stack of changelists by rebasing them in order.
    
    Assumes the base CL (the first in the list) is the *source of the fix*
    and propagates its changes up the stack.
    """
    
    if not stack:
        console.print("[red]Error:[/red] Must specify a stack of CLs.")
        raise typer.Exit(code=1)
    
    if len(stack) < 2:
        console.print(
            "[yellow]Warning:[/yellow] A stack of 1 CL was provided. "
            "The base CL is assumed to be fixed. Nothing to update."
        )
        raise typer.Exit(code=0)

    parent_cl = stack[0]
    children_cls = stack[1:]
    
    console.print(
        f"Starting stack update. Base: [bold]{parent_cl}[/bold]. "
        f"Children: {', '.join(children_cls)}"
    )

    is_conflict_exit = False
    
    try:
        with P4Connection() as p4:                
            for i, child_cl in enumerate(children_cls):
                console.print(
                    f"Rebasing [bold]{child_cl}[/bold] onto [bold]{parent_cl}[/bold]..."
                )
                
                try:
                    _run_rebase_step(p4, parent_cl=parent_cl, child_cl=child_cl)
                except P4ConflictException as e:
                    # This is the stateless exit specified in the mvp_action_plan
                    is_conflict_exit = True
                    console.print(f"\n[bold yellow]CONFLICT:[/bold yellow] {e}")
                    console.print("Please run [bold]'p4 resolve'[/bold] manually to fix.")
                    console.print("After resolving, shelve your changes:")
                    console.print(f"  [bold]$ p4 shelve -f -c {child_cl}[/bold]")
                    
                    # Instruct user on how to run the *rest* of the stack
                    remaining_stack = stack[i+1:]
                    if remaining_stack:
                        remaining_str = " ".join([child_cl] + remaining_stack)
                        console.print("\nThen, re-run the rest of the update:")
                        console.print(f"  [bold]$ p4-stack update {remaining_str}[/bold]")
                    else:
                        console.print("\nThis was the last CL in the stack. You are done.")
                    
                    # Exit the loop and the program
                    sys.exit(0)

                console.print(
                    f"  [green]✓[/green] Successfully rebased [bold]{child_cl}[/bold]"
                )
                
                # The rebased child is now the parent for the next loop
                parent_cl = child_cl

    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0)
    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]Unexpected Error:[/bold red] {e}")
        console.print("Reverting workspace due to error...")
        if not is_conflict_exit:
            try:
                with P4Connection() as p4_cleanup:
                    p4_cleanup.revert_all()
            except Exception as e_clean:
                console.print(f"  [red]Cleanup revert failed:[/red] {e_clean}")
        raise typer.Exit(code=1)
    
    finally:
        if not is_conflict_exit:
            console.print("\n[bold green]Stack update complete.[/bold green]")
            console.print("Cleaning up workspace...")
            try:
                with P4Connection() as p4_cleanup:
                    p4_cleanup.revert_all()
            except Exception as e:
                console.print(f"Unexpected error during cleanup: {e}")

def _run_rebase_step(p4: P4Connection, parent_cl: str, child_cl: str) -> None:
    """
    Executes the atomic rebase operation:
    Rebases child_cl onto parent_cl.
    
    This implements the logic from mvp_action_plan section 8.D
    """
    
    # Step 1: Clean workspace
    console.print("  1. Reverting workspace...")
    p4.revert_all()
    
    # Step 2: Unshelve the PARENT's fixed changes. This is the new "base".
    console.print(f"  2. Unshelving base [bold]{parent_cl}[/bold] into CL [bold]{child_cl}[/bold]...")
    p4.unshelve(parent_cl, child_cl)
    
    # Step 3: Force-unshelve the CHILD's original changes on top. This is "YOURS".
    console.print(f"  3. Unshelving changes [bold]{child_cl}[/bold] on top (force)...")
    p4.unshelve(child_cl, child_cl, force=True)
    
    # Step 4: Run `p4 resolve -af`. This merges what it can
    console.print("  4. Resolving conflicts (auto-merge, then auto-yours)...")
    try:
        p4.resolve_auto_merge()
    except P4ConflictException as e:
        raise
    except P4Exception as e:
        raise P4OperationError(f"Error during auto-resolve: {e}")

    # Step 5: Clean up the conflict markers `resolve -af` left behind.
    console.print(f"  5. Cleaning conflict markers from CL [bold]{child_cl}[/bold]...")
    try:
        p4.fix_conflict_markers(child_cl)
    except P4Exception as e:
        raise P4OperationError(f"Error cleaning conflict markers: {e}")
    
    # Step 6: Shelve the newly rebased result
    console.print(f"  6. Shelving rebased CL [bold]{child_cl}[/bold]...")
    try:

        p4.force_shelve(child_cl)
        console.print(f"    [green]✓[/green] Successfully shelved CL [bold]{child_cl}[/bold]")
    except P4Exception as e:
        console.print(f"    [red]Shelving failed with error:[/red] {str(e)}")
        if "must resolve" in str(e).lower():
            raise P4ConflictException(
                "Shelving failed because files remain unresolved. "
                "This indicates the automatic resolver failed."
            )
        raise