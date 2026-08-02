# src/discography_toolkit/cli/commands/tags/notices.py
"""What a tag pass saw but would not act on.

Two things every tag command reports the same way: a file it could not
read, and a file it had nothing to derive a value from. Both come out of
the plan as "nothing written", which is exactly why they are said aloud
-- a bare zero cannot tell a shelf already correct from one the pass
could make no sense of.

Phrased here rather than per command because they were phrased per
command, four times over, and the wording had already begun to differ.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discography_toolkit.cli.console import Notice

if TYPE_CHECKING:
    from collections.abc import Callable

    from discography_toolkit.operations import tagging


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def unreadable(plan: tagging.TagPlan) -> Notice | None:
    """Name the files the pass could not read at all.

    An unreadable file is not a failure of the run -- nothing was
    attempted on it -- but it is the one thing here a person can go and
    look at, so it carries the reason mutagen gave alongside the path.

    Args:
        plan: The plan to inspect.

    Returns:
        The notice, or `None` when every file read cleanly.
    """
    if not plan.errors:
        return None

    return Notice(
        summary=f"{len(plan.errors)} file(s) could not be read",
        details=tuple(f"{str(outcome.path)!r} - {outcome.detail}" for outcome in plan.errors),
    )


def underivable(
    plan: tagging.TagPlan,
    missing: Callable[[tagging.TrackOutcome], bool],
    summary: str,
) -> Notice | None:
    """Name the files the pass had nothing to write a value from.

    Only tracks the plan calls already correct are considered: a track
    with nothing to derive is left untouched, which is indistinguishable
    from one already right until it is asked why. `missing` is what
    asks, and it differs per command -- no album folder above the track,
    no year in that folder's name, no Title tag to recase.

    Args:
        plan: The plan to inspect.
        missing: Whether one outcome had nothing to derive from.
        summary: How the count reads, e.g. "sit under no album folder".

    Returns:
        The notice, or `None` when every track had something to work
        from.
    """
    found: list[tagging.TrackOutcome] = [
        outcome
        for outcome in plan.outcomes
        if outcome.status == "already_correct" and missing(outcome)
    ]
    if not found:
        return None

    return Notice(
        summary=f"{len(found)} file(s) {summary}",
        details=tuple(f"{str(outcome.path)!r}" for outcome in found),
    )
