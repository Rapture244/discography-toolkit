# src/discography_toolkit/cli/commands/__init__.py
"""One module per command group.

A command owns everything about how it is asked for and how it reads:
its options, its guards, the order it runs its steps in, and what it
prints. The `operations` beneath it compute and return results without
printing, and `core` answers questions without deciding anything -- so
what is left here is the policy, which is the part that differs per
command and belongs where the command is.

`layout` is the clearest case: the five folder steps could be reordered
by anyone reading them, and the fact that they cannot be is written
down here rather than in any of them.
"""

from __future__ import annotations
