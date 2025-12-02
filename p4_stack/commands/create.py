"""
Implements the `p4-stack create` command.
"""

import typer
import logging
from rich.console import Console

from ..core.p4_actions import (
    P4Connection,
    P4LoginRequiredError,
)

log = logging.getLogger(__name__)
console = Console(stderr=True, no_color=True)


def create_stack(parent_cl: int) -> None:
    """
    Creates a new pending changelist dependent on the specified parent.
    """
    try:
        with P4Connection() as p4_conn:
            # 1. Validate Parent
            if not p4_conn.run_describe(parent_cl):
                console.print(f"[red]Error:[/red] Parent CL '{parent_cl}' not found.")
                raise typer.Exit(1)

            # 2. Create Child
            desc = "[Edit description]\n\n" f"Depends-On: {parent_cl}\n"

            new_cl = p4_conn.create_change(desc)

            console.print(f"Created new changelist: {new_cl}")
            console.print(f"Run 'p4 change {new_cl}' to edit.")

    except P4LoginRequiredError:
        console.print("[yellow]Session expired.[/yellow] Please login.")
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
