# src/discography_toolkit/cli/commands/__init__.py
"""One module per command group.

Each pairs with a step: the step computes and returns a result, the
command renders it. Nothing in `steps` prints; nothing here computes.
"""

from __future__ import annotations
