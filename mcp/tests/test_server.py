"""End-to-end over the real MCP protocol: list tools, read a schema, call a tool.

The in-memory session from the MCP SDK speaks JSON-RPC to the same FastMCP app
a client would spawn, so a broken tool registration or a missing description
fails here rather than at someone's first use.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cinema_mcp.client import CinemaClient
from cinema_mcp.server import build_server

EXPECTED = {
    "list_camera_moves",
    "plan_shot",
    "render_shot",
    "reshoot_shot",
    "grade_shot",
    "list_shots",
    "get_shot",
    "cut_sequence",
    "cut_music_video",
    "backend_status",
}


def _client_for(backend) -> CinemaClient:
    return CinemaClient(url=backend.url)


def test_every_tool_is_registered_with_an_agent_sized_description(backend):
    server = build_server(_client_for(backend))

    async def scenario():
        async with create_connected_server_and_client_session(server) as session:
            return await session.list_tools()

    listing = anyio.run(scenario)
    names = {tool.name for tool in listing.tools}
    assert names == EXPECTED
    for tool in listing.tools:
        assert tool.description, "%s has no description" % tool.name
        assert len(tool.description) > 80, "%s description is too thin for an agent" % tool.name
        assert tool.inputSchema.get("type") == "object"

    by_name = {tool.name: tool for tool in listing.tools}
    props = by_name["render_shot"].inputSchema["properties"]
    assert {"engine", "camera_rig", "prompt", "quality"} <= set(props)
    assert "engine" in by_name["render_shot"].inputSchema.get("required", [])


def test_render_shot_round_trips_through_the_protocol(backend):
    server = build_server(_client_for(backend))

    async def scenario():
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool(
                "render_shot",
                {
                    "engine": "t2v",
                    "prompt": "a red kite over a cold sea",
                    "camera_rig": {"move": "dolly_in", "lens": "35mm"},
                    "quality": "draft",
                },
            )

    result = anyio.run(scenario)
    assert not result.isError
    text = "".join(block.text for block in result.content if block.type == "text")
    assert "shot_id" in text
    assert backend.posts("/api/shots")[0]["camera"]["move"] == "dolly_in"


def test_a_tool_error_reaches_the_client_as_an_error_result(backend):
    server = build_server(_client_for(backend))

    async def scenario():
        async with create_connected_server_and_client_session(server) as session:
            return await session.call_tool("get_shot", {"shot_id": "s_missing"})

    result = anyio.run(scenario)
    assert result.isError
    text = "".join(block.text for block in result.content if block.type == "text")
    assert "shot not found" in text


def test_build_server_accepts_an_injected_client_without_a_backend(monkeypatch):
    monkeypatch.setenv("CINEMA_URL", "http://127.0.0.1:1")
    server = build_server()
    assert server.name == "cinema"
    assert server.instructions
    assert "plan_shot" in server.instructions


@pytest.mark.parametrize("flag", ["--check"])
def test_check_mode_exits_nonzero_when_the_backend_is_down(flag, capsys):
    from cinema_mcp import server as server_mod

    code = server_mod.main([flag, "--url", "http://127.0.0.1:1"])
    out = capsys.readouterr().out
    assert code == 1
    assert "cannot reach the Cinema backend" in out
    assert "CINEMA_URL" in out
