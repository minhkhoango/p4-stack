"""
Contains the SwarmClient class for interacting with the Helix Swarm API.
"""

import logging
import httpx
import subprocess
import getpass
import os
import time
from pathlib import Path
from typing import Any, cast

from .p4_actions import P4Connection
from .types import (
    RunSwarmGet,
    RunSwarmPost,
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
    """

    API_VERSION = "v11"
    TICKET_VALIDITY_SECONDS = 11 * 60 * 60  # 11 hours

    def __init__(self, p4_conn: P4Connection) -> None:
        """
        Args:
            p4_conn: A P4Connection object
        """
        self.p4_conn = p4_conn

        self._user = self._resolve_user()
        self._base_url = self._resolve_swarm_url()
        self._ticket = self._resolve_ticket()

        self._client = httpx.Client(
            base_url=f"{self._base_url}/api/{self.API_VERSION}",
            auth=(self._user, self._ticket),
            timeout=30.0,
        )

        log.debug(f"SwarmClient init: {self._user} @ {self._base_url}")

    def __enter__(self) -> "SwarmClient":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    # --- Resolution Helpers ---

    def _resolve_user(self) -> str:
        """Get the P4 username."""
        user = self.p4_conn.user
        if not user:
            raise SwarmAuthError("Could not determine P4 user.")
        return user

    def _resolve_swarm_url(self) -> str:
        """
        Get the Swarm URL from P4 properties P4.Swarm.URL server property
        """
        url = self.p4_conn.get_property("P4.Swarm.URL")
        if url:
            return url.rstrip("/")

        env_url = os.getenv("SWARM_URL")
        if env_url:
            return env_url.rstrip("/")

        raise SwarmConfigError(
            "Swarm URL not found. Set 'P4.Swarm.URL' property or SWARM_URL env var."
        )

    # --- Ticket Management ---

    def _resolve_ticket(self) -> str:
        """Orchestrates ticket fetching: Cache -> Login Prompt -> Cache."""
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

    def _interactive_login(self) -> str:
        """Prompts user for password to generate a host-unlocked ticket."""
        try:
            password = getpass.getpass("Enter P4 password for Swarm authentication: ")
            # -a: host-unlocked (essential for Swarm API which might be on different host)
            # -p: print ticket
            proc = subprocess.run(
                ["p4", "login", "-a", "-p"],
                input=password,
                capture_output=True,
                text=True,
            )

            if proc.returncode != 0:
                raise SwarmAuthError("Password invalid or p4 login failed.")

            # stdout format: 'Enter password: \nAEBC...\n'
            ticket_match = re.search(r"[0-9A-Fa-f]{32}", proc.stdout)

            if ticket_match:
                ticket = ticket_match.group(0)
                log.debug(f"Generated host-unlocked P4 ticket: {ticket[:5]}...")
                self._cache_ticket(ticket)
                return ticket

            raise SwarmAuthError("Could not parse ticket from p4 login output.")

        except Exception as e:
            raise SwarmAuthError(f"Login process failed: {e}")

    def _get_ticket_cache_path(self) -> Path:
        return Path.home() / ".p4stack" / "ticket"

    def _read_cached_ticket(self) -> str | None:
        cache_path = self._get_ticket_cache_path()
        if not cache_path.exists():
            return None

        try:
            # Format: User\nPort\nTicket\nTimestamp
            lines = cache_path.read_text(encoding="utf-8").strip().split("\n")
            if len(lines) < 4:
                return None

            cached_user, cached_server, ticket, timestamp_str = lines[:4]

            p4port = self.p4_conn.port

            # 1. Context Check (User/Port match)
            if cached_user != self._user or cached_server != p4port:
                return None

            # 2. Integrity Check (Regex) - Fail fast on corruption
            if len(ticket) != 32 or not re.match(r"^[0-9A-Fa-f]{32}$", ticket):
                log.warning("Cached ticket corrupted.")
                return None

            # 3. Expiration CHeck
            age = time.time() - float(timestamp_str)
            if age > self.TICKET_VALIDITY_SECONDS:
                log.debug("Cached ticket expired.")
                return None

            return ticket
        except Exception:
            return None

    def _cache_ticket(self, ticket: str) -> None:
        """Cache the host-unlocked ticket with creation timestamp."""
        cache_path = self._get_ticket_cache_path()
        try:
            cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            p4port = self.p4_conn.port

            cache_path.write_text(f"{self._user}\n{p4port}\n{ticket}\n{time.time()}")
            cache_path.chmod(0o600)
            log.debug(
                f"Cached ticket, valid for {self.TICKET_VALIDITY_SECONDS / 3600:.0f} hours"
            )
        except Exception as e:
            log.warning(f"Failed to cache ticket: {e}")

    # --- API Methods ---

    def get_review_id(self, cl_num: int) -> int | None:
        """
        Get the review ID associated with a changelist.
        """
        try:
            response = self._client.get(
                "/reviews",
                params={
                    "author": self._user,
                },
            )
            response.raise_for_status()

            data = cast(RunSwarmGet, response.json())
            review_entries = data.get("data").get("reviews", [])

            # The array contains [swarm_shelf_cl, local_cl, swarm_shelf_cl, ...]
            for review in review_entries:
                if cl_num in review.get("changes", []):
                    review_id = review.get("id")
                    log.debug(f"Found match: CL {cl_num} is in Review {review_id}")
                    return int(review_id)

            log.debug(f"No review found for CL {cl_num}")
            return None
        except Exception as e:
            log.warning(f"Failed to fetch review for CL {cl_num}: {e}")
            return None

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

            review = data.get("data").get("review", [])
            if not review:
                raise SwarmAPIError(f"No review entry in response: {data}")

            review_id = review[0].get("id")

            log.info(f"Created review {review_id} for CL {cl_num}")
            return int(review_id)
        except Exception as e:
            raise SwarmAPIError(f"Create review failed: {e}")

    def update_review_description(self, review_id: int, description: str) -> None:
        """
        Update the description of an existing Swarm review.
        """
        try:
            response = self._client.put(
                f"/reviews/{review_id}/description",
                json={
                    "description": description,
                },
            )
            response.raise_for_status()

        except Exception as e:
            raise SwarmAPIError(f"Update review description failed: {e}")

    def update_review_content(self, review_id: int, change_num: int) -> None:
        """
        Update an existing review with a new changelist (creates a new version).
        """
        try:
            response = self._client.post(
                f"/reviews/{review_id}/replacewithchange",
                json={
                    "changeId": change_num,
                },
            )
            log.debug(
                f"update_review response: {response.status_code} - {response.text}"
            )
            response.raise_for_status()

        except Exception as e:
            raise SwarmAPIError(f"Update review failed: {e}")

    def build_review_url(
        self,
        review_id: int,
        from_version: int | None = None,
        to_version: int | None = None,
    ) -> str:
        """
        Build a URL to view a review, optionally with version comparison.
        """
        base_url = f"{self._base_url}/reviews/{review_id}"
        if from_version and to_version:
            return f"{base_url}/?v={from_version},{to_version}"

        return base_url
