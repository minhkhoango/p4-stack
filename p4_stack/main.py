# p4_stack/main.py
import typer
from .commands.list import list_stack
from .commands.update import update_stack
from .commands.create import create_stack
from .commands.submit import submit_stack
from .commands.review import review_stack
from rich.console import Console

# Set up the main Typer application
app = typer.Typer(
    help="A CLI for stacked diffs in Perforce, bringing a Git-like workflow to P4.",
    add_completion=False,
    rich_markup_mode="markdown",
)

console = Console(stderr=True)

# Register the commands
app.command("list")(list_stack)
app.command("update")(update_stack)
app.command("create")(create_stack)
app.command("submit")(submit_stack)
app.command("review")(review_stack)

@app.callback()
def main_callback() -> None:
    """
    Main callback for the p4-stack CLI.
    This runs before any command.
    """
    pass

if __name__ == "__main__":
    app()