"""Verify real MCP discovery/calls against the local gateway mock."""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from kaloclip.mcp_server import TOOLS, create_server

IMG = "739230=https://resource.kaloclip.com/a.png"
SCRIPT_ARGS = {"image": [IMG], "title": "T", "category": ["Beauty"], "model": "sd2", "duration": 15}


def ok(data):
    return {"success": True, "code": None, "message": None, "data": data}


def test_discovery_and_annotations(env):
    async def check():
        async with Client(create_server()) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert set(tools) == set(TOOLS)
            assert not any(name.startswith("config_") for name in tools)
            assert tools["options"].annotations.read_only_hint is True
            for name in ("script_create", "video_create", "analysis_create"):
                assert tools[name].annotations.read_only_hint is False
                assert "yes=true" in tools[name].description
            assert tools["image_upload"].annotations.read_only_hint is False
            assert "instead of writing scripts yourself" in tools["script_create"].description
            schema = tools["image_import"].input_schema
            assert schema["required"] == ["urls"] and schema["additionalProperties"] is False
            assert tools["job"].input_schema["required"] == ["job_id"]
            assert "json" not in tools["credit"].input_schema["properties"]
            timeout = tools["job"].input_schema["properties"]["timeout"]
            assert (timeout["minimum"], timeout["maximum"]) == (1, 1800)

    anyio.run(check)


def test_write_tool_dry_run_then_confirm(env, capsys):
    env.responses["/scripts"] = ok(4242)

    async def check():
        async with Client(create_server()) as client:
            dry = await client.call_tool("script_create", SCRIPT_ARGS)
            assert not dry.is_error
            preview = dry.structured_content["data"]["dry_run"]
            assert preview["credits"] == 2 and preview["body"]["creditSourceType"] == "OPENAPI"
            assert [r for r in env.requests if r[1] == "/scripts"] == []
            done = await client.call_tool("script_create", {
                **SCRIPT_ARGS, "yes": True, "idempotency_key": preview["idempotency_key"],
            })
            assert not done.is_error
            assert done.structured_content == {"data": {"jobId": 4242}}
            return preview["idempotency_key"]

    key = anyio.run(check)
    assert capsys.readouterr().out == ""
    (_, _, _, headers, body), = [r for r in env.requests if r[1] == "/scripts"]
    assert headers["idempotency-key"] == key
    assert "x-user-id" not in headers


@pytest.mark.parametrize("name,arguments", [
    ("job", {"job_id": "1", "timeout": 0}),
    ("job", {"job_id": "1", "timeout": 5000}),
    ("job", {}),
    ("options", {"unknown": True}),
    ("video_preview", {"model": "sd2", "duration": "15"}),
    ("video_preview", {"model": "sd2", "duration": 15, "resolution": "8K"}),
    ("image_import", {"urls": []}),
])
def test_invalid_arguments_do_not_call(env, name, arguments):
    async def check():
        async with Client(create_server()) as client:
            result = await client.call_tool(name, arguments)
            assert result.is_error

    anyio.run(check)
    assert env.requests == []


def test_upstream_error_is_tool_error(env):
    env.responses["/jobs"] = ok(None) | {"success": False, "code": "RESOURCE_NOT_FOUND", "message": "nope"}

    async def check():
        async with Client(create_server()) as client:
            result = await client.call_tool("job", {"job_id": "1"})
            assert result.is_error
            assert result.structured_content["kind"] == "parameter"
            assert result.structured_content["help"]

    anyio.run(check)


def test_real_stdio_process(env):
    env.responses["/credits"] = ok({"totalRemain": 7})
    scripts = Path(__file__).resolve().parents[1] / "skills" / "kaloclip" / "scripts"
    process_env = dict(os.environ, PYTHONPATH=str(scripts))

    async def check():
        async with Client(StdioServerParameters(
            command=sys.executable, args=["-m", "kaloclip.mcp_server"], env=process_env,
        )) as client:
            assert len((await client.list_tools()).tools) == len(TOOLS)
            result = await client.call_tool("credit", {})
            assert not result.is_error
            assert result.structured_content == {"data": {"totalRemain": 7}}

    anyio.run(check)
