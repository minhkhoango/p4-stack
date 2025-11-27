"""
Contains the core 3-way merge rebase engine logic.
"""
import logging
import tempfile
import os
import subprocess
from P4 import P4 # type: ignore
from typing import cast, Any

from .types import (
    Snapshot,
    FileToDepot,
    MergeResult,
    RunPrintMetaData,
    RunWhere,
)
from .p4_actions import P4OperationError

log = logging.getLogger(__name__)


def get_cl_snapshot(p4: P4, cl_num: int) -> tuple[Snapshot, FileToDepot]:
    """
    Fetches the content of every file in a shelved changelist.
    Returns: (snapshot, filename_to_depot_map)
    """
    snapshot: Snapshot = {}
    filename_to_depot: FileToDepot = {}
    try:
        shelved_files = cast(list[Any], p4.run_print(f"//...@={cl_num}")) # type: ignore
        for i in range(0, len(shelved_files), 2):
            metadata = cast(RunPrintMetaData, shelved_files[i])
            content = cast(str, shelved_files[i+1])

            depot_file: str = metadata["depotFile"].strip("'\"")
            filename: str = os.path.basename(depot_file)

            snapshot[filename] = content
            filename_to_depot[filename] = depot_file

    except Exception as e:
        log.error(f"Error getting snapshot for CL {cl_num}: {e}")
        # Check if it's a "no shelved files" error, which is non-fatal
        if "no such file(s)" in str(e) or "empty changelist" in str(e):
             return snapshot, filename_to_depot # Return empty
        raise P4OperationError(f"Failed to p4 print @={cl_num}: {e}")

    return snapshot, filename_to_depot

def edit_snapshot_with_editor(snapshot: Snapshot) -> Snapshot:
    """
    Writes a snapshot to a temp dir, launches $EDITOR, and reads it back.
    Uses file basenames as temp filenames.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        local_paths: list[str] = []
        filename_to_depot: FileToDepot = {}

        for depot_path, content in snapshot.items():
            local_path = os.path.join(temp_dir, depot_path)
            local_paths.append(local_path)
            filename_to_depot[local_path] = depot_path

            with open(local_path, "w", encoding="utf-8") as f:
                f.write(content)

        # Launch editor
        editor = os.getenv("EDITOR", "nano")
        try:
            subprocess.run([editor] + local_paths, check=True)
        except Exception as e:
            log.error(f"Error running editor '{editor}'. Aborting. {e}")
            raise P4OperationError(f"Editor '{editor}' failed. Aborting update.")

        # Read thew new snapshot
        new_snapshot: Snapshot = {}
        for local_path in local_paths:
            depot_path = filename_to_depot[local_path]
            with open(local_path, "r", encoding="utf-8") as f:
                new_snapshot[depot_path] = str(f.read())
        
        return new_snapshot
    
def _three_way_merge_file(
    base: str | None,
    ours: str | None,
    theirs: str | None
) -> MergeResult:
    """
    Performs a 3-way merge on string content.
    Handles None inputs (adds/deletes) by writing empty temp files.)
    """
    # Use delete=False to manage paths, clean up in finally
    base_f = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding="utf-8")
    ours_f = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding="utf-8")
    theirs_f = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding="utf-8")

    try:
        base_f.write(base or "")
        ours_f.write(ours or "")
        theirs_f.write(theirs or "")

        base_f.close()
        ours_f.close()
        theirs_f.close()

        base_path, ours_path, theirs_path = base_f.name, ours_f.name, theirs_f.name

        # Run diff3 (diff3 -m -E MYFILE OLDFILE YOURFILE)
        proc = subprocess.run(
            ['diff3', '-m', '-E', ours_path, base_path, theirs_path],
            capture_output=True,
            text=True,
            encoding="utf-8"
        )
        merged_content = str(proc.stdout)
        has_conflict: bool = (proc.returncode == 1)

        return merged_content, has_conflict

    finally:
        os.remove(base_f.name)
        os.remove(ours_f.name)
        os.remove(theirs_f.name)

def three_way_merge_folder(
    base_folder: Snapshot,
    ours_folder: Snapshot,
    theirs_folder: Snapshot
) -> dict[str, MergeResult]:
    """
    Merges three folder snapshots using file-by-file 3-way merge.\n
    Returns {file_name, (merged_content, has_conflict)}
    """
    all_files: set[str] = set(base_folder.keys()) | \
                            set(ours_folder.keys()) | \
                            set(theirs_folder.keys())
    
    merged_snapshot: dict[str, MergeResult] = {}

    for file_path in all_files:
        base_content = base_folder.get(file_path)
        ours_content = ours_folder.get(file_path)
        theirs_content = theirs_folder.get(file_path)

        # 1. File added *only* in our (child) branch
        if base_content is None and theirs_content is None and ours_content is not None:
            merged_snapshot[file_path] = (ours_content, False) # No conflict
            continue
        
        # 2. File added *only* in their (new parent) branch
        if base_content is None and theirs_content is not None and ours_content is None:
            merged_snapshot[file_path] = (theirs_content, False)
            continue
        
        # 3. File deleted in our branch, unchanged in theirs
        if base_content is not None and ours_content is None and theirs_content == base_content:
            continue

        # 4. File deleted in their branch, unchanged in ours
        if base_content is not None and theirs_content is None and ours_content == base_content:
            continue

        # 5. File deleted in *both* branches
        if base_content is not None and ours_content is None and theirs_content is None:
            continue

        merged_snapshot[file_path] = _three_way_merge_file(base_content, ours_content, theirs_content)
    
    return merged_snapshot

def commit_snapshot_to_cl(
    p4: P4, 
    cl_num: int, 
    new_snapshot: Snapshot, 
    original_snapshot: Snapshot,
    filename_to_depot: FileToDepot
) -> None:
    """
    Atomically updates a shelved CL to match the new snapshot.
    Handles file adds, edits, and deletes with batched commands.
    """
    try:
        # Revert any pending changes in this CL
        try:
            p4.run_revert("-c", cl_num, "//...") # type: ignore
        except:
            pass

        original_files = set(original_snapshot.keys())
        new_files = set(new_snapshot.keys())

        # Only include files that actually changed content
        files_to_edit = {
            f for f in (original_files & new_files)
            if original_snapshot.get(f) != new_snapshot.get(f)
        }
        files_to_add = new_files - original_files
        files_to_delete = original_files - new_files

        log.debug(f"CL {cl_num}: files_to_edit: {files_to_edit}")
        log.debug(f"CL {cl_num}: files_to_add: {files_to_add}")
        log.debug(f"CL {cl_num}: files_to_delete: {files_to_delete}")

        # --- Handle Adds/Edits ---
        files_to_write = list(files_to_edit | files_to_add)
        if files_to_write:
            # Convert filenames to depot paths for Perforce commands
            depot_paths_to_write: list[str] = []
            for f in files_to_write:
                if f not in filename_to_depot:
                    raise P4OperationError(f"Cannot add new file '{f}' to CL {cl_num}. "
                        "File was not in original snapshot or inherited from parent. "
                        "Adding new files during 'update' is not yet supported.")
                depot_paths_to_write.append(filename_to_depot[f])
            
            log.debug(f"Attempt to run_edit, depot_paths_to_write: {depot_paths_to_write}")
            p4.run_edit("-c", cl_num, *depot_paths_to_write) # type: ignore

            for filename in files_to_write:
                try:
                    depot_path = filename_to_depot[filename]
                    client_path_map = cast(list[RunWhere], p4.run_where(depot_path)) # type: ignore
                    
                    log.debug(f"client_path_map for {filename}: {client_path_map}")
                    if not client_path_map or "path" not in client_path_map[0]:
                        raise Exception(f"File not in client view: {depot_path}")
                    
                    local_path: str = client_path_map[0]["path"]

                    local_dir = os.path.dirname(local_path)
                    if not os.path.exists(local_dir):
                        os.makedirs(local_dir)

                    # Write the new content from memory to the local file
                    with open(local_path, "w", encoding="utf-8") as f:
                        f.write(new_snapshot[filename])

                except Exception as e:
                    log.error(f"Failed to write/map file {filename}: {e}")
                    raise
        
        # --- Handle Deletes ---
        files_to_delete_list = list(files_to_delete)
        if files_to_delete_list:
            # Convert filenames to depot paths for Perforce commands
            depot_paths_to_delete = [filename_to_depot[f] for f in files_to_delete_list]
            p4.run_delete("-c", cl_num, *depot_paths_to_delete) # type: ignore

        # --- Commit to Shelf ---
        if files_to_write or files_to_delete_list:
            p4.run_shelve("-f", "-c", cl_num) # type: ignore
        else:
            # If the CL is now empty, delete the shelve
            if not new_files and original_files:
                p4.run_shelve("-d", "-c", cl_num) # type: ignore

    except Exception as e:
        log.error(f"Failed to commit snapshot to CL {cl_num}: {e}")
        raise P4OperationError(f"Failed to commit snapshot to CL {cl_num}: {e}")
    finally:
        p4.run_revert("-c", cl_num, "//...") # type: ignore