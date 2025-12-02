"""
Implements the `p4-stack upload` command.
Uploads an entire stack to Swarm for review, creating and linking reviews.
"""

import logging
import re
import typer
from rich.console import Console

from ..core.p4_actions import P4Connection, P4LoginRequiredError
from ..core.graph import build_stack_graph, get_stack_from_base
from ..core.swarm import SwarmClient, SwarmAuthError, SwarmConfigError

log = logging.getLogger(__name__)
console = Console(stderr=True, no_color=True)


def _strip_existing_stack_info(description: str) -> str:
    """Remove any existing stack navigation info from description."""
    # Remove Depends-On tags
    desc = re.sub(r"(\\n)+Depends-On: \d+", "", description)
    desc = re.sub(r"\n+Depends-On: \d+\s*$", "", desc, flags=re.MULTILINE)

    # Remove old Navigation blocks
    lines = [line for line in desc.split("\n") if "**Stack Navigation: **" not in line]

    return "\n".join(lines).strip()


def _generate_nav_description(
    original_desc: str,
    cl_to_review: dict[int, int],  # CL -> ReviewID
    stack: list[int],
    current_idx: int,
    swarm: SwarmClient,
) -> str:
    """Generates the description with Next/Prev links."""

    clean_desc = _strip_existing_stack_info(original_desc)

    suffix = {1: "st", 2: "nd", 3: "rd"}.get(current_idx + 1, "th")
    nav_parts: list[str] = []

    if current_idx > 0:
        prev_cl = stack[current_idx - 1]
        if prev_id := cl_to_review.get(prev_cl):
            if current_idx > 1:
                parent_url = swarm.build_review_url(prev_id, 1, 2)
            else:
                parent_url = swarm.build_review_url(prev_id)

            nav_parts.append(f"Parent CL: [Review {prev_id}]({parent_url})")

    if current_idx < len(stack) - 1:
        next_cl = stack[current_idx + 1]
        if next_id := cl_to_review.get(next_cl):
            child_url = swarm.build_review_url(next_id, 1, 2)
            nav_parts.append(f"Child CL: [Review {next_id}]({child_url})")

    nav_line = " | ".join(nav_parts) if nav_parts else ""

    new_desc = (
        f"{current_idx + 1}{suffix} changelist of the stack\n"
        f"{nav_line}\n\n"
        f"{clean_desc}"
    )
    return new_desc


# --- Main Command ---


def upload_stack(root_cl: int) -> None:
    """
    Upload an entire stack to Swarm, creating or updating reviews
    and linking them with navigation in the descriptions.
    """
    try:
        with P4Connection() as p4_conn:
            # 1. Validation & Graph Building
            graph, child_to_parent, pending_cls = build_stack_graph(p4_conn)

            # Validate: root_cl must have NO parent (it's the base of a stack)
            if root_cl in child_to_parent or root_cl not in pending_cls:
                console.print(
                    f"Error: Please upload from the root of your changelist stack."
                )
                raise typer.Exit(code=1)

            stack = get_stack_from_base(root_cl, graph)
            console.print(
                f"Found stack with {len(stack)} changelist(s): {' → '.join(map(str, stack))}"
            )

            # 2. Init Swarm
            try:
                swarm = SwarmClient(p4_conn)
            except (SwarmConfigError, SwarmAuthError) as e:
                console.print(f"Swarm Setup Error: {e}")
                raise typer.Exit(1)

            cl_to_review: dict[int, int] = {}

            # 3. Execution (Two Pass)
            with swarm:
                # --- PASS 1: Create/Update Reviews ---
                for idx, cl_num in enumerate(stack):
                    if not p4_conn.ensure_shelved(cl_num):
                        log.warning(f"CL {cl_num} empty or unshelvable.")

                    # Check existing
                    existing_id = swarm.get_review_id(cl_num)

                    if existing_id:
                        # Already exists, just track it
                        cl_to_review[cl_num] = existing_id

                    else:
                        # Create new review
                        parent_cl = stack[idx - 1] if idx else None
                        desc = p4_conn.get_cl_description(cl_num)

                        if parent_cl:
                            # Stacked Review Logic (Seed from parent)
                            seed_cl = p4_conn.create_review_seed(parent_cl, cl_num)

                            if seed_cl:
                                # Create review from seed (v1)
                                new_review_id = swarm.create_review(seed_cl, desc)
                                # Update with actual content (v2)
                                swarm.update_review_content(new_review_id, cl_num)

                                p4_conn.cleanup_seed(seed_cl)
                                cl_to_review[cl_num] = new_review_id

                            else:
                                # Fallback: Create review directly (depot base vs child)
                                new_review_id = swarm.create_review(cl_num, desc)
                                cl_to_review[cl_num] = new_review_id

                        else:
                            # Root CL: Standard creation (depot base vs root)
                            new_review_id = swarm.create_review(cl_num, desc)
                            console.print(
                                f"  CL {cl_num} → Review {new_review_id} (created root)"
                            )
                            cl_to_review[cl_num] = new_review_id

                # --- PASS 2: Link Descriptions ---
                for idx, cl_num in enumerate(stack):
                    review_id = cl_to_review[cl_num]

                    # Get fresh description from P4 (safety: don't overwrite manual edits)
                    original_desc = p4_conn.get_cl_description(cl_num)

                    # Build the updated description with links
                    updated_desc = _generate_nav_description(
                        original_desc=original_desc,
                        cl_to_review=cl_to_review,
                        stack=stack,
                        current_idx=idx,
                        swarm=swarm,
                    )

                    # Update the review description on Swarm
                    swarm.update_review_description(review_id, updated_desc)

                # --- Step 6: Summary ---
                console.print("\nStack uploaded successfully!")

    except P4LoginRequiredError:
        console.print("[red]Login required.[/red] Run `p4 login`.")
        raise typer.Exit(1)
    except Exception as e:
        log.exception("Upload failed")
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)
