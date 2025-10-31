# p4_stack/commands/update.py
import typer
import os
import subprocess
from typing import List, Dict, Optional
from ..p4_actions import (
    P4Connection, P4Exception, P4OperationError, 
    P4ConflictException, P4LoginRequiredError, P4LibException
)
from ..graph_utils import build_stack_graph, StackedChange
from ..state import UpdateState, save_state, load_state, clear_state, STATE_FILE
from rich.console import Console

console = Console(stderr=True)

# --- Helper Functions ---

def _find_node_in_graph(
    cl_num: str, roots: List[StackedChange]
) -> Optional[StackedChange]:
    """Finds a specific CL node in the graph by number."""
    stack = list(roots)
    while stack:
        node = stack.pop()
        if node.cl_num == cl_num:
            return node
        stack.extend(node.children)
    return None

def _get_stack_from_node(base_node: StackedChange) -> List[StackedChange]:
    """Returns a flat list of all nodes in the subtree, starting from base_node."""
    stack_nodes: List[StackedChange] = []
    stack_to_visit = [base_node]
    while stack_to_visit:
        node = stack_to_visit.pop(0) # BFS to maintain order
        stack_nodes.append(node)
        # Add children in sorted order
        stack_to_visit.extend(sorted(node.children, key=lambda c: int(c.cl_num)))
    return stack_nodes

def _get_all_nodes_map(
    roots: List[StackedChange]
) -> Dict[str, StackedChange]:
    """Returns a flat map of {cl_num: StackedChange} for all nodes."""
    all_nodes: Dict[str, StackedChange] = {}
    stack = list(roots)
    while stack:
        node = stack.pop()
        all_nodes[node.cl_num] = node
        stack.extend(node.children)
    return all_nodes

def _launch_editor(p4: P4Connection, cl_num: str) -> None:
    """
    Launches the user's $EDITOR for the files in the base CL.
    Per the plan: Respects $EDITOR env var.
    """
    console.print(f"  Launching editor for changelist [bold]{cl_num}[/bold]...")
    
    editor = os.getenv("EDITOR")
    if not editor:
        editor = "vi" # Fallback as per the plan
        
    try:
        opened_list = p4.get_opened_files(cl_num)
        local_paths = [f['clientFile'] for f in opened_list if 'clientFile' in f]

        if not local_paths:
            console.print(
                f"[yellow]Warning:[/yellow] No files found open in {cl_num} to edit."
            )
            return

        try:
            subprocess.run([editor, *local_paths], check=True)
        except FileNotFoundError:
            raise P4OperationError(f"Editor '{editor}' not found. Check $EDITOR.")
        except subprocess.CalledProcessError as e:
            raise P4OperationError(f"Editor exited with error: {e}")
            
    except P4LibException as e: # This is from p4.get_opened_files
        raise P4OperationError(f"Failed to get files for editor: {e}")

# --- Main Command Logic ---

def update_stack(
    cl_num: str = typer.Argument(
        None,
        help="The base changelist number of the stack to update."
    ),
    continue_op: bool = typer.Option(
        False,
        "--continue",
        help="Continue a previously conflicting update operation.",
    ),
) -> None:
    """
    Updates a changelist and propagates the changes up its stack.
    
    This is the "Rebase Engine."
    """
    
    if not cl_num and not continue_op:
        console.print("[red]Error:[/red] Must specify a changelist number or --continue.")
        raise typer.Exit(code=1)

    is_conflict_exit = False
    is_login_exit = False
    
    try:
        with P4Connection() as p4:
            if continue_op:
                _handle_continue_update(p4)
            else:
                _handle_new_update(p4, cl_num)
                
    except P4LoginRequiredError as e:
        is_login_exit = True
        console.print(f"\n[bold yellow]Login required:[/bold yellow] {e}")
        raise typer.Exit(code=0) # Graceful exit

    except P4ConflictException as e:
        is_conflict_exit = True
        console.print(f"\n[bold yellow]CONFLICT:[/bold yellow] {e}")
        console.print(
            "Please run [bold]'p4 resolve'[/bold] manually to fix."
        )
        console.print(
            "Once resolved, run [bold]'p4-stack update --continue'[/bold]."
        )
        raise typer.Exit(code=0) # Successful exit, awaiting user
        
    except P4Exception as e:
        console.print(f"\n[bold red]Perforce Error:[/bold red] {e}")
        # On error, guarantee workspace is clean
        with P4Connection() as p4_cleanup:
            p4_cleanup.revert_all()
        raise typer.Exit(code=1)
        
    except Exception as e:
        console.print(f"\n[bold red]Unexpected Error:[/bold red] {e}")
        # On *any* crash, guarantee workspace is clean
        console.print("Reverting workspace due to error...")
        with P4Connection() as p4_cleanup:
            p4_cleanup.revert_all()
        raise typer.Exit(code=1)
    
    finally:
        # Per the plan: *always* revert on a successful,
        # non-conflict exit. Skip if session expired.
        if not is_conflict_exit and not is_login_exit:
            with P4Connection() as p4_cleanup:
                p4_cleanup.revert_all()


def _handle_new_update(p4: P4Connection, cl_num: str) -> None:
    """Starts a fresh p4-stack update operation."""
    if os.path.exists(STATE_FILE):
        console.print(
            f"[red]Error:[/red] State file '{STATE_FILE}' already exists."
        )
        console.print("An 'update' operation is already in progress.")
        console.print("Run 'p4-stack update --continue' or delete the file.")
        raise typer.Exit(code=1)

    console.print(f"Starting update for stack based at [bold]{cl_num}[/bold]...")
    
    raw_changes = p4.get_pending_changelists()
    roots = build_stack_graph(raw_changes)
    base_node = _find_node_in_graph(cl_num, roots)
    
    if not base_node:
        console.print(
            f"[red]Error:[/red] Changelist {cl_num} not found in pending stacks."
        )
        raise typer.Exit(code=1)
        
    stack_nodes = _get_stack_from_node(base_node)
    stack_cl_nums = [node.cl_num for node in stack_nodes]
    
    state = UpdateState(
        base_cl=cl_num,
        stack_to_update=stack_cl_nums,
        rebased_cls=[]
    )
    save_state(state)
    
    all_nodes_map = _get_all_nodes_map(roots)
    _run_update_loop(p4, state, all_nodes_map)

def _handle_continue_update(p4: P4Connection) -> None:
    """Resumes an update operation from the state file."""
    state = load_state()
    if not state:
        console.print("[red]Error:[/red] No state file found. Cannot --continue.")
        raise typer.Exit(code=1)
        
    console.print(f"Continuing update for stack [bold]{state.base_cl}[/bold]...")

    raw_changes = p4.get_pending_changelists()
    roots = build_stack_graph(raw_changes)
    all_nodes_map = _get_all_nodes_map(roots)
    
    if state.conflict_cl:
        cl_to_shelve = state.conflict_cl
        console.print(
            f"  Shelving resolved changelist [bold]{cl_to_shelve}[/bold]..."
        )
        p4.force_shelve(cl_to_shelve)
        
        state.rebased_cls.append(cl_to_shelve)
        state.conflict_cl = None
        save_state(state)
    
    _run_update_loop(p4, state, all_nodes_map)

def _run_update_loop(
    p4: P4Connection,
    state: UpdateState,
    all_nodes_map: Dict[str, StackedChange]
) -> None:
    """
    The main rebase engine loop.
    Iterates through CLs, applying the update or rebase logic.
    """
    
    stack_to_process = [
        cl for cl in state.stack_to_update 
        if cl not in state.rebased_cls
    ]

    for cl_to_rebase in stack_to_process:
        node = all_nodes_map.get(cl_to_rebase)
        if not node:
            raise P4OperationError(f"Internal Error: CL {cl_to_rebase} not in graph.")
        
        p4.revert_all()
        
        if cl_to_rebase == state.base_cl:
            console.print(
                f"Updating base changelist [bold]{cl_to_rebase}[/bold]..."
            )
            p4.unshelve(cl_to_rebase, cl_to_rebase)
            _launch_editor(p4, cl_to_rebase)
            console.print(f"  Shelving changes to [bold]{cl_to_rebase}[/bold]...")
            p4.force_shelve(cl_to_rebase)
        
        else:
            parent_cl = node.parent_cl
            if not parent_cl:
                raise P4OperationError(f"Internal Error: CL {cl_to_rebase} has no parent.")
            
            console.print(
                f"Rebasing child [bold]{cl_to_rebase}[/bold] "
                f"onto [bold]{parent_cl}[/bold]..."
            )
            p4.unshelve(cl_to_rebase, cl_to_rebase)
            p4.unshelve(parent_cl, cl_to_rebase)

            try:
                p4.resolve_auto_merge()
            except P4ConflictException as e:
                # Save the conflict state and re-raise
                state.conflict_cl = cl_to_rebase
                save_state(state)
                raise e # Re-raise to be caught by main handler

            console.print(f"  Shelving rebased [bold]{cl_to_rebase}[/bold]...")
            p4.force_shelve(cl_to_rebase)
        
        state.rebased_cls.append(cl_to_rebase)
        save_state(state)
        
    console.print("\n[bold green]Stack update complete.[/bold green]")
    clear_state()