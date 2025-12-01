"""
Contains the P4Connection context manager and custom exceptions
for robust Perforce API interaction.
"""

from P4 import P4, P4Exception as P4LibException  # type: ignore
from typing import Any, cast
import os
import logging
import re

from .types import RunChangeO, RunDescribeSs

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
        self.user: str | None = None

    def __enter__(self) -> "P4Connection":
        """Establishes P4 connection as a context manager."""
        try:
            self.p4.connect()
            self.user = cast(str | None, self.p4.user or os.getenv("P4USER"))  # type: ignore

            if not self.user:
                raise P4ConnectionError(
                    "Could not determine P4 user. "
                    "Ensure $P4USER is set or P4CONFIG is configured."
                )
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


# --- Seed Shelf Helpers for Stacked Reviews ---


def create_empty_change(p4: P4, description: str) -> int:
    """
    Create a new empty pending changelist.

    Args:
        p4: Connected P4 instance
        description: Description for the new changelist

    Returns:
        The new changelist number
    """
    try:
        change_spec = cast(list[RunChangeO], p4.run_change("-o"))  # type: ignore
    except P4OperationError as e:
        log.error(f"Failed to get new CL spec: {e}")
        raise P4OperationError(f"Failed to get new CL spec: {e}")

    # 3. Set Parent: Set the Description field
    change_spec[0]["Description"] = description

    # 4. Save: Run p4 save_change to handles p4.input for spec dictionaries
    result_str = cast(str, p4.save_change(change_spec[0])[0])  # type: ignore

    # 5. Output: Confirm the new CL
    match = re.search(r"Change (\d+) created.", result_str)
    if not match:
        raise P4OperationError(f"Could not parse new CL number from: {result_str}")

    new_cl_num = match.group(1)
    return int(new_cl_num)


def shelve_change(p4: P4, change_num: int) -> None:
    """
    Shelve all files in a changelist.

    Args:
        p4: Connected P4 instance
        change_num: The changelist number to shelve
    """
    try:
        p4.run_shelve("-c", change_num)  # type: ignore
        log.debug(f"Shelved CL {change_num}")
    except P4LibException as e:
        if _is_login_error(str(e)):
            raise P4LoginRequiredError("Session expired. Please run 'p4 login'.")
        raise P4OperationError(f"Failed to shelve CL {change_num}: {e}")


def unshelve_to(p4: P4, target_change: int, from_shelf: int) -> None:
    """
    Unshelve files from one changelist into another.

    Args:
        p4: Connected P4 instance
        target_change: The changelist to unshelve into
        from_shelf: The shelved changelist to unshelve from
    """
    try:
        p4.run_unshelve("-s", from_shelf, "-c", target_change)  # type: ignore
        log.debug(f"Unshelved CL {from_shelf} into CL {target_change}")
    except P4LibException as e:
        if _is_login_error(str(e)):
            raise P4LoginRequiredError("Session expired. Please run 'p4 login'.")
        raise P4OperationError(
            f"Failed to unshelve CL {from_shelf} to {target_change}: {e}"
        )


def revert_change(p4: P4, change_num: int) -> None:
    """
    Revert all files in a changelist (to clean up workspace after shelving).

    Args:
        p4: Connected P4 instance
        change_num: The changelist number to revert
    """
    try:
        p4.run_revert("-c", change_num, "//...")  # type: ignore
        log.debug(f"Reverted files in CL {change_num}")
    except P4LibException as e:
        # Ignore "file(s) not opened" errors - means nothing to revert
        if "not opened" not in str(e).lower():
            if _is_login_error(str(e)):
                raise P4LoginRequiredError("Session expired. Please run 'p4 login'.")
            raise P4OperationError(f"Failed to revert CL {change_num}: {e}")


def is_change_shelved(p4: P4, change_num: int) -> bool:
    """
    Check if a changelist has shelved files.

    Args:
        p4: Connected P4 instance
        change_num: The changelist number to check

    Returns:
        True if the changelist has shelved files
    """
    try:
        result = cast(
            list[RunDescribeSs], p4.run_describe("-S", "-s", change_num)  # type: ignore
        )
        if result and len(result) > 0:
            # If depotFile is present, there are shelved files
            return "depotFile" in result[0]
        return False
    except P4LibException:
        return False


def create_seed_shelf_from_parent(p4: P4, parent_cl: int, child_cl: int) -> int | None:
    """
    Create a new shelved changelist containing the parent's content.
    This "seed" CL can be used to create a child review where:
    - Version 1 = parent content (seed)
    - Version 2 = child content

    Args:
        p4: Connected P4 instance
        parent_cl: The parent changelist (must be shelved)
        child_cl: The child changelist (for description context)

    Returns:
        The new seed changelist number, or None if creation fails
    """
    try:
        # Check if parent is shelved
        if not is_change_shelved(p4, parent_cl):
            log.warning(f"Parent CL {parent_cl} is not shelved, cannot create seed")
            return None

        # Create a new empty change for the seed
        seed_desc = f"[p4-stack seed] Base for CL {child_cl} (parent: CL {parent_cl})"
        seed_cl = create_empty_change(p4, seed_desc)
        log.debug(f"Created seed CL {seed_cl} for child {child_cl}")

        # Unshelve parent content into the seed CL
        unshelve_to(p4, seed_cl, parent_cl)

        # Shelve the seed CL (this creates the shelf we need for review creation)
        shelve_change(p4, seed_cl)

        # Revert the workspace files (clean up after shelving)
        revert_change(p4, seed_cl)

        log.info(f"Created seed shelf CL {seed_cl} from parent CL {parent_cl}")
        return seed_cl

    except P4OperationError as e:
        log.warning(f"Failed to create seed shelf: {e}")
        return None
    except Exception as e:
        log.warning(f"Unexpected error creating seed shelf: {e}")
        return None


def ensure_change_shelved(p4: P4, change_num: int) -> bool:
    """
    Ensure a changelist is shelved. If not, shelve it.

    Args:
        p4: Connected P4 instance
        change_num: The changelist number

    Returns:
        True if the change is now shelved, False if shelving failed
    """
    if is_change_shelved(p4, change_num):
        return True

    try:
        shelve_change(p4, change_num)
        return True
    except P4OperationError as e:
        log.warning(f"Could not shelve CL {change_num}: {e}")
        return False


def ensure_review_safe_seed(p4: P4, parent_cl: int, child_cl: int) -> int | None:
    """
    Ensure a review-safe seed exists for creating a child review with parent-vs-child diff.

    1. Ensures parent CL is shelved
    2. Creates a new seed shelf with parent's content

    Args:
        p4: Connected P4 instance
        parent_cl: The parent changelist
        child_cl: The child changelist

    Returns:
        The seed changelist number, or None if seed creation fails
    """
    # Ensure parent is shelved first
    if not ensure_change_shelved(p4, parent_cl):
        log.warning(f"Cannot ensure parent CL {parent_cl} is shelved")
        return None

    # Create the seed shelf
    return create_seed_shelf_from_parent(p4, parent_cl, child_cl)
