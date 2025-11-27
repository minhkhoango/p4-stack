# p4_stack/main.py
import logging
import typer
from rich.console import Console

from .logging_config import setup_logging
from .commands.create import create_stack
from .commands.list import list_stack
from .commands.update import update_stack
from .commands.upload import upload_stack

# Configure logging once at application startup
setup_logging()

log = logging.getLogger(__name__)

# Set up the main Typer application
app = typer.Typer(
    help="A CLI for stacked diffs in Perforce, bringing a Git-like workflow to P4.",
    add_completion=False,
)

console = Console(stderr=True)

# Register the commands
@app.command(
    "create",
    help="Create a new stacked CL dependent on a parent CL."
)
def create_cmd(
    parent_cl: int = typer.Argument(
        ...,
        help="The parent changelist number to stack on top of.",
        metavar="PARENT_CL",
    )
) -> None:
    create_stack(parent_cl=parent_cl)

@app.command(
    "list",
    help="List all pending stacks for the current user."
)
def list_cmd() -> None:
    list_stack()
@app.command(
    "update",
    help="Rebase a stack: edit a base CL and re-apply all children."
)

def update_cmd(
    base_cl: int = typer.Argument(
        ...,
        help="The changelist to edit and rebase children onto.",
        metavar="BASE_CL",
    )
) -> None:
    update_stack(base_cl=base_cl)

@app.command(
    "upload",
    help="Upload an entire stack to Swarm for review, creating/linking reviews."
)
def upload_cmd(
    root_cl: int = typer.Argument(
        ...,
        help="The ROOT changelist of the stack to upload. Must not have a parent.",
        metavar="ROOT_CL",
    )
) -> None:
    upload_stack(root_cl=root_cl)

if __name__ == "__main__":
    app()