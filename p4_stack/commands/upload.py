"""
Implements the `p4-stack upload` command.
Uploads an entire stack to Swarm for review, creating and linking reviews.
"""

import logging
from typing import cast
import re

import typer
from rich.console import Console
from P4 import P4  # type: ignore

from ..core.p4_actions import (
    P4Connection,
    P4Exception,
    P4LoginRequiredError,
)
from ..core.graph import build_stack_graph, get_stack_from_base
from ..core.swarm import (
    SwarmClient,
    SwarmError,
    SwarmAuthError,
    SwarmConfigError,
)
from ..core.types import RunDescribeS

log = logging.getLogger(__name__)
console = Console(stderr=True, no_color=True)


def _strip_existing_stack_info(description: str) -> str:
    """
    Remove any existing stack navigation info from description.
    """
    # Remove Depends-On tags - handles both actual newlines and literal \n strings
    new_desc = re.sub(r"(\\n)+Depends-On: \d+", "", description)
    new_desc = re.sub(r"\n+Depends-On: \d+\s*$", "", new_desc, flags=re.MULTILINE)

    lines = new_desc.split("\n")
    # remove any Stack Navigation lines
    filtered_lines = [line for line in lines if "**Stack Navigation: **" not in line]

    new_desc = "\n".join(filtered_lines).strip()

    return new_desc


def _build_stack_description(
    original_desc: str,
    cl_to_review: dict[int, int],
    stack: list[int],
    current_idx: int,
) -> str:
    """
    Build the updated description with stack navigation links.

    Args:
        original_desc: The original CL description.
        cl_to_review: Mapping of CL numbers to review IDs.
        stack: The ordered stack list [Root, Child1, Child2, ...].
        current_idx: The index of the current CL in the stack.
    """
    # Clean any existing stack info first
    clean_desc = _strip_existing_stack_info(original_desc)

    suffix = {1: "st", 2: "nd", 3: "rd"}.get(current_idx + 1, "th")
    nav_parts: list[str] = []

    if current_idx > 0:
        prev_cl = stack[current_idx - 1]
        prev_review = cl_to_review.get(prev_cl)
        nav_parts.append(f"Parent CL: Review {prev_review}")

    if current_idx < len(stack) - 1:
        next_cl = stack[current_idx + 1]
        next_review = cl_to_review.get(next_cl)
        nav_parts.append(f"Child CL: Review {next_review}")

    nav_line = " | ".join(nav_parts) if nav_parts else ""

    new_desc = (
        f"{current_idx + 1}{suffix} changelist of the stack\n"
        f"{nav_line}\n\n"
        f"{clean_desc}"
    )
    return new_desc


def _get_cl_description(p4: P4, cl_num: int) -> str:
    """
    Fetch the current description of a changelist from Perforce.
    """
    try:
        result = cast(list[RunDescribeS], p4.run_describe("-s", cl_num))  # type: ignore
        if result and len(result) > 0:
            return result[0].get("desc", "")
    except Exception as e:
        log.warning(f"Failed to get description for CL {cl_num}: {e}")
    return ""


def upload_stack(root_cl: int) -> None:
    """
    Upload an entire stack to Swarm, creating or updating reviews
    and linking them with navigation in the descriptions.
    """
    try:
        with P4Connection() as p4_conn:
            p4 = p4_conn.p4

            # --- Step 1: Build the graph and validate root ---
            graph, child_to_parent, pending_cls = build_stack_graph(p4)

            # Validate: root_cl must have NO parent (it's the base of a stack)
            if root_cl in child_to_parent or root_cl not in pending_cls:
                console.print(
                    f"Error: Please upload from the root of your changelist stack."
                )
                raise typer.Exit(code=1)

            # --- Step 2: Get the full stack ---
            stack = get_stack_from_base(root_cl, graph)

            if not stack:
                console.print(f"Error:Could not find stack for CL {root_cl}.")
                raise typer.Exit(code=1)

            console.print(
                f"Found stack with {len(stack)} changelist(s): {' → '.join(map(str, stack))}"
            )

            # --- Step 3: Connect to Swarm ---
            try:
                swarm = SwarmClient(p4)
            except SwarmConfigError as e:
                console.print(f"Swarm Configuration Error: {e}")
                raise typer.Exit(code=1)
            except SwarmAuthError as e:
                console.print(f"Authentication Error: {e}")
                raise typer.Exit(code=1)

            with swarm:
                # --- Step 4: Phase 1 - Upsert reviews ---
                console.print("\nPhase 1: Creating/finding reviews...")
                cl_to_review: dict[int, int] = {}

                for cl_num in stack:
                    # Get current description from P4
                    description = _get_cl_description(p4, cl_num)
                    # Check if review already exists

                    existing_review = swarm.get_review_id(cl_num)

                    if existing_review:
                        console.print(
                            f"  CL {cl_num} → Review {existing_review} (existing)"
                        )
                        cl_to_review[cl_num] = existing_review
                    else:
                        # Create new review
                        new_review = swarm.create_review(cl_num, description)
                        console.print(f"  CL {cl_num} → Review {new_review} (created)")
                        cl_to_review[cl_num] = new_review

                # --- Step 5: Phase 2 - Link reviews ---
                console.print("\nPhase 2: Linking reviews...")

                for idx, cl_num in enumerate(stack):
                    review_id = cl_to_review[cl_num]

                    # Get fresh description from P4 (safety: don't overwrite manual edits)
                    original_desc = _get_cl_description(p4, cl_num)

                    # Build the updated description with links
                    updated_desc = _build_stack_description(
                        original_desc=original_desc,
                        cl_to_review=cl_to_review,
                        stack=stack,
                        current_idx=idx,
                    )

                    # Update the review description on Swarm
                    swarm.update_review_description(review_id, updated_desc)

                # --- Step 6: Summary ---
                console.print("\nStack uploaded successfully!")

    except P4LoginRequiredError as e:
        console.print(f"\nLogin required: {e}")
        raise typer.Exit(code=1)
    except SwarmError as e:
        console.print(f"\nSwarm Error: {e}")
        raise typer.Exit(code=1)
    except P4Exception as e:
        console.print(f"\nPerforce Error: {e}")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        log.exception(f"Unexpected error during upload: {e}")
        console.print(f"\nAn unexpected error occurred: {e}")
        raise typer.Exit(code=1)
