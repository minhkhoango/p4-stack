# p4_stack/commands/update.py
import typer
from typing import List, Dict, Optional, Any
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
                console.print(f"Starting update for stack based at {base_cl}...")
                _run_update_loop(p4, base_cl, children_cls)

    except P4LoginRequiredError as e:
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0)
      
    except P4ConflictException as e:
        is_conflict_exit = True
        console.print(f"\n[bold yellow]CONFLICT:[/bold yellow] {e}")
        console.print("Please run [bold]'p4 resolve'[/bold] manually to fix.")
        console.print("Once resolved, run [bold]'p4-stack update --continue'[/bold].")
        # We don't exit with code 1, it's a planned stop.
        
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
        if not is_conflict_exit:
            console.print("Cleaning up workspace...")
            try:
                with P4Connection() as p4_cleanup:
                    p4_cleanup.revert_all()
            except P4LoginRequiredError:
                console.print("Skipping workspace cleanup: 'p4 login' required.")
            except P4Exception as e:
                console.print(f"Warning: workspace cleanup failed: {e}")
            except Exception as e:
                console.print(f"Unexpected error during cleanup: {e}")

# --- We need a minimal state file just for --continue ---
STATE_FILE = ".p4-stack-state.json"

def _save_conflict_state(base_cl: str, conflict_cl: str, remaining_cls: List[str]) -> None:
    """Saves complete state for resuming after conflict resolution."""
    state: Dict[str, Any] = {
        "base_cl": base_cl,
        "conflict_cl": conflict_cl,
        "remaining_cls": remaining_cls
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
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
    base_cl = state["base_cl"]
    remaining_cls_raw = state.get("remaining_cls", [])
    
    # Ensure remaining_cls is a list of strings
    remaining_cls: List[str] = []
    if isinstance(remaining_cls_raw, list):
        remaining_cls = [str(cl) for cl in remaining_cls_raw]
    
    console.print(
        f"Continuing update for [bold]{conflict_cl}[/bold]..."
    )
    
    # User has resolved. We just need to shelve.
    console.print(f"  Shelving resolved changelist [bold]{conflict_cl}[/bold]...")
    p4.force_shelve(conflict_cl)
    console.print(f"  [green]✓[/green] Successfully rebased [bold]{conflict_cl}[/bold]\n")
    
    # If there are remaining CLs, continue with them
    if remaining_cls:
        console.print(f"Continuing with remaining CLs: {', '.join(remaining_cls)}")
        _run_update_loop(p4, base_cl, [conflict_cl] + remaining_cls)
    else:
        _clear_state()
        console.print("[bold green]Stack update complete.[/bold green]")


def _run_update_loop(
    p4: P4Connection,
    base_cl: str,
    children_cls: List[str]
) -> None:
    """
    The main rebase engine loop.
    """
    
    for i, cl_to_rebase in enumerate(children_cls):
        # Determine the immediate parent for this child
        if i == 0:
            # First child: parent is the base CL
            parent_cl = base_cl
        else:
            # Subsequent children: parent is the previous child (already rebased)
            parent_cl = children_cls[i - 1]
        
        console.print(
            f"Rebasing [bold]{cl_to_rebase}[/bold] "
            f"onto [bold]{parent_cl}[/bold]..."
        )
        
        # Step 1: Clean workspace
        p4.revert_all()
        p4.sync_head()
        
        # Step 2: Unshelve the child's own changes first to establish the base
        try:
            p4.unshelve(cl_to_rebase, cl_to_rebase, force=False)
        except P4Exception as e:
            err_msg = str(e).lower()
            if "already opened" not in err_msg and "already open" not in err_msg:
                console.print(f"  [yellow]Warning during child unshelve: {e}[/yellow]")

        # Step 3: Unshelve the parent's changes on top, which will be treated as "theirs"
        try:
            p4.unshelve(parent_cl, cl_to_rebase, force=False)
        except P4Exception as e:
            # Check if this is a benign "files already open" error
            err_msg = str(e).lower()
            if "already opened" not in err_msg and "already open" not in err_msg:
                console.print(f"  [yellow]Warning during parent unshelve: {e}[/yellow]")

        # Step 4: Resolve conflicts
        try:
            p4.resolve_auto_merge()
        except P4ConflictException as e:
            # Auto-merge failed - try interactive resolution
            console.print(
                f"\n[yellow]Automatic merge failed:[/yellow] {e}\n"
                f"Opening interactive resolve for [bold]{cl_to_rebase}[/bold]..."
            )
            try:
                p4.resolve_interactive()
                console.print("[green]Interactive resolution completed.[/green]")
            except P4ConflictException:
                # Still has conflicts - save state and exit
                remaining_cls = children_cls[i+1:] if i+1 < len(children_cls) else []
                _save_conflict_state(base_cl, cl_to_rebase, remaining_cls)
                raise P4ConflictException(
                    "Interactive resolution incomplete. "
                    "Please manually resolve remaining conflicts."
                )

        # Step 5: Shelve the result
        try:
            p4.force_shelve(cl_to_rebase)
        except P4Exception as e:
            # If shelving fails due to unresolved files, persist state and stop
            if "must resolve" in str(e).lower():
                remaining_cls = children_cls[i+1:] if i+1 < len(children_cls) else []
                _save_conflict_state(base_cl, cl_to_rebase, remaining_cls)
                raise P4ConflictException(
                    "Shelving failed because files remain unresolved. Please run 'p4 resolve', then 'p4-stack update --continue'."
                )
            raise
        console.print(f"[green]✓[/green] Successfully rebased [bold]{cl_to_rebase}[/bold]")
    _clear_state()