# p4_stack/p4_actions.py
from __future__ import annotations

from P4 import P4, P4Exception as P4LibException  # type: ignore[import-not-found]
from typing import List, Dict, Any, Optional, Union
import os

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

    def sync_head(self) -> None:
        """Syncs the workspace to head revision."""
        try:
            self.p4.run('sync', '//...')  # type: ignore[attr-defined]
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            err_str = str(e)
            # "file(s) up-to-date" is not an error - workspace is already synced
            if "up-to-date" not in err_str.lower():
                raise P4OperationError(f"Error syncing workspace: {e}")

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

    def resolve_auto_merge(self) -> None:
        """Attempts an automatic resolve ('p4 resolve -am')."""
        try:
            resolve_list: P4CommandOutput = self.p4.run('resolve', '-n')  # type: ignore[attr-defined]
            if not resolve_list:
                return
            
            self.p4.run('resolve', '-am')  # type: ignore[attr-defined]

            post_check: P4CommandOutput = self.p4.run('resolve', '-n')  # type: ignore[attr-defined]
            if post_check:
                raise P4ConflictException(
                    "Automatic resolve incomplete. Manual resolution required."
                )
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Perforce session expired. Please run 'p4 login'.")
            err_str = str(e)
            if "must resolve" in err_str or "resolve skipped" in err_str:
                raise P4ConflictException(
                    f"Automatic resolve failed: {e}. Manual resolution required."
                )
            # "No file(s) to resolve" is not an error
            if "no file(s) to resolve" in err_str.lower():
                return
            raise P4OperationError(f"Error during resolve: {e}")

    def resolve_interactive(self) -> None:
        """
        Opens an interactive resolve session using the system's default editor.
        This runs 'p4 resolve' which will prompt the user to resolve conflicts.
        """
        import subprocess
        try:
            subprocess.run(
                ['p4', 'resolve'],
                check=False,
                capture_output=False  # Let it use the terminal directly
            )
            
            still_unresolved: P4CommandOutput = self.p4.run('resolve', '-n')  # type: ignore[attr-defined]
            if still_unresolved:
                raise P4ConflictException(
                    "Interactive resolution incomplete. Some files remain to be resolved."
                )
        except FileNotFoundError:
            raise P4OperationError("Could not find 'p4' command in PATH.")
        except Exception as e:
            raise P4OperationError(f"Error during interactive resolve: {e}")