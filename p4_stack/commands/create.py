"""
Implements the `p4-stack create` command.
"""

import typer
import logging
from typing import cast
import re
from rich.console import Console

from ..core.p4_actions import (
    P4Connection,
    P4Exception,
    P4LoginRequiredError,
    P4OperationError,
)

from ..core.types import RunChangeO

log = logging.getLogger(__name__)
console = Console(stderr=True)


def create_stack(parent_cl: int) -> None:
    """
    Creates a new pending changelist (a "node") that is dependent
    on the specified parent changelist.
    """
    try:
        with P4Connection() as p4_conn:
            p4 = p4_conn.p4
            # 1. Check for files in the default changelist
            try:
                p4.run_describe("-s", parent_cl)  # type: ignore
            except P4OperationError as e:
                console.print(
                    f"Error: Parent CL '{parent_cl}' not found or is invalid."
                )
                log.error(f"Failed to fetch parent CL {parent_cl}: {e}")
                raise typer.Exit(code=1)

            # 2. Create CL: Run p4 change -o to get a new changelist spec
            try:
                change_spec = cast(list[RunChangeO], p4.run_change("-o"))  # type: ignore
            except P4OperationError as e:
                console.print(f"Error: Fail to get new CL spec.")
                log.error(f"Failed to get new CL spec: {e}")
                raise typer.Exit(code=1)

            # 3. Set Parent: Set the Description field
            change_spec[0]["Description"] = (
                "[Edit description in P4V or 'p4 change']\n\n"
                f"Depends-On: {parent_cl}\n"
            )

            # 4. Save: Run p4 save_change to handles p4.input for spec dictionaries
            result_str = cast(str, p4.save_change(change_spec[0])[0])  # type: ignore

            # 5. Output: Confirm the new CL
            match = re.search(r"Change (\d+) created.", result_str)
            if not match:
                raise P4OperationError(
                    f"Could not parse new CL number from: {result_str}"
                )

            new_cl_num = match.group(1)
            console.print(f"Created new changelist: {new_cl_num}")
            console.print(
                f"Run 'p4 change {new_cl_num}' to add files and edit the description."
            )

    except P4LoginRequiredError as e:
        console.print(f"\nLogin required: {e}")
        raise typer.Exit(code=0)
    except P4Exception as e:
        console.print(f"\nPerforce Error: {e}")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\nAn unexpected error occurred: {e}")
        raise typer.Exit(code=1)
