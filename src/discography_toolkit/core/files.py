# src/discography_toolkit/core/files.py
"""The careful part of moving something on disk.

One function, because one thing about renaming is not obvious: a change
of case alone is not a rename on a case-insensitive filesystem, where
the source and the target are the same file. Windows and macOS are both
case-insensitive by default, and recasing folder and track names is half
of what this toolkit does -- so the awkward case is the common one, not
an edge.

Deciding whether a rename is wanted, and refusing one that would
overwrite something, belong to the step asking for it. Two steps guard
that differently -- one at plan time, one at apply time -- so neither
guard lives here. This only carries the move out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# ==================================================================================== #
#                                      PUBLIC API                                      #
# ==================================================================================== #
def rename(source: Path, target: Path, *, staging_prefix: str) -> str | None:
    """Rename a file or folder, safely for a change of case alone.

    A change that alters more than case is a plain rename. One that
    alters only case goes via a staging name first: on a case-insensitive
    filesystem the source and the target are one file, and a direct
    rename is refused or silently does nothing depending on the platform.

    Case is compared with `casefold` rather than `lower`, since lowering
    is not one-to-one everywhere -- German "STRASSE" and "straße" are the
    same word cased two ways, and `lower` alone would call that a plain
    rename and skip the staging the filesystem needs.

    Nothing is checked before the move: a target that must not be
    overwritten is the caller's to guard.

    Args:
        source: The file or folder to move.
        target: Where it should go.
        staging_prefix: What the intermediate name starts with, for a
            case-only change. Distinct per step, so a run interrupted
            between the two moves leaves behind a name that says which
            step left it.

    Returns:
        The failure's detail, or `None` on success.
    """
    try:
        if source.name.casefold() == target.name.casefold():
            staging: Path = source.with_name(f"{staging_prefix}{target.name}")
            _ = source.rename(staging)
            _ = staging.rename(target)
        else:
            _ = source.rename(target)
    except OSError as exc:
        return str(exc)
    return None
