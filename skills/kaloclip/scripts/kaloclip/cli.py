"""Entry point and dispatcher for the `clip` CLI."""

from __future__ import annotations

import sys

from . import __version__, config, reference, render, update
from .commands import ALIASES, COMMANDS, DEFAULT_SUB
from .core import command_help, parse_args
from .errors import ClipError, UsageError


def _index():
    groups: dict[str, dict[str, object]] = {}
    for cmd in COMMANDS:
        parts = cmd.path.split()
        groups.setdefault(parts[0], {})[parts[1] if len(parts) > 1 else ""] = cmd
    return groups


def _resolve(argv: list[str]):
    groups = _index()
    head = ALIASES.get(argv[0], argv[0])
    group = groups.get(head)
    if group is None:
        known = sorted(groups)
        raise UsageError(
            f"unknown command '{argv[0]}'",
            [f"commands: {', '.join(known)}", "Run `clip` for the full command map"],
        )
    if "" in group:  # single command (no subcommands)
        return group[""], argv[1:]
    if len(argv) > 1 and argv[1] in group:
        return group[argv[1]], argv[2:]
    default = DEFAULT_SUB.get(head)
    if default and (len(argv) == 1 or argv[1].startswith("-")):
        # bare noun: `clip config` runs `config show`
        return group[default], argv[1:]
    subs = ", ".join(sorted(group))
    raise UsageError(
        f"unknown subcommand '{argv[1] if len(argv) > 1 else ''}' for `{head}`",
        [f"valid subcommands: clip {head} {{{subs}}}"],
    )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cfg = config.load()
    update.check(cfg)  # stderr-only upgrade hint; never raises

    if not argv or argv[0] in ("--help", "-h", "help"):
        render.out(reference.home_text(cfg))
        return 0
    if argv[0] in ("--version", "-v", "version"):
        render.out(f"clip {__version__}")
        return 0

    try:
        cmd, rest = _resolve(argv)
        opts, pos = parse_args(cmd, rest)
        if opts.get("help"):
            render.out(command_help(cmd))
            return 0
        return cmd.handler(cfg, opts, pos)
    except UsageError as e:
        render.emit_error(str(e), e.help_lines)
        return 2
    except ClipError as e:
        render.emit_error(str(e), e.help_lines)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


def entry() -> None:
    sys.exit(main())
