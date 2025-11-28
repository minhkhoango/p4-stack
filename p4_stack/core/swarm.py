"""
Contains the SwarmClient class for interacting with the Helix Swarm API.
Uses the existing P4 session ticket for authentication.
"""

import logging
import httpx
import subprocess
import getpass
import os
import time
from pathlib import Path
from typing import Any, cast
from P4 import P4  # type: ignore

from .types import (
    RunPropertyL,
    RunSwarmGet,
    RunSwarmGetEntry,
    RunSwarmPost,
    RunSwarmPostEntry,
)
import re

log = logging.getLogger(__name__)

# --- Custom Swarm Exceptions ---


class SwarmError(Exception):
    """Base exception for Swarm API errors."""

    pass


class SwarmAuthError(SwarmError):
    """Raised when Swarm authentication fails."""

    pass


class SwarmConfigError(SwarmError):
    """Raised when Swarm URL cannot be determined."""

    pass


class SwarmAPIError(SwarmError):
    """Raised when a Swarm API call fails."""

    pass


# --- SwarmClient Class ---


class SwarmClient:
    """
    A client for interacting with the Helix Swarm REST API.

    Uses the existing P4 session ticket for authentication (no password prompts).
    Auto-detects Swarm URL from P4 server properties or environment variable.
    """

    API_VERSION = "v11"

    def __init__(self, p4: P4) -> None:
        """
        Initialize the Swarm client.
        """
        self.p4 = p4
        self._user = self._get_user()
        self._ticket = self._get_ticket()
        self._base_url = self._get_swarm_url()

        # Create httpx client with basic auth
        self._client = httpx.Client(
            base_url=f"{self._base_url}/api/{self.API_VERSION}",
            auth=(self._user, self._ticket),
            timeout=30.0,
        )

        # Output looks good, maybe swarm local config still doesn't allow working with ticket?
        log.debug(
            f"SwarmClient initialized for user '{self._user}' at {self._base_url}"
        )

    def _get_user(self) -> str:
        """Get the P4 username."""
        user = getattr(self.p4, "user", None) or os.getenv("P4USER")
        if not user:
            raise SwarmAuthError("Could not determine P4 user.")
        return cast(str, user)

    def _get_ticket(self) -> str:
        """
        Get the cached host-unlocked ticket in ~/.p4stack_ticket
        If not found/expired, generate new ticket and cache it
        """
        # Try to read cached ticket first
        cached_ticket = self._read_cached_ticket()
        if cached_ticket:
            log.debug(f"Using cached host-unlocked ticket: {cached_ticket[:5]}...")
            return cached_ticket

        # No cached ticket - prompt for password
        try:
            password = getpass.getpass("Enter P4 password: ")

            result = subprocess.run(
                ["p4", "login", "-a", "-p"],
                input=password,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                raise SwarmAuthError("Password invalid.")

            # stdout format: 'Enter password: \nAEBC...\n'
            ticket_match = re.search(r"[0-9A-Fa-f]{32}", result.stdout)

            if ticket_match:
                ticket = ticket_match.group(0)
                log.debug(f"Generated host-unlocked P4 ticket: {ticket[:5]}...")
                # Cache the ticket for future use
                self._cache_ticket(ticket)
                return ticket

            raise SwarmAuthError("Failed to parse P4 ticket from response.")

        except subprocess.SubprocessError as e:
            raise SwarmAuthError(f"Failed to run p4 login: {e}")
        except KeyboardInterrupt:
            raise SwarmAuthError("Password entry cancelled.")

    def _get_ticket_cache_path(self) -> Path:
        """Get the path to the ticket cache file."""
        return Path.home() / ".p4stack" / "ticket"

    # Ticket validity period in seconds (11 hours - gives 1 hour buffer before 12-hour default expiration)
    TICKET_VALIDITY_SECONDS = 11 * 60 * 60

    def _read_cached_ticket(self) -> str | None:
        """Read cached host-unlocked ticket if valid and not expired."""
        cache_path = self._get_ticket_cache_path()

        if not cache_path.exists():
            return None

        try:
            lines = cache_path.read_text(encoding="utf-8").strip().split("\n")

            if len(lines) < 4:
                # Old format without timestamp - clear and re-authenticate
                self._clear_cached_ticket()
                return None

            cached_user, cached_server, ticket, timestamp_str = (
                lines[0],
                lines[1],
                lines[2],
                lines[3],
            )
            p4port = getattr(self.p4, "port", None) or os.getenv("P4PORT", "")

            # Validate user/server match and ticket format
            if cached_user != self._user or cached_server != p4port:
                return None
            if len(ticket) != 32 or not re.match(r"^[0-9A-Fa-f]{32}$", ticket):
                return None

            # Check if ticket has expired based on creation timestamp
            try:
                created_at = float(timestamp_str)
                elapsed = time.time() - created_at
                if elapsed >= self.TICKET_VALIDITY_SECONDS:
                    log.debug(
                        f"Cached ticket expired ({elapsed / 3600:.1f} hours old), clearing cache"
                    )
                    self._clear_cached_ticket()
                    return None
                log.debug(
                    f"Cached ticket still valid ({(self.TICKET_VALIDITY_SECONDS - elapsed) / 3600:.1f} hours remaining)"
                )
            except ValueError:
                # Invalid timestamp - clear and re-authenticate
                self._clear_cached_ticket()
                return None

            return ticket
        except Exception:
            return None

    def _cache_ticket(self, ticket: str) -> None:
        """Cache the host-unlocked ticket with creation timestamp."""
        cache_path = self._get_ticket_cache_path()
        try:
            cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            p4port = getattr(self.p4, "port", None) or os.getenv("P4PORT", "")
            timestamp = time.time()
            # Store as separate lines: user, server, ticket, timestamp
            cache_path.write_text(f"{self._user}\n{p4port}\n{ticket}\n{timestamp}")
            cache_path.chmod(0o600)
            log.debug(
                f"Cached ticket, valid for {self.TICKET_VALIDITY_SECONDS / 3600:.0f} hours"
            )
        except Exception as e:
            log.warning(f"Failed to cache ticket: {e}")

    def _clear_cached_ticket(self) -> None:
        """Remove the cached ticket file."""
        cache_path = self._get_ticket_cache_path()
        try:
            if cache_path.exists():
                cache_path.unlink()
                log.debug("Cleared cached ticket")
        except Exception as e:
            log.warning(f"Failed to clear cached ticket: {e}")

    def _get_swarm_url(self) -> str:
        """
        Get the Swarm URL from P4 properties P4.Swarm.URL server property
        """
        # Try to get from P4 server property
        try:
            props = cast(
                list[RunPropertyL],
                self.p4.run_property("-l", "-n", "P4.Swarm.URL"),  # type: ignore
            )
            if props and len(props) > 0:
                url = props[0].get("value", "")
                if url:
                    return url.rstrip("/")
        except Exception as e:
            log.warning(f"Failed to fetch P4.Swarm.URL property: {e}")

        raise SwarmConfigError(
            "Could not determine Swarm URL. "
            "Set the SWARM_URL environment variable or configure P4.Swarm.URL on the server."
        )

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def __enter__(self) -> "SwarmClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def swarm_url(self) -> str:
        """Returns the base Swarm URL (for building review links)."""
        return self._base_url

    def get_review_id(self, cl_num: int) -> int | None:
        """
        Get the review ID associated with a changelist.
        """
        try:
            # By default get 100 most recent review ids
            response = self._client.get(
                "/reviews",
                params={
                    "author": self._user,
                },
            )
            response.raise_for_status()

            data = cast(RunSwarmGet, response.json())
            log.debug(f"get_review_id data for {cl_num}: {data}")

            review_entries: list[RunSwarmGetEntry] = data.get("data").get("reviews", [])

            # Look for the first cl_num in the 'changes' array of each review
            # The array contains [local_cl, swarm_shelf_cl] (e.g. [214, 236])
            for review in review_entries:
                if cl_num == review.get("changes", [])[0]:
                    review_id = review.get("id")
                    log.debug(f"Found match: CL {cl_num} is in Review {review_id}")
                    return int(review_id)

            log.debug(f"No review found for CL {cl_num}")
            return None

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise SwarmAuthError("Swarm authentication failed. ")
            raise SwarmAPIError(f"Failed to get review for CL {cl_num}: {e}")
        except httpx.RequestError as e:
            raise SwarmAPIError(f"Network error fetching review for CL {cl_num}: {e}")

    def create_review(self, cl_num: int, description: str) -> int:
        """
        Create a new Swarm review for a changelist.
        """
        try:
            response = self._client.post(
                "/reviews",
                data={
                    "change": cl_num,
                    "description": description,
                },
            )
            response.raise_for_status()

            data = cast(RunSwarmPost, response.json())

            review_data = data.get("data")
            log.debug(f"create_review review_data for {cl_num}: {data}")

            review_entries: list[RunSwarmPostEntry] = review_data.get("review", [])

            if not review_entries:
                raise SwarmAPIError(f"No review entry in response: {data}")

            review_id = review_entries[0].get("id")

            if not review_id:
                raise SwarmAPIError(f"No review ID in response: {data}")

            log.info(f"Created review {review_id} for CL {cl_num}")
            return int(review_id)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise SwarmAuthError("Swarm authentication failed. ")
            raise SwarmAPIError(f"Failed to create review for CL {cl_num}: {e}")
        except httpx.RequestError as e:
            raise SwarmAPIError(f"Network error creating review for CL {cl_num}: {e}")

    def update_review_description(self, review_id: int, description: str) -> None:
        """
        Update the description of an existing Swarm review.
        Note: Uses v9 API as the PATCH endpoint is not available in v11.
        """
        try:
            # Use v9 API - the PATCH /reviews/{id} endpoint is not available in v11
            response = self._client.patch(
                f"{self._base_url}/api/v9/reviews/{review_id}",
                data={
                    "description": description,
                },
            )
            response.raise_for_status()

            log.info(f"response for update {response.json()}")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise SwarmAuthError("Swarm authentication failed. ")
            raise SwarmAPIError(f"Failed to update review {review_id}: {e}")
        except httpx.RequestError as e:
            raise SwarmAPIError(f"Network error updating review {review_id}: {e}")
