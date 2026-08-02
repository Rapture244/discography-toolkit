# src/discography_toolkit/core/__init__.py
"""The domain: what a discography is made of and how to read it.

Nothing here knows about the command line, and nothing here prints.
Modules in this package answer questions -- where albums live, what a
name means, what a file's tags say -- and carry out the small, careful
writes those answers lead to. Everything above them is built out of
both.
"""

from __future__ import annotations
