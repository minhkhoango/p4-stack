"""
Implements the `p4-stack update` command.

This file orchestrates the rebase process by calling
functions from the `core` modules.
"""
import logging
import typer
from rich.console import Console

from ..core.p4_actions import (
    P4Connection,
    P4Exception,
    P4LoginRequiredError,
    P4OperationError
)

from ..core.types import (
    Snapshot,
    StackSnapshot,
    FileToDepot,
)

from ..core.graph import (
    build_stack_graph,
    get_stack_from_base,
)

from ..core.rebase import (
    get_cl_snapshot,
    edit_snapshot_with_editor,
    three_way_merge_folder,
    commit_snapshot_to_cl,
)

log = logging.getLogger(__name__)
console = Console(stderr=True)


def update_stack(base_cl: int) -> None:
    """
    Performs an in-memory rebase on a stack, starting from base_cl.
    """

    try:
        with P4Connection() as p4:
            # Get the stack dependancy graph
            graph, child_to_parent = build_stack_graph(p4.p4)

            # Get the full stack to process in parent-first order (BFS)
            stack_to_process = get_stack_from_base(base_cl, graph)
            log.debug(f"stack_to_process: {stack_to_process}")

            # --- Phase 1: Load Phase ---
            original_stack: StackSnapshot = {}
            # CL -> {filename: depot_path}
            filename_to_depot_map: dict[int, FileToDepot] = {}
            try:
                for cl_num in stack_to_process:
                    snapshot, filename_to_depot = get_cl_snapshot(p4.p4, cl_num)
                    log.debug(filename_to_depot)
                    original_stack[cl_num] = snapshot
                    filename_to_depot_map[cl_num] = filename_to_depot
            except Exception as e:
                log.error(f"Failed to load snapshots. Aborting. {e}")
                console.print(f"Error: Failed to load snapshots: {e}")
                return
            log.debug(f"original_stack: {original_stack}")
            
            # --- Phase 2: Edit Phase ---
            try:
                new_base_folder = edit_snapshot_with_editor(original_stack[base_cl])
                log.debug(f"base_cl: {original_stack[base_cl]}")
                log.debug(f"new_base_folder: {new_base_folder}")
            except Exception as e:
                log.error(f"Failed during edit phase. Aborting. {e}")
                console.print(f"Error: Editor failed: {e}")
                return
            
            # --- Phase 3: Rebase (In-Memory) Phase ---
            new_stack: StackSnapshot = {}
            new_stack[base_cl] = new_base_folder

            # Iterate children, skipping base_cl
            for cl_num in stack_to_process[1:]:
                parent_cl = child_to_parent.get(cl_num)
                if parent_cl is None: # Should be impossible if stack is correct
                    log.error(f"Logic error: CL {cl_num} in stack but has no parent. Aborting.")
                    raise P4OperationError(f"Logic error: CL {cl_num} has no parent.")
                
                base_folder = original_stack[parent_cl] # Original parent
                ours_folder = original_stack[cl_num]    # Oringal child
                theirs_folder = new_stack[parent_cl]    # New parent

                log.debug(f"base_folder: {base_folder}")
                log.debug(f"ours_folder: {ours_folder}")
                log.debug(f"theirs_folder: {theirs_folder}")

                # Run merge, returns: {file: (content, has_conflict)}
                merged_result = three_way_merge_folder(base_folder, ours_folder, theirs_folder)
                log.debug(f"Merged result for {cl_num}: {merged_result}")

                # Process results
                merged_snapshot: Snapshot = {}
                conflicted_files: Snapshot = {}

                for file_path, (content, has_conflict) in merged_result.items():
                    if has_conflict:
                        conflicted_files[file_path] = content
                    else:
                        merged_snapshot[file_path] = content
                    
                # Interactive Conflict Resolution Loop
                while conflicted_files:
                    try:
                        if not typer.confirm("\nConflict detected, press ENTER to resolve... (or 'n' to abort)", default=True):
                            log.error("Update aborted by user.")
                            raise typer.Abort()
                    except typer.Abort:
                        console.print("\nUpdate aborted.")
                        raise typer.Exit(code=1)
                    
                    try:
                        resolved_files = edit_snapshot_with_editor(conflicted_files)
                    except Exception as e:
                        log.error(f"Editor failed. Aborting update. {e}")
                        raise typer.Exit(code=1)
                    
                    # Re-validate user's edits, good enough regex detection
                    conflicted_files = {}
                    for file_path, content in resolved_files.items():
                        if ">>>>>>>" in content or "<<<<<<<" in content or "=======" in content:
                            console.print(f"Conflict markers still detected in: {file_path}")
                            conflicted_files[file_path] = content
                        else:
                            merged_snapshot[file_path] = content

                console.print(f"CL {cl_num} successfully rebased")
                new_stack[cl_num] = merged_snapshot

            # --- Phase 4: Commit Phase ---
            console.print("In-memory rebase successful. Committing changes to Perforce...")
            try:
                for cl_num in stack_to_process:
                    log.debug(f"cl_num: {cl_num}")
                    log.debug(f"new_stack[cl_num]: {new_stack[cl_num]}")
                    log.debug(f"original_stack[cl_num]: {original_stack[cl_num]}")

                    commit_snapshot_to_cl(
                        p4.p4, 
                        cl_num, 
                        new_stack[cl_num], 
                        original_stack[cl_num],
                        filename_to_depot_map[cl_num]
                    )
                console.print(f"Stack update complete for CL {base_cl}")
            except Exception as e:
                log.error(f"An error occurred during commit: {e}")

    # --- Global Error Handling ---
    except P4LoginRequiredError as e:
        console.print(f"\nLogin required: {e}")
        raise typer.Exit(code=0)
    except P4Exception as e:
        console.print(f"\nPerforce Error: {e}")
        log.exception(f"P4 Error in update_stack for CL {base_cl}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\nAn unexpected error occurred: {e}")
        log.exception(f"Unexpected error in update_stack for CL {base_cl}")
        raise typer.Exit(code=1)
    
# if __name__ == '__main__':
#     from ..logging_config import setup_logging
#     setup_logging()
#     update_stack(214)