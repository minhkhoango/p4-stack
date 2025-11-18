"""
Centralized logging configuration for p4-stack.
Import and call setup_logging() at the entry point of any module.
"""
import logging
from pathlib import Path

_logging_configured = False


def setup_logging(log_file: str | None = None, level: int = logging.DEBUG) -> None:
    """
    Configure logging for the entire application.
    
    Args:
        log_file: Path to log file. If None, uses example1.log at workspace root.
        level: Logging level (default: DEBUG)
    """
    global _logging_configured
    
    if _logging_configured:
        return  # Already configured, skip
    
    # Get the absolute path to the workspace root
    if log_file is None:
        workspace_root = Path(__file__).parent.parent
        log_file = str(workspace_root / "example1.log")
    
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(log_file)],
        force=True  # Force reconfiguration
    )
    
    # Set P4 library logging to WARNING to reduce noise
    logging.getLogger("P4").setLevel(logging.WARNING)
    
    _logging_configured = True
