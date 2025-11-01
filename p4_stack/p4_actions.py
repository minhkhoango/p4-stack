# p4_stack/p4_actions.py
from __future__ import annotations

from P4 import P4, P4Exception as P4LibException  # type: ignore[import-not-found]
from typing import List, Dict, Any, Optional, Union
import os
import re

# --- Type Aliases for P4 Library ---
P4Result = Union[Dict[str, Any], str]  # p4.run() returns list of these
P4ChangeSpec = Dict[str, Any]  # Changelist specification dict
P4CommandOutput = List[P4Result]  # Standard P4 command output

# --- Custom Domain-Specific Exceptions ---

class P4Exception(Exception):
    """Base exception for p4-stack errors."""
    pass

class P4ConnectionError(P4Exception):
    """Failed to connect to Perforce."""
    pass

class P4LoginRequiredError(P4ConnectionError):
    """
    Raised when a P4 command fails because the
    user's session ticket has expired.
    """
    pass

class P4OperationError(P4Exception):
    """Failed to run a P4 command."""
    pass

class P4ConflictException(P4OperationError):
    """
    Raised when 'p4 resolve -am' fails and
    manual user intervention is required.
    """
    pass

# --- Helper for Error Parsing ---

def _is_login_error(err_str: str) -> bool:
    """Checks if a P4Exception string indicates a login is required."""
    err_lower = err_str.lower()
    return "session has expired" in err_lower or "please login" in err_lower

# --- P4Connection Class ---

class P4Connection:
    """
    Manages the connection and core ops for P4.
    Respects all standard P4 environment variables.
    """
    
    def __init__(self) -> None:
        self.p4: P4 = P4()
        self.user: Optional[str] = None

    def __enter__(self) -> P4Connection:
        """Establishes P4 connection as a context manager."""
        try:
            self.p4.connect()  # type: ignore[attr-defined]
            user_from_p4: Optional[str] = self.p4.user  # type: ignore[attr-defined]
            self.user = user_from_p4 or os.getenv("P4USER")
            
            if not self.user:  # type: ignore[misc]
                raise P4ConnectionError(
                    "Could not determine P4 user. "
                    "Ensure $P4USER is set or P4CONFIG is configured."
                )
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4ConnectionError(f"Failed to connect to P4: {e}")
        return self
    
    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Ensures P4 connection is disconnected."""
        if self.p4.connected():  # type: ignore[attr-defined]
            self.p4.disconnect()  # type: ignore[attr-defined]
            
    def get_pending_changelists(self) -> List[Dict[str, Any]]:
        """Fetches all pending changelists for the connected user."""
        if not self.user or not self.p4.connected():  # type: ignore[attr-defined]
            raise P4ConnectionError("User not set or P4 not connected.")
            
        try:
            result: P4CommandOutput = self.p4.run(  # type: ignore[attr-defined]
                'changes', '-s', 'pending', '-u', self.user, '-l'
            )
            # Filter to ensure we only return dictionaries
            return [item for item in result if isinstance(item, dict)]  # type: ignore[return-value]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error fetching pending changes: {e}")

    # --- Week 2: Write Operations ---

    def revert_all(self) -> None:
        """Reverts all files in the client workspace."""
        try:
            self.p4.run('revert', '//...')  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            err_str = str(e)
            # Handle both variations of the "no files open" message
            if "file(s) not open" not in err_str and "not opened" not in err_str:
                raise P4OperationError(f"Error reverting workspace: {e}")

    def unshelve(self, source_cl: str, target_cl: str, force: bool = False) -> None:
        """Unshelves files from source_cl into target_cl."""
        try:
            if force:
                self.p4.run('unshelve', '-f', '-s', source_cl, '-c', target_cl)  # type: ignore[attr-defined]
            else:
                self.p4.run('unshelve', '-s', source_cl, '-c', target_cl)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(
                f"Error unshelving {source_cl} into {target_cl}: {e}"
            )

    def force_shelve(self, cl_num: str) -> None:
        """Force-shelves all open files into the specified changelist."""
        try:
            self.p4.run('shelve', '-f', '-c', cl_num)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error force-shelving {cl_num}: {e}")

    def get_opened_files(self, cl_num: str) -> List[Dict[str, Any]]:
        """Gets a list of opened files (as dicts) in a changelist."""
        try:
            result: P4CommandOutput = self.p4.run('opened', '-c', cl_num)  # type: ignore[attr-defined]
            return [item for item in result if isinstance(item, dict)]  # type: ignore[return-value]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error getting opened files for {cl_num}: {e}")

    def resolve_auto_merge(self) -> None:
        """Attempts an automatic merge ('p4 resolve -am')."""
        try:
            # First check if there are files to resolve
            resolve_list: P4CommandOutput = self.p4.run('resolve', '-n')  # type: ignore[attr-defined]
            if not resolve_list:
                # No files need resolving
                return
                
            # Try automatic merge
            results: P4CommandOutput = self.p4.run('resolve', '-am')  # type: ignore[attr-defined]
            has_conflict = False
            for msg in results:  # type: ignore[misc]
                if isinstance(msg, dict):
                    action: str = msg.get("action", "")  # type: ignore[misc]
                    # Check if resolve failed or needs manual intervention
                    if "resolve" in action or action == "":
                        has_conflict = True
                        break
                elif isinstance(msg, str) and "must resolve" in msg:
                    has_conflict = True
                    break
            if has_conflict:
                raise P4ConflictException(
                    "Automatic merge failed. Manual conflict resolution required."
                )
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            err_str = str(e)
            if "must resolve" in err_str or "resolve skipped" in err_str:
                raise P4ConflictException(
                    f"Automatic merge failed: {e}. Manual resolution required."
                )
            # "No file(s) to resolve" is not an error
            if "no file(s) to resolve" in err_str.lower():
                return
            raise P4OperationError(f"Error during resolve: {e}")

    # --- Week 3: Workflow Operations ---

    def get_files_in_default_changelist(self) -> List[Dict[str, Any]]:
        """Gets a list of files (as dicts) in the default changelist."""
        return self.get_opened_files('default')

    def create_new_changelist(self, description: str) -> str:
        """Creates a new empty pending CL and returns its number."""
        try:
            change_spec: P4ChangeSpec = self.p4.fetch_change()  # type: ignore[attr-defined]
            change_spec['Description'] = description
            result: P4CommandOutput = self.p4.save_change(change_spec)  # type: ignore[attr-defined]
            
            result_str = " ".join(str(item) for item in result)  # type: ignore[misc]
            match = re.search(r"Change (\d+) created", result_str)
            if not match:
                raise P4OperationError(f"Could not parse new CL number from: {result}")
            return match.group(1)
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error creating new changelist: {e}")

    def reopen_files(self, target_cl: str, files: List[str]) -> None:
        """Moves a list of depot paths to a target changelist."""
        try:
            self.p4.run('reopen', '-c', target_cl, *files)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error reopening files into {target_cl}: {e}")

    def get_changelist(self, cl_num: str) -> Dict[str, Any]:
        """Fetches the full changelist object/spec."""
        try:
            return self.p4.fetch_change(cl_num) # type: ignore
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error fetching changelist {cl_num}: {e}")

    def update_changelist(self, cl_spec: Dict[str, Any]) -> None:
        """Saves an updated changelist spec (e.g., to change description)."""
        try:
            self.p4.save_change(cl_spec)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error updating changelist: {e}")

    def submit_changelist(self, cl_num: str) -> str:
        """
        Submits a changelist and returns the new, permanent CL number.
        """
        try:
            result: P4CommandOutput = self.p4.run('submit', '-c', cl_num)  # type: ignore[attr-defined]
            
            submitted_cl: Optional[str] = None
            for line in result:  # type: ignore[misc]
                if isinstance(line, dict):
                    submitted_cl_value: Any = line.get('submittedChange')  # type: ignore[misc]
                    if submitted_cl_value:
                        submitted_cl = str(submitted_cl_value)  # type: ignore[arg-type]
                        break
                elif isinstance(line, str):
                    match = re.search(r"Change (\d+) submitted", line)
                    if match:
                        submitted_cl = match.group(1)
                        break

            if not submitted_cl:
                raise P4OperationError(f"Could not parse submitted CL number from: {result}")
            return submitted_cl
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error submitting {cl_num}: {e}")

    def delete_changelist(self, cl_num: str) -> None:
        """Deletes a pending changelist."""
        try:
            self.p4.run('change', '-d', cl_num)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Error deleting changelist {cl_num}: {e}")
            
    def create_review(self, cl_num: str) -> None:
        """Creates a Swarm review for a changelist."""
        try:
            self.p4.run('review', '-c', cl_num)  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            if "must be enabled" in str(e) or "unknown command" in str(e):
                raise P4OperationError(
                    f"Command 'p4 review' failed. "
                    "Ensure Swarm is configured on the Perforce server."
                )
            raise P4OperationError(f"Error creating review for {cl_num}: {e}")