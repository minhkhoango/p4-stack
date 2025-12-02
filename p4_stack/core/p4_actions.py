"""
Contains the P4Connection class which acts as the primary interface (Facade)
for all Perforce interactions. This centralizes exception handling, logging,
and type safety.
"""

from P4 import P4, P4Exception as P4LibException  # type: ignore
from typing import Any, cast
import os
import logging
import re

from .types import (
    RunChangeO,
    RunDescribeSs,
    RunDescribeS,
    RunChangesS,
    RunWhere,
    RunPropertyL,
    RunPrintMetaData,
)

log = logging.getLogger(__name__)

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


# --- Helper for Error Parsing ---


def _is_login_error(err_str: str) -> bool:
    """Checks if a P4Exception string indicates a login is required."""
    err_lower = err_str.lower()
    return "session has expired" in err_lower or "please login" in err_lower


# --- P4Connection Class ---


class P4Connection:
    """
    The main entry point for P4 Stack operations.
    Wraps the P4Python library to provide a clean, typed, and domain-specific API.
    """

    def __init__(self) -> None:
        self.p4: P4 = P4()
        self._user: str = ""
        self._client: str = ""

    def __enter__(self) -> "P4Connection":
        """Establishes P4 connection."""
        try:
            self.p4.connect()
            self._user = cast(str | None, self.p4.user or os.getenv("P4USER"))  # type: ignore

            if not self._user:
                raise P4ConnectionError(
                    "Could not determine P4 user. "
                    "Ensure $P4USER is set or P4CONFIG is configured."
                )

            if not self._client:
                log.warning("Could not determine P4 client workspace.")

        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError(
                    "Perforce session expired. Please run 'p4 login'."
                )
            raise P4ConnectionError(f"Failed to connect to P4: {e}")
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        """Ensures P4 connection is disconnected."""
        if self.p4.connected():  # type: ignore
            self.p4.disconnect()  # type: ignore

    # =========================================================================
    # Simple properties
    # =========================================================================

    @property
    def port(self) -> str:
        """Get the P4PORT value."""
        return cast(str, getattr(self.p4, "port", "") or os.getenv("P4PORT", ""))

    @property
    def user(self) -> str:
        """Get the P4USER value."""
        return cast(str, getattr(self.p4, "user", "") or os.getenv("P4USER", ""))

    # =========================================================================
    # Core Primitives (Wrappers around p4.run_*)
    # =========================================================================

    def run_describe(self, cl_num: int) -> RunDescribeS | None:
        """Fetches description/status for a single CL."""
        try:
            # -s: Short output (no diffs)
            result = cast(list[RunDescribeS], self.p4.run_describe("-s", cl_num))  # type: ignore
            if result and len(result) > 0:
                return result[0]
            return None
        except P4LibException as e:
            log.warning(f"Failed to describe CL {cl_num}: {e}")
            return None

    def get_cl_description(self, cl_num: int) -> str:
        """Returns the text description of a CL, or empty string if failed."""
        info = self.run_describe(cl_num)
        return info.get("desc", "") if info else ""

    def get_pending_changes(self) -> list[RunChangesS]:
        """Fetches all pending changes for the current user."""
        try:
            # -s pending: status pending
            # -l: long descriptions
            # --me: implies -u <current_user>
            return cast(
                list[RunChangesS],
                self.p4.run_changes("-s", "pending", "-l", "--me"),  # type: ignore
            )
        except P4LibException as e:
            raise P4OperationError(f"Failed to fetch pending changelists: {e}")

    def get_property(self, name: str) -> str | None:
        """Fetches a P4 property value (e.g. P4.Swarm.URL) for base url"""
        try:
            # -l: List -n: Name
            results = cast(
                list[RunPropertyL], self.p4.run_property("-l", "-n", name)  # type: ignore
            )
            if results and len(results) > 0:
                return results[0].get("value")
            return None
        except P4LibException as e:
            log.warning(f"Failed to get property {name}: {e}")
            return None

    # =========================================================================
    # File Operations (Edit, Add, Delete, Where, Print)
    # =========================================================================

    def run_where(self, depot_path: str) -> list[RunWhere]:
        """Maps a depot path to a local client path."""
        try:
            return cast(list[RunWhere], self.p4.run_where(depot_path))  # type: ignore
        except P4LibException as e:
            log.warning(f"Failed to run 'where' on {depot_path}: {e}")
            return []

    def run_print(self, cl_num: int) -> list[RunPrintMetaData | str]:
        """
        Runs p4 print on a specific CL.
        Returns the raw list [metadata, content, metadata, content...].
        """
        try:
            return cast(
                list[RunPrintMetaData | str],
                self.p4.run_print(f"//...@={cl_num}"),  # type: ignore
            )
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(f"Failed to print CL {cl_num}: {e}")

    def run_edit(self, cl_num: int, *files: str) -> None:
        """Opens files for edit in a specific changelist."""
        if not files:
            return
        try:
            # -c: changelist
            self.p4.run_edit("-c", cl_num, *files)  # type: ignore
            log.debug(f"Opened {len(files)} files for edit in CL {cl_num}")
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(f"Failed to open files for edit in CL {cl_num}: {e}")

    def run_delete_files(self, cl_num: int, *files: str) -> None:
        """Marks files for delete in a specific changelist."""
        if not files:
            return
        try:
            # -c: changelist
            self.p4.run_delete("-c", cl_num, *files)  # type: ignore
            log.debug(f"Marked {len(files)} files for delete in CL {cl_num}")
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(
                f"Failed to mark files for delete in CL {cl_num}: {e}"
            )

    # =========================================================================
    # Changelist Management
    # =========================================================================

    def create_change(self, description: str) -> int:
        """
        Creates a new empty pending changelist with the given description.
        """
        try:
            # 1. Get the 'change -o' spec (template)
            change_spec = cast(
                list[RunChangeO], self.p4.run_change("-o")  # type: ignore
            )
            spec = change_spec[0]

            # 2. Modify the spec
            spec["Description"] = description

            # 3. Save the spec
            # save_change returns a list like ["Change 123 created."]
            result_str = cast(str, self.p4.save_change(spec)[0])  # type: ignore

            # 4. Parse the result
            match = re.search(r"Change (\d+) created", result_str)
            if not match:
                raise P4OperationError(
                    f"Could not parse new CL number from: {result_str}"
                )

            return int(match.group(1))

        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(f"Failed to create changelist: {e}")

    def delete_change(self, cl_num: int) -> None:
        """Deletes a pending changelist."""
        try:
            self.p4.run_delete("-c", cl_num)  # type: ignore
            log.debug(f"Deleted CL {cl_num}")
        except P4LibException as e:
            log.warning(f"Failed to delete CL {cl_num}: {e}")

    # =========================================================================
    # Shelving & Unshelving
    # =========================================================================

    def is_change_shelved(self, cl_num: int) -> bool:
        """Checks if a changelist contains any shelved files."""
        try:
            # -S: Shelf info, -s: short, no diff
            result = cast(
                list[RunDescribeSs],
                self.p4.run_describe("-S", "-s", cl_num),  # type: ignore
            )
            if result and len(result) > 0:
                # If 'depotFile' key exists, files are present
                return "depotFile" in result[0]
            return False
        except P4LibException:
            return False

    def shelve(self, cl_num: int, force: bool = False, delete: bool = False) -> None:
        """
        Shelve files in a changelist.
        Args:
            force: If True, uses -f (replace).
            delete: If True, uses -d (delete shelf).
        """
        args: list[int | str] = ["-c", cl_num]
        if force:
            args.insert(0, "-f")
        if delete:
            args.insert(0, "-d")

        try:
            self.p4.run_shelve(*args)  # type: ignore
            log.debug(f"Shelve (d={delete}, f={force}) on CL {cl_num}")
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")

            # If deleting and it doesn't exist, generic p4 error usually, can ignore
            if delete and "no such file" in str(e):
                return

            raise P4OperationError(f"Failed to shelve CL {cl_num}: {e}")

    def unshelve(self, source_cl: int, target_cl: int) -> None:
        """Unshelves files from source_cl into target_cl."""
        try:
            self.p4.run_unshelve("-s", source_cl, "-c", target_cl)  # type: ignore
            log.debug(f"Unshelved {source_cl} -> {target_cl}")
        except P4LibException as e:
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(
                f"Failed to unshelve {source_cl} to {target_cl}: {e}"
            )

    def revert(self, cl_num: int) -> None:
        """Reverts all open files in the specified changelist."""
        try:
            self.p4.run_revert("-c", cl_num, "//...")  # type: ignore
            log.debug(f"Reverted CL {cl_num}")
        except P4LibException as e:
            if "not opened" in str(e).lower():
                return  # Nothing to do
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired.")
            raise P4OperationError(f"Failed to revert CL {cl_num}: {e}")

    def ensure_shelved(self, cl_num: int) -> bool:
        """Idempotent ensure shelved. Returns True if successful."""
        if self.is_change_shelved(cl_num):
            return True
        try:
            self.shelve(cl_num, force=True)
            return True
        except P4OperationError as e:
            log.warning(f"Could not auto-shelve CL {cl_num}: {e}")
            return False

    # =========================================================================
    # High-Level Workflow Actions
    # =========================================================================

    def create_review_seed(self, parent_cl: int, child_cl: int | None) -> int | None:
        """
        Create an new and identical copy of parent_cl, and returns its id.
        A CL can only be linked to 1 review, while a review can be linked to many CLs.

        child_cl recommended for logging purposes.
        """
        if not self.ensure_shelved(parent_cl):
            log.warning(f"Parent CL {parent_cl} could not be shelved for seeding.")
            return None

        seed_desc = f"[p4-stack seed] Base for CL {child_cl} (parent: CL {parent_cl})"

        try:
            seed_cl = self.create_change(seed_desc)
            log.debug(f"Created seed CL {seed_cl} for child {child_cl}")

            self.unshelve(source_cl=parent_cl, target_cl=seed_cl)
            self.shelve(seed_cl)
            self.revert(seed_cl)

            return seed_cl
        except Exception as e:
            log.error(f"Failed to create seed shelf: {e}")
            return None

    def cleanup_seed(self, seed_cl: int) -> None:
        """Clean up a temporary seed CL."""
        try:
            self.shelve(seed_cl, delete=True)
            self.delete_change(seed_cl)
        except Exception as e:
            log.warning(f"Failed to delete seed CL {seed_cl}: {e}")
