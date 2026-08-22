"""The powerio server driven the way a model drives it: over stdio, through
the MCP SDK.

Every other powerio test calls the tool functions in process, which skips the
SDK's argument handling entirely. That gap hid a real defect: the SDK rewrites
a string argument whose text parses as JSON into the parsed object before
validation, which destroyed every `json` / `content` / `package_json` argument
carrying JSON before the tool saw it. powerio 0.9.0 annotates those arguments as
bare `str` on its registered tools, which is what stops the rewriting, and the
tests below are what hold it closed. Nothing in an in-process suite can see any
of this, which is why this file drives the real transport.

Launching through `python -m powermcp run powerio` also covers the runner and
registry wiring end to end, so a broken launch fails here rather than only for
a user.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("powerio", minversion="0.9.0")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

CASE9 = Path(__file__).resolve().parent / "data" / "case9.m"

# The SDK waits forever by default, so a server that starts and then blocks
# would hang the suite with nothing to fail it.
TIMEOUT = 60.0


def _run(steps):
    """Drive one stdio session, returning whatever ``steps`` returns.

    ``steps`` is an async callable taking the initialized session.
    """

    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "powermcp", "run", "powerio"],
            env=dict(os.environ),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
                # wait_for rather than asyncio.timeout: this runs on 3.10 too.
                await asyncio.wait_for(session.initialize(), TIMEOUT)
                return await steps(session)

    return asyncio.run(go())


def _payload(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def test_launch_serves_the_canonical_tool_surface():
    async def steps(session):
        return sorted(t.name for t in (await session.list_tools()).tools)

    required = {
        "convert",
        "diagnostics",
        "display",
        "matrix",
        "normalize",
        "parse",
        "save",
        "summary",
    }
    assert required <= set(_run(steps))


def test_a_path_argument_survives_the_transport():
    async def steps(session):
        return _payload(await session.call_tool("summary", {"path": str(CASE9)}))

    summary = _run(steps)
    assert summary["schema"] == "powerio.summary"
    assert summary["elements"]["buses"] == 9


def test_non_json_content_survives_the_transport():
    # MATPOWER text does not parse as JSON, so the SDK leaves it alone. This is
    # the control for the two JSON carrying cases below.
    async def steps(session):
        return _payload(
            await session.call_tool(
                "convert",
                {
                    "to_format": "psse",
                    "content": CASE9.read_text(),
                    "from_format": "matpower",
                },
            )
        )

    assert _run(steps)["text"]


def test_the_json_transport_round_trips_over_the_transport():
    # Recorded the SDK rewriting a string that parses as JSON, which made every
    # argument not annotated exactly `str` unusable. powerio 0.9.0's bare `str`
    # annotations close it for every mcp 2.x, so this now asserts the round trip.
    async def steps(session):
        parsed = _payload(await session.call_tool("parse", {"path": str(CASE9)}))
        assert parsed["json_format"] == "model-json"
        return _payload(
            await session.call_tool(
                "summary", {"json": parsed["json"], "json_format": "model-json"}
            )
        )

    assert _run(steps)["elements"]["buses"] == 9


def test_the_package_transport_reaches_summary_over_the_transport():
    # Same SDK rewriting as above. `diagnostics` always took `package_json` as a
    # required bare `str`, so it kept working while `summary` refused the same
    # package text; both take it now.
    async def steps(session):
        parsed = _payload(
            await session.call_tool(
                "parse", {"path": str(CASE9), "transport": "package"}
            )
        )
        package = parsed["package_json"]
        assert not (
            await session.call_tool("diagnostics", {"package_json": package})
        ).is_error
        return _payload(
            await session.call_tool("summary", {"package_json": package})
        )

    assert _run(steps)["elements"]["buses"] == 9
