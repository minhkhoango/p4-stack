"""
Contains the P4Connection context manager and custom exceptions
for robust Perforce API interaction.
"""
from P4 import P4, P4Exception as P4LibException # type: ignore
from typing import Any, cast
import os
import logging


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

    def __enter__(self) -> 'P4Connection':
        """Establishes P4 connection as a context manager."""
        try:
            self.p4.connect()
            self.user = cast(str | None, self.p4.user or os.getenv("P4USER")) # type: ignore
            
            if not self.user:
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
        if self.p4.connected():  # type: ignore
            self.p4.disconnect() # type: ignore