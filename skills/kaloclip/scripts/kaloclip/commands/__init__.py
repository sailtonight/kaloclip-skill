"""Command registry: every module contributes Command objects."""

from __future__ import annotations

from ..core import Command
from . import assets, creation, meta

COMMANDS: list[Command] = []
for _mod in (assets, creation, meta):
    COMMANDS.extend(_mod.COMMANDS)

# noun -> default subcommand when invoked bare
DEFAULT_SUB = {"config": "show", "asset": "list"}

ALIASES = {
    "assets": "asset",
    "credits": "credit",
    "images": "image",
    "products": "product",
    "scripts": "script",
    "videos": "video",
    "jobs": "job",
}
