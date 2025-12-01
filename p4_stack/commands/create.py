"""
Implements the `p4-stack create` command.
"""

import typer
import logging
from rich.console import Console

from ..core.p4_actions import (
    P4Connection,
    P4Exception,
    P4LoginRequiredError,
    P4OperationError,
    create_empty_change,
)

log = logging.getLogger(__name__)
console = Console(stderr=True, no_color=True)


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

            # 3. Set Parent: Set the Description field
            default_description = (
                "[Edit description in P4V or 'p4 change']\n\n"
                f"Depends-On: {parent_cl}\n"
            )

            try:
                new_cl_num = create_empty_change(p4, default_description)
            except Exception as e:
                console.print(f"Error: {e}")
                raise typer.Exit(code=1)

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
