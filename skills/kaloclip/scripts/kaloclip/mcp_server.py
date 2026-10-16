"""Local stdio MCP adapter over the CLI's command registry.

SDK dependencies are optional; importing or running the CLI never loads them.
"""

from __future__ import annotations

import json
import sys
from typing import Literal

from . import __version__, config, render
from .commands import COMMANDS
from .errors import ClipError, UsageError

# Configuration is controlled by the operator, never by model-supplied arguments.
TOOLS = {c.path.replace(" ", "_"): c for c in COMMANDS if not c.path.startswith("config ")}
# Terminal presentation only; MCP always returns the CLI's raw JSON data.
PRESENTATION_FLAGS = {"json"}
# Free, but they store new image assets on the account.
_CREATES_ASSETS = {"image upload", "image import"}

_WRITE_NOTE = (
    " Spends credits. Without yes=true nothing is sent: the result is a dry_run with the exact request, "
    "its credit cost and an idempotency_key. Show it to the user, and only after they agree call again "
    "with the same arguments plus yes=true and that idempotency_key."
)


def _list_field(cmd) -> str:
    return f"{cmd.positional}s"


def create_server():
    import anyio
    from mcp import types
    from mcp.server import Server
    from pydantic import ConfigDict, Field, ValidationError, create_model

    models = {}
    tools = []
    for name, cmd in TOOLS.items():
        fields = {}
        if cmd.positional:
            if cmd.pos_max == 1:
                fields[cmd.positional] = (str, Field(min_length=1, description=cmd.positional))
            else:
                fields[_list_field(cmd)] = (
                    list[str], Field(min_length=max(cmd.pos_min, 1), max_length=6,
                                     description=f"1-6 {cmd.positional} values"),
                )
        for flag in cmd.flags:
            if flag.dest in PRESENTATION_FLAGS:
                continue
            annotation = {"str": str, "int": int, "flag": bool, "multi": list[str]}[flag.kind]
            if flag.choices:
                annotation = Literal[flag.choices]
            if flag.default is None:
                annotation = annotation | None
            fields[flag.dest] = (annotation, Field(default=flag.default, description=flag.help,
                                                   ge=flag.min, le=flag.max))
        model = create_model(
            f"{name}Arguments", __config__=ConfigDict(extra="forbid", strict=True), **fields
        )
        models[name] = model
        description = cmd.summary + (_WRITE_NOTE if cmd.writes else "")
        read_only = not cmd.writes and cmd.path not in _CREATES_ASSETS
        tools.append(types.Tool(
            name=name, description=description, input_schema=model.model_json_schema(),
            annotations=types.ToolAnnotations(
                read_only_hint=read_only, destructive_hint=False,
                idempotent_hint=read_only, open_world_hint=True,
            ),
        ))

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=tools)

    def execute(name, arguments):
        cmd = TOOLS[name]
        opts = {flag.dest: list(flag.default) if isinstance(flag.default, list) else flag.default
                for flag in cmd.flags}
        arguments = dict(arguments)
        args = []
        if cmd.positional:
            args = ([arguments.pop(cmd.positional)] if cmd.pos_max == 1
                    else arguments.pop(_list_field(cmd)))
            if any(not value.strip() for value in args):
                raise UsageError(f"{cmd.positional} values must not be empty")
        opts.update(arguments)
        opts["json"] = True
        with render.collect_results() as results:
            status = cmd.handler(config.load(), opts, args)
        if len(results) != 1:
            raise RuntimeError(f"{name} did not produce one structured result")
        return {"data": results[0]}, status != 0

    async def call_tool(ctx, params):
        try:
            if params.name not in models:
                raise UsageError(f"unknown tool '{params.name}'")
            arguments = models[params.name].model_validate(params.arguments or {}).model_dump()
            # urllib requests, retries and --wait polling block; keep them off the event loop.
            payload, is_error = await anyio.to_thread.run_sync(execute, params.name, arguments)
        except ValidationError as exc:
            payload = {"error": "Invalid tool arguments", "kind": "parameter",
                       "details": exc.errors(include_input=False, include_url=False)}
            is_error = True
        except (ClipError, UsageError) as exc:
            payload = {"error": str(exc), "kind": getattr(exc, "kind", "parameter"),
                       "help": exc.help_lines}
            is_error = True
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))],
            structured_content=payload, is_error=is_error,
        )

    return Server(
        "kaloclip", version=__version__,
        instructions="Kaloclip AI short-video creation. Flow: options → product_resolve (or image_upload) → "
                     "script_create → script_get(wait) → video_preview → video_create → video_get(wait). "
                     "model, duration and resolution must come from one options row; its credits is the "
                     "video's price. "
                     "script_create, video_create and analysis_create spend credits and only return a "
                     "dry run until called again with yes=true after the user agrees. Jobs are async: "
                     "poll them, never resubmit one that is queued/processing. Credentials come from the "
                     "local environment/configuration. Results contain raw upstream JSON data.",
        on_list_tools=list_tools, on_call_tool=call_tool,
        get_tool_input_schema=lambda name: models[name].model_json_schema() if name in models else None,
    )


async def serve():
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def entry() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Kaloclip local stdio MCP server")
    parser.add_argument("--version", action="version", version=f"kaloclip-mcp {__version__}")
    parser.parse_args()
    try:
        import anyio
        import mcp  # noqa: F401
    except ImportError:
        print('MCP dependencies missing. Install with: uv pip install "kaloclip-mcp[mcp]"',
              file=sys.stderr)
        raise SystemExit(1)
    try:
        anyio.run(serve)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    entry()
