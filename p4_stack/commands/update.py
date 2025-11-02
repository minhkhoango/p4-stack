# p4_stack/commands/update.py
import typer
from typing import List, Dict, Optional
from ..p4_actions import (
    P4Connection, P4Exception,
    P4ConflictException, P4LoginRequiredError
)
from rich.console import Console
import json
import os


console = Console(stderr=True)

def update_stack(
    stack: List[str] = typer.Argument(
        ...,
        help="The stack of changelists to update, from base to tip.",
    ),
    continue_op: bool = typer.Option(
        False,
        "--continue",
        help="Continue a previously conflicting update operation.",
    ),
) -> None:
    """
    Updates a stack of changelists by rebasing them in order.
    
    Assumes the base CL (the first in the list) is the *source of the fix*
    and propagates its changes up the stack.
    """
    
    if not stack and not continue_op:
        console.print("[red]Error:[/red] Must specify a stack of CLs or --continue.")
        raise typer.Exit(code=1)

    is_conflict_exit = False
    
    try:
        with P4Connection() as p4:
            if continue_op:
                # The --continue operation re-runs the *last attempted rebase*.
                _handle_continue_update(p4)
            else:
                if len(stack) < 2:
                    console.print(
                        "[yellow]Warning:[/yellow] A stack of 1 CL was provided. "
                        "Nothing to update."
                    )
                    raise typer.Exit(code=0)
                
                # We only rebase the *children*
                base_cl = stack[0]
                children_cls = stack[1:]
                console.print(f"Starting update for stack based at [bold]{base_cl}[/bold]...")
                _run_update_loop(p4, base_cl, children_cls)
                
    except P4ConflictException as e:
        is_conflict_exit = True
        console.print(f"\n[bold yellow]CONFLICT:[/bold yellow] {e}")
        console.print("Please run [bold]'p4 resolve'[/bold] manually to fix.")
        console.print("Once resolved, run [bold]'p4-stack update --continue'[/bold].")
        # We don't exit with code 1, it's a planned stop.

    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0)
        
    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        raise typer.Exit(code=1)
        
    except Exception as e:
        console.print(f"\n[bold red]Unexpected Error:[/bold red] {e}")
        console.print("Reverting workspace due to error...")
        with P4Connection() as p4_cleanup:
            p4_cleanup.revert_all()
        raise typer.Exit(code=1)
    
    finally:
        # *Always* revert on a successful or error exit.
        # Skip only on a *conflict* exit, to let the user resolve.
        if not is_conflict_exit:
            console.print("Cleaning up workspace...")
            with P4Connection() as p4_cleanup:
                p4_cleanup.revert_all()

# --- We need a minimal state file just for --continue ---
STATE_FILE = ".p4-stack-state.json"

def _save_conflict_state(parent_cl: str, conflict_cl: str) -> None:
    """Saves *only* the CLs involved in the conflict."""
    state = {"parent_cl": parent_cl, "conflict_cl": conflict_cl}
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
    except IOError as e:
        console.print(f"Error: Could not write state file {STATE_FILE}: {e}")

def _load_conflict_state() -> Optional[Dict[str, str]]:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (IOError, json.JSONDecodeError):
        return None

def _clear_state() -> None:
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)

# --- End State Helpers ---

def _handle_continue_update(p4: P4Connection) -> None:
    """Resumes an update operation from the state file."""
    state = _load_conflict_state()
    if not state:
        console.print("[red]Error:[/red] No state file found. Cannot --continue.")
        raise typer.Exit(code=1)
        
    conflict_cl = state["conflict_cl"]
    parent_cl = state["parent_cl"]
    
    console.print(
        f"Continuing update for [bold]{conflict_cl}[/bold] onto [bold]{parent_cl}[/bold]..."
    )
    
    # User has resolved. We just need to shelve.
    console.print(f"  Shelving resolved changelist [bold]{conflict_cl}[/bold]...")
    p4.force_shelve(conflict_cl)
    
    _clear_state()
    console.print(
        "\n[bold green]Continue operation complete.[/bold green]\n"
        "Please re-run your original 'p4-stack update ...' command, \n"
        f"starting from the *next* CL: [bold]{conflict_cl}[/bold]"
    )
    console.print(
        f"Example: p4-stack update {conflict_cl} [child_of_conflict_cl] ..."
    )


def _run_update_loop(
    p4: P4Connection,
    base_cl: str,
    children_cls: List[str]
) -> None:
    """
    The main rebase engine loop.
    """
    
    current_parent = base_cl
    
    for cl_to_rebase in children_cls:
        p4.revert_all()
        
        console.print(
            f"Rebasing child [bold]{cl_to_rebase}[/bold] "
            f"onto [bold]{current_parent}[/bold]..."
        )
        
        # 1. Unshelve the *newly fixed* parent (base)
        p4.unshelve(current_parent, cl_to_rebase)
        
        # 2. Force-unshelve child's original changes on top
        p4.unshelve(cl_to_rebase, cl_to_rebase, force=True)

        try:
            # 3. Attempt auto-merge
            p4.resolve_auto_merge()
        except P4ConflictException as e:
            # Save the *minimal* conflict state and re-raise
            _save_conflict_state(current_parent, cl_to_rebase)
            raise e # Re-raise to be caught by main handler

        # 4. Shelve the result, which becomes the new parent
        console.print(f"  Shelving rebased [bold]{cl_to_rebase}[/bold]...")
        p4.force_shelve(cl_to_rebase)
        
        # 5. The rebased CL is now the parent for the next loop
        current_parent = cl_to_rebase
        
    console.print("\n[bold green]Stack update complete.[/bold green]")
    _clear_state()